from __future__ import annotations

import asyncio
import pytest

from common.observability import (
    SafeRotatingFileHandler, configure_json_logging, logging_sink_state_snapshot,
)
from modules.mcp.core.audit import AuditEvent, DurableJsonlAuditSink
from modules.runtime_manager.coordinator import RuntimeManagerWorkers
from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec, RuntimeState
from modules.runtime_manager.health import RuntimeManagerHealthService


def _record(runtime_id: str, state: RuntimeState, *, generation: int = 1,
            started: bool = True,
            exit_reason: str | None = None) -> RuntimeProductRecord:
    from datetime import datetime, timezone
    return RuntimeProductRecord(
        RuntimeProductSpec(
            runtime_id, generation, f"activation-{runtime_id}", "user", "agent", "fake",
            "adapter/default", "persona/default",
        ),
        actual_state=state,
        started_at=datetime.now(timezone.utc) if started else None,
        exit_reason=exit_reason,
    )


class Repository:
    def __init__(self, records=(), quarantined=0):
        self.records = tuple(records)
        self.quarantined = quarantined

    async def list_active(self):
        return self.records

    async def diagnostic_quarantined_count(self):
        return self.quarantined


class Coordinator:
    def __init__(self, repository, owned=()):
        self.repository = repository
        self.owned = frozenset(owned)

    def owned_handle_keys_snapshot(self):
        return self.owned

    def provider_state_snapshot(self):
        return {"last_failure_at": None, "last_failure_code": None, "adapter_kind": None}

    async def reconcile_all(self): return 0
    async def sweep_idle(self): return 0
    async def shutdown(self): return None


