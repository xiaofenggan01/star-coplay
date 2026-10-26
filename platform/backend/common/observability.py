from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import math
import os
import re
import socket
import sys
import traceback
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SINK_OBSERVATIONS: dict[str, dict[str, Any]] = {
    "operational": {"configured": False, "writable": None, "last_success_at": None, "last_error_code": None},
    "metric": {"configured": False, "writable": None, "last_success_at": None, "last_error_code": None},
}


def logging_sink_state_snapshot() -> Mapping[str, Mapping[str, Any]]:
    """Return a copy of state learned only from real logging operations."""
    return {kind: dict(state) for kind, state in _SINK_OBSERVATIONS.items()}


def _observe_sink(kind: str, *, configured: bool | None = None,
                  writable: bool | None = None, error_code: str | None = None) -> None:
    state = _SINK_OBSERVATIONS[kind]
    if configured is not None:
        state["configured"] = configured
    if writable is not None:
        state["writable"] = writable
        if writable:
            state["last_success_at"] = datetime.now(UTC).isoformat()
            state["last_error_code"] = None
        else:
            state["last_error_code"] = error_code or "write_failed"


def _reset_sink_observation(kind: str) -> None:
    _SINK_OBSERVATIONS[kind] = {
        "configured": False, "writable": None,
        "last_success_at": None, "last_error_code": None,
    }

LOG_FIELDS = (
    "component",
    "event",
    "user_id",
    "agent_instance_id",
    "connection_id",
    "session_id",
    "action_id",
    "request_id",
    "log_id",
    "runtime_id",
    "generation",
    "binding_id",
    "game_session_id",
    "skill_execution_id",
    "tool_call_id",
    "command_id",
    "operation_id",
    "capability",
    "target_player_id",
    "pid",
    "error_code",
    "exception_type",
    "category",
    "kind",
    "value",
    "duration_ms",
    "outcome",
    "limit_kind",
    "policy_kind",
    "adapter_kind",
    "runtime_state",
    "binding_state",
    "worker_kind",
    "outbox_kind",
    "stage",
    "result_state",
    "failure_kind",
    "returncode",
    "diagnostic_tail",
)

_METRIC_KINDS = frozenset({"counter", "gauge", "timing"})
_METRIC_DIMENSIONS = frozenset(
    {
        "outcome",
        "error_code",
        "limit_kind",
        "policy_kind",
        "user_id",
        "agent_instance_id",
        "connection_id",
        "session_id",
        "request_id",
        "log_id",
        "runtime_id",
        "generation",
        "binding_id",
        "game_session_id",
        "skill_execution_id",
        "tool_call_id",
        "command_id",
        "operation_id",
        "capability",
        "adapter_kind",
        "runtime_state",
        "binding_state",
        "worker_kind",
        "outbox_kind",
        "stage",
        "result_state",
        "failure_kind",
        "filter_reason",
        "input_source",
    }
)
_METRIC_ENUM_DIMENSIONS = {
    "adapter_kind": frozenset({"claude", "fake", "unknown"}),
    "runtime_state": frozenset({
        "provisioning", "starting", "ready_unbound", "running_bound", "idle",
        "degraded", "draining", "stopping", "stopped", "failed", "unknown",
    }),
    "binding_state": frozenset({"pending", "active", "suspended", "revoked", "expired", "unknown"}),
    "worker_kind": frozenset({"idle_sweep", "reconcile", "authority", "gateway_outbox", "binding_expiry"}),
    "outbox_kind": frozenset({"authority", "gateway"}),
    "stage": frozenset({
        "claim", "apply", "acknowledge", "retry", "renew", "dispatch", "result", "cancel",
        "configured", "rotation", "write", "start", "stop", "turn", "exit",
    }),
    "result_state": frozenset({
        "created", "accepted", "running", "succeeded", "failed", "cancelled",
        "timed_out", "expired_unknown", "unknown", "rejected", "completed", "partial",
    }),
    "failure_kind": frozenset({
        "timeout", "process_exited", "provider_rejected", "readiness_failed",
        "route_unavailable", "protocol_error", "internal", "unknown",
    }),
    "filter_reason": frozenset({
        "observer_connection", "controlled_player", "agent_say_reflection",
        "agent_origin", "system_message", "missing_provenance",
        "duplicate_event", "loop_circuit_open",
    }),
    "input_source": frozenset({
        "background_job_completion", "game_chat", "platform", "say_compensation",
    }),
}
_METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._][a-z0-9]+)*$")
_MAX_METRIC_NAME_LENGTH = 128

