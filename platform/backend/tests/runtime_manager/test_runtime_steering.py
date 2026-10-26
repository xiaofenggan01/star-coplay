from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.runtime_manager.contracts import RuntimeInput
from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
from modules.runtime_manager.domain import DesiredState, RuntimeState


@pytest.mark.asyncio
async def test_active_runtime_reservation_can_steer_adapter_once():
    coordinator = object.__new__(RuntimeManagerCoordinator)
    coordinator.max_input_bytes = 64 * 1024
    coordinator._locks = {}
    coordinator._input_reservations = {
        ("runtime-1", 3): (("runtime-1", 3, "original"), "fingerprint", object()),
    }
    coordinator._handles = {("runtime-1", 3): object()}
    coordinator.repository = SimpleNamespace(get_generation=AsyncMock(return_value=SimpleNamespace(
        desired_state=DesiredState.RUNNING,
        actual_state=RuntimeState.RUNNING_BOUND,
        spec=SimpleNamespace(adapter_kind="steerable"),
    )))
    adapter = SimpleNamespace(steer_input=AsyncMock(return_value=True))
    coordinator.adapters = {"steerable": adapter}
    coordinator.activity = SimpleNamespace(report=AsyncMock())
    value = RuntimeInput(request_id="correction", content="Player: turn left")

    assert await coordinator.steer_input("runtime-1", 3, value) is True

    adapter.steer_input.assert_awaited_once_with(
        coordinator._handles[("runtime-1", 3)], value,
    )


@pytest.mark.asyncio
async def test_runtime_without_active_reservation_cannot_be_steered():
    coordinator = object.__new__(RuntimeManagerCoordinator)
    coordinator.max_input_bytes = 64 * 1024
    coordinator._locks = {}
    coordinator._input_reservations = {}
    coordinator._handles = {("runtime-1", 3): object()}
    coordinator.repository = SimpleNamespace(get_generation=AsyncMock(return_value=SimpleNamespace(
        desired_state=DesiredState.RUNNING,
        actual_state=RuntimeState.RUNNING_BOUND,
        spec=SimpleNamespace(adapter_kind="steerable"),
    )))
    adapter = SimpleNamespace(steer_input=AsyncMock(return_value=True))
    coordinator.adapters = {"steerable": adapter}
    coordinator.activity = SimpleNamespace(report=AsyncMock())

    assert await coordinator.steer_input(
        "runtime-1", 3, RuntimeInput(request_id="new-turn", content="Player: hello"),
    ) is False
    adapter.steer_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_interrupt_fences_active_correlation_before_calling_adapter():
    coordinator = object.__new__(RuntimeManagerCoordinator)
    coordinator._locks = {}
    coordinator._input_reservations = {
        ("runtime-1", 3): (("runtime-1", 3, "original"), "fingerprint", object()),
    }
    coordinator._handles = {("runtime-1", 3): object()}
    coordinator.repository = SimpleNamespace(get_generation=AsyncMock(return_value=SimpleNamespace(
        desired_state=DesiredState.RUNNING,
        actual_state=RuntimeState.RUNNING_BOUND,
        spec=SimpleNamespace(adapter_kind="interruptible"),
    )))
    calls = []

    class Registry:
        def mark_interrupted(self, runtime_id, generation, reason):
            calls.append(("fence", runtime_id, generation, reason))

    async def interrupt(_handle):
        calls.append(("adapter",))
        return True

    coordinator.turn_correlation_registry = Registry()
    coordinator.adapters = {
        "interruptible": SimpleNamespace(interrupt_input=interrupt),
    }

    operation_id = await coordinator.interrupt_input("runtime-1", 3)

    assert operation_id == "original"
    assert calls == [
        ("fence", "runtime-1", 3, "player_superseded"),
        ("adapter",),
    ]