class Outbox:
    def __init__(self, stats=None, fail=False):
        self.stats = stats or {"pending": 0, "claimed": 0, "oldest_age_seconds": 0}
        self.fail = fail
        self.calls = 0

    async def diagnostic_stats(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("private database detail")
        return self.stats


class RawOutbox:
    def __init__(self, value): self.value = value
    async def diagnostic_stats(self): return self.value


@pytest.mark.asyncio
async def test_health_uses_raw_owner_snapshots_and_excludes_legitimate_pending_orphan(tmp_path):
    records = (
        _record("owned", RuntimeState.RUNNING_BOUND),
        _record("orphan", RuntimeState.STARTING),
        _record("pending", RuntimeState.PROVISIONING, started=False),
    )
    coordinator = Coordinator(Repository(records, quarantined=2), owned={("owned", 1)})
    authority = Outbox({"pending": 3, "claimed": 1, "oldest_age_seconds": 4.5})
    gateway = Outbox(fail=True)
    audit = DurableJsonlAuditSink((tmp_path / "audit" / "events.jsonl").resolve())
    service = RuntimeManagerHealthService(
        coordinator, authority_outbox=authority, gateway_outbox=gateway, audit_sink=audit,
    )

    payload = await service.snapshot()

    assert payload["runtime"] == {
        "active_count": 3, "owned_handle_count": 1,
        "orphan_candidate_count": 1, "quarantined_count": 2,
    }
    assert payload["outboxes"]["authority"] == {
        "pending_count": 3, "claimed_count": 1,
        "oldest_pending_age_seconds": 4.5, "available": True, "error_code": None,
    }
    assert payload["outboxes"]["gateway"]["available"] is False
    assert payload["outboxes"]["gateway"]["error_code"] == "diagnostic_stats_unavailable"
    assert all(value is None for value in payload["workers"]["idle_sweep"].values())
    assert payload["sinks"]["audit"]["configured"] is True
    assert payload["sinks"]["audit"]["writable"] is None


@pytest.mark.asyncio
async def test_health_composes_redacted_hosted_gateway_and_delivery_diagnostics():
    service = RuntimeManagerHealthService(Coordinator(Repository()))
    service.bind_hosted_diagnostics(
        gateway_reader=lambda: {
            "summary": {"controller": 1, "observer": 2, "reconnecting": 1},
            "sessions": ({
                "role": "controller", "transport_state": "connected",
                "target_player_id": "42", "control_revision": 7,
            },),
            "last_takeover": {"outcome": "completed", "target_player_id": "42", "revision": 7},
        },
        input_reader=lambda: {
            "queue": {"total": 2, "compensation": 1, "player": 1, "background": 0},
            "delivery": {"silent_turns": 3, "compensation_delivered": 2,
                         "compensation_failed": 1, "last_outcome": "delivered"},
            },
        mcp_reader=lambda: {
            "repeated_failure_total": 4,
            "last_repeated_failure": {
                "capability": "buy_items", "error_code": "invalid_arguments",
            },
        },
    )

    payload = await service.snapshot()

    assert payload["schema_version"] == 2
    assert payload["hosted"]["gateway"]["summary"]["controller"] == 1
    assert payload["hosted"]["input"]["queue"]["compensation"] == 1
    assert payload["hosted"]["mcp"]["repeated_failure_total"] == 4
    encoded = str(payload["hosted"])
    assert "tenant" not in encoded and "token" not in encoded and "message" not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    None,
    [],
    {},
    {"pending": None, "claimed": 0, "oldest_age_seconds": 0},
    {"pending": "bad", "claimed": 0, "oldest_age_seconds": 0},
    {"pending": -1, "claimed": 0, "oldest_age_seconds": 0},
    {"pending": 1, "claimed": object(), "oldest_age_seconds": 0},
    {"pending": 1, "claimed": 0, "oldest_age_seconds": None},
    {"pending": 1, "claimed": 0, "oldest_age_seconds": "nan"},
    {"pending": 1, "claimed": 0, "oldest_age_seconds": float("inf")},
])
async def test_bad_outbox_shapes_degrade_only_that_section(bad):
    service = RuntimeManagerHealthService(
        Coordinator(Repository()),
        authority_outbox=RawOutbox(bad),
        gateway_outbox=Outbox({"pending": 2, "claimed": 1, "oldest_age_seconds": 3}),
    )
    payload = await service.snapshot()
    assert payload["ok"] is True
    assert payload["outboxes"]["authority"] == {
        "pending_count": None, "claimed_count": None,
        "oldest_pending_age_seconds": None, "available": False,
        "error_code": "diagnostic_stats_unavailable",
    }
    assert payload["outboxes"]["gateway"]["available"] is True
    assert payload["outboxes"]["gateway"]["pending_count"] == 2


@pytest.mark.asyncio
async def test_worker_success_then_failure_preserves_both_observations():
    coordinator = Coordinator(Repository())
    workers = RuntimeManagerWorkers(coordinator, authority_worker=None, interval_seconds=0.01)

    async def success():
        workers._stop.set()
        return 7

    await workers._loop("reconcile", success)
    first = workers.worker_state_snapshot()["reconcile"]
    workers._stop = asyncio.Event()

    async def failure():
        workers._stop.set()
        raise RuntimeError("provider body must not escape")

    await workers._loop("reconcile", failure)
    final = workers.worker_state_snapshot()["reconcile"]
    assert final["last_success_at"] == first["last_success_at"]
    assert final["last_batch_size"] == 7
    assert final["last_error_at"] is not None
    assert final["last_error_code"] == "internal_error"


@pytest.mark.asyncio
async def test_health_does_not_create_or_grow_sink_files(tmp_path):
    log_dir = tmp_path / "logs"
    configure_json_logging(component="health-test", log_dir=log_dir, stderr=False)
    audit_path = (tmp_path / "audit" / "events.jsonl").resolve()
    audit = DurableJsonlAuditSink(audit_path)
    audit.emit(AuditEvent("test", "tool", "command", "runtime", "binding", "session"))
    coordinator = Coordinator(Repository())
    service = RuntimeManagerHealthService(coordinator, audit_sink=audit)
    before = {path: path.stat().st_size for path in tmp_path.rglob("*.jsonl")}

    first = await service.snapshot()
    second = await service.snapshot()
    after = {path: path.stat().st_size for path in tmp_path.rglob("*.jsonl")}

    assert before == after
    assert first["sinks"] == second["sinks"]
    assert first["sinks"]["audit"]["writable"] is True


