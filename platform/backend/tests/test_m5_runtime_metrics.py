from __future__ import annotations

import asyncio
import json
import logging
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

import common.observability as observability
import modules.mcp.capabilities.runtime as capability_module
import modules.mcp.skills.runtime as skill_module
import modules.mcp.transports.hosted_server as hosted_module
import modules.gateway.runtime_contracts as gateway_module
import modules.runtime_manager.persistence as persistence_module
import modules.runtime_manager.async_application as async_application_module
import modules.runtime_manager.admission as admission_module
import modules.runtime_manager.coordinator as coordinator_module
from modules.mcp.transports.stdio import StdioMcpTransport
from modules.mcp.core.errors import McpError
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.async_application import AsyncBindingService, AsyncRuntimeService
from modules.runtime_manager.async_application import AuthorityOutboxWorker
from modules.runtime_manager.coordinator import BindingExpiryWorker, GatewayEventOutboxWorker, RuntimeManagerWorkers
from modules.runtime_manager.coordinator import HostedGatewayEventConsumer, RuntimeManagerCoordinator
from modules.runtime_manager.health import RuntimeManagerHealthService
from modules.runtime_manager.contracts import ReadinessFailureKind, RuntimeInput
from modules.runtime_manager.domain import (
    BindingState, HostedRuntimeNotAllowed, ResourceExhausted, RuntimeBinding,
    RuntimeProductSpec,
)
from modules.gateway.runtime_contracts import GameSessionSnapshot
from modules.runtime_manager.persistence import InMemoryRuntimeAdmissionRepository
from modules.runtime_manager.hosted_mcp_authority import HostedCredentialFileAuthority
from modules.runtime_manager.adapters.claude.transport import ClaudeProcessTransport
from common.observability import (
    bind_log_context, configure_json_logging, reset_log_context,
)
from tests.mcp.test_hosted_dynamic_product_chain import _stack
from tests.runtime_manager.test_gateway_router_boundary import command, router_stack
from tests.runtime_manager.test_product_composition import (
    Adapter, Authority, MemoryRepository, PersonaReader, spec as product_spec,
)


