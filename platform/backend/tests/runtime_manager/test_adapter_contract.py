from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import ReadinessFailureKind, RuntimeInput, WorkdirConflictError
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from tests.runtime_manager.conftest import make_spec


@pytest.mark.asyncio
async def test_ready_handshake_matches_runtime_and_generation(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-ready", generation=1)
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        result = await adapter.wait_ready(handle, deadline=5.0)
        assert result.ready is True
        assert result.runtime_id == "rt-ready"
        assert result.generation == 1
    finally:
        await adapter.stop(handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_ready_timeout_is_explicit_not_pid_alive(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-never-ready", generation=1, fake_never_ready=True)
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        # PID is alive (process spawned and running) but must NOT count as ready.
        status = await adapter.inspect(handle)
        assert status.pid is not None
        assert status.returncode is None

        result = await adapter.wait_ready(handle, deadline=0.3)
        assert result.ready is False
        assert result.failure_kind is ReadinessFailureKind.TIMEOUT
    finally:
        stop_result = await adapter.stop(handle, reason="cleanup-never-ready", grace_period=2.0)
        assert stop_result.stopped is True
        assert stop_result.exit_code == 0


@pytest.mark.asyncio
async def test_three_turns_same_pid_runtime_generation_with_memory_marker(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-turns", generation=3)
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        ready = await adapter.wait_ready(handle, deadline=5.0)
        assert ready.ready is True

        first_status = await adapter.inspect(handle)
        pid = first_status.pid
        assert pid is not None

        marker_counts = []
        for i in range(3):
            turn = await adapter.submit_input(
                handle, RuntimeInput(request_id=f"req-{i}", content=f"hello-{i}")
            )
            assert turn.runtime_id == "rt-turns"
            assert turn.generation == 3
            assert turn.turn == i + 1
            marker_counts.append(turn.detail["marker_count"])

            status = await adapter.inspect(handle)
            assert status.pid == pid, "PID must not change across turns (no restart)"

        # Memory marker (turn count) strictly increases -> same in-process state, no restart.
        assert marker_counts == [1, 2, 3]
    finally:
        await adapter.stop(handle, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_converges(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-stop-twice", generation=1)
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    await adapter.wait_ready(handle, deadline=5.0)

    first = await adapter.stop(handle, reason="first-stop", grace_period=2.0)
    second = await adapter.stop(handle, reason="second-stop", grace_period=2.0)

    assert first.stopped is True
    assert second.stopped is True
    assert first.exit_code == second.exit_code == 0
    assert second.escalation == "already_stopped"


@pytest.mark.asyncio
async def test_prepare_creates_isolated_workdir_with_0600_credential(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-prepare", generation=1)
    prepared = await adapter.prepare(spec)

    owner_file = prepared.workdir / "runtime-owner.json"
    mcp_file = prepared.workdir / "mcp.json"
    assert owner_file.exists()
    assert mcp_file.exists()
    assert (prepared.workdir / "scratch").is_dir()
    assert (prepared.workdir / "logs").is_dir()

    mode = mcp_file.stat().st_mode & 0o777
    assert mode == 0o600


@pytest.mark.asyncio
async def test_prepare_rejects_reuse_of_other_runtime_generation_dir(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec_a = make_spec(workspace_root, runtime_id="rt-shared", generation=1)
    await adapter.prepare(spec_a)

    # Same runtime_id path space, but a different generation "claims" the same
    # directory only via crafted spec (simulate corrupted/duplicated call by
    # writing generation=1 twice with a different owner file already present
    # for a different runtime_id at the same generation directory name).
    conflicting_spec = make_spec(workspace_root, runtime_id="rt-shared", generation=1)
    # Tamper the owner file to claim a different runtime_id, simulating a stale/foreign directory.
    owner_path = workspace_root / "rt-shared" / "generation-1" / "runtime-owner.json"
    owner_path.write_text(json.dumps({"runtime_id": "someone-else", "generation": 1}), encoding="utf-8")

    with pytest.raises(WorkdirConflictError):
        await adapter.prepare(conflicting_spec)


@pytest.mark.asyncio
async def test_prepare_rejects_path_traversal(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="../../etc", generation=1)

    with pytest.raises(WorkdirConflictError):
        await adapter.prepare(spec)


@pytest.mark.asyncio
async def test_mcp_initialize_tools_list_call_trace_and_revocation(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-mcp", generation=1)
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        ready = await adapter.wait_ready(handle, deadline=5.0)
        assert ready.ready is True

        init_resp = await adapter.mcp_initialize(handle, request_id="mcp-init")
        assert init_resp["type"] == "mcp_initialized"
        assert init_resp["runtime_id"] == "rt-mcp"

        tools_resp = await adapter.mcp_tools_list(handle, request_id="mcp-tools")
        assert tools_resp["type"] == "mcp_tools"
        assert "echo" in tools_resp["tools"]

        call_resp = await adapter.mcp_call(handle, tool="echo", args={"x": 1}, request_id="mcp-call-1")
        assert call_resp["type"] == "mcp_result"
        assert call_resp["result"] == {"ok": True}

        credential = json.loads((prepared.workdir / "mcp.json").read_text())["credential"]
        assert credential not in json.dumps(call_resp)  # full credential text must never leak

        await adapter.revoke_credential(handle)
        revoked_resp = await adapter.mcp_call(handle, tool="echo", args={}, request_id="mcp-call-2")
        assert revoked_resp["type"] == "mcp_error"
        assert revoked_resp["reason"] == "credential_revoked"
    finally:
        await adapter.stop(handle, reason="test-teardown", grace_period=2.0)
