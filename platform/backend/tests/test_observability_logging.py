from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pytest

from common.observability import configure_json_logging, emit_metric, redact, resolve_log_dir


def _jsonl_family(directory: Path, pattern: str):
    return [
        json.loads(line)
        for path in sorted(directory.glob(pattern))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_configure_json_logging_writes_redacted_jsonl(tmp_path: Path):
    log_dir = configure_json_logging(
        component="gateway",
        log_level="INFO",
        log_dir=tmp_path,
        replace_handlers=True,
    )

    logging.getLogger("test.observability").error(
        "request failed Authorization: Bearer secret-token",
        extra={
            "event": "gateway.auth.failed",
            "user_id": "user-1",
            "agent_instance_id": "agent-1",
            "authorization": "Bearer another-secret",
        },
    )

    log_file = log_dir / "operations" / "gateway-error.jsonl"
    line = log_file.read_text(encoding="utf-8").splitlines()[-1]
    payload = json.loads(line)

    assert payload["level"] == "ERROR"
    assert payload["component"] == "gateway"
    assert payload["event"] == "gateway.auth.failed"
    assert payload["user_id"] == "user-1"
    assert payload["agent_instance_id"] == "agent-1"
    assert "secret-token" not in line
    assert "another-secret" not in line
    assert "[REDACTED]" in line

    assert not (log_dir / "gateway" / "gateway.jsonl").exists()
    assert not (log_dir / "errors" / "gateway-errors.jsonl").exists()


def test_level_and_metric_files_are_strictly_isolated(tmp_path: Path):
    log_dir = configure_json_logging(component="gateway", log_dir=tmp_path)
    logger = logging.getLogger("test.observability.isolation")

    logger.info("info", extra={"event": "test.info"})
    logger.warning("warn", extra={"event": "test.warn"})
    logger.error("error", extra={"event": "test.error"})
    emit_metric(
        "runtime.ready.duration_ms",
        "timing",
        12.5,
        duration_ms=12.5,
        outcome="success",
        runtime_id="runtime-1",
    )

    paths = {
        "info": log_dir / "operations" / "gateway-info.jsonl",
        "warn": log_dir / "operations" / "gateway-warn.jsonl",
        "error": log_dir / "operations" / "gateway-error.jsonl",
        "metric": log_dir / "metrics" / "gateway-metrics.jsonl",
    }
    payloads = {
        key: [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for key, path in paths.items()
    }

    # configure_json_logging emits one operational INFO event of its own.
    assert {item["level"] for item in payloads["info"]} == {"INFO"}
    assert [item["event"] for item in payloads["warn"]] == ["test.warn"]
    assert [item["event"] for item in payloads["error"]] == ["test.error"]
    assert {item["name"] for item in payloads["metric"]} == {
        "logging_sink_configured_total", "runtime.ready.duration_ms",
    }
    metric = next(item for item in payloads["metric"] if item["name"] == "runtime.ready.duration_ms")
    assert metric["category"] == "metric"
    assert metric["name"] == "runtime.ready.duration_ms"
    assert metric["kind"] == "timing"
    assert metric["value"] == 12.5
    assert metric["duration_ms"] == 12.5
    assert metric["outcome"] == "success"
    assert all(item.get("category") != "metric" for key in ("info", "warn", "error") for item in payloads[key])


def test_warning_keeps_explicit_exception_context_without_level_duplication(tmp_path: Path):
    log_dir = configure_json_logging(component="gateway", log_dir=tmp_path)
    logger = logging.getLogger("test.observability.stack")
    try:
        raise RuntimeError("classified failure")
    except RuntimeError:
        logger.warning("warning with exception", exc_info=True, extra={"event": "test.warn.exc"})
        logger.error("error with exception", exc_info=True, extra={"event": "test.error.exc"})

    warning = json.loads(
        (log_dir / "operations" / "gateway-warn.jsonl").read_text(encoding="utf-8").strip()
    )
    error = json.loads(
        (log_dir / "operations" / "gateway-error.jsonl").read_text(encoding="utf-8").strip()
    )
    assert warning["level"] == "WARNING"
    assert warning["exception_type"] == "RuntimeError"
    assert "classified failure" in warning["stack"]
    assert error["exception_type"] == "RuntimeError"
    assert "classified failure" in error["stack"]


def test_error_file_accepts_only_error_and_critical_levels(tmp_path: Path):
    log_dir = configure_json_logging(component="gateway", log_dir=tmp_path)
    logger = logging.getLogger("test.observability.error-levels")
    logger.error("error", extra={"event": "test.error"})
    logger.critical("critical", extra={"event": "test.critical"})
    logger.log(60, "custom high level", extra={"event": "test.custom-60"})

    payloads = [
        json.loads(line)
        for line in (log_dir / "operations" / "gateway-error.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [payload["level"] for payload in payloads] == ["ERROR", "CRITICAL"]
    assert [payload["event"] for payload in payloads] == ["test.error", "test.critical"]


def test_rotating_files_are_bounded_and_contain_valid_json(tmp_path: Path):
    log_dir = configure_json_logging(
        component="gateway",
        log_dir=tmp_path,
        max_bytes=350,
        backup_count=2,
    )
    logger = logging.getLogger("test.observability.rotation")
    for index in range(30):
        logger.warning("rotation record %s %s", index, "x" * 80)

    files = sorted((log_dir / "operations").glob("gateway-warn.jsonl*"))
    assert {path.name for path in files} == {
        "gateway-warn.jsonl",
        "gateway-warn.jsonl.1",
        "gateway-warn.jsonl.2",
    }
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines
        assert all(json.loads(line) for line in lines)
    metrics = _jsonl_family(log_dir / "metrics", "gateway-metrics.jsonl*")
    assert any(item.get("name") == "logging_sink_rotation_total" for item in metrics)


def test_session_diagnostic_file_excludes_metrics(tmp_path: Path):
    log_dir = configure_json_logging(
        component="agent-runtime",
        log_dir=tmp_path,
        agent_instance_id="agent-1",
    )
    logging.getLogger("test.observability.session").info("session operation")
    emit_metric("runtime.input.total", "counter", 1, outcome="success")

    session_file = next((log_dir / "sessions").glob("*/agent-1.jsonl"))
    payloads = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert payloads
    assert all(payload.get("category") != "metric" for payload in payloads)


def test_file_handler_failure_uses_non_recursive_stderr_fallback(tmp_path: Path, monkeypatch, capsys):
    log_dir = configure_json_logging(component="gateway", log_dir=tmp_path)
    handler = next(
        item
        for item in logging.getLogger().handlers
        if getattr(item, "baseFilename", "").endswith("gateway-warn.jsonl")
    )
    monkeypatch.setattr(handler, "shouldRollover", lambda record: True)
    monkeypatch.setattr(handler, "doRollover", lambda: (_ for _ in ()).throw(OSError("disk failure")))

    logging.getLogger("test.observability.failure").warning("cannot persist")

    fallback = json.loads(capsys.readouterr().err.strip())
    assert fallback["event"] == "logging.write_failed"
    assert fallback["handler"].endswith("gateway-warn.jsonl")
    metrics = _jsonl_family(log_dir / "metrics", "gateway-metrics.jsonl*")
    failures = [item for item in metrics if item.get("name") == "logging_sink_write_failure_total"]
    assert len(failures) == 1
    assert failures[0]["stage"] == "write"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("metric", "histogram", 1), "metric kind"),
        (("Bad Name", "counter", 1), "metric name"),
        (("valid.name", "counter", "1"), "metric value"),
        (("valid.name", "counter", math.inf), "metric value"),
    ],
)
def test_emit_metric_rejects_invalid_schema(args, message):
    with pytest.raises(ValueError, match=message):
        emit_metric(*args)


def test_emit_metric_rejects_unknown_dimension():
    with pytest.raises(ValueError, match="unknown metric dimensions: arbitrary"):
        emit_metric("valid.name", "counter", 1, arbitrary="value")


@pytest.mark.parametrize("value", [{"nested": "payload"}, ["unbounded"], "x" * 257])
def test_emit_metric_rejects_unbounded_dimension_values(value):
    with pytest.raises(ValueError, match="metric dimensions must be bounded scalar values: outcome"):
        emit_metric("valid.name", "counter", 1, outcome=value)


def test_redact_nested_secret_values():
    assert redact(
        {
            "platform_token": "token-1",
            "headers": {"Authorization": "Bearer token-2"},
            "safe": "value",
        }
    ) == {
        "platform_token": "[REDACTED]",
        "headers": {"Authorization": "[REDACTED]"},
        "safe": "value",
    }


def test_resolve_log_dir_falls_back_when_target_is_not_directory(tmp_path: Path):
    bad_target = tmp_path / "not-a-directory"
    bad_target.write_text("x", encoding="utf-8")

    resolved = resolve_log_dir(bad_target)

    assert resolved.exists()
    assert resolved.name == "logs"
