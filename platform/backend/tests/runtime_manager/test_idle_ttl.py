from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import RuntimeActualState, RuntimeInput
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.harness import RuntimeHarness
from tests.runtime_manager.conftest import ManualClock, make_spec


def test_default_idle_ttl_is_3600_seconds(tmp_path: Path):
    spec = make_spec(tmp_path, runtime_id="rt-default-ttl")
    assert spec.idle_ttl_seconds == 3600.0


@pytest.mark.asyncio
async def test_online_business_silence_3599s_no_reclaim_3600s_reclaims(workspace_root: Path):
    clock = ManualClock()
    adapter = FakeAgentRuntimeAdapter(clock=clock)
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-ttl", generation=1)

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY

    await clock.advance(3599.0)
    state = await harness.check_idle_ttl("rt-ttl", 1)
    assert state not in (RuntimeActualState.DRAINING, RuntimeActualState.STOPPED)

    status = await adapter.inspect(entry.handle)
    assert status.returncode is None  # still alive before the 3600s boundary

    await clock.advance(1.0)  # now exactly 3600s business-idle
    state = await harness.check_idle_ttl("rt-ttl", 1)
    assert state == RuntimeActualState.STOPPED

    status = await adapter.inspect(entry.handle)
    assert status.returncode == 0  # normal app-level stop, not a kill


@pytest.mark.asyncio
async def test_input_output_and_mcp_result_refresh_ttl_but_logs_do_not(workspace_root: Path):
    clock = ManualClock()
    adapter = FakeAgentRuntimeAdapter(clock=clock)
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-refresh", generation=1, idle_ttl_seconds=10.0)

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY

    await clock.advance(9.0)
    await harness.submit_input("rt-refresh", 1, RuntimeInput(request_id="r1", content="hi"))
    # submit_input's normal turn completion refreshes effective activity to "now" (9.0)
    # inside the entry lock.

    await clock.advance(9.0)  # only 9.0s idle since the refresh above
    state = await harness.check_idle_ttl("rt-refresh", 1)
    assert state not in (RuntimeActualState.DRAINING, RuntimeActualState.STOPPED)

    # Successful MCP call: notify harness via report_effective_activity.
    mcp_resp = await adapter.mcp_call(entry.handle, tool="echo", args={}, request_id="mcp-1")
    assert mcp_resp["type"] == "mcp_result"
    await harness.report_effective_activity("rt-refresh", 1)

    harness.note_log_activity("rt-refresh", 1)  # pure log/heartbeat: must NOT refresh
    await clock.advance(10.0)  # 10.0s since the mcp_result refresh
    state = await harness.check_idle_ttl("rt-refresh", 1)
    assert state == RuntimeActualState.STOPPED


@pytest.mark.asyncio
async def test_expiry_with_inflight_turn_enters_draining_not_direct_kill(workspace_root: Path):
    clock = ManualClock()
    adapter = FakeAgentRuntimeAdapter(clock=clock)
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-drain", generation=1, idle_ttl_seconds=10.0)

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY

    async with entry.lock:
        entry.turn_inflight = True
        entry.turn_done_event.clear()

    await clock.advance(10.0)
    state = await harness.check_idle_ttl("rt-drain", 1)
    assert state == RuntimeActualState.DRAINING

    status = await adapter.inspect(entry.handle)
    assert status.returncode is None  # DRAINING must not have killed the process

    # The in-flight turn now completes.
    async with entry.lock:
        entry.turn_inflight = False
        entry.turn_done_event.set()

    final_state = await harness.drain_inflight_and_stop("rt-drain", 1, wait_timeout=2.0)
    assert final_state == RuntimeActualState.STOPPED
    status = await adapter.inspect(entry.handle)
    assert status.returncode == 0  # normal stop, not a kill


