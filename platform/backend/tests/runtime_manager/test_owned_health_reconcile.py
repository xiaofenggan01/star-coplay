"""In-memory tests for owned-handle live-process health reconcile.

Exercises RuntimeManagerCoordinator._reconcile_owned_health and the
reconcile_all dispatch path: healthy process, crashed process, inspect
exception, CAS conflict with concurrent terminal/desired STOPPED, and
generation fencing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
from modules.runtime_manager.contracts import (
    PreparedRuntime, ProviderStatus, ReadyResult, ReconcileAction,
    ReconcileDecision, RuntimeHandle, RuntimeInput, StopResult, TurnResult,
)
from modules.runtime_manager.domain import (
    Conflict, DesiredState, RuntimeBinding, RuntimeProductRecord,
    RuntimeProductSpec, RuntimeState,
)
from modules.runtime_manager.persona import PersonaArtifactSnapshot
from modules.runtime_manager.product_repository import OperationResult


# ── Memory test doubles ────────────────────────────────────────────────

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
    async def list_failed_desired_running(self):
        return tuple(
            v for v in self.records.values()
            if v.actual_state is RuntimeState.FAILED and v.desired_state is DesiredState.RUNNING
        )
    async def get_operation(self, owner, operation): return self.operations.get((owner, operation))
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


class HealthAdapter:
    """Test adapter whose inspect() result is programmable per-test."""

    def __init__(self):
        self.events = []
        self.ready = True
        self.reconcile_action = ReconcileAction.ADOPT
        self.input_gate = None
        self.last_handle = None
        self.stop_error = None
        # Programmable inspect behaviour
        self._inspect_result: ProviderStatus | None = None
        self._inspect_error: Exception | None = None

    def set_inspect(self, *, pid=None, returncode=None, ready=True, healthy=True,
                    protocol_errors=0):
        self._inspect_result = ProviderStatus(
            pid=pid, returncode=returncode, ready=ready, healthy=healthy,
            last_frame_monotonic=None, protocol_errors=protocol_errors,
        )
        self._inspect_error = None

    def set_inspect_error(self, exc: Exception):
        self._inspect_result = None
        self._inspect_error = exc

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
        return ReadyResult(self.ready, handle.runtime_id, handle.generation)
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
    async def inspect(self, handle):
        self.events.append("inspect")
        if self._inspect_error is not None:
            raise self._inspect_error
        if self._inspect_result is not None:
            return self._inspect_result
        # Default: healthy
        return ProviderStatus(None, None, True, True, None)
    async def reconcile(self, record):
        return ReconcileDecision(self.reconcile_action, record.runtime_id, record.generation)
    async def adopt(self, record, decision): return self.last_handle


class BlockingReadinessAdapter(HealthAdapter):
    def __init__(self):
        super().__init__()
        self.readiness_entered = asyncio.Event()
        self.release_readiness = asyncio.Event()
        self.reconcile_calls = 0

    async def wait_ready(self, handle, deadline):
        self.events.append("ready")
        self.readiness_entered.set()
        await self.release_readiness.wait()
        return ReadyResult(self.ready, handle.runtime_id, handle.generation)

    async def reconcile(self, record):
        self.reconcile_calls += 1
        return await super().reconcile(record)


def _spec(**overrides):
    kw = dict(runtime_id="runtime-1", generation=1, activation_key="activation-1",
              user_id="user-1", agentee_id="agentee-1", adapter_kind="fake",
              adapter_config_ref="adapter/default", persona_artifact_ref="persona/rev-1",
              idle_ttl_seconds=600)
    kw.update(overrides)
    return RuntimeProductSpec(**kw)


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_during_readiness_uses_owned_handle(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), BlockingReadinessAdapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)

    ensure_task = asyncio.create_task(manager.ensure(_spec()))
    await adapter.readiness_entered.wait()

    starting = await repo.get("runtime-1")
    assert starting.actual_state is RuntimeState.STARTING
    assert ("runtime-1", 1) in manager._handles
    assert await manager.reconcile_all() == 0
    assert adapter.reconcile_calls == 0
    assert "stop" not in adapter.events
    assert (await repo.get("runtime-1")).actual_state is RuntimeState.STARTING

    adapter.release_readiness.set()
    running = await ensure_task
    assert running.actual_state is RuntimeState.READY_UNBOUND
    assert running.ready is True


@pytest.mark.asyncio
async def test_readiness_failure_removes_early_owned_handle(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    adapter.ready = False
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)

    with pytest.raises(RuntimeError, match="readiness_failed"):
        await manager.ensure(_spec())

    assert ("runtime-1", 1) not in manager._handles
    assert "stop" in adapter.events
    failed = await repo.get("runtime-1")
    assert failed.actual_state is RuntimeState.FAILED


@pytest.mark.asyncio
async def test_healthy_owned_process_does_not_change_state_or_bump_version(tmp_path):
    """Health reconcile on a live, healthy owned handle must not change
    durable state or bump status_version."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    adapter.set_inspect(healthy=True, returncode=None)
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    before = await repo.get("runtime-1")
    assert before.actual_state is RuntimeState.READY_UNBOUND
    version_before = before.status_version

    changed = await manager.reconcile_all()
    assert changed == 0
    after = await repo.get("runtime-1")
    assert after.actual_state is RuntimeState.READY_UNBOUND
    assert after.status_version == version_before, "healthy process must not bump status_version"
    assert "inspect" in adapter.events


