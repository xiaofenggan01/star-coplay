from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import ReconcileAction, RuntimeActualState
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.harness import RuntimeHarness, RuntimeRegistry
from tests.runtime_manager.conftest import make_spec


@pytest.mark.asyncio
async def test_second_harness_adopts_live_trusted_runtime_then_terminates_it(workspace_root: Path):
    """Real Fake subprocess, no mocking: first Harness/adapter is dropped entirely;
    a second, independent Harness/adapter reconciles purely from the on-disk
    owner file and OS process liveness, then converges to STOPPED."""
    spec = make_spec(workspace_root, runtime_id="rt-reconcile", generation=1)

    adapter_a = FakeAgentRuntimeAdapter()
    harness_a = RuntimeHarness(adapter_a)
    entry = await harness_a.start_runtime(spec, ready_timeout=5.0)
    assert entry.actual_state == RuntimeActualState.READY
    record = harness_a.to_record("rt-reconcile", 1)
    assert record is not None
    pid = entry.handle.transport.pid
    assert pid is not None

    # Simulate a Manager crash/restart: drop the first Harness/adapter/handle entirely.
    del harness_a
    del adapter_a
    del entry

    adapter_b = FakeAgentRuntimeAdapter()
    registry_b = RuntimeRegistry(adapter_b, workspace_root)
    decisions = await registry_b.reconcile_all([record])
    assert len(decisions) == 1
    assert decisions[0].action == ReconcileAction.ADOPT
    assert decisions[0].detail.get("pid") == pid

    # Desired state flips to STOPPED (e.g. operator asked to stop while Manager was down).
    stopped_record = replace(record, desired_state=RuntimeActualState.STOPPED)
    decisions2 = await registry_b.reconcile_all([stopped_record])
    assert decisions2[0].action == ReconcileAction.TERMINATE_ORPHAN

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("orphan process was not terminated by reconcile")


@pytest.mark.asyncio
async def test_nonce_mismatch_is_quarantined_not_adopted(workspace_root: Path):
    spec = make_spec(workspace_root, runtime_id="rt-nonce", generation=1)
    adapter = FakeAgentRuntimeAdapter()
    harness = RuntimeHarness(adapter)
    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    record = harness.to_record("rt-nonce", 1)
    assert record is not None
    tampered = replace(record, owner_nonce="not-the-real-nonce")

    registry = RuntimeRegistry(adapter, workspace_root)
    decisions = await registry.reconcile_all([tampered])
    assert decisions[0].action == ReconcileAction.QUARANTINE_UNKNOWN

    await adapter.stop(entry.handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_stale_generation_exit_does_not_affect_current_generation(workspace_root: Path):
    spec_gen1 = make_spec(workspace_root, runtime_id="rt-gen", generation=1)
    spec_gen2 = make_spec(workspace_root, runtime_id="rt-gen", generation=2)
    adapter = FakeAgentRuntimeAdapter()
    harness = RuntimeHarness(adapter)

    entry1 = await harness.start_runtime(spec_gen1, ready_timeout=5.0)
    await adapter.stop(entry1.handle, reason="superseded", grace_period=2.0)
    record1 = harness.to_record("rt-gen", 1)
    assert record1 is not None

    entry2 = await harness.start_runtime(spec_gen2, ready_timeout=5.0)
    record2 = harness.to_record("rt-gen", 2)
    assert record2 is not None

    registry = RuntimeRegistry(adapter, workspace_root)
    decisions = await registry.reconcile_all([record1, record2])

    stale_decision = next(d for d in decisions if d.generation == 1)
    current_decision = next(d for d in decisions if d.generation == 2)
    assert stale_decision.action == ReconcileAction.IGNORE_STALE_GENERATION
    assert current_decision.action == ReconcileAction.ADOPT

    await adapter.stop(entry2.handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_unknown_directory_without_matching_record_is_quarantined_not_deleted(workspace_root: Path):
    spec = make_spec(workspace_root, runtime_id="rt-unknown", generation=1)
    adapter = FakeAgentRuntimeAdapter()
    prepared = await adapter.prepare(spec)  # creates the dir/owner file, but the process is never started

    registry = RuntimeRegistry(adapter, workspace_root)
    decisions = registry.scan_unknown(known=set())

    assert any(d.runtime_id == "rt-unknown" and d.action == ReconcileAction.QUARANTINE_UNKNOWN for d in decisions)
    assert prepared.workdir.exists()  # never deleted