@pytest.mark.asyncio
async def test_new_input_is_rejected_once_stopping_has_begun(workspace_root: Path):
    clock = ManualClock()
    adapter = FakeAgentRuntimeAdapter(clock=clock)
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-race", generation=1, idle_ttl_seconds=10.0)

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY

    await clock.advance(10.0)
    state = await harness.check_idle_ttl("rt-race", 1)
    assert state == RuntimeActualState.STOPPED

    from modules.runtime_manager.contracts import RuntimeStoppingError

    with pytest.raises(RuntimeStoppingError):
        await harness.submit_input("rt-race", 1, RuntimeInput(request_id="too-late", content="hi"))


@pytest.mark.asyncio
async def test_repeated_mcp_error_and_log_activity_do_not_refresh_ttl(workspace_root: Path):
    """Direct proof (not just a docstring claim) that neither repeated
    mcp_error responses (credential revoked / mismatched) nor pure log
    observation refresh effective activity: the 10s idle-TTL boundary is
    still crossed exactly on schedule."""
    clock = ManualClock()
    adapter = FakeAgentRuntimeAdapter(clock=clock)
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-error-loop", generation=1, idle_ttl_seconds=10.0)

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY

    # Revoke immediately so every subsequent mcp_call is a structured error,
    # not a result -- this is the "repeated-error loop" the spec says must
    # never refresh effective activity.
    await adapter.revoke_credential(entry.handle)

    await clock.advance(4.0)
    for i in range(3):
        resp = await adapter.mcp_call(entry.handle, tool="echo", args={}, request_id=f"err-{i}")
        assert resp["type"] == "mcp_error"
        assert resp["reason"] == "credential_revoked"
        # mcp_error is NOT an effective-activity event; intentionally skip
        # harness.report_effective_activity() here.

    harness.note_log_activity("rt-error-loop", 1)  # pure log/heartbeat: must not refresh

    await clock.advance(5.9)  # 9.9s total since last_effective_runtime_activity (t=0)
    state = await harness.check_idle_ttl("rt-error-loop", 1)
    assert state not in (RuntimeActualState.DRAINING, RuntimeActualState.STOPPED)

    for i in range(3):
        resp = await adapter.mcp_call(entry.handle, tool="echo", args={}, request_id=f"err-late-{i}")
        assert resp["type"] == "mcp_error"
    harness.note_log_activity("rt-error-loop", 1)

    await clock.advance(0.1)  # exactly 10.0s idle since t=0, still never refreshed
    state = await harness.check_idle_ttl("rt-error-loop", 1)
    assert state == RuntimeActualState.STOPPED


@pytest.mark.asyncio
async def test_inflight_turn_at_ttl_boundary_prevents_reclaim(workspace_root: Path):
    """Input accepted at t=9 resets effective idle time before it blocks."""
    clock = ManualClock()

    class BlockingAdapter(FakeAgentRuntimeAdapter):
        def __init__(self) -> None:
            super().__init__(clock=clock)
            self.input_entered = asyncio.Event()
            self.release_input = asyncio.Event()

        async def submit_input(self, handle, value):
            self.input_entered.set()
            await self.release_input.wait()
            return await super().submit_input(handle, value)

    adapter = BlockingAdapter()
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-boundary", generation=1, idle_ttl_seconds=10.0)

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY

    await clock.advance(9.0)
    turn_task = asyncio.create_task(
        harness.submit_input(
            "rt-boundary", 1, RuntimeInput(request_id="accepted-at-nine", content="hi")
        )
    )
    await adapter.input_entered.wait()

    await clock.advance(1.0)

    # Only one second has elapsed since the accepted input refreshed both
    # activity clocks, so the original t=10 boundary cannot drain it.
    state = await harness.check_idle_ttl("rt-boundary", 1)
    assert state == RuntimeActualState.RUNNING
    status = await adapter.inspect(entry.handle)
    assert status.returncode is None

    await clock.advance(9.0)  # t=19: ten seconds since input acceptance
    state = await harness.check_idle_ttl("rt-boundary", 1)
    assert state == RuntimeActualState.DRAINING
    assert (await adapter.inspect(entry.handle)).returncode is None

    adapter.release_input.set()
    result = await turn_task
    assert result.request_id == "accepted-at-nine"

    final_state = await harness.drain_inflight_and_stop("rt-boundary", 1, wait_timeout=2.0)
    assert final_state == RuntimeActualState.STOPPED
    status = await adapter.inspect(entry.handle)
    assert status.returncode == 0  # normal stop