@pytest.mark.asyncio
async def test_crashed_process_converges_to_failed_with_exit_reason(tmp_path):
    """When inspect shows returncode is not None, the owned handle must
    be failed-closed with a process_exited exit_reason."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    adapter.set_inspect(healthy=False, returncode=137)
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    changed = await manager.reconcile_all()
    assert changed == 1
    after = await repo.get("runtime-1")
    assert after.actual_state is RuntimeState.FAILED
    assert after.ready is False
    assert "process_exited:returncode=137" in (after.exit_reason or "")
    revoked = [e for e in authority.events if e[0] == "revoke"]
    assert len(revoked) >= 1
    assert revoked[-1][1] == "runtime-1"


@pytest.mark.asyncio
async def test_inspect_exception_fails_closed(tmp_path):
    """When adapter.inspect() raises, the owned handle must converge to
    FAILED with an inspect_failed exit_reason."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    adapter.set_inspect_error(OSError("connection lost"))
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    changed = await manager.reconcile_all()
    assert changed == 1
    after = await repo.get("runtime-1")
    assert after.actual_state is RuntimeState.FAILED
    assert after.ready is False
    assert "inspect_failed:connection lost" in (after.exit_reason or "")
    revoked = [e for e in authority.events if e[0] == "revoke"]
    assert len(revoked) >= 1


@pytest.mark.asyncio
async def test_cas_conflict_with_concurrent_stopped_is_respected(tmp_path):
    """When inspect shows a dead process but a concurrent stop() already
    transitioned the state to STOPPED before the CAS lands, the health
    reconcile must re-read and respect the STOPPED state without
    resurrecting or overwriting it."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    # First ensure a running runtime.
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    # Now configure inspect to show dead, BUT also set up a concurrent
    # CAS to STOPPED that races with our FAILED write.
    adapter.set_inspect(healthy=False, returncode=1)

    # Simulate a concurrent stop by directly calling CAS to STOPPED
    # before reconcile_all's CAS loop has its first attempt.
    class RacingRepo(MemoryRepository):
        def __init__(self, base):
            super().__init__()
            self.records = dict(base.records)
            self.activation = dict(base.activation)
            self._race_done = False

        async def compare_and_swap(self, record, expected):
            # On the first CAS attempt, inject a concurrent STOPPED
            # transition then raise Conflict so the health reconcile
            # retries.
            if not self._race_done:
                self._race_done = True
                # Simulate concurrent stop: transition to STOPPED
                current = self.records[(record.runtime_id, record.generation)]
                stopped = replace(current, actual_state=RuntimeState.STOPPED,
                                  ready=False, exit_reason="concurrent_stop",
                                  status_version=current.status_version + 1)
                self.records[(record.runtime_id, record.generation)] = stopped
                raise Conflict("runtime status_version mismatch")
            return await super().compare_and_swap(record, expected)

    racing_repo = RacingRepo(repo)
    manager2 = RuntimeManagerCoordinator(racing_repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    # Transfer the handle so it's "owned"
    manager2._handles[("runtime-1", 1)] = adapter.last_handle
    manager2._inflight[("runtime-1", 1)] = 0

    changed = await manager2.reconcile_all()
    # The CAS conflict causes re-read; the re-read sees STOPPED so no change.
    assert changed == 0
    after = await racing_repo.get("runtime-1")
    assert after.actual_state is RuntimeState.STOPPED
    assert after.exit_reason == "concurrent_stop"


@pytest.mark.asyncio
async def test_terminal_durable_state_skips_inspect_and_reconcile(tmp_path):
    """When the durable state is already DRAINING, STOPPING, STOPPED, or
    FAILED, health reconcile must skip without calling inspect."""
    for terminal_state in (RuntimeState.DRAINING, RuntimeState.STOPPING,
                           RuntimeState.STOPPED, RuntimeState.FAILED):
        repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
        adapter.set_inspect(healthy=False, returncode=1)  # would trigger FAILED
        manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
        await manager.ensure(_spec())

        # Transition durable state to terminal
        current = await repo.get_generation("runtime-1", 1)
        await repo.compare_and_swap(
            replace(current, actual_state=terminal_state, ready=False),
            current.status_version,
        )

        inspect_before = adapter.events.count("inspect")
        changed = await manager.reconcile_all()
        assert changed == 0, f"terminal {terminal_state.value} must not be changed"
        assert adapter.events.count("inspect") == inspect_before, (
            f"terminal {terminal_state.value} must not call inspect"
        )


@pytest.mark.asyncio
async def test_desired_stopped_is_not_resurrected(tmp_path):
    """When desired_state is STOPPED, health reconcile must never
    resurrect the runtime, even if inspect shows a live process."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    adapter.set_inspect(healthy=True, returncode=None)
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    # Set desired STOPPED + DRAINING (as stop() would do)
    current = await repo.get_generation("runtime-1", 1)
    await repo.compare_and_swap(
        replace(current, desired_state=DesiredState.STOPPED,
                actual_state=RuntimeState.DRAINING),
        current.status_version,
    )

    changed = await manager.reconcile_all()
    assert changed == 0
    after = await repo.get("runtime-1")
    assert after.desired_state is DesiredState.STOPPED
    assert after.actual_state is RuntimeState.DRAINING