# External providers and Mods may attach arbitrary ``error_code`` strings.
# Telemetry is a bounded contract, so only this stable vocabulary crosses an
# emission boundary; everything else collapses to ``unknown``.
_STABLE_ERROR_CODES = frozenset({
    "already_stopping", "binding_unavailable", "cancelled", "conflict",
    "diagnostic_stats_unavailable", "dispatch_conflict", "expired_unknown",
    "gateway_outbox_apply_failed", "hosted_runtime_not_allowed", "internal",
    "internal_error", "invalid_arguments", "invalid_spec", "invalid_transition",
    "not_found", "process_exited", "protocol_error", "provider_rejected",
    "readiness_failed", "resource_exhausted", "route_unavailable",
    "runtime_start_failed", "stale_generation", "timeout", "timed_out",
    "unavailable", "unknown", "upstream_protocol_error",
})


def normalize_error_code(value: Any, *, fallback: str = "unknown") -> str:
    candidate = str(value or fallback).strip().lower()
    return candidate if candidate in _STABLE_ERROR_CODES else "unknown"

SENSITIVE_KEYS = {
    "authorization",
    "apikey",
    "api_key",
    "access_token",
    "refresh_token",
    "platform_token",
    "supabase_service_role_key",
    "database_url",
    "llm_relay_api_key",
    "cookie",
    "set-cookie",
    "password",
    "secret",
    "token",
}

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(apikey|api_key|platform_token|access_token|refresh_token|llm_relay_api_key)\s*[:=]\s*[^,\s]+"),
    re.compile(r"(?i)\b(SUPABASE_SERVICE_ROLE_KEY|DATABASE_URL|LLM_RELAY_API_KEY)\s*[:=]\s*[^,\s]+"),
    re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*[^;\n]+"),
)

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "starcoplay_log_context",
    default={},
)


def new_log_id() -> str:
    """Return an opaque process-generated diagnostic correlation id."""
    return f"log_{uuid.uuid4().hex}"


def bind_log_context(**fields: Any) -> contextvars.Token[dict[str, Any]]:
    current = dict(_context.get())
    for key, value in fields.items():
        if key in LOG_FIELDS and value not in (None, ""):
            current[key] = value
    return _context.set(current)


def reset_log_context(token: contextvars.Token[dict[str, Any]]) -> None:
    _context.reset(token)


def clear_log_context() -> None:
    _context.set({})


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                clean[key_text] = "[REDACTED]"
            else:
                clean[key_text] = redact(item)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    return value


def configure_json_logging(
    *,
    component: str,
    log_level: str = "INFO",
    log_dir: str | Path | None = None,
    agent_instance_id: str = "",
    user_id: str = "",
    target_player_id: str | int | None = None,
    stderr: bool = False,
    replace_handlers: bool = True,
    max_bytes: int = 20 * 1024 * 1024,
    backup_count: int = 7,
) -> Path:
    base_dir = resolve_log_dir(log_dir)
    operations_dir = base_dir / "operations"
    metrics_dir = base_dir / "metrics"
    operations_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    # A reconfiguration installs new sink instances. Their state must not
    # inherit write observations from handlers that have just been retired.
    _reset_sink_observation("operational")
    _reset_sink_observation("metric")

    root = logging.getLogger()
    if replace_handlers:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    root.setLevel(_parse_level(log_level))

    formatter = JsonLogFormatter(component=component)
    for filename, level_filter in (
        (f"{component}-info.jsonl", ExactOperationalLevelFilter(logging.INFO)),
        (f"{component}-warn.jsonl", ExactOperationalLevelFilter(logging.WARNING)),
        (f"{component}-error.jsonl", ErrorOperationalLevelFilter()),
    ):
        handler = SafeRotatingFileHandler(
            operations_dir / filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            report_sink_events=True,
            sink_kind="operational",
        )
        handler.setFormatter(formatter)
        handler.addFilter(level_filter)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
        _observe_sink("operational", configured=True)

    metric_handler = SafeRotatingFileHandler(
        metrics_dir / f"{component}-metrics.jsonl",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        report_sink_events=False,
        sink_kind="metric",
    )
    metric_handler.setFormatter(formatter)
    metric_handler.addFilter(MetricFilter())
    metric_handler.addFilter(RedactingFilter())
    root.addHandler(metric_handler)
    _observe_sink("metric", configured=True)

    if agent_instance_id:
        sessions_dir = base_dir / "sessions" / datetime.now(UTC).strftime("%Y-%m-%d")
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_handler = SafeRotatingFileHandler(
            sessions_dir / f"{agent_instance_id}.jsonl",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            report_sink_events=True,
        )
        session_handler.setFormatter(formatter)
        session_handler.addFilter(NonMetricFilter())
        session_handler.addFilter(RedactingFilter())
        root.addHandler(session_handler)

    if stderr:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(logging.ERROR)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(RedactingFilter())
        root.addHandler(stream_handler)

    bind_log_context(
        component=component,
        agent_instance_id=agent_instance_id,
        user_id=user_id,
        target_player_id=target_player_id,
        pid=os.getpid(),
    )
    os.environ["STARCOPLAY_STRUCTURED_LOGGING"] = "1"
    tune_noisy_loggers()
    logging.getLogger("starcoplay.observability").info(
        "structured logging configured",
        extra={
            "event": "logging.configured",
            "component": component,
            "pid": os.getpid(),
        },
    )
    # Configuration is outside any handler failure path, so this observation
    # can safely use the normal metric sink. Rollover/write failures below
    # deliberately use only the direct stderr fallback to avoid recursion.
    emit_metric(
        "logging_sink_configured_total", "counter", 1,
        outcome="success", stage="configured",
    )
    return base_dir


