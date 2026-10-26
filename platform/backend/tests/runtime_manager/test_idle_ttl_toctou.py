from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.runtime_manager.coordinator import ActivityKind, RuntimeManagerCoordinator
from modules.runtime_manager.contracts import PreparedRuntime, ReadyResult, RuntimeHandle, StopResult
from modules.runtime_manager.domain import DesiredState, Conflict, RuntimeProductSpec, RuntimeState
from modules.runtime_manager.persona import PersonaArtifactSnapshot


class MemoryRepository:
    def __init__(self):
        self.records = {}
        self.activation = {}

    async def ensure(self, record, _digest):
        self.records[(record.runtime_id, record.generation)] = record
        self.activation[(record.spec.user_id, record.spec.agentee_id, record.spec.activation_key)] = record
        return record

    async def find_activation(self, *key):
        return self.activation.get(tuple(key))

    async def get(self, runtime_id):
        rows = [value for (rid, _), value in self.records.items() if rid == runtime_id]
        return max(rows, key=lambda value: value.generation) if rows else None

    async def get_generation(self, runtime_id, generation):
        return self.records.get((runtime_id, generation))

    async def list_active(self):
        return tuple(value for value in self.records.values() if value.active)

    async def compare_and_swap(self, record, expected):
        current = self.records[(record.runtime_id, record.generation)]
        if current.status_version != expected:
            raise Conflict("runtime status_version mismatch")
        result = replace(record, status_version=expected + 1)
        self.records[(record.runtime_id, record.generation)] = result
        for key, value in tuple(self.activation.items()):
            if (value.runtime_id, value.generation) == (result.runtime_id, result.generation):
                self.activation[key] = result
        return result


class PersonaReader:
    async def read(self, _reference, _user_id, agentee_id):
        payload = "Be kind"
        return PersonaArtifactSnapshot(
            agentee_id, "rev-1", hashlib.sha256(payload.encode()).hexdigest(),
            datetime.now(timezone.utc).isoformat(), payload,
        )


class Authority:
    async def allow_ingress(self, *_identity):
        return None

    async def deny_and_revoke(self, *_identity):
        return None

    async def is_bound(self, *_identity):
        return False


class Adapter:
    def __init__(self):
        self.events = []

    async def prepare(self, runtime_spec):
        workdir = runtime_spec.workspace_root / runtime_spec.runtime_id / f"generation-{runtime_spec.generation}"
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "runtime-owner.json").write_text("{}")
        return PreparedRuntime(runtime_spec, workdir)

    async def start(self, prepared):
        return RuntimeHandle(
            prepared.spec.runtime_id, prepared.spec.generation, prepared.workdir, object(),
        )

    async def wait_ready(self, handle, _deadline):
        return ReadyResult(True, handle.runtime_id, handle.generation)

    async def checkpoint(self, _handle, _deadline):
        return None

    async def stop(self, _handle, reason, _grace_period):
        self.events.append("stop")
        return StopResult(True, 0, "app_stop", reason)


def spec():
    return RuntimeProductSpec(
        "runtime-1", 1, "activation-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/rev-1", idle_ttl_seconds=600,
    )
class IdleClaimMemoryRepository(MemoryRepository):
    def __init__(self):
        super().__init__()
        self.before_claim = None

    async def claim_idle_stop(self, runtime_id, generation, expected_version, expected_state, cutoff):
        if self.before_claim is not None:
            callback, self.before_claim = self.before_claim, None
            await callback()
        current = await self.get_generation(runtime_id, generation)
        last = current.last_effective_activity_at or current.started_at if current else None
        if (
            current is None
            or current.status_version != expected_version
            or current.actual_state is not expected_state
            or current.actual_state not in {
                RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
            }
            or current.desired_state is not DesiredState.RUNNING
            or last is None
            or last > cutoff
        ):
            return None
        return await self.compare_and_swap(
            replace(
                current, desired_state=DesiredState.STOPPED,
                actual_state=RuntimeState.DRAINING,
            ),
            expected_version,
        )


def manager(tmp_path):
    repository = IdleClaimMemoryRepository()
    adapter = Adapter()
    coordinator = RuntimeManagerCoordinator(
        repository, PersonaReader(), Authority(), {"fake": adapter}, tmp_path,
    )
    return coordinator, repository, adapter


@pytest.mark.asyncio
async def test_activity_wins_after_idle_candidate_then_claim_misses(tmp_path):
    coordinator, repository, adapter = manager(tmp_path)
    candidate = await coordinator.ensure(spec())
    cutoff_now = candidate.last_effective_activity_at + timedelta(seconds=600)

    async def activity_wins():
        assert await coordinator.activity.report(
            candidate.runtime_id, candidate.generation, ActivityKind.MCP_SUCCESS,
            candidate.last_effective_activity_at + timedelta(seconds=1),
        )

    repository.before_claim = activity_wins
    assert await coordinator.sweep_idle(cutoff_now) == 0
    current = await repository.get_generation(candidate.runtime_id, candidate.generation)
    assert current.desired_state is DesiredState.RUNNING
    assert current.actual_state is RuntimeState.READY_UNBOUND
    assert "stop" not in adapter.events


@pytest.mark.asyncio
async def test_idle_claim_wins_then_activity_is_rejected_and_stop_does_not_double_cas(tmp_path):
    coordinator, repository, adapter = manager(tmp_path)
    candidate = await coordinator.ensure(spec())
    cutoff_now = candidate.last_effective_activity_at + timedelta(seconds=600)
    claimed = asyncio.Event()
    release = asyncio.Event()
    original_stop = coordinator.stop

    async def paused_stop(*args, **kwargs):
        current = await repository.get_generation(candidate.runtime_id, candidate.generation)
        assert current.actual_state is RuntimeState.DRAINING
        assert current.status_version == candidate.status_version + 1
        claimed.set()
        await release.wait()
        return await original_stop(*args, **kwargs)

    coordinator.stop = paused_stop
    sweep = asyncio.create_task(coordinator.sweep_idle(cutoff_now))
    await claimed.wait()
    assert not await coordinator.activity.report(
        candidate.runtime_id, candidate.generation, ActivityKind.USER_INPUT,
        candidate.last_effective_activity_at + timedelta(seconds=601),
    )
    current = await repository.get_generation(candidate.runtime_id, candidate.generation)
    assert current.status_version == candidate.status_version + 1
    release.set()
    assert await sweep == 1
    assert (await repository.get_generation(candidate.runtime_id, candidate.generation)).actual_state is RuntimeState.STOPPED
    assert adapter.events.count("stop") == 1


@pytest.mark.asyncio
async def test_repeated_idle_sweep_is_idempotent(tmp_path):
    coordinator, repository, adapter = manager(tmp_path)
    candidate = await coordinator.ensure(spec())
    cutoff_now = candidate.last_effective_activity_at + timedelta(seconds=600)
    assert await coordinator.sweep_idle(cutoff_now) == 1
    assert await coordinator.sweep_idle(cutoff_now) == 0
    assert adapter.events.count("stop") == 1