@pytest.mark.asyncio
async def test_desired_stopped_takes_precedence_over_dead_process(tmp_path):
    """When desired_state is STOPPED and inspect shows a dead process,
    health reconcile must not overwrite with FAILED — the stop()
    path owns the terminal transition."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    adapter.set_inspect(healthy=False, returncode=1)
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    # Set desired STOPPED + DRAINING (as stop() would do)
    current = await repo.get_generation("runtime-1", 1)
    await repo.compare_and_swap(
        replace(current, desired_state=DesiredState.STOPPED,
                actual_state=RuntimeState.DRAINING),
        current.status_version,
    )

    changed = await manager.reconcile_all()
    assert changed == 0
    after = await repo.get("runtime-1")
    assert after.desired_state is DesiredState.STOPPED
    assert after.actual_state is RuntimeState.DRAINING


@pytest.mark.asyncio
async def test_reconcile_all_still_adopts_non_owned_handles(tmp_path):
    """Regression: the non-owned reconcile path (adopt, mark_failed, etc.)
    must still work for handles not in self._handles."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())

    # Remove from owned handles to simulate Manager restart
    manager._handles.clear()

    # reconcile_all should go through the ADOPT path
    changed = await manager.reconcile_all()
    assert changed == 1
    assert ("runtime-1", 1) in manager._handles  # adopted back


@pytest.mark.asyncio
async def test_generation_fencing_old_generation_handle_does_not_affect_new(tmp_path):
    """A handle for an old generation must not interfere with the current
    generation's health reconcile or durable state."""
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)

    # Start gen 1
    await manager.ensure(_spec(runtime_id="rt-gen", generation=1))
    gen1_handle = manager._handles[("rt-gen", 1)]

    # Start gen 2 (new generation)
    await manager.ensure(_spec(runtime_id="rt-gen", generation=2, activation_key="activation-2"))

    # Stale gen 1 is still in _handles but list_active only returns gen 2
    # (gen 1 was stopped/transitioned by ensure for gen 2)

    # Verify gen 2 is healthy and gen 1 is not in list_active
    adapter.set_inspect(healthy=True, returncode=None)
    changed = await manager.reconcile_all()
    assert changed == 0
    gen2 = await repo.get_generation("rt-gen", 2)
    assert gen2.actual_state is RuntimeState.READY_UNBOUND
    assert gen2.ready is True


@pytest.mark.asyncio
async def test_mismatched_owned_handle_generation_cannot_fail_current_generation(tmp_path):
    repo, authority, adapter = MemoryRepository(), Authority(), HealthAdapter()
    manager = RuntimeManagerCoordinator(repo, PersonaReader(), authority, {"fake": adapter}, tmp_path)
    await manager.ensure(_spec())
    current = await repo.get_generation("runtime-1", 1)
    manager._handles[("runtime-1", 1)] = RuntimeHandle(
        "runtime-1", 0, tmp_path / "stale-generation", object(),
    )
    adapter.set_inspect(healthy=False, returncode=137)

    assert await manager.reconcile_all() == 0
    unchanged = await repo.get_generation("runtime-1", 1)
    assert unchanged == current
    assert "inspect" not in adapter.events
    assert not any(event[0] == "revoke" for event in authority.events)
