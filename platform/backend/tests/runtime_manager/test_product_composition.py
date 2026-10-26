from __future__ import annotations

import hashlib
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.runtime_manager.coordinator import (
    ActivityKind, EffectiveActivityReporter, HostedGatewayEventConsumer, RuntimeManagerCoordinator,
    RuntimeManagerWorkers,
)
from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayEvent, GatewayEventType
from modules.runtime_manager.contracts import (
    PreparedRuntime, ProviderStatus, ReadyResult, ReadinessFailureKind,
    ReconcileAction, ReconcileDecision,
    RuntimeHandle, RuntimeInput, StopResult, TurnResult,
)
from modules.runtime_manager.domain import (
    BindingState, Conflict, DesiredState, InvalidSpec, ResourceExhausted, RuntimeBinding,
    RuntimeProductSpec, RuntimeState,
)
from modules.runtime_manager.persona import PersonaArtifactSnapshot
from modules.runtime_manager.product_repository import OperationResult
from modules.runtime_manager.emergency_terminator import EmergencyTerminationResult
from modules.runtime_manager.correlation import ActiveTurnCorrelationRegistry


class MemoryRepository:
    def __init__(self):
        self.records = {}
        self.activation = {}
        self.operations = {}
        self.input_operations = {}

    async def ensure(self, record, digest):
        key = (record.spec.user_id, record.spec.agentee_id, record.spec.activation_key)
        old = self.activation.get(key)
        if old and old.active:
            return old
        self.records[(record.runtime_id, record.generation)] = record
        self.activation[key] = record
        return record

    async def find_activation(self, *key): return self.activation.get(tuple(key))
    async def get(self, runtime_id):
        rows = [v for (rid, _), v in self.records.items() if rid == runtime_id]
        return max(rows, key=lambda item: item.generation) if rows else None
    async def get_generation(self, runtime_id, generation): return self.records.get((runtime_id, generation))
    async def list_active(self): return tuple(v for v in self.records.values() if v.active)
    async def get_operation(self, owner, operation):
        input_operation = self.input_operations.get((owner, operation))
        if input_operation is not None:
            return OperationResult(
                owner, operation, input_operation["fingerprint"],
                input_operation["payload"],
            )
        return self.operations.get((owner, operation))
    async def claim_input_operation(self, owner, operation, fingerprint):
        existing = self.input_operations.get((owner, operation))
        if existing:
            if existing["fingerprint"] != fingerprint: raise Conflict("operation collision")
            return existing["payload"]
        self.input_operations[(owner, operation)] = {
            "fingerprint": fingerprint, "payload": {"status": "pending"},
        }
        return {"status": "claimed"}
    async def complete_input_operation(self, owner, operation, result):
        self.input_operations[(owner, operation)]["payload"] = {"status": "completed", "result": dict(result)}
    async def compare_and_swap(self, record, expected):
        current = self.records[(record.runtime_id, record.generation)]
        if current.status_version != expected: raise Conflict("runtime status_version mismatch")
        result = replace(record, status_version=expected + 1)
        self.records[(record.runtime_id, record.generation)] = result
        for key, value in list(self.activation.items()):
            if value.runtime_id == result.runtime_id and value.generation == result.generation:
                self.activation[key] = result
        return result
    async def compare_and_swap_with_operation(self, record, expected, operation):
        cached = self.operations.get((operation.owner_ref, operation.operation_id))
        if cached:
            if cached.fingerprint != operation.fingerprint: raise Conflict("operation collision")
            return cached.result
        result = await self.compare_and_swap(record, expected)
        self.operations[(operation.owner_ref, operation.operation_id)] = replace(operation, result=result)
        return result


class PersonaReader:
    def __init__(self, owner="agentee-1"):
        payload = "Be kind"
        self.artifact = PersonaArtifactSnapshot(
            owner, "rev-1", hashlib.sha256(payload.encode()).hexdigest(),
            datetime.now(timezone.utc).isoformat(), payload,
        )
    async def read(self, _reference, user_id, agentee_id):
        if agentee_id != self.artifact.agentee_id: raise ValueError("persona_owner_mismatch")
        return self.artifact


class Authority:
    def __init__(self): self.events = []
    async def allow_ingress(self, *identity): self.events.append(("allow", *identity))
    async def deny_and_revoke(self, *identity): self.events.append(("revoke", *identity))
    async def is_bound(self, *identity): return False


class StartGateAuthority(Authority):
    def __init__(self):
        super().__init__()
        self.credential_issued = False
    async def is_bound(self, *identity): return True
    async def is_credential_issued(self, *identity): return self.credential_issued


