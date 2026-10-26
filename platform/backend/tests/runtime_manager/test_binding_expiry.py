from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.runtime_manager.coordinator import BindingExpiryWorker, HostedGatewayEventConsumer
from modules.runtime_manager.domain import (
    BindingState, Conflict, RuntimeBinding, RuntimeProductRecord, RuntimeProductSpec, RuntimeState,
)


class MemoryBindings:
    def __init__(self, bindings):
        self.bindings = {item.binding_id: item for item in bindings}
        self.intents = set()

    async def expire_due(self, limit=50):
        now = datetime.now(timezone.utc)
        expired = []
        for item in list(self.bindings.values()):
            if len(expired) >= limit:
                break
            if item.status not in {BindingState.PENDING, BindingState.ACTIVE, BindingState.SUSPENDED}:
                continue
            if item.expires_at is None or item.expires_at > now:
                continue
            updated = replace(
                item, status=BindingState.EXPIRED,
                binding_version=item.binding_version + 1,
                lease_epoch=item.lease_epoch + 1,
            )
            self.bindings[item.binding_id] = updated
            self.intents.add(f"expire:{item.binding_id}:{updated.binding_version}")
            expired.append(updated)
        return tuple(expired)

    async def list_expired_for_projection(self, limit=50):
        return tuple(item for item in self.bindings.values() if item.status is BindingState.EXPIRED)[:limit]


class MemoryRuntimes:
    def __init__(self, records):
        self.records = {(item.runtime_id, item.generation): item for item in records}

    async def get_generation(self, runtime_id, generation):
        return self.records.get((runtime_id, generation))

    async def compare_and_swap(self, record, expected):
        current = self.records[(record.runtime_id, record.generation)]
        if current.status_version != expected:
            raise Conflict("runtime status_version mismatch")
        result = replace(record, status_version=expected + 1)
        self.records[(record.runtime_id, record.generation)] = result
        return result


def binding(binding_id, runtime_id, status=BindingState.ACTIVE, expires_at=None):
    return RuntimeBinding(
        binding_id, runtime_id, 1, "user-1", "agentee-1",
        binding_version=2, lease_epoch=1, status=status, expires_at=expires_at,
    )


def runtime(runtime_id, state=RuntimeState.RUNNING_BOUND):
    return RuntimeProductRecord(
        RuntimeProductSpec(
            runtime_id, 1, runtime_id, "user-1", "agentee-1", "fake",
            "adapter/default", "persona/rev-1",
        ),
        actual_state=state, status_version=4, ready=True,
    )


@pytest.mark.asyncio
async def test_expiry_projects_idle_skips_future_null_and_is_idempotent():
    now = datetime.now(timezone.utc)
    due = binding("due", "runtime-due", expires_at=now - timedelta(seconds=1))
    future = binding("future", "runtime-future", expires_at=now + timedelta(hours=1))
    no_deadline = binding("null", "runtime-null")
    bindings = MemoryBindings((due, future, no_deadline))
    runtimes = MemoryRuntimes((runtime("runtime-due"), runtime("runtime-future"), runtime("runtime-null")))
    worker = BindingExpiryWorker(bindings, HostedGatewayEventConsumer(None, bindings, runtimes))

    assert await worker.run_once() == 1
    expired = bindings.bindings["due"]
    assert (expired.status, expired.binding_version, expired.lease_epoch) == (BindingState.EXPIRED, 3, 2)
    assert runtimes.records[("runtime-due", 1)].actual_state is RuntimeState.IDLE
    assert bindings.intents == {"expire:due:3"}
    versions = (expired.binding_version, runtimes.records[("runtime-due", 1)].status_version)
    assert await worker.run_once() == 0
    assert (bindings.bindings["due"].binding_version, runtimes.records[("runtime-due", 1)].status_version) == versions
    assert bindings.bindings["future"].status is bindings.bindings["null"].status is BindingState.ACTIVE


@pytest.mark.asyncio
async def test_restart_repairs_projection_without_rewriting_binding_and_terminal_is_not_resurrected():
    expired = replace(binding("expired", "runtime-expired"), status=BindingState.EXPIRED)
    terminal = replace(binding("terminal", "runtime-terminal"), status=BindingState.EXPIRED)
    bindings = MemoryBindings((expired, terminal))
    runtimes = MemoryRuntimes((runtime("runtime-expired"), runtime("runtime-terminal", RuntimeState.STOPPED)))
    worker = BindingExpiryWorker(bindings, HostedGatewayEventConsumer(None, bindings, runtimes))
    assert await worker.run_once() == 0
    assert bindings.bindings["expired"].binding_version == 2
    assert runtimes.records[("runtime-expired", 1)].actual_state is RuntimeState.IDLE
    assert runtimes.records[("runtime-terminal", 1)].actual_state is RuntimeState.STOPPED
