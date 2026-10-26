from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import ReadyResult, ReadyTimeoutError, RuntimeConflictError
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.harness import RuntimeHarness
from tests.runtime_manager.conftest import make_spec


@pytest.mark.asyncio
async def test_concurrent_same_identity_start_is_idempotent_single_process(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-idempotent", generation=1)

    entries = await asyncio.gather(*(harness.start_runtime(spec, ready_timeout=5.0) for _ in range(8)))
    assert all(entry is entries[0] for entry in entries)
    pids = {entry.handle.transport.pid for entry in entries}
    assert len(pids) == 1
    try:
        assert (await entries[0].handle.transport.inspect()).returncode is None
    finally:
        await adapter.stop(entries[0].handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_same_identity_conflicting_spec_is_structured_rejection(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-conflict", generation=1)
    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    try:
        with pytest.raises(RuntimeConflictError, match="conflicting spec"):
            await harness.start_runtime(replace(spec, user_id="another-user"))
        with pytest.raises(RuntimeConflictError, match="active generation 1"):
            await harness.start_runtime(replace(spec, generation=2))
    finally:
        await adapter.stop(entry.handle, reason="test-teardown", grace_period=2.0)


class _BlockedPrepareAdapter(FakeAgentRuntimeAdapter):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.prepare_entered = asyncio.Event()
        self.release_prepare = asyncio.Event()
        self.prepare_calls = 0
        self.fail = fail

    async def prepare(self, spec):
        self.prepare_calls += 1
        self.prepare_entered.set()
        await self.release_prepare.wait()
        if self.fail:
            raise RuntimeError("shared startup failed")
        return await super().prepare(spec)


@pytest.mark.asyncio
async def test_starting_slot_is_atomic_and_rejects_conflicting_spec(workspace_root: Path):
    adapter = _BlockedPrepareAdapter()
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-starting", generation=1)
    owner = asyncio.create_task(harness.start_runtime(spec, ready_timeout=5.0))
    await adapter.prepare_entered.wait()
    waiter = asyncio.create_task(harness.start_runtime(spec, ready_timeout=5.0))

    with pytest.raises(RuntimeConflictError, match="starting with a conflicting spec"):
        await harness.start_runtime(replace(spec, user_id="different"), ready_timeout=5.0)
    with pytest.raises(RuntimeConflictError, match="starting generation 1"):
        await harness.start_runtime(replace(spec, generation=2), ready_timeout=5.0)

    adapter.release_prepare.set()
    first, second = await asyncio.gather(owner, waiter)
    assert first is second
    assert adapter.prepare_calls == 1
    await adapter.stop(first.handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_all_start_waiters_receive_same_startup_exception(workspace_root: Path):
    adapter = _BlockedPrepareAdapter(fail=True)
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-start-fail", generation=1)
    calls = [asyncio.create_task(harness.start_runtime(spec, ready_timeout=5.0)) for _ in range(3)]
    await adapter.prepare_entered.wait()
    adapter.release_prepare.set()
    results = await asyncio.gather(*calls, return_exceptions=True)
    assert adapter.prepare_calls == 1
    assert all(isinstance(result, RuntimeError) for result in results)
    assert {str(result) for result in results} == {"shared startup failed"}


@pytest.mark.asyncio
async def test_cancelling_one_start_waiter_does_not_cancel_shared_startup(workspace_root: Path):
    adapter = _BlockedPrepareAdapter()
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-cancel-waiter", generation=1)
    owner = asyncio.create_task(harness.start_runtime(spec, ready_timeout=5.0))
    await adapter.prepare_entered.wait()
    waiter = asyncio.create_task(harness.start_runtime(spec, ready_timeout=5.0))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    adapter.release_prepare.set()
    entry = await owner
    assert adapter.prepare_calls == 1
    assert harness.get_entry(spec.runtime_id, spec.generation) is entry
    await adapter.stop(entry.handle, reason="test-teardown", grace_period=2.0)


class _FirstReadyFailsAdapter(FakeAgentRuntimeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.ready_calls = 0
        self.stop_calls = 0

    async def wait_ready(self, handle, deadline):
        self.ready_calls += 1
        if self.ready_calls == 1:
            return ReadyResult(
                ready=False, runtime_id=handle.runtime_id, generation=handle.generation
            )
        return await super().wait_ready(handle, deadline)

    async def stop(self, handle, reason, grace_period):
        self.stop_calls += 1
        return await super().stop(handle, reason, grace_period)


@pytest.mark.asyncio
async def test_ready_false_stops_cleans_slot_and_allows_retry(workspace_root: Path):
    adapter = _FirstReadyFailsAdapter()
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-ready-retry", generation=1)

    with pytest.raises(ReadyTimeoutError, match="did not become ready"):
        await harness.start_runtime(spec, ready_timeout=0.1)
    assert adapter.stop_calls == 1
    assert harness.get_entry(spec.runtime_id, spec.generation) is None
    assert (spec.runtime_id, spec.generation) not in harness._startup_tasks

    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    assert entry is harness.get_entry(spec.runtime_id, spec.generation)
    assert adapter.ready_calls == 2
    await adapter.stop(entry.handle, reason="test-teardown", grace_period=2.0)