@pytest.mark.asyncio
async def test_new_input_cancels_stale_drain_before_stopping(workspace_root: Path):
    clock = ManualClock()

    class TwoTurnBlockingAdapter(FakeAgentRuntimeAdapter):
        def __init__(self) -> None:
            super().__init__(clock=clock)
            self.entered = {"old": asyncio.Event(), "new": asyncio.Event()}
            self.release = {"old": asyncio.Event(), "new": asyncio.Event()}

        async def submit_input(self, handle, value):
            self.entered[value.request_id].set()
            await self.release[value.request_id].wait()
            return await super().submit_input(handle, value)

    adapter = TwoTurnBlockingAdapter()
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-cancel-drain", generation=1, idle_ttl_seconds=10.0)
    entry = await harness.start_runtime(spec, ready_timeout=5.0)

    old_turn = asyncio.create_task(
        harness.submit_input("rt-cancel-drain", 1, RuntimeInput(request_id="old", content="old"))
    )
    await adapter.entered["old"].wait()
    await clock.advance(10.0)
    assert await harness.check_idle_ttl("rt-cancel-drain", 1) == RuntimeActualState.DRAINING
    old_drain_epoch = entry.drain_epoch
    stale_drain = asyncio.create_task(harness.drain_inflight_and_stop("rt-cancel-drain", 1))
    await asyncio.sleep(0)

    new_turn = asyncio.create_task(
        harness.submit_input("rt-cancel-drain", 1, RuntimeInput(request_id="new", content="new"))
    )
    await adapter.entered["new"].wait()
    assert entry.drain_epoch > old_drain_epoch
    assert entry.actual_state == RuntimeActualState.RUNNING

    # Even if the old waiter observes a stale completion signal, its epoch is
    # invalid and it cannot cross STOPPING or stop the newly accepted turn.
    entry.turn_done_event.set()
    assert await stale_drain == RuntimeActualState.RUNNING
    assert (await adapter.inspect(entry.handle)).returncode is None
    assert not new_turn.done()

    adapter.release["old"].set()
    adapter.release["new"].set()
    await asyncio.gather(old_turn, new_turn)
    assert (await adapter.inspect(entry.handle)).returncode is None
    await adapter.stop(entry.handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_failed_turn_does_not_refresh_completion_activity(workspace_root: Path):
    clock = ManualClock()

    class DelayedFailureAdapter(FakeAgentRuntimeAdapter):
        def __init__(self) -> None:
            super().__init__(clock=clock)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def submit_input(self, handle, value):
            self.entered.set()
            await self.release.wait()
            raise RuntimeError("delayed turn failure")

    adapter = DelayedFailureAdapter()
    harness = RuntimeHarness(adapter, clock=clock)
    spec = make_spec(workspace_root, runtime_id="rt-failed-turn", generation=1, idle_ttl_seconds=10.0)
    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    failed_turn = asyncio.create_task(
        harness.submit_input("rt-failed-turn", 1, RuntimeInput(request_id="fail", content="fail"))
    )
    await adapter.entered.wait()
    accepted_at = entry.last_effective_runtime_activity
    await clock.advance(5.0)
    adapter.release.set()
    with pytest.raises(RuntimeError, match="delayed turn failure"):
        await failed_turn
    assert entry.last_effective_runtime_activity == accepted_at

    await clock.advance(5.0)
    assert await harness.check_idle_ttl("rt-failed-turn", 1) == RuntimeActualState.STOPPED
