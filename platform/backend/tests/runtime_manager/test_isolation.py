from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import RuntimeInput
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from tests.runtime_manager.conftest import make_spec


@pytest.mark.asyncio
async def test_dual_runtimes_have_isolated_workdir_credential_and_state(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec_a = make_spec(workspace_root, runtime_id="rt-a", generation=1, env={"RUNTIME_ISOLATION_MARKER": "marker-a"})
    spec_b = make_spec(workspace_root, runtime_id="rt-b", generation=1, env={"RUNTIME_ISOLATION_MARKER": "marker-b"})

    prepared_a = await adapter.prepare(spec_a)
    prepared_b = await adapter.prepare(spec_b)
    assert prepared_a.workdir != prepared_b.workdir

    cred_a = json.loads((prepared_a.workdir / "mcp.json").read_text())["credential"]
    cred_b = json.loads((prepared_b.workdir / "mcp.json").read_text())["credential"]
    assert cred_a != cred_b

    handle_a = await adapter.start(prepared_a)
    handle_b = await adapter.start(prepared_b)
    try:
        ready_a = await adapter.wait_ready(handle_a, deadline=5.0)
        ready_b = await adapter.wait_ready(handle_b, deadline=5.0)
        assert ready_a.ready is True and ready_b.ready is True
        assert handle_a.transport.pid != handle_b.transport.pid

        turn_a = await adapter.submit_input(handle_a, RuntimeInput(request_id="a1", content="hello-a"))
        turn_b = await adapter.submit_input(handle_b, RuntimeInput(request_id="b1", content="hello-b"))
        assert turn_a.detail["marker_count"] == 1
        assert turn_b.detail["marker_count"] == 1  # B's marker sequence is independent of A's

        resp_a = await adapter.mcp_call(handle_a, tool="echo", args={}, request_id="mcp-a")
        resp_b = await adapter.mcp_call(handle_b, tool="echo", args={}, request_id="mcp-b")
        assert resp_a["credential_fingerprint"] != resp_b["credential_fingerprint"]
        assert resp_a["credential_fingerprint"] == hashlib.sha256(cred_a.encode("utf-8")).hexdigest()[:16]
        assert resp_b["credential_fingerprint"] == hashlib.sha256(cred_b.encode("utf-8")).hexdigest()[:16]

        # A's credential must be structurally rejected when presented to B's context,
        # and vice versa -- never merely "unused", but explicitly refused.
        cross_call_a_cred_on_b = await adapter.mcp_call(
            handle_b, tool="echo", args={"credential": cred_a}, request_id="mcp-cross-a-on-b"
        )
        assert cross_call_a_cred_on_b["type"] == "mcp_error"
        assert cross_call_a_cred_on_b["reason"] == "credential_mismatch"
        assert cred_a not in json.dumps(cross_call_a_cred_on_b)
        assert cred_b not in json.dumps(cross_call_a_cred_on_b)

        cross_call_b_cred_on_a = await adapter.mcp_call(
            handle_a, tool="echo", args={"credential": cred_b}, request_id="mcp-cross-b-on-a"
        )
        assert cross_call_b_cred_on_a["type"] == "mcp_error"
        assert cross_call_b_cred_on_a["reason"] == "credential_mismatch"
        assert cred_a not in json.dumps(cross_call_b_cred_on_a)
        assert cred_b not in json.dumps(cross_call_b_cred_on_a)

        # B's own credential must still work after the failed cross-context attempt.
        still_ok_b_after_cross = await adapter.mcp_call(handle_b, tool="echo", args={}, request_id="mcp-b-after-cross")
        assert still_ok_b_after_cross["type"] == "mcp_result"

        # Explicit isolation evidence: cwd, credential fingerprint, whitelisted
        # env marker, and log sentinel content must all differ between A and B.
        iso_a = await adapter.query_isolation(handle_a, request_id="iso-a")
        iso_b = await adapter.query_isolation(handle_b, request_id="iso-b")
        assert iso_a["cwd"] != iso_b["cwd"]
        assert Path(iso_a["cwd"]) == prepared_a.workdir.resolve()
        assert Path(iso_b["cwd"]) == prepared_b.workdir.resolve()
        assert iso_a["credential_fingerprint"] == hashlib.sha256(cred_a.encode("utf-8")).hexdigest()[:16]
        assert iso_b["credential_fingerprint"] == hashlib.sha256(cred_b.encode("utf-8")).hexdigest()[:16]
        assert iso_a["env_marker"] == "marker-a"
        assert iso_b["env_marker"] == "marker-b"
        assert iso_a["log_sentinel_path"] != iso_b["log_sentinel_path"]
        assert iso_a["log_sentinel_value"] != iso_b["log_sentinel_value"]
        log_content_a = Path(iso_a["log_sentinel_path"]).read_text(encoding="utf-8")
        log_content_b = Path(iso_b["log_sentinel_path"]).read_text(encoding="utf-8")
        assert log_content_a != log_content_b
        assert iso_a["log_sentinel_value"] in log_content_a
        assert iso_b["log_sentinel_value"] in log_content_b
        assert iso_a["log_sentinel_value"] not in log_content_b
        assert iso_b["log_sentinel_value"] not in log_content_a

        # A's credential must be unusable in B's context: revoking A must not affect B.
        await adapter.revoke_credential(handle_a)
        revoked_a = await adapter.mcp_call(handle_a, tool="echo", args={}, request_id="mcp-a-2")
        assert revoked_a["type"] == "mcp_error"
        still_ok_b = await adapter.mcp_call(handle_b, tool="echo", args={}, request_id="mcp-b-2")
        assert still_ok_b["type"] == "mcp_result"

        # Stopping A must not affect B.
        stop_a = await adapter.stop(handle_a, reason="isolation-test", grace_period=2.0)
        assert stop_a.stopped is True
        status_b = await adapter.inspect(handle_b)
        assert status_b.returncode is None

        turn_b2 = await adapter.submit_input(handle_b, RuntimeInput(request_id="b2", content="hello-b-2"))
        assert turn_b2.detail["marker_count"] == 2  # B kept running and completed its next turn
    finally:
        await adapter.stop(handle_b, reason="test-teardown", grace_period=2.0)


@pytest.mark.asyncio
async def test_new_generation_does_not_reuse_old_generation_credential(workspace_root: Path):
    adapter = FakeAgentRuntimeAdapter()
    spec_gen1 = make_spec(workspace_root, runtime_id="rt-gen-cred", generation=1)
    spec_gen2 = make_spec(workspace_root, runtime_id="rt-gen-cred", generation=2)

    prepared1 = await adapter.prepare(spec_gen1)
    prepared2 = await adapter.prepare(spec_gen2)

    cred1 = json.loads((prepared1.workdir / "mcp.json").read_text())["credential"]
    cred2 = json.loads((prepared2.workdir / "mcp.json").read_text())["credential"]
    assert cred1 != cred2
    assert prepared1.workdir != prepared2.workdir