def emit_metric(
    name: str,
    kind: str,
    value: int | float,
    *,
    duration_ms: int | float | None = None,
    outcome: str | None = None,
    **dimensions: Any,
) -> None:
    if not isinstance(name, str) or len(name) > _MAX_METRIC_NAME_LENGTH or not _METRIC_NAME_RE.fullmatch(name):
        raise ValueError("metric name must be 1-128 lowercase machine-safe characters")
    if kind not in _METRIC_KINDS:
        raise ValueError("metric kind must be counter/gauge/timing")
    if not _is_finite_number(value):
        raise ValueError("metric value must be a finite number")
    if duration_ms is not None and (not _is_finite_number(duration_ms) or duration_ms < 0):
        raise ValueError("metric duration_ms must be a finite non-negative number")
    if outcome is not None:
        dimensions["outcome"] = outcome
    if dimensions.get("error_code") not in (None, ""):
        dimensions["error_code"] = normalize_error_code(dimensions["error_code"])
    unknown = set(dimensions) - _METRIC_DIMENSIONS
    if unknown:
        raise ValueError(f"unknown metric dimensions: {','.join(sorted(unknown))}")
    invalid = [key for key, item in dimensions.items() if not _is_metric_dimension_value(item)]
    if invalid:
        raise ValueError(f"metric dimensions must be bounded scalar values: {','.join(sorted(invalid))}")
    invalid_enums = [
        key for key, allowed in _METRIC_ENUM_DIMENSIONS.items()
        if key in dimensions and dimensions[key] not in (None, "") and dimensions[key] not in allowed
    ]
    if invalid_enums:
        raise ValueError(f"invalid metric enum dimensions: {','.join(sorted(invalid_enums))}")

    extra: dict[str, Any] = {
        "category": "metric",
        "metric_name": name,
        "kind": kind,
        "value": value,
        **{key: item for key, item in dimensions.items() if item not in (None, "")},
    }
    if duration_ms is not None:
        extra["duration_ms"] = duration_ms
    metric_logger = logging.getLogger("starcoplay.metric")
    metric_logger.setLevel(logging.INFO)
    metric_logger.info(name, extra=extra)


def resolve_log_dir(log_dir: str | Path | None = None) -> Path:
    raw = str(log_dir or os.environ.get("STARCOPLAY_LOG_DIR") or "/var/log/starcoplay").strip()
    preferred = Path(raw).expanduser()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return preferred
    except OSError:
        fallback = _repo_root() / ".runtime" / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def tune_noisy_loggers() -> None:
    for name in (
        "hpack",
        "h2",
        "websockets",
        "websockets.protocol",
        "aiohttp",
        "asyncpg",
        "httpcore",
        "httpx",
        "fsevents",
        "watchdog",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.INFO)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, Mapping):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(item) for item in record.args)
        else:
            record.args = redact(record.args)
        return True


class NonMetricFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "category", None) != "metric"


class ExactOperationalLevelFilter(NonMetricFilter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self._level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return super().filter(record) and record.levelno == self._level


class ErrorOperationalLevelFilter(NonMetricFilter):
    def filter(self, record: logging.LogRecord) -> bool:
        return super().filter(record) and record.levelno in {
            logging.ERROR,
            logging.CRITICAL,
        }


class MetricFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "category", None) == "metric"


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating handler whose failure path never re-enters logging."""

    _reporting_sink_event = False

    def __init__(self, *args: Any, report_sink_events: bool = False,
                 sink_kind: str | None = None, **kwargs: Any) -> None:
        self._report_sink_events = report_sink_events
        self._sink_kind = sink_kind
        super().__init__(*args, **kwargs)

    def emit(self, record: logging.LogRecord) -> None:
        self._emit_failed = False
        super().emit(record)
        if self._sink_kind is not None and not self._emit_failed:
            _observe_sink(self._sink_kind, writable=True)

    def _report(self, name: str, *, outcome: str, stage: str) -> None:
        # A metric sink never reports its own rollover/write failures. For an
        # operational/session sink this guarded call is routed only to the
        # separate metric handler; operational filters reject metric records.
        if not self._report_sink_events or type(self)._reporting_sink_event:
            return
        type(self)._reporting_sink_event = True
        try:
            emit_metric(name, "counter", 1, outcome=outcome, stage=stage)
        except Exception:
            pass
        finally:
            type(self)._reporting_sink_event = False

    def handleError(self, record: logging.LogRecord) -> None:
        self._emit_failed = True
        if self._sink_kind is not None:
            _observe_sink(self._sink_kind, writable=False, error_code="write_failed")
        try:
            event = {
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "level": "ERROR",
                "event": "logging.write_failed",
                "handler": str(self.baseFilename),
            }
            sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        self._report(
            "logging_sink_write_failure_total", outcome="failed", stage="write",
        )

    def doRollover(self) -> None:
        super().doRollover()
        try:
            event = {
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "level": "INFO",
                "event": "logging.rotation.completed",
                "handler": str(self.baseFilename),
            }
            sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        self._report(
            "logging_sink_rotation_total", outcome="success", stage="rotation",
        )


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, component: str) -> None:
        super().__init__()
        self._component = component
        self._host = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        context = _context.get()
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "component": getattr(record, "component", None) or context.get("component") or self._component,
            "event": getattr(record, "event", None) or record.name,
            "message": _redact_string(record.getMessage()),
            "logger": record.name,
            "host": self._host,
            "pid": getattr(record, "pid", None) or context.get("pid") or record.process,
        }
        if getattr(record, "category", None) == "metric":
            payload["name"] = getattr(record, "metric_name")
        for field in LOG_FIELDS:
            if field in {"component", "event", "pid"}:
                continue
            value = getattr(record, field, None)
            if value in (None, ""):
                value = context.get(field)
            if value not in (None, ""):
                payload[field] = value

        if record.exc_info:
            exc_type = record.exc_info[0]
            payload["exception_type"] = getattr(exc_type, "__name__", str(exc_type))
            payload["stack"] = _redact_string(
                "".join(traceback.format_exception(*record.exc_info))
            )
        elif record.stack_info:
            payload["stack"] = _redact_string(str(record.stack_info))

        extras = _extract_extra(record)
        if extras:
            payload["extra"] = redact(extras)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def _extract_extra(record: logging.LogRecord) -> dict[str, Any]:
    standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
    standard.update(LOG_FIELDS)
    standard.update({"message", "asctime", "stack", "metric_name"})
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in standard and not key.startswith("_")
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized.endswith("_token") or normalized.endswith("_secret")


def _redact_string(value: str) -> str:
    clean = value
    for pattern in _SECRET_PATTERNS:
        clean = pattern.sub(lambda m: f"{m.group(0).split()[0]} [REDACTED]" if m.group(0).lower().startswith("bearer") else "[REDACTED]", clean)
    return clean


def _parse_level(level: str) -> int:
    parsed = logging.getLevelName(str(level or "INFO").upper())
    return parsed if isinstance(parsed, int) else logging.INFO


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_metric_dimension_value(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= 256
    return _is_finite_number(value)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