class StartGateBindings:
    def __init__(self): self.binding = None; self.operations = {}
    async def find_for_runtime(self, runtime_id, generation):
        if self.binding and (self.binding.runtime_id, self.binding.generation) == (runtime_id, generation):
            return self.binding
        return None
    async def insert_with_operation(self, binding, operation):
        cached = self.operations.get((operation.owner_ref, operation.operation_id))
        if cached is not None: return cached
        self.binding = binding
        self.operations[(operation.owner_ref, operation.operation_id)] = binding
        return binding


class Adapter:
    def __init__(self):
        self.events = []; self.ready = True; self.reconcile_action = ReconcileAction.ADOPT
        self.input_gate = None; self.last_handle = None; self.stop_error = None
        self.readiness_failure_kind = ReadinessFailureKind.TIMEOUT
    async def prepare(self, spec):
        self.events.append("prepare")
        workdir = spec.workspace_root / spec.runtime_id / f"generation-{spec.generation}"
        workdir.mkdir(parents=True, exist_ok=True)
        if spec.persona_artifact and not (workdir / "persona-artifact.json").exists():
            (workdir / "persona-artifact.json").write_text(spec.persona_artifact.instruction_payload)
        (workdir / "runtime-owner.json").write_text(json.dumps({
            "runtime_id": spec.runtime_id, "generation": spec.generation, "nonce": "nonce-1",
        }))
        return PreparedRuntime(spec, workdir)
    async def start(self, prepared):
        self.events.append("start")
        self.last_handle = RuntimeHandle(prepared.spec.runtime_id, prepared.spec.generation, prepared.workdir, object())
        return self.last_handle
    async def wait_ready(self, handle, deadline):
        self.events.append("ready")
        return ReadyResult(
            self.ready, handle.runtime_id, handle.generation,
            failure_kind=None if self.ready else self.readiness_failure_kind,
        )
    async def submit_input(self, handle, value):
        self.events.append("input")
        if self.input_gate is not None:
            await self.input_gate.wait()
        return TurnResult(value.request_id, handle.runtime_id, handle.generation, 1, "ok")
    async def checkpoint(self, handle, deadline): self.events.append("checkpoint"); return None
    async def stop(self, handle, reason, grace_period):
        self.events.append("stop")
        if self.stop_error: raise self.stop_error
        return StopResult(True, 0, "app_stop", reason)
    async def inspect(self, handle): return ProviderStatus(None, None, True, True, None)
    async def reconcile(self, record):
        return ReconcileDecision(self.reconcile_action, record.runtime_id, record.generation)
    async def adopt(self, record, decision): return self.last_handle


def spec():
    return RuntimeProductSpec(
        "runtime-1", 1, "activation-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/rev-1", idle_ttl_seconds=600,
    )


@pytest.mark.asyncio
async def test_product_start_requires_exact_active_owner_binding_and_processed_credential(tmp_path):
    repo, authority, adapter, bindings = (
        MemoryRepository(), StartGateAuthority(), Adapter(), StartGateBindings()
    )
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        binding_repository=bindings,
    )
    provisioned = await manager.provision(spec())
    assert provisioned.actual_state is RuntimeState.PROVISIONING

    # The formal provision -> Binding activation -> ensure sequence may take
    # longer than multiple background-worker intervals.  A never-started
    # PROVISIONING record has no process to quarantine and must remain intact.
    adapter.reconcile_action = ReconcileAction.QUARANTINE_UNKNOWN
    for _ in range(3):
        assert await manager.reconcile_all() == 0
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.PROVISIONING
    assert authority.events == []

    with pytest.raises(RuntimeError, match="binding_not_active"):
        await manager.ensure(spec())
    assert adapter.events == []

    pending = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "binding-op"
    )
    with pytest.raises(RuntimeError, match="binding_not_active"):
        await manager.ensure(spec())

    bindings.binding = replace(
        pending, status=BindingState.ACTIVE, binding_version=2, lease_epoch=1,
        game_session_id="game-1", game_instance_nonce="nonce-1",
        capability_digest="digest-1",
    )
    with pytest.raises(RuntimeError, match="authority_credential_not_issued"):
        await manager.ensure(spec())
    assert adapter.events == []

    bindings.binding = replace(bindings.binding, user_id="foreign-user")
    authority.credential_issued = True
    with pytest.raises(RuntimeError, match="binding_not_active"):
        await manager.ensure(spec())
    assert adapter.events == []

    bindings.binding = replace(bindings.binding, user_id="user-1")
    started = await manager.ensure(spec())
    assert started.actual_state is RuntimeState.RUNNING_BOUND
    assert adapter.events == ["prepare", "start", "ready"]