def test_sink_failure_state_changes_only_after_real_failed_operations(tmp_path, monkeypatch):
    import logging
    import modules.mcp.core.audit as audit_module

    handler = SafeRotatingFileHandler(
        tmp_path / "operation.jsonl", maxBytes=1024, backupCount=1,
        encoding="utf-8", sink_kind="operational",
    )
    handler.stream = None
    monkeypatch.setattr(handler, "_open", lambda: (_ for _ in ()).throw(OSError("denied")))
    handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None))
    assert logging_sink_state_snapshot()["operational"]["writable"] is False
    assert logging_sink_state_snapshot()["operational"]["last_error_code"] == "write_failed"

    audit = DurableJsonlAuditSink((tmp_path / "audit" / "events.jsonl").resolve())
    original_open = audit_module.os.open
    monkeypatch.setattr(
        audit_module.os, "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    with pytest.raises(OSError):
        audit.emit(AuditEvent("test", "tool", "command", "runtime", "binding", "session"))
    assert audit.health_state_snapshot()["writable"] is False
    assert audit.health_state_snapshot()["last_error_code"] == "write_failed"
    monkeypatch.setattr(audit_module.os, "open", original_open)


def test_reconfigure_replaces_old_sink_observations(tmp_path):
    import common.observability as observability

    configure_json_logging(component="first", log_dir=tmp_path / "first", stderr=False)
    observability._observe_sink(
        "operational", writable=False, error_code="write_failed"
    )
    observability._observe_sink("metric", writable=False, error_code="write_failed")
    assert logging_sink_state_snapshot()["operational"]["writable"] is False

    configure_json_logging(component="second", log_dir=tmp_path / "second", stderr=False)
    current = logging_sink_state_snapshot()
    # Configuration itself performs real operational and metric writes, so
    # the new handlers truthfully become writable rather than remaining null.
    assert current["operational"]["configured"] is True
    assert current["operational"]["writable"] is True
    assert current["operational"]["last_success_at"] is not None
    assert current["operational"]["last_error_code"] is None
    assert current["metric"]["configured"] is True
    assert current["metric"]["writable"] is True
    assert current["metric"]["last_success_at"] is not None
    assert current["metric"]["last_error_code"] is None


@pytest.mark.asyncio
async def test_health_trusts_canonical_list_active_single_generation_boundary():
    historical = _record("same", RuntimeState.STOPPED)
    current = _record("same", RuntimeState.RUNNING_BOUND)

    class CanonicalRepository(Repository):
        async def list_active(self):
            assert historical.actual_state is RuntimeState.STOPPED
            return (current,)

    payload = await RuntimeManagerHealthService(
        Coordinator(CanonicalRepository(), owned={("same", 1)})
    ).snapshot()
    assert payload["runtime"]["active_count"] == 1
    assert payload["runtime"]["orphan_candidate_count"] == 0


@pytest.mark.asyncio
async def test_broken_single_active_generation_invariant_remains_visible():
    records = (
        _record("same", RuntimeState.RUNNING_BOUND, generation=1),
        _record("same", RuntimeState.STARTING, generation=2),
    )
    payload = await RuntimeManagerHealthService(
        Coordinator(Repository(records), owned={("same", 1)})
    ).snapshot()
    # Health counts canonical rows/generations just like admission capacity.
    # It must expose an invariant breach instead of hiding it by runtime_id.
    assert payload["runtime"] == {
        "active_count": 2,
        "owned_handle_count": 1,
        "orphan_candidate_count": 1,
        "quarantined_count": 0,
    }
