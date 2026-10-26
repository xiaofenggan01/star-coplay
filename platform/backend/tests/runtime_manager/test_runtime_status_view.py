from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from modules.runtime_manager.contracts import RuntimeSpec
from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
from modules.runtime_manager.domain import BindingState, RuntimeBinding, RuntimeProductRecord, RuntimeProductSpec, RuntimeState
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.runtime_status_view import assemble_runtime_status


def record(state=RuntimeState.RUNNING_BOUND):
    return RuntimeProductRecord(
        RuntimeProductSpec(
            "runtime-1", 1, "activation-1", "user-1", "agentee-1", "fake",
            "adapter/default", "persona/rev-1",
        ),
        actual_state=state, status_version=7, ready=True,
        started_at=datetime.now(timezone.utc),
    )


class Bindings:
    def __init__(self, value): self.value = value
    async def find_for_runtime(self, *_identity): return self.value


@pytest.mark.asyncio
async def test_runtime_status_null_binding_exact_fencing_projection_and_fail_closed_owner():
    current = record()
    no_binding = await assemble_runtime_status(current, binding_lookup=Bindings(None))
    assert no_binding.binding_projection is None

    binding = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=4, lease_epoch=3, status=BindingState.ACTIVE,
        game_session_id="session-1", game_instance_nonce="nonce-1",
        capability_digest="a" * 64,
    )
    projected = (await assemble_runtime_status(
        current, binding_lookup=Bindings(binding),
    )).binding_projection
    assert projected is not None
    assert set(projected.__dict__) == {
        "contract_version", "binding_id", "binding_version", "lease_epoch",
        "status", "game_session_id", "game_instance_nonce", "capability_digest", "expires_at",
    }
    assert projected.contract_version == "starcoplay.binding.v1"
    assert projected.game_instance_nonce == "nonce-1"
    assert (await assemble_runtime_status(
        current, binding_lookup=Bindings(replace(binding, user_id="other")),
    )).binding_projection is None
    assert (await assemble_runtime_status(
        current, binding_lookup=Bindings(replace(binding, generation=2)),
    )).binding_projection is None


@pytest.mark.asyncio
async def test_owned_handle_without_adapter_is_unknown():
    current = record()
    handle = type("Handle", (), {
        "runtime_id": "runtime-1", "generation": 1,
    })()
    view = await assemble_runtime_status(current, handle=handle, adapter=None)
    assert view.process_health == "unknown"
    assert view.status_version == current.status_version


class Repository:
    async def get(self, _runtime_id): return None


class Persona:
    pass


class Authority:
    pass


@pytest.mark.asyncio
async def test_real_fake_process_health_is_read_only_in_runtime_status(tmp_path):
    adapter = FakeAgentRuntimeAdapter()
    prepared = await adapter.prepare(RuntimeSpec(
        "runtime-1", 1, "user-1", "agentee-1", "fake", tmp_path,
    ))
    handle = await adapter.start(prepared)
    assert (await adapter.wait_ready(handle, 3.0)).ready
    current = record()
    coordinator = RuntimeManagerCoordinator(
        Repository(), Persona(), Authority(), {"fake": adapter}, tmp_path,
    )
    coordinator._handles[("runtime-1", 1)] = handle
    try:
        first = await coordinator.status_for(current)
        second = await coordinator.status_for(current)
        assert first.process_health == second.process_health == "healthy"
        assert first.status_version == second.status_version == 7
        assert first.binding_projection is None
    finally:
        await adapter.stop(handle, "test_cleanup", 2.0)