@pytest.mark.asyncio
async def test_provisioning_with_startup_trace_is_still_fail_closed_as_orphan(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    current = await manager.provision(spec())
    workdir = tmp_path / "runtime-1/generation-1"
    workdir.mkdir(parents=True)
    (workdir / "runtime-owner.json").write_text("{}")
    adapter.reconcile_action = ReconcileAction.QUARANTINE_UNKNOWN

    assert await manager.reconcile_all() == 1
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.FAILED
    assert authority.events[-1] == ("revoke", "runtime-1", 1, "reconcile_failed")


@pytest.mark.asyncio
async def test_formal_provision_sequence_may_exceed_multiple_worker_intervals(tmp_path):
    repo, authority, adapter, bindings = (
        MemoryRepository(), StartGateAuthority(), Adapter(), StartGateBindings()
    )
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        binding_repository=bindings,
    )
    class NoopAuthorityWorker:
        async def run_once(self): return 0
    workers = RuntimeManagerWorkers(manager, NoopAuthorityWorker(), interval_seconds=.005)

    await manager.provision(spec())
    await workers.start()
    try:
        # Six worker intervals elapse before the product finishes Binding and
        # credential setup.  Reconcile must not revoke the pending sequence.
        await asyncio.sleep(.03)
        assert (await repo.get("runtime-1")).actual_state is RuntimeState.PROVISIONING
        pending = await manager.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "slow-binding-op"
        )
        bindings.binding = replace(
            pending, status=BindingState.ACTIVE, binding_version=2, lease_epoch=1,
            game_session_id="game-1", game_instance_nonce="nonce-1",
            capability_digest="digest-1",
        )
        authority.credential_issued = True
        await asyncio.sleep(.015)
        started = await manager.ensure(spec())
        assert started.actual_state is RuntimeState.RUNNING_BOUND
        assert adapter.events == ["prepare", "start", "ready"]
    finally:
        await workers.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent_and_materializes_persona_before_ready(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    first = await manager.ensure(spec())
    second = await manager.ensure(spec())
    assert first.actual_state is RuntimeState.READY_UNBOUND and second == first
    assert adapter.events == ["prepare", "start", "ready"]
    assert authority.events == [("allow", "runtime-1", 1)]
    assert (tmp_path / "runtime-1/generation-1/persona-artifact.json").exists()


@pytest.mark.asyncio
async def test_stop_fences_before_checkpoint_stop_and_cleanup(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())
    result = await manager.stop("runtime-1", 1, "stop-op")
    assert result.actual_state is RuntimeState.STOPPED
    assert authority.events[-1][:3] == ("revoke", "runtime-1", 1)
    assert adapter.events[-2:] == ["checkpoint", "stop"]
    assert not (tmp_path / "runtime-1/generation-1").exists()
    assert await manager.stop("runtime-1", 1, "stop-op") == result


@pytest.mark.asyncio
async def test_control_stop_waits_for_durable_revocation_before_adapter_stop(tmp_path):
    order = []

    class DurableAuthority(Authority):
        async def deny_and_revoke(self, *identity):
            order.append("revoke")
            await super().deny_and_revoke(*identity)
            return "revoke-binding-1"

    class OrderedAdapter(Adapter):
        async def stop(self, handle, reason, grace_period):
            order.append("adapter-stop")
            return await super().stop(handle, reason, grace_period)

    repo, authority, adapter = MemoryRepository(), DurableAuthority(), OrderedAdapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())

    async def confirm(intent_id):
        assert intent_id == "revoke-binding-1"
        order.append("durable-ack")

    manager._confirm_authority_revoked = confirm
    stopped = await manager.stop(
        "runtime-1", 1, "controller-close", "agent_control_closed",
        require_durable_revocation=True,
    )

    assert stopped.actual_state is RuntimeState.STOPPED
    assert order == ["revoke", "durable-ack", "adapter-stop"]