def _records(root: Path, family: str):
    return [
        json.loads(line)
        for path in sorted(root.glob(family))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _spec(**changes) -> RuntimeProductSpec:
    return replace(RuntimeProductSpec(
        "metric-runtime", 1, "metric-activation", "metric-user", "metric-agent",
        "fake", "adapter/default", "persona/rev-1",
    ), **changes)


def _event(records, name):
    return next(item for item in records if item.get("event") == name)


def _metric(records, name, outcome):
    return next(
        item for item in records
        if item.get("name") == name and item.get("outcome") == outcome
    )


@pytest.mark.asyncio
async def test_admission_rejections_are_correlated_and_have_no_runtime_side_effect(tmp_path):
    log_dir = configure_json_logging(component="m5-incident-admission", log_dir=tmp_path)
    token = bind_log_context(log_id="log_" + "1" * 32)
    try:
        denied = RuntimeAdmissionPolicy()
        with pytest.raises(HostedRuntimeNotAllowed) as allowlist_error:
            denied.require_allowed("not-allowed")
        assert getattr(allowlist_error.value, "code", None) == "hosted_runtime_not_allowed"

        repository = InMemoryRuntimeAdmissionRepository(RuntimeAdmissionPolicy(
            allowed_user_ids=frozenset(("allowed", "other")),
            max_active_global=1, max_active_per_user=1,
        ))
        service = AsyncRuntimeService(repository)
        await service.ensure_runtime(_spec(user_id="allowed"))
        active_before = await repository.list_active()
        with pytest.raises(ResourceExhausted) as capacity_error:
            await service.ensure_runtime(_spec(
                runtime_id="must-not-exist", activation_key="must-not-exist",
                user_id="other", agentee_id="other-agent",
            ))
        assert capacity_error.value.code == "resource_exhausted"
        assert await repository.list_active() == active_before
        assert all(item.runtime_id != "must-not-exist" for item in active_before)
    finally:
        reset_log_context(token)

    warnings = _records(log_dir / "operations", "m5-incident-admission-warn.jsonl*")
    metrics = _records(log_dir / "metrics", "m5-incident-admission-metrics.jsonl*")
    for record in (
        _event(warnings, "runtime.admission.rejected"),
        _metric(metrics, "runtime_admission_total", "rejected"),
    ):
        assert record["log_id"] == "log_" + "1" * 32
    assert {item["error_code"] for item in warnings} == {
        "hosted_runtime_not_allowed", "resource_exhausted",
    }
    assert {item["error_code"] for item in metrics
            if item.get("name") == "runtime_admission_total"
            and item.get("outcome") == "rejected"} == {
        "hosted_runtime_not_allowed", "resource_exhausted",
    }


@pytest.mark.asyncio
async def test_provider_start_and_turn_failures_keep_one_log_and_runtime_identity(tmp_path):
    log_dir = configure_json_logging(component="m5-incident-provider", log_dir=tmp_path)

    start_adapter = Adapter()
    start_adapter.ready = False
    start_adapter.readiness_failure_kind = ReadinessFailureKind.PROVIDER_REJECTED
    start_manager = RuntimeManagerCoordinator(
        MemoryRepository(), PersonaReader(), Authority(), {"fake": start_adapter},
        tmp_path / "start-runtime",
    )
    start_spec = replace(product_spec(), runtime_id="runtime-start-failure")
    await start_manager.provision(start_spec)
    start_token = bind_log_context(log_id="log_" + "2" * 32)
    try:
        with pytest.raises(RuntimeError, match="provider_rejected"):
            await start_manager.ensure(start_spec)
    finally:
        reset_log_context(start_token)
    assert ("runtime-start-failure", 1) not in start_manager._handles

    class TurnFailureAdapter(Adapter):
        async def submit_input(self, handle, value):
            self.events.append("input")
            raise RuntimeError("provider turn failed")

    turn_adapter = TurnFailureAdapter()
    turn_manager = RuntimeManagerCoordinator(
        MemoryRepository(), PersonaReader(), Authority(), {"fake": turn_adapter},
        tmp_path / "turn-runtime",
    )
    turn_spec = replace(product_spec(), runtime_id="runtime-turn-failure")
    await turn_manager.provision(turn_spec)
    await turn_manager.ensure(turn_spec)
    turn_token = bind_log_context(log_id="log_" + "3" * 32)
    try:
        with pytest.raises(RuntimeError, match="provider turn failed"):
            await turn_manager.submit_input(
                "runtime-turn-failure", 1,
                RuntimeInput("turn-failure", "hello", {"log_id": "log_" + "3" * 32}),
            )
    finally:
        reset_log_context(turn_token)
    assert turn_adapter.events.count("input") == 1

    operational = (
        _records(log_dir / "operations", "m5-incident-provider-warn.jsonl*")
        + _records(log_dir / "operations", "m5-incident-provider-error.jsonl*")
    )
    metrics = _records(log_dir / "metrics", "m5-incident-provider-metrics.jsonl*")
    evidence = (
        (_event(operational, "runtime.operation.start_failed"),
         _metric(metrics, "runtime_start_duration_ms", "failed"),
         "runtime-start-failure", "log_" + "2" * 32),
        (_event(operational, "runtime.input.failed"),
         _metric(metrics, "runtime_input_total", "failed"),
         "runtime-turn-failure", "log_" + "3" * 32),
    )
    for log_record, metric_record, runtime_id, log_id in evidence:
        assert (log_record["runtime_id"], log_record["log_id"]) == (runtime_id, log_id)
        assert (metric_record["runtime_id"], metric_record["log_id"]) == (runtime_id, log_id)
        assert log_record["generation"] == metric_record["generation"] == 1


@pytest.mark.asyncio
async def test_route_unavailable_never_dispatches_and_persists_correlated_evidence(tmp_path):
    log_dir = configure_json_logging(component="m5-incident-route", log_dir=tmp_path)
    router, context = router_stack(False)
    context = replace(context, trace_context={"log_id": "log_" + "4" * 32})

    resolve_calls = 0

    async def unavailable(_game_session_id):
        nonlocal resolve_calls
        resolve_calls += 1
        return None

    router.registry.resolve_authenticated = unavailable
    with pytest.raises(McpError) as caught:
        await router.dispatch(context, command(context))
    assert getattr(caught.value, "code", None) == "route_unavailable"
    assert resolve_calls == 1

    warnings = _records(log_dir / "operations", "m5-incident-route-warn.jsonl*")
    metrics = _records(log_dir / "metrics", "m5-incident-route-metrics.jsonl*")
    log_record = _event(warnings, "gateway.session.route_unavailable")
    metric_record = _metric(metrics, "gateway_session_route_total", "unavailable")
    for record in (log_record, metric_record):
        assert record["error_code"] == "route_unavailable"
        assert record["log_id"] == "log_" + "4" * 32
        assert record["runtime_id"] == "runtime-1"
        assert record["generation"] == 1


@pytest.mark.asyncio
async def test_admission_metric_counts_new_insert_once_and_capacity_reject(monkeypatch):
    events = []
    monkeypatch.setattr(persistence_module, "emit_metric", lambda *a, **kw: events.append((a, kw)))
    repo = InMemoryRuntimeAdmissionRepository(RuntimeAdmissionPolicy(
        max_active_global=1, max_active_per_user=1,
    ))
    service = AsyncRuntimeService(repo)

    first = await service.ensure_runtime(_spec())
    replay = await service.ensure_runtime(_spec())
    assert first == replay
    with pytest.raises(ResourceExhausted):
        await service.ensure_runtime(_spec(
            runtime_id="metric-runtime-2", activation_key="metric-activation-2",
            user_id="other-user", agentee_id="other-agent",
        ))

    admission = [item for item in events if item[0][0] == "runtime_admission_total"]
    assert [item[1]["outcome"] for item in admission] == ["accepted", "rejected"]

    policy_events = []
    monkeypatch.setattr(admission_module, "emit_metric", lambda *a, **kw: policy_events.append((a, kw)))
    with pytest.raises(Exception):
        RuntimeAdmissionPolicy().require_allowed("not-allowed")
    assert policy_events[0][1] == {
        "outcome": "rejected", "policy_kind": "allowlist",
        "error_code": "hosted_runtime_not_allowed",
    }


@pytest.mark.asyncio
async def test_real_hosted_call_emits_correlated_mcp_skill_capability_metrics(monkeypatch):
    events = []
    capture = lambda *a, **kw: events.append((a, kw))
    monkeypatch.setattr(observability, "emit_metric", capture)
    monkeypatch.setattr(skill_module, "emit_metric", capture)
    monkeypatch.setattr(capability_module, "emit_metric", capture)

    session, _, _ = await _stack()
    try:
        await session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        await session.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        result = await session.request({
            "jsonrpc": "2.0", "id": "metric-call", "method": "tools/call",
            "params": {
                "name": "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                "arguments": {}, "_meta": {"toolCallId": "metric-tool"},
            },
        })
        assert result["result"]["isError"] is False
    finally:
        await session.close()

    names = [item[0][0] for item in events]
    assert "hosted_mcp_call_duration_ms" in names
    assert "skill_execution_duration_ms" in names
    assert "capability_execution_duration_ms" in names
    per_chain = [item[1] for item in events if item[0][0] in {
        "hosted_mcp_call_total", "skill_execution_total", "capability_execution_total",
    }]
    assert {item.get("tool_call_id") for item in per_chain} == {"metric-tool"}
    assert len({item.get("log_id") for item in per_chain if item.get("log_id")}) == 1

    failed_session, _, _ = await _stack(fail_capability="query_ui_snapshot")
    try:
        await failed_session.request({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}})
        await failed_session.request({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})
        failed = await failed_session.request({
            "jsonrpc": "2.0", "id": "metric-failed", "method": "tools/call",
            "params": {"name": "skill:stardew.basic.observe_current_view@1.0.0",
                       "arguments": {}, "_meta": {"toolCallId": "metric-tool-failed"}},
        })
        assert "error" in failed or failed["result"]["isError"] is True
    finally:
        await failed_session.close()
    assert any(item[0][0] == "capability_execution_total"
               and item[1].get("result_state") == "failed" for item in events)
    assert any(item[0][0] == "skill_execution_total"
               and item[1].get("result_state") in {"failed", "partial"} for item in events)

    exception_session, exception_router, _ = await _stack()
    async def fail_dispatch(*args, **kwargs):
        error = RuntimeError("external body")
        error.code = "tenant-controlled-code"
        raise error
    exception_router.dispatch = fail_dispatch
    try:
        await exception_session.request({"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {}})
        await exception_session.request({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
        failed = await exception_session.request({
            "jsonrpc": "2.0", "id": "metric-exception", "method": "tools/call",
            "params": {"name": "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                       "arguments": {}, "_meta": {"toolCallId": "metric-tool-exception"}},
        })
        assert "error" in failed or failed["result"]["isError"] is True
    finally:
        await exception_session.close()
    failure_durations = [item for item in events
                         if item[0][0] == "capability_execution_duration_ms"
                         and item[1].get("outcome") == "failed"]
    assert any(item[1].get("stage") == "dispatch" for item in failure_durations)
    assert all(item[1].get("error_code") == "internal" for item in failure_durations)


def test_local_stdio_has_no_hosted_call_observer():
    transport = StdioMcpTransport(object(), object(), writer=object())
    assert transport._tool_call_observer is None


def test_hosted_observer_counts_only_same_operation_tool_and_arguments_repeated_failure(monkeypatch):
    events = []
    monkeypatch.setattr(
        observability, "emit_metric", lambda *args, **kwargs: events.append((args, kwargs)),
    )
    observer = hosted_module._HostedToolCallObserver()
    correlation = {
        "log_id": "log-1", "runtime_id": "runtime-1", "generation": 3,
        "operation_id": "operation-1",
    }

    first = observer.started(
        correlation=correlation, tool_call_id="tool-1", command_id="command-1",
        tool_name="buy_items", arguments={"shop": "SeedShop", "items": []},
    )
    observer.finished(
        first, result_state="failed", error=None, error_code="invalid_arguments",
    )
    other_runtime = observer.started(
        correlation={**correlation, "runtime_id": "runtime-2"},
        tool_call_id="tool-other", command_id="command-other",
        tool_name="buy_items", arguments={"shop": "SeedShop", "items": []},
    )
    observer.finished(
        other_runtime, result_state="failed", error=None,
        error_code="invalid_arguments",
    )
    second = observer.started(
        correlation=correlation, tool_call_id="tool-2", command_id="command-2",
        tool_name="buy_items", arguments={"shop": "SeedShop", "items": []},
    )
    observer.finished(
        second, result_state="failed", error=None, error_code="invalid_arguments",
    )
    corrected = observer.started(
        correlation=correlation, tool_call_id="tool-3", command_id="command-3",
        tool_name="buy_items", arguments={"shop": "SeedShop", "items": [{"name": "Parsnip Seeds"}]},
    )
    observer.finished(
        corrected, result_state="failed", error=None, error_code="invalid_arguments",
    )

    repeated = [event for event in events if event[0][0] == "hosted_mcp_repeated_failure_total"]
    assert len(repeated) == 1
    assert repeated[0][1]["operation_id"] == "operation-1"
    assert repeated[0][1]["capability"] == "buy_items"
    assert observer.diagnostics_snapshot() == {
        "repeated_failure_total": 1,
        "last_repeated_failure": {
            "capability": "buy_items",
            "error_code": "invalid_arguments",
        },
    }


@pytest.mark.asyncio
async def test_worker_tracker_and_metrics_record_success_and_failure(monkeypatch):
    events = []
    monkeypatch.setattr(
        "modules.runtime_manager.coordinator.emit_metric",
        lambda *a, **kw: events.append((a, kw)),
    )

    class Coordinator:
        async def reconcile_all(self): return 0
        async def shutdown(self): return None

    workers = RuntimeManagerWorkers(Coordinator(), object(), interval_seconds=0.001)

    async def success():
        workers._stop.set()
        return 3

    await workers._loop("reconcile", success)
    assert workers.worker_state_snapshot()["reconcile"]["last_batch_size"] == 3

    workers._stop = asyncio.Event()
    async def failure():
        workers._stop.set()
        raise RuntimeError("boom")

    await workers._loop("authority", failure)
    assert workers.worker_state_snapshot()["authority"]["last_error_code"] == "internal_error"
    outcomes = [item[1]["outcome"] for item in events if item[0][0] == "runtime_worker_batch_total"]
    assert outcomes == ["started", "success", "started", "failed"]


def test_metric_enum_dimensions_are_finite_and_reject_unknown_values():
    assert coordinator_module._metric_adapter_kind("future-adapter") == "unknown"
    assert observability.normalize_error_code("tenant-controlled-code") == "unknown"
    assert observability.normalize_error_code("route_unavailable") == "route_unavailable"
    observability.emit_metric(
        "hosted_runtime_input_total", "counter", 1,
        input_source="background_job_completion", outcome="queued",
    )
    with pytest.raises(ValueError, match="invalid metric enum"):
        observability.emit_metric(
            "runtime_worker_batch_total", "counter", 1,
            worker_kind="tenant-controlled-value",
        )


@pytest.mark.asyncio
async def test_gateway_metrics_cover_success_and_route_failure(monkeypatch):
    events = []
    monkeypatch.setattr(gateway_module, "emit_metric", lambda *a, **kw: events.append((a, kw)))
    router, context = router_stack()
    await router.dispatch(context, command(context))
    await router.await_result(context, "command-1", 10.0)
    await router.cancel(context, "command-1")

    unavailable, unavailable_context = router_stack(False)
    with pytest.raises(Exception):
        await unavailable.dispatch(unavailable_context, command(unavailable_context))

    names = [item[0][0] for item in events]
    assert names.count("gateway_command_duration_ms") == 3
    assert names.count("gateway_session_route_total") == 4
    assert {item[1]["stage"] for item in events if item[0][0] == "gateway_command_total"} == {
        "dispatch", "result", "cancel",
    }
    assert any(item[1]["outcome"] == "failed" for item in events)
    assert any(item[0][0] == "gateway_session_route_total"
               and item[1]["outcome"] == "unavailable" for item in events)

    class ArbitraryProviderError(Exception):
        code = "tenant-controlled-code"
    class FailingSession:
        async def dispatch(self, value): raise ArbitraryProviderError()
        async def await_result(self, command_id, deadline): raise ArbitraryProviderError()
    class FailingRegistry:
        async def resolve_authenticated(self, game_session_id): return FailingSession()
    failing, failing_context = router_stack()
    failing.registry = FailingRegistry()
    with pytest.raises(ArbitraryProviderError):
        await failing.dispatch(failing_context, command(failing_context))
    with pytest.raises(ArbitraryProviderError):
        await failing.await_result(failing_context, "command-1", 10.0)
    failure_durations = [kw for args, kw in events
                         if args[0] == "gateway_command_duration_ms"
                         and kw["outcome"] == "failed" and kw["error_code"] == "unknown"]
    assert {item["stage"] for item in failure_durations} == {"dispatch", "result"}
    assert {item["error_code"] for item in failure_durations} == {"unknown"}


@pytest.mark.asyncio
async def test_authority_outbox_metrics_count_visible_batch_and_durable_retry(monkeypatch):
    events = []
    monkeypatch.setattr(
        async_application_module, "emit_metric",
        lambda *a, **kw: events.append((a, kw)),
    )

    class Repository:
        retried = []
        acknowledged = []
        async def claim_pending_intents(self, limit=50):
            return (
                {"intent_id": "ok", "intent_kind": "revoke", "binding_id": "b1",
                 "binding_version": 1, "lease_epoch": 1, "payload": {}},
                {"intent_id": "bad", "intent_kind": "revoke", "binding_id": "b2",
                 "binding_version": 1, "lease_epoch": 1, "payload": {}},
            )
        async def acknowledge_intent(self, intent_id): self.acknowledged.append(intent_id)
        async def retry_intent(self, intent_id, error, delay_seconds): self.retried.append(intent_id)

    class Authority:
        async def revoke(self, binding_id, version, epoch):
            if binding_id == "b2":
                raise RuntimeError("provider unavailable")

    repository = Repository()
    assert await AuthorityOutboxWorker(repository, Authority()).run_once() == 1
    assert repository.acknowledged == ["ok"]
    assert repository.retried == ["bad"]
    assert any(item[0][0] == "runtime_outbox_claimed" and item[0][2] == 2 for item in events)
    assert any(item[0][0] == "runtime_outbox_retry_total" for item in events)


@pytest.mark.asyncio
async def test_runtime_transition_input_and_readiness_metrics_have_unique_owners(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(coordinator_module, "emit_metric", lambda *a, **kw: events.append((a, kw)))
    adapter = Adapter()
    manager = RuntimeManagerCoordinator(
        MemoryRepository(), PersonaReader(), Authority(), {"fake": adapter}, tmp_path,
    )
    await manager.provision(product_spec())
    await manager.ensure(product_spec())
    value = RuntimeInput("request-1", "hello", {"log_id": "log_" + "a" * 32})
    first, replay = await asyncio.gather(
        manager.submit_input("runtime-1", 1, value),
        manager.submit_input("runtime-1", 1, value),
    )
    assert first == replay

    assert sum(item[0][0] == "runtime_adapter_readiness_total" and item[1]["outcome"] == "success"
               for item in events) == 1
    assert sum(item[0][0] == "runtime_input_total" and item[1]["outcome"] == "accepted"
               for item in events) == 1
    assert sum(item[0][0] == "runtime_input_total" and item[1]["outcome"] == "completed"
               for item in events) == 1
    assert any(item[0][0] == "runtime_transition_total" for item in events)

    failing = Adapter()
    failing.ready = False
    failing.readiness_failure_kind = ReadinessFailureKind.PROVIDER_REJECTED
    failed_events = []
    monkeypatch.setattr(coordinator_module, "emit_metric", lambda *a, **kw: failed_events.append((a, kw)))
    failed_manager = RuntimeManagerCoordinator(
        MemoryRepository(), PersonaReader(), Authority(), {"fake": failing}, tmp_path / "failed",
    )
    failed_spec = replace(product_spec(), runtime_id="runtime-failed", activation_key="failed")
    await failed_manager.provision(failed_spec)
    with pytest.raises(RuntimeError, match="provider_rejected"):
        await failed_manager.ensure(failed_spec)
    assert any(item[0][0] == "runtime_adapter_readiness_total"
               and item[1]["failure_kind"] == "provider_rejected" for item in failed_events)


@pytest.mark.asyncio
async def test_binding_transition_metric_emits_only_after_durable_version_change(monkeypatch):
    events = []
    monkeypatch.setattr(coordinator_module, "emit_metric", lambda *a, **kw: events.append((a, kw)))

    class Bindings:
        async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
            return binding

    binding = RuntimeBinding(
        "binding-metric", "runtime-metric", 1, "user", "agent", 2, 1,
        BindingState.ACTIVE, "game", "nonce", "digest", "resume",
    )
    snapshot = GameSessionSnapshot(
        "game", "nonce", "user", None, 2, "digest", False, True, "resume",
    )
    consumer = HostedGatewayEventConsumer(object(), Bindings())
    committed = await consumer.converge_snapshot(
        snapshot, binding, operation_identity="binding-metric-op",
    )
    assert committed.status is BindingState.SUSPENDED
    transition = [item for item in events if item[0][0] == "runtime_binding_transition_total"]
    assert len(transition) == 1
    assert transition[0][1]["binding_state"] == "suspended"


@pytest.mark.asyncio
async def test_binding_metrics_cover_every_durable_state_without_replay_duplicates(monkeypatch):
    async_events, coordinator_events = [], []
    monkeypatch.setattr(async_application_module, "emit_metric", lambda *a, **kw: async_events.append((a, kw)))
    monkeypatch.setattr(coordinator_module, "emit_metric", lambda *a, **kw: coordinator_events.append((a, kw)))

    class Runtimes:
        async def get_generation(self, runtime_id, generation):
            return SimpleNamespace(active=True, spec=SimpleNamespace(user_id="user", agentee_id="agent"))
        async def get(self, runtime_id): return None

    class Bindings:
        def __init__(self): self.current = None; self.operations = {}
        async def find_for_runtime(self, *_): return self.current
        async def insert_with_operation(self, binding, operation):
            if operation.operation_id in self.operations:
                return self.operations[operation.operation_id]
            if self.current is None:
                self.current = binding
            self.operations[operation.operation_id] = self.current
            return self.current
        async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
            if operation.operation_id in self.operations:
                return self.operations[operation.operation_id]
            self.current = binding
            self.operations[operation.operation_id] = binding
            return binding

    bindings = Bindings()
    service = AsyncBindingService(Runtimes(), bindings)
    pending = await service.ensure_binding("runtime", 1, "user", "agent", "create")
    assert (await service.ensure_binding("runtime", 1, "user", "agent", "create")) == pending

    consumer = HostedGatewayEventConsumer(object(), bindings)
    active = await consumer.converge_snapshot(
        GameSessionSnapshot("game", "nonce", "user", "conn", 1, "digest", True, True, "resume"),
        pending, operation_identity="active",
    )
    suspended = await consumer.converge_snapshot(
        GameSessionSnapshot("game", "nonce", "user", None, 2, "digest", False, True, "resume"),
        active, operation_identity="suspended",
    )
    revoked = await consumer.converge_snapshot(
        GameSessionSnapshot("game", "nonce", "user", None, 3, "digest", False, False, "resume"),
        suspended, operation_identity="revoked",
    )
    assert (await consumer.converge_snapshot(
        GameSessionSnapshot("game", "nonce", "user", None, 3, "digest", False, False, "resume"),
        revoked, operation_identity="revoked",
    )) == revoked

    expired = replace(pending, binding_id="expired", status=BindingState.EXPIRED, binding_version=2)
    class ExpiryBindings:
        def __init__(self): self.first = True
        async def expire_due(self, limit):
            if self.first: self.first = False; return (expired,)
            return ()
        async def list_expired_for_projection(self, limit): return ()
    class Projector:
        async def project_runtime_state(self, binding): return True
    expiry = BindingExpiryWorker(ExpiryBindings(), Projector())
    assert await expiry.run_once() == 1
    assert await expiry.run_once() == 0

    states = [kw["binding_state"] for args, kw in async_events + coordinator_events
              if args[0] == "runtime_binding_transition_total"]
    assert states.count("pending") == 1
    assert states.count("active") == 1
    assert states.count("suspended") == 1
    assert states.count("revoked") == 1
    assert states.count("expired") == 1


@pytest.mark.asyncio
async def test_outbox_diagnostics_failure_is_best_effort_after_successful_ack(monkeypatch):
    authority_events, gateway_events = [], []
    monkeypatch.setattr(async_application_module, "emit_metric", lambda *a, **kw: authority_events.append((a, kw)))
    monkeypatch.setattr(coordinator_module, "emit_metric", lambda *a, **kw: gateway_events.append((a, kw)))

    class AuthorityRepo:
        acknowledged = []
        async def claim_pending_intents(self, limit=50):
            return ({"intent_id": "i", "intent_kind": "revoke", "binding_id": "b",
                     "binding_version": 1, "lease_epoch": 1, "payload": {}},)
        async def acknowledge_intent(self, intent_id): self.acknowledged.append(intent_id)
        async def retry_intent(self, *args): raise AssertionError("must not retry")
        async def diagnostic_stats(self): raise RuntimeError("stats backend body")
    class Authority:
        async def revoke(self, *args): return None
    authority_repo = AuthorityRepo()
    assert await AuthorityOutboxWorker(authority_repo, Authority()).run_once() == 1
    assert authority_repo.acknowledged == ["i"]

    event = SimpleNamespace(message_id="message")
    monkeypatch.setattr(persistence_module, "_gateway_event_from_payload", lambda payload: event)
    class GatewayRepo:
        acknowledged = []
        async def claim_pending(self, worker_id, limit): return ({"event_payload": {}},)
        async def update_stage(self, *args): return None
        async def acknowledge(self, message_id): self.acknowledged.append(message_id)
        async def release_claim(self, *args): raise AssertionError("must not retry")
        async def diagnostic_stats(self): raise RuntimeError("stats backend body")
    class Consumer:
        async def consume(self, event, recorder): return None
    gateway_repo = GatewayRepo()
    assert await GatewayEventOutboxWorker(gateway_repo, Consumer()).run_once() == 1
    assert gateway_repo.acknowledged == ["message"]
    for events, kind in ((authority_events, "authority"), (gateway_events, "gateway")):
        unavailable = [kw for args, kw in events if args[0] == "runtime_outbox_diagnostics_total"]
        assert unavailable == [{"outcome": "unavailable", "outbox_kind": kind,
                                "error_code": "diagnostic_stats_unavailable"}]


@pytest.mark.asyncio
async def test_real_hosted_websocket_endpoint_persists_open_and_close_jsonl(tmp_path):
    log_dir = configure_json_logging(component="m5-hosted-ws", log_dir=tmp_path)
    endpoint = hosted_module.HostedMcpWebSocketEndpoint(object(), object())
    app = web.Application()
    app.router.add_get("/mcp", endpoint.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as client:
            websocket = await client.ws_connect(
                f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": "Bearer endpoint-test"},
            )
            await websocket.close()
    finally:
        await runner.cleanup()

    operations = _records(log_dir / "operations", "m5-hosted-ws-info.jsonl*")
    metrics = _records(log_dir / "metrics", "m5-hosted-ws-metrics.jsonl*")
    assert [item.get("event") for item in operations].count("mcp.hosted.session.opened") == 1
    assert [item.get("event") for item in operations].count("mcp.hosted.session.closed") == 1
    outcomes = [item.get("outcome") for item in metrics
                if item.get("name") == "hosted_mcp_session_total"]
    assert outcomes == ["opened", "closed"]


@pytest.mark.asyncio
async def test_m5_regions_persist_machine_readable_operational_and_metric_jsonl(tmp_path, monkeypatch):
    log_dir = configure_json_logging(component="m5-metrics", log_dir=tmp_path)

    # Admission positive/reject and Runtime/Input/Adapter readiness positive.
    repo = InMemoryRuntimeAdmissionRepository(RuntimeAdmissionPolicy(max_active_global=1))
    await AsyncRuntimeService(repo).ensure_runtime(_spec())
    with pytest.raises(Exception):
        RuntimeAdmissionPolicy().require_allowed("denied")
    adapter = Adapter()
    manager = RuntimeManagerCoordinator(
        MemoryRepository(), PersonaReader(), Authority(), {"fake": adapter}, tmp_path / "runtime",
    )
    await manager.provision(product_spec())
    await manager.ensure(product_spec())
    await manager.submit_input("runtime-1", 1, RuntimeInput("persisted-input", "hello"))
    await manager.stop("runtime-1", 1, "persisted-stop")

    class SensitiveProviderError(RuntimeError):
        code = "provider-secret-code"

    class FailingInputAdapter(Adapter):
        async def submit_input(self, handle, value):
            raise SensitiveProviderError(
                "turn provider body credential=secret runtime=private-runtime"
            )
    input_repo, input_adapter = MemoryRepository(), FailingInputAdapter()
    input_manager = RuntimeManagerCoordinator(
        input_repo, PersonaReader(), Authority(), {"fake": input_adapter}, tmp_path / "input-failed",
    )
    input_spec = replace(product_spec(), runtime_id="runtime-input-failed", activation_key="input-failed")
    await input_manager.provision(input_spec)
    await input_manager.ensure(input_spec)
    with pytest.raises(SensitiveProviderError):
        await input_manager.submit_input(
            "runtime-input-failed", 1, RuntimeInput("failed-input", "hello"),
        )
    provider_health = (await RuntimeManagerHealthService(input_manager).snapshot())["provider"]
    assert provider_health["last_failure_at"] is not None
    assert provider_health["last_failure_code"] == "unknown"
    assert provider_health["adapter_kind"] == "fake"
    assert set(provider_health) == {
        "last_failure_at", "last_failure_code", "adapter_kind",
    }
    assert "secret" not in str(provider_health)
    assert "runtime-input-failed" not in str(provider_health)

    readiness_adapter = Adapter()
    readiness_adapter.ready = False
    readiness_adapter.readiness_failure_kind = ReadinessFailureKind.PROVIDER_REJECTED
    readiness_manager = RuntimeManagerCoordinator(
        MemoryRepository(), PersonaReader(), Authority(), {"fake": readiness_adapter},
        tmp_path / "readiness-failed",
    )
    readiness_spec = replace(product_spec(), runtime_id="runtime-readiness-failed",
                             activation_key="readiness-failed")
    await readiness_manager.provision(readiness_spec)
    with pytest.raises(RuntimeError, match="provider_rejected"):
        await readiness_manager.ensure(readiness_spec)
    degraded_repo = MemoryRepository()
    degraded_manager = RuntimeManagerCoordinator(
        degraded_repo, PersonaReader(), Authority(), {"fake": Adapter()}, tmp_path / "degraded",
    )
    degraded = await degraded_manager.provision(replace(product_spec(), runtime_id="runtime-degraded"))
    await degraded_manager._transition(degraded, coordinator_module.RuntimeState.DEGRADED)

    class ExitedAdapter(Adapter):
        async def inspect(self, handle):
            from modules.runtime_manager.contracts import ProviderStatus
            return ProviderStatus(123, 9, True, False, None)
    exit_repo, exit_adapter = MemoryRepository(), ExitedAdapter()
    exit_manager = RuntimeManagerCoordinator(
        exit_repo, PersonaReader(), Authority(), {"fake": exit_adapter}, tmp_path / "exit",
    )
    exit_spec = replace(product_spec(), runtime_id="runtime-exit", activation_key="runtime-exit")
    await exit_manager.provision(exit_spec)
    await exit_manager.ensure(exit_spec)
    assert await exit_manager._reconcile_owned_health(
        ("runtime-exit", 1), exit_manager._handles[("runtime-exit", 1)], exit_adapter,
    ) is True

    # Gateway positive and bounded route failure.
    router, context = router_stack()
    await router.dispatch(context, command(context))
    await router.await_result(context, "command-1", 10.0)
    unavailable, unavailable_context = router_stack(False)
    with pytest.raises(Exception):
        await unavailable.dispatch(unavailable_context, command(unavailable_context))

    # Worker success/failure and durable outbox retry/stats.
    class Coordinator:
        async def reconcile_all(self): return 0
        async def shutdown(self): return None
    workers = RuntimeManagerWorkers(Coordinator(), object(), interval_seconds=0.001)
    async def worker_success(): workers._stop.set(); return 2
    await workers._loop("reconcile", worker_success)
    workers._stop = asyncio.Event()
    async def worker_failure(): workers._stop.set(); raise RuntimeError("not logged")
    await workers._loop("authority", worker_failure)

    class OutboxRepository:
        async def claim_pending_intents(self, limit=50):
            return ({"intent_id": "bad", "intent_kind": "revoke", "binding_id": "b",
                     "binding_version": 1, "lease_epoch": 1, "payload": {}},)
        async def retry_intent(self, intent_id, error, delay_seconds): return None
        async def acknowledge_intent(self, intent_id): return None
        async def diagnostic_stats(self): return {"pending": 7, "oldest_age_seconds": 42.0}
    class FailingAuthority:
        async def revoke(self, *_): raise RuntimeError("provider body must not be logged")
    await AuthorityOutboxWorker(OutboxRepository(), FailingAuthority()).run_once()

    # Credential owner failure and Claude spawn failure, both with stable-only diagnostics.
    binding = RuntimeBinding(
        "credential-binding", "credential-runtime", 1, "user", "agent", 1, 1,
        BindingState.ACTIVE, "game", "nonce", "digest", "resume",
    )
    authority = HostedCredentialFileAuthority.__new__(HostedCredentialFileAuthority)
    async def skip_signing_key_check(): return None
    authority._ensure_signing_key = skip_signing_key_check
    async def fail_issue(*args, **kwargs): raise RuntimeError("credential secret body")
    authority._issue = fail_issue
    with pytest.raises(RuntimeError):
        await authority.issue(binding)
    async def fail_revoke(*args, **kwargs): raise RuntimeError("revocation secret body")
    authority._revoke = fail_revoke
    with pytest.raises(RuntimeError):
        await authority.revoke(binding.binding_id, 1, 1)
    class Signer:
        def current_key_remaining_seconds(self): return None
        def sign(self, material): return "header.payload.signature"
    class Security:
        async def revoke_jti(self, jti): return None
        async def revoke_binding(self, *args): return None
    class Bindings:
        async def get(self, binding_id): return binding
    successful_authority = HostedCredentialFileAuthority(
        Signer(), Security(), Bindings(), tmp_path / "credentials",
    )
    await successful_authority.issue(binding)
    await successful_authority.revoke(binding.binding_id, 1, 1)

    async def fail_spawn(*args, **kwargs): raise OSError("provider spawn body")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)
    with pytest.raises(OSError):
        await ClaudeProcessTransport("spawn-runtime", 1).start(
            command=("claude",), cwd=tmp_path, env={},
        )

    metrics = _records(log_dir / "metrics", "m5-metrics-metrics.jsonl*")
    info = _records(log_dir / "operations", "m5-metrics-info.jsonl*")
    warn = _records(log_dir / "operations", "m5-metrics-warn.jsonl*")
    error = _records(log_dir / "operations", "m5-metrics-error.jsonl*")
    names = {item.get("name") for item in metrics}
    assert {
        "runtime_admission_total", "runtime_transition_total", "runtime_start_duration_ms",
        "runtime_stop_duration_ms", "runtime_input_total", "runtime_adapter_readiness_total",
        "runtime_input_duration_ms", "runtime_input_inflight",
        "gateway_command_total", "gateway_command_duration_ms", "gateway_session_route_total",
        "runtime_worker_batch_total", "runtime_outbox_retry_total",
        "runtime_outbox_pending", "runtime_outbox_oldest_age_seconds",
        "runtime_credential_total", "runtime_adapter_spawn_total",
        "runtime_adapter_process_exit_total",
    } <= names
    runtime_states = {item.get("runtime_state") for item in metrics
                      if item.get("name") == "runtime_transition_total"}
    assert {"provisioning", "starting", "ready_unbound", "degraded", "draining",
            "stopping", "stopped", "failed"} <= runtime_states
    events = {item.get("event") for item in info + warn + error}
    assert {
        "runtime.admission.accepted", "runtime.lifecycle.transition", "runtime.operation.start",
        "runtime.operation.stop", "runtime.input.completed", "runtime.adapter.readiness",
        "runtime.input.accepted", "runtime.input.failed", "runtime.operation.start_failed",
        "gateway.session.route_resolved", "gateway.session.route_unavailable",
        "gateway.command.dispatch", "gateway.command.dispatch_failed",
        "runtime.worker.batch.started", "runtime.worker.batch.failed", "runtime.outbox.retry",
        "runtime.credential.issue_failed", "runtime_manager.claude.process.spawn_failed",
        "runtime.credential.revoke_failed",
        "runtime.credential.issued", "runtime.credential.revoked",
        "runtime.adapter.process_exit",
    } <= events
    rendered = "\n".join(json.dumps(item) for item in info + warn + error)
    assert "provider body must not be logged" not in rendered
    assert "credential secret body" not in rendered
    assert "revocation secret body" not in rendered
    assert "provider spawn body" not in rendered
    assert "turn provider body" not in rendered
    assert "not logged" not in rendered


@pytest.mark.asyncio
async def test_hosted_chain_persists_mcp_skill_capability_jsonl_while_local_does_not(tmp_path):
    log_dir = configure_json_logging(component="m5-hosted", log_dir=tmp_path)
    session, _, _ = await _stack()
    try:
        await session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        await session.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        result = await session.request({
            "jsonrpc": "2.0", "id": "hosted-real", "method": "tools/call",
            "params": {"name": "skill:stardew.proto.query_runtime_snapshot@1.0.0",
                       "arguments": {}, "_meta": {"toolCallId": "hosted-real-tool"}},
        })
        assert result["result"]["isError"] is False
    finally:
        await session.close()

    failed_session, _, _ = await _stack(fail_capability="query_ui_snapshot")
    try:
        await failed_session.request({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}})
        await failed_session.request({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})
        failed = await failed_session.request({
            "jsonrpc": "2.0", "id": "hosted-real-failed", "method": "tools/call",
            "params": {"name": "skill:stardew.basic.observe_current_view@1.0.0",
                       "arguments": {}, "_meta": {"toolCallId": "hosted-real-failed-tool"}},
        })
        assert "error" in failed or failed["result"]["isError"] is True
    finally:
        await failed_session.close()

    metrics = _records(log_dir / "metrics", "m5-hosted-metrics.jsonl*")
    operations = (
        _records(log_dir / "operations", "m5-hosted-info.jsonl*")
        + _records(log_dir / "operations", "m5-hosted-warn.jsonl*")
    )
    names = {item.get("name") for item in metrics}
    assert {"hosted_mcp_call_total", "hosted_mcp_call_duration_ms",
            "skill_execution_total", "skill_execution_duration_ms",
            "capability_execution_total", "capability_execution_duration_ms"} <= names
    events = {item.get("event") for item in operations}
    assert {"mcp.hosted.call.started", "mcp.hosted.call.finished",
            "skill.execution.started", "skill.execution.finished",
            "capability.execution.dispatched", "capability.execution.result"} <= events
    assert any(item.get("name") == "capability_execution_total"
               and item.get("result_state") == "failed" for item in metrics)
    assert any(item.get("event") == "capability.execution.result"
               and item.get("result_state") == "failed" for item in operations)
    chain = [item for item in metrics if item.get("tool_call_id") == "hosted-real-tool"]
    assert chain and len({item.get("log_id") for item in chain}) == 1

    before = len([item for item in metrics if str(item.get("name", "")).startswith("hosted_mcp_")])
    class LocalAuthority:
        async def establish(self, metadata): return object()
        async def close(self, handle): return None
    class LocalCore:
        catalog_digest = "local"
        _projection = None
        async def list_tools(self, handle): return ()
    local = StdioMcpTransport(LocalCore(), LocalAuthority(), writer=io.StringIO(),
                             connection_metadata={"credential": "local-only"})
    await local.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    await local.handle_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
    await local.close()
    after_metrics = _records(log_dir / "metrics", "m5-hosted-metrics.jsonl*")
    after = len([item for item in after_metrics if str(item.get("name", "")).startswith("hosted_mcp_")])
    assert after == before