@pytest.mark.asyncio
async def test_effective_activity_matrix_and_fixed_600_second_boundary(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    record = await manager.ensure(spec())
    baseline = record.last_effective_activity_at
    assert baseline is not None
    for kind in (ActivityKind.LOG, ActivityKind.HEARTBEAT, ActivityKind.FAILURE):
        assert not await manager.activity.report("runtime-1", 1, kind, baseline + timedelta(seconds=100))
    assert (await repo.get("runtime-1")).last_effective_activity_at == baseline
    assert await manager.activity.report("runtime-1", 1, ActivityKind.MCP_SUCCESS, baseline + timedelta(seconds=1))
    assert await manager.sweep_idle(baseline + timedelta(seconds=600)) == 0
    assert await manager.sweep_idle(baseline + timedelta(seconds=601)) == 1


@pytest.mark.asyncio
async def test_start_failure_revokes_cleans_and_persists_failed(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.ready = False
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    with pytest.raises(RuntimeError, match="ready_timeout"):
        await manager.ensure(spec())
    record = await repo.get("runtime-1")
    assert record.actual_state is RuntimeState.FAILED
    assert authority.events[-1] == ("revoke", "runtime-1", 1, "startup_failed")
    assert "stop" in adapter.events
    assert not (tmp_path / "runtime-1/generation-1").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "error_code"),
    [
        (ReadinessFailureKind.PROCESS_EXITED, "process_exited"),
        (ReadinessFailureKind.PROVIDER_REJECTED, "provider_rejected"),
        (ReadinessFailureKind.READINESS_FAILED, "readiness_failed"),
    ],
)
async def test_start_readiness_failure_preserves_safe_reason_and_still_revokes(
    tmp_path, failure_kind, error_code,
):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.ready = False
    adapter.readiness_failure_kind = failure_kind
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)

    with pytest.raises(RuntimeError, match=f"^{error_code}$"):
        await manager.ensure(spec())

    record = await repo.get("runtime-1")
    assert record.actual_state is RuntimeState.FAILED
    assert record.exit_reason == error_code
    assert authority.events[-1] == ("revoke", "runtime-1", 1, "startup_failed")
    assert "stop" in adapter.events
    assert not (tmp_path / "runtime-1/generation-1").exists()


@pytest.mark.asyncio
async def test_failed_runtime_rolls_to_next_generation_through_provisioning(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.ready = False
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    with pytest.raises(RuntimeError, match="ready_timeout"):
        await manager.ensure(spec())
    failed = await repo.get("runtime-1")
    assert failed.actual_state is RuntimeState.FAILED
    assert failed.exit_reason == "ready_timeout"

    adapter.ready = True
    next_spec = replace(spec(), generation=2)
    recovered = await manager.provision(next_spec)
    assert recovered.generation == 2
    assert recovered.actual_state is RuntimeState.PROVISIONING
    assert recovered.desired_state is DesiredState.RUNNING
    assert recovered.ready is False
    assert recovered.exit_reason is None
    assert (await repo.get_generation("runtime-1", 1)).actual_state is RuntimeState.STOPPED

    ready = await manager.ensure(next_spec)
    assert ready.generation == 2
    assert ready.actual_state is RuntimeState.READY_UNBOUND
    assert ready.ready is True


@pytest.mark.asyncio
async def test_stopped_runtime_creates_next_generation(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    await manager.ensure(spec())
    stopped = await manager.stop("runtime-1", 1, "stop-before-retry")
    assert stopped.actual_state is RuntimeState.STOPPED

    recovered = await manager.provision(replace(spec(), generation=2))
    assert recovered.generation == 2
    assert recovered.actual_state is RuntimeState.PROVISIONING
    assert recovered.desired_state is DesiredState.RUNNING


@pytest.mark.asyncio
async def test_concurrent_failed_recovery_is_idempotent(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.ready = False
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    with pytest.raises(RuntimeError, match="ready_timeout"):
        await manager.ensure(spec())

    first, second = await asyncio.gather(
        manager.provision(replace(spec(), generation=2)),
        manager.provision(replace(spec(), generation=2)),
    )
    assert first == second
    assert first.generation == 2
    assert first.actual_state is RuntimeState.PROVISIONING


@pytest.mark.asyncio
async def test_terminal_recovery_rejects_activation_intent_mismatch(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.ready = False
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    with pytest.raises(RuntimeError, match="ready_timeout"):
        await manager.ensure(spec())

    changed = replace(spec(), generation=2, adapter_config_ref="adapter/changed")
    with pytest.raises(Conflict, match="runtime activation intent mismatch"):
        await manager.provision(changed)
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.FAILED


@pytest.mark.asyncio
async def test_terminal_hosted_activation_can_roll_to_updated_server_intent(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    await manager.ensure(spec())
    await manager.stop("runtime-1", 1, "hosted-upgrade-stop", "idle_ttl_expired")

    upgraded = replace(
        spec(), generation=2,
        allowed_capabilities=("say", "skill:stardew.advanced.observe@1.0.0"),
    )
    result = await manager.provision(
        upgraded, allow_terminal_intent_change=True,
    )

    assert result.generation == 2
    assert result.actual_state is RuntimeState.PROVISIONING
    assert result.spec.allowed_capabilities == upgraded.allowed_capabilities


@pytest.mark.asyncio
async def test_persona_owner_mismatch_fails_before_spawn(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader("other"), authority, {"fake": adapter}, tmp_path)
    with pytest.raises(ValueError, match="persona_owner_mismatch"):
        await manager.ensure(spec())
    assert adapter.events == []
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.FAILED


@pytest.mark.asyncio
async def test_stop_rejects_new_input_and_bounded_drain_waits_for_admitted_turn(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.input_gate = asyncio.Event()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        drain_timeout_seconds=1,
    )
    await manager.ensure(spec())
    turn = asyncio.create_task(manager.submit_input("runtime-1", 1, RuntimeInput("turn-1", "hello")))
    await asyncio.sleep(0)
    stopping = asyncio.create_task(manager.stop("runtime-1", 1, "stop-inflight"))
    await asyncio.sleep(0)
    with pytest.raises(ResourceExhausted, match="already in flight"):
        await manager.submit_input("runtime-1", 1, RuntimeInput("turn-2", "late"))
    assert not stopping.done()
    adapter.input_gate.set()
    await turn
    assert (await stopping).actual_state is RuntimeState.STOPPED


@pytest.mark.asyncio
async def test_runtime_input_reservation_precedes_claim_and_shares_same_operation(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.input_gate = asyncio.Event()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())

    first_caller = asyncio.create_task(
        manager.submit_input("runtime-1", 1, RuntimeInput("turn-a", "hello"))
    )
    for _ in range(20):
        if "input" in adapter.events:
            break
        await asyncio.sleep(0)
    assert "input" in adapter.events

    # Caller cancellation cannot release the reservation while the shielded
    # owner task still has Provider work in progress.
    first_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_caller
    with pytest.raises(ResourceExhausted, match="already in flight"):
        await manager.submit_input("runtime-1", 1, RuntimeInput("turn-b", "other"))
    with pytest.raises(Conflict, match="different input"):
        await manager.submit_input("runtime-1", 1, RuntimeInput("turn-a", "changed"))

    shared = asyncio.create_task(
        manager.submit_input("runtime-1", 1, RuntimeInput("turn-a", "hello"))
    )
    await asyncio.sleep(0)
    assert adapter.events.count("input") == 1
    assert len(repo.input_operations) == 1
    adapter.input_gate.set()
    assert (await shared).request_id == "turn-a"

    # Compare-and-pop runs only when the owner task is truly terminal.
    fresh = await manager.submit_input("runtime-1", 1, RuntimeInput("turn-b", "other"))
    assert fresh.request_id == "turn-b"
    assert adapter.events.count("input") == 2


@pytest.mark.asyncio
async def test_turn_completion_hook_runs_after_reservation_release(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    followup_completed = asyncio.Event()
    manager = None

    async def completion_hook(runtime_id, generation, value, result, error):
        if value.request_id != "original-turn":
            return
        assert error is None
        followup = await manager.submit_input(
            runtime_id, generation, RuntimeInput("followup-turn", "reply visibly"),
        )
        assert followup.request_id == "followup-turn"
        followup_completed.set()

    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        turn_completion_hook=completion_hook,
    )
    await manager.ensure(spec())

    original = await manager.submit_input(
        "runtime-1", 1, RuntimeInput("original-turn", "hello"),
    )
    await asyncio.wait_for(followup_completed.wait(), timeout=1)

    assert original.request_id == "original-turn"
    assert adapter.events.count("input") == 2


@pytest.mark.asyncio
async def test_runtime_input_limit_uses_utf8_bytes_before_durable_claim(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        max_input_bytes=65536,
    )
    await manager.ensure(spec())

    accepted = await manager.submit_input(
        "runtime-1", 1, RuntimeInput("exact", "a" * 65536)
    )
    assert accepted.request_id == "exact"
    before = len(repo.input_operations)
    with pytest.raises(ResourceExhausted, match="byte limit"):
        await manager.submit_input(
            "runtime-1", 1, RuntimeInput("ascii-too-large", "a" * 65537)
        )
    assert len(repo.input_operations) == before
    assert adapter.events.count("input") == 1

    multibyte_exact = "a" * 65533 + "界"
    assert len(multibyte_exact.encode("utf-8")) == 65536
    assert (await manager.submit_input(
        "runtime-1", 1, RuntimeInput("multibyte-exact", multibyte_exact)
    )).request_id == "multibyte-exact"
    multibyte_too_large = "a" * 65534 + "界"
    assert len(multibyte_too_large.encode("utf-8")) == 65537
    before = len(repo.input_operations)
    with pytest.raises(ResourceExhausted, match="byte limit"):
        await manager.submit_input(
            "runtime-1", 1, RuntimeInput("multibyte-too-large", multibyte_too_large)
        )
    assert len(repo.input_operations) == before
    assert adapter.events.count("input") == 2

    with pytest.raises(InvalidSpec, match="valid UTF-8"):
        await manager.submit_input(
            "runtime-1", 1, RuntimeInput("bad-utf8", "\ud800")
        )


@pytest.mark.asyncio
async def test_runtime_input_reservation_releases_on_provider_failure_and_cancel(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())
    original = adapter.submit_input

    async def fail(_handle, _value):
        raise OSError("provider failed")

    adapter.submit_input = fail
    with pytest.raises(OSError, match="provider failed"):
        await manager.submit_input("runtime-1", 1, RuntimeInput("failure", "one"))

    async def cancel(_handle, _value):
        raise asyncio.CancelledError

    adapter.submit_input = cancel
    with pytest.raises(asyncio.CancelledError):
        await manager.submit_input("runtime-1", 1, RuntimeInput("cancel", "two"))

    adapter.submit_input = original
    result = await manager.submit_input("runtime-1", 1, RuntimeInput("after", "three"))
    assert result.request_id == "after"


@pytest.mark.asyncio
async def test_completed_replay_bypasses_different_active_reservation_read_only(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())
    completed = await manager.submit_input(
        "runtime-1", 1, RuntimeInput("completed-a", "original")
    )

    adapter.input_gate = asyncio.Event()
    active = asyncio.create_task(
        manager.submit_input("runtime-1", 1, RuntimeInput("active-b", "blocked"))
    )
    for _ in range(20):
        if adapter.events.count("input") == 2:
            break
        await asyncio.sleep(0)
    assert adapter.events.count("input") == 2
    operations_before = len(repo.input_operations)

    replay = await manager.submit_input(
        "runtime-1", 1, RuntimeInput("completed-a", "original")
    )
    assert replay == completed
    assert len(repo.input_operations) == operations_before
    assert adapter.events.count("input") == 2
    with pytest.raises(Conflict, match="different input"):
        await manager.submit_input(
            "runtime-1", 1, RuntimeInput("completed-a", "changed")
        )
    with pytest.raises(ResourceExhausted, match="already in flight"):
        await manager.submit_input(
            "runtime-1", 1, RuntimeInput("novel-c", "novel")
        )
    assert len(repo.input_operations) == operations_before

    adapter.input_gate.set()
    assert (await active).request_id == "active-b"


@pytest.mark.asyncio
async def test_runtime_input_without_log_id_registers_active_turn_and_only_clears_matching_operation(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.input_gate = asyncio.Event()
    correlations = ActiveTurnCorrelationRegistry()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        turn_correlation_registry=correlations,
    )
    await manager.ensure(spec())
    turn = asyncio.create_task(manager.submit_input(
        "runtime-1", 1,
        RuntimeInput(
            "turn-1", "hello",
            {"operation_id": "turn-1"},
        ),
    ))
    for _ in range(20):
        if correlations.lookup("runtime-1", 1) is not None:
            break
        await asyncio.sleep(0)
    active = correlations.lookup("runtime-1", 1)
    assert active is not None
    assert active.log_id.startswith("log_") and len(active.log_id) == 36
    assert active.operation_id == "turn-1"

    # A stale completion must not erase a newer diagnostic registration.
    correlations.register("runtime-1", 1, "log_" + "b" * 32, "turn-newer")
    adapter.input_gate.set()
    await turn
    assert correlations.lookup("runtime-1", 1).operation_id == "turn-newer"


@pytest.mark.asyncio
async def test_restart_reconcile_rebuilds_persona_and_converges_crash(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())
    persona = tmp_path / "runtime-1/generation-1/persona-artifact.json"
    persona.chmod(0o644)
    persona.unlink()
    adapter.reconcile_action = ReconcileAction.MARK_FAILED
    restarted = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    assert await restarted.reconcile_all() == 1
    assert persona.exists()
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.FAILED
    assert authority.events[-1] == ("revoke", "runtime-1", 1, "reconcile_failed")


@pytest.mark.asyncio
async def test_reconnectable_adopt_restores_input_and_persisted_retry_does_not_repeat(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    first = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await first.ensure(spec())
    original = await first.submit_input("runtime-1", 1, RuntimeInput("stable-op", "hello"))
    assert adapter.events.count("input") == 1
    restarted = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    assert await restarted.reconcile_all() == 1
    restored = await restarted.submit_input("runtime-1", 1, RuntimeInput("stable-op", "hello"))
    assert restored == original
    assert adapter.events.count("input") == 1
    fresh = await restarted.submit_input("runtime-1", 1, RuntimeInput("fresh-op", "again"))
    assert fresh.content == "ok" and adapter.events.count("input") == 2


@pytest.mark.asyncio
async def test_desired_stop_after_restart_terminates_orphan_then_marks_stopped(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    first = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await first.ensure(spec())
    restarted = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    draining = await restarted.stop("runtime-1", 1, "restart-stop")
    assert draining.actual_state is RuntimeState.DRAINING
    adapter.reconcile_action = ReconcileAction.TERMINATE_ORPHAN
    assert await restarted.reconcile_all() == 1
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.STOPPED


@pytest.mark.asyncio
async def test_desired_stop_reconcile_never_adopts_even_if_adapter_requests_adopt(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    first = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await first.ensure(spec())
    restarted = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await restarted.stop("runtime-1", 1, "restart-stop-adopt")
    adapter.reconcile_action = ReconcileAction.ADOPT
    stops_before = adapter.events.count("stop")
    assert await restarted.reconcile_all() == 1
    assert adapter.events.count("stop") == stops_before + 1
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.STOPPED
    assert restarted._handles == {}
    assert not (tmp_path / "runtime-1/generation-1").exists()


@pytest.mark.asyncio
async def test_gateway_disconnect_only_suspends_binding_and_never_stops_runtime(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    before = await manager.ensure(spec())

    class Projection:
        async def consume(self, event): return type("Result", (), {"duplicate": False, "stale": False})()
    class Bindings:
        def __init__(self):
            self.binding = RuntimeBinding(
                "binding-1", "runtime-1", 1, "user-1", "agentee-1", 2, 1,
                BindingState.ACTIVE, "game-1", "nonce-1", "digest-1", "resume-1",
            )
        async def find_for_game_session(self, game_session_id): return self.binding
        async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
            self.binding = binding; return binding
    bindings = Bindings()
    consumer = HostedGatewayEventConsumer(Projection(), bindings)
    snapshot = GameSessionSnapshot("game-1", "nonce-1", "user-1", None, 2, "digest-1", False, True, "resume-1")
    await consumer.consume(GatewayEvent(GatewayEventType.SUSPENDED, snapshot))
    assert bindings.binding.status is BindingState.SUSPENDED
    assert bindings.binding.binding_version == 3 and bindings.binding.lease_epoch == 1
    after = await repo.get("runtime-1")
    assert after.actual_state == before.actual_state and after.desired_state == before.desired_state
    assert "stop" not in adapter.events


@pytest.mark.asyncio
async def test_stop_failure_stays_recoverable_and_reconcile_closes_orphan(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())
    adapter.stop_error = OSError("term failed")
    with pytest.raises(RuntimeError, match="adapter_unavailable"):
        await manager.stop("runtime-1", 1, "stop-failure")
    failed_stop = await repo.get("runtime-1")
    assert failed_stop.actual_state is RuntimeState.STOPPING
    assert failed_stop.desired_state.value == "stopped"
    assert (tmp_path / "runtime-1/generation-1/runtime-owner.json").exists()
    assert authority.events[-1][:3] == ("revoke", "runtime-1", 1)
    adapter.stop_error = None
    adapter.reconcile_action = ReconcileAction.TERMINATE_ORPHAN
    restarted = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    assert await restarted.reconcile_all() == 1
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.STOPPED
    assert not (tmp_path / "runtime-1/generation-1").exists()


@pytest.mark.asyncio
async def test_two_adapter_stop_failures_use_emergency_terminator_then_clear_all_evidence(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.stop_error = OSError("provider stop unavailable")
    class Emergency:
        def __init__(self): self.calls = []
        async def terminate(self, runtime_id, generation, workdir):
            self.calls.append((runtime_id, generation, workdir))
            return EmergencyTerminationResult(True, False, "kill")
    emergency = Emergency()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        emergency_terminator=emergency,
    )
    await manager.ensure(spec())
    result = await manager.stop("runtime-1", 1, "persistent-stop-failure")
    assert adapter.events.count("stop") == 2
    assert len(emergency.calls) == 1
    assert result.actual_state is RuntimeState.STOPPED
    assert result.exit_reason == "emergency_kill"
    assert manager._handles == {}
    assert not (tmp_path / "runtime-1/generation-1").exists()


@pytest.mark.asyncio
async def test_emergency_identity_mismatch_quarantines_failed_and_retains_evidence(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    adapter.stop_error = OSError("provider stop unavailable")
    class Quarantine:
        async def terminate(self, runtime_id, generation, workdir):
            return EmergencyTerminationResult(False, True, "process_identity_mismatch")
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
        emergency_terminator=Quarantine(),
    )
    await manager.ensure(spec())
    with pytest.raises(RuntimeError, match="runtime_quarantined"):
        await manager.stop("runtime-1", 1, "quarantine-stop")
    record = await repo.get("runtime-1")
    assert record.actual_state is RuntimeState.FAILED
    assert record.exit_reason == "quarantine:process_identity_mismatch"
    assert (tmp_path / "runtime-1/generation-1/runtime-owner.json").exists()
    assert manager._handles == {}


@pytest.mark.asyncio
async def test_shutdown_fences_drains_and_stops_every_active_handle(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(spec())
    await manager.ensure(replace(spec(), runtime_id="runtime-2", activation_key="activation-2"))
    await manager.shutdown()
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.STOPPED
    assert (await repo.get("runtime-2")).actual_state is RuntimeState.STOPPED
    assert adapter.events.count("stop") == 2
    revoked = [event for event in authority.events if event[0] == "revoke"]
    assert {event[1] for event in revoked} == {"runtime-1", "runtime-2"}
    assert manager._handles == {}
    assert not (tmp_path / "runtime-1/generation-1").exists()
    assert not (tmp_path / "runtime-2/generation-1").exists()


# ── Generation rollover contract tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_provision_rollover_failed_to_stopped_then_creates_generation_plus_one(tmp_path):
    """FAILED is terminal. provision with gen+1 finalizes FAILED→STOPPED,
    then inserts gen+1 with PROVISIONING."""
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    # Start and fail generation 1.
    await manager.ensure(spec())
    await manager._fail(await repo.get("runtime-1"), "test_crash")

    gen1 = await repo.get_generation("runtime-1", 1)
    assert gen1.actual_state is RuntimeState.FAILED

    # provision with gen+1 should rollover.
    gen2_spec = replace(spec(), generation=2)
    result = await manager.provision(gen2_spec)

    # Old generation finalized to STOPPED.
    gen1_after = await repo.get_generation("runtime-1", 1)
    assert gen1_after.actual_state is RuntimeState.STOPPED
    assert "rollover_to_generation_2" in (gen1_after.exit_reason or "")

    # New generation provisioned.
    assert result.runtime_id == "runtime-1"
    assert result.generation == 2
    assert result.actual_state is RuntimeState.PROVISIONING

    # Authority was revoked for old generation, workdir cleaned up.
    revoked = [e for e in authority.events if e[0] == "revoke"]
    assert any(e[1] == "runtime-1" and e[2] == 1 for e in revoked)
    assert not (tmp_path / "runtime-1/generation-1").exists()


@pytest.mark.asyncio
async def test_provision_rollover_stopped_creates_generation_plus_one(tmp_path):
    """STOPPED is terminal. provision with gen+1 inserts a fresh gen+1."""
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    # Start and stop generation 1.
    await manager.ensure(spec())
    await manager.stop("runtime-1", 1, "stop-op", "desired_stopped")

    gen1 = await repo.get_generation("runtime-1", 1)
    assert gen1.actual_state is RuntimeState.STOPPED

    # provision with gen+1 should create new generation.
    gen2_spec = replace(spec(), generation=2)
    result = await manager.provision(gen2_spec)

    assert result.runtime_id == "runtime-1"
    assert result.generation == 2
    assert result.actual_state is RuntimeState.PROVISIONING

    # Generation 1 remains STOPPED.
    gen1_after = await repo.get_generation("runtime-1", 1)
    assert gen1_after.actual_state is RuntimeState.STOPPED


@pytest.mark.asyncio
async def test_provision_rejects_same_generation_resurrection(tmp_path):
    """Same-generation resurrection of FAILED/STOPPED must fail."""
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    await manager.ensure(spec())
    await manager._fail(await repo.get("runtime-1"), "test_crash")

    with pytest.raises(Conflict, match="terminal generation requires rollover"):
        await manager.provision(spec())  # same gen=1


@pytest.mark.asyncio
async def test_rollover_idempotent_under_duplicate_provision(tmp_path):
    """Duplicate provision with gen+1 is idempotent — second call returns
    the already-created gen+1."""
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    await manager.ensure(spec())
    await manager._fail(await repo.get("runtime-1"), "test_crash")

    gen2_spec = replace(spec(), generation=2)
    first = await manager.provision(gen2_spec)
    second = await manager.provision(gen2_spec)

    assert (second.runtime_id, second.generation, second.actual_state) == (
        first.runtime_id, first.generation, first.actual_state,
    )
    assert second.generation == 2
    assert second.actual_state is RuntimeState.PROVISIONING


@pytest.mark.asyncio
async def test_old_generation_cas_rejected_after_rollover(tmp_path):
    """After rollover creates gen+1, a CAS targeting the old STOPPED
    generation must fail (status_version mismatch)."""
    repo, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repo, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    await manager.ensure(spec())
    original = await repo.get("runtime-1")
    await manager._fail(original, "test_crash")

    # Rollover finalizes gen 1 → STOPPED with a status_version bump.
    gen2_spec = replace(spec(), generation=2)
    await manager.provision(gen2_spec)

    # A stale writer trying to mutate the old gen's status_version must fail.
    with pytest.raises(Conflict):
        await repo.compare_and_swap(
            replace(original, actual_state=RuntimeState.PROVISIONING),
            original.status_version,
        )
