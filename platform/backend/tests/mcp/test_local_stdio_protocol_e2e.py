from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import uuid
from pathlib import Path

import pytest
import websockets

REPO_ROOT = Path(__file__).resolve().parents[4]
_RESULT_VECTORS = json.loads(
    (REPO_ROOT / "agent/tests/fixtures/canonical-result-vectors.json").read_text()
)["vectors"]
_SUCCESS_RESULT_BY_TOOL = {
    vector["canonical_name"]: vector
    for vector in _RESULT_VECTORS.values()
    if isinstance(vector.get("expected"), dict)
    and vector["result"]["state"] == "SUCCEEDED"
    and vector["expected"].get("success") is True
}


def _source_result(tool_name: str, command_id: str):
    vector = _SUCCESS_RESULT_BY_TOOL[tool_name]
    output = json.loads(json.dumps(vector["result"]["output"]))
    if "commandId" in output:
        output["commandId"] = command_id
    else:
        output["action_id"] = command_id
    expected = json.loads(json.dumps(vector["expected"]))
    expected["command_id"] = command_id
    return output, expected


def _minimal(schema):
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {
            name: _minimal(schema["properties"][name])
            for name in schema.get("required", [])
        }
    if kind == "array":
        return [_minimal(schema["items"])] if schema.get("minItems", 0) else []
    if kind == "string":
        return "x" * max(1, schema.get("minLength", 0))
    if kind == "integer":
        return schema.get("minimum", 0)
    if kind == "number":
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


async def _wait_and_claim(path: Path) -> dict[str, object]:
    # A full-suite run starts several process/database fixtures concurrently;
    # keep this client-side publication budget above the CLI's 5s Mod
    # readiness budget so scheduler pressure is not misclassified as a
    # rendezvous contract failure.
    deadline = asyncio.get_running_loop().time() + 15
    while not path.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("CLI did not publish Local Direct rendezvous")
        await asyncio.sleep(0.01)
    claimed = path.with_name(f"{path.name}.claimed-{uuid.uuid4().hex}")
    path.rename(claimed)
    try:
        return json.loads(claimed.read_text())
    finally:
        claimed.unlink(missing_ok=True)


def _hello(material: dict[str, object]) -> dict[str, object]:
    return {
        "type": "local_direct_hello",
        "profile": "local_direct",
        "proof": material["proof"],
        "generation": material["generation"],
        "binding_ref": material["binding_ref"],
        "binding_version": material["binding_version"],
        "game_session_ref": material["game_session_ref"],
        "game_instance_nonce": material["game_instance_nonce"],
        "lease_epoch": material["lease_epoch"],
        "capability_entries": material["capability_entries"],
        "capability_digest": material["capability_digest"],
    }


async def _rpc(process, request_id, method, params=None, *, timeout=5):
    request = {
        "jsonrpc": "2.0", "id": request_id,
        "method": method, "params": params or {},
    }
    process.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
    await process.stdin.drain()
    raw = await asyncio.wait_for(process.stdout.readline(), timeout)
    assert raw, f"CLI closed stdout before JSON-RPC response {request_id}"
    response = json.loads(raw)
    assert response["jsonrpc"] == "2.0" and response["id"] == request_id
    return response


async def _notify(process, method, params=None):
    notification = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    process.stdin.write((json.dumps(notification, separators=(",", ":")) + "\n").encode())
    await process.stdin.drain()


def _assert_transient_cleanup_and_persistent_audit(
    rendezvous: Path,
    *,
    expected_tool_call_ids: tuple[str, ...] = (),
    expected_command_ids: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Separate one-shot rendezvous cleanup from durable audit retention."""
    assert not rendezvous.exists()
    assert not list(rendezvous.parent.glob(".rendezvous-*"))
    children = list(rendezvous.parent.iterdir())
    assert children == [rendezvous.parent / "audit"]
    audit_dir = children[0]
    assert audit_dir.is_dir() and stat.S_IMODE(audit_dir.stat().st_mode) == 0o700
    files = list(audit_dir.iterdir())
    assert len(files) == 1 and files[0].is_file() and files[0].suffix == ".jsonl"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    records = [json.loads(line) for line in files[0].read_text().splitlines() if line]
    assert all(
        isinstance(record, dict)
        and record.get("schema_version") == "starcoplay.mcp-audit.v1"
        and isinstance(record.get("event"), str)
        for record in records
    )
    observed_tool_calls = {record.get("tool_call_id") for record in records}
    observed_commands = {record.get("command_id") for record in records}
    assert set(expected_tool_call_ids) <= observed_tool_calls
    assert set(expected_command_ids) <= observed_commands
    for tool_call_id in expected_tool_call_ids:
        correlated = [
            record for record in records
            if record.get("tool_call_id") == tool_call_id
        ]
        assert any(
            isinstance(record.get("skill_execution_id"), str)
            and record["skill_execution_id"]
            for record in correlated
        )
    for command_id in expected_command_ids:
        assert any(
            record.get("command_id") == command_id
            and record.get("tool_call_id") in expected_tool_call_ids
            for record in records
        )
    return records


@pytest.mark.asyncio
async def test_real_cli_stdio_projects_safe_skills_and_executes_parameterized_atomic(tmp_path):
    """Exercise the real Proto-only projection and SkillRuntime over loopback WS."""
    rendezvous = tmp_path / "real-transport" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((
        str(REPO_ROOT), str(REPO_ROOT / "agent"), str(REPO_ROOT / "platform/backend"),
    ))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    responses = []
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        await websocket.send(json.dumps(_hello(material)))
        ready = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert ready["type"] == "local_direct_ready"

        rejected = await _rpc(process, 1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "attacker", "version": "1"},
            "metadata": {"game_session_ref": "injected"},
        })
        responses.append(rejected)
        assert rejected["error"]["data"]["code"] == "invalid_arguments"

        initialized = await _rpc(process, 2, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "transport-e2e", "version": "1"},
        })
        responses.append(initialized)
        assert initialized["result"]["protocolVersion"] == "2025-03-26"

        listed = await _rpc(process, 3, "tools/list")
        responses.append(listed)
        tools = listed["result"]["tools"]
        names = {tool["name"] for tool in tools}
        expected_safe = {
            "emote", "equip", "face", "go_to", "menu_close", "move_to",
            "open_menu", "query_inspect_refs", "query_inventory_snapshot",
            "query_menu", "query_runtime_snapshot", "query_ui_snapshot",
            "query_world_region", "say",
        }
        assert names == {
            *(f"skill:stardew.proto.{name}@1.0.0" for name in expected_safe),
            "skill:stardew.basic.observe_current_view@1.0.0",
        }
        assert len(tools) == 15
        assert all(name.startswith("skill:") for name in names)
        assert all("inputSchema" in tool and "outputSchema" in tool for tool in tools)

        tool_name = "skill:stardew.proto.say@1.0.0"
        say_tool = next(tool for tool in tools if tool["name"] == tool_name)
        assert say_tool["inputSchema"]["required"] == ["message"]
        call = asyncio.create_task(_rpc(process, 10, "tools/call", {
            "name": tool_name, "arguments": {"message": "proto-only"},
            "_meta": {"toolCallId": "atomic-say", "commandId": "ignored-by-skill-runtime"},
        }))
        dispatched = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert dispatched["type"] == "local_command"
        assert dispatched["wire_format"] == "protocol-2.4.5"
        assert "legacy" not in json.dumps(dispatched).lower()
        command_id = dispatched["command_id"]
        source_output, expected_output = _source_result("say", command_id)
        terminal = {
            "type": "local_command_result", "command_id": command_id,
            "state": "SUCCEEDED", "output": source_output, "error_code": None,
        }
        await websocket.send(json.dumps(terminal))
        # Duplicate terminal delivery is idempotent at the real route boundary.
        await websocket.send(json.dumps(terminal))
        response = await call
        responses.append(response)
        structured = response["result"]["structuredContent"]
        assert structured["state"] == "SUCCEEDED"
        assert structured["output"] == expected_output

        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await asyncio.wait_for(websocket.wait_closed(), 5)
        assert websocket.close_code == 1001
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0
        assert stderr == ""
        _assert_transient_cleanup_and_persistent_audit(
            rendezvous,
            expected_tool_call_ids=("atomic-say",),
            expected_command_ids=(command_id,),
        )
        # Every stdout line was consumed as one JSON-RPC frame by _rpc.
        assert all(set(frame).issuperset({"jsonrpc", "id"}) for frame in responses)
        assert await process.stdout.read() == b""
        rendered_protocol = json.dumps(responses) + stderr
        assert material["proof"] not in rendered_protocol
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_real_cli_builtin_composite_succeeds_and_short_circuits_on_failure(tmp_path):
    rendezvous = tmp_path / "composite" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((
        str(REPO_ROOT), str(REPO_ROOT / "agent"), str(REPO_ROOT / "platform/backend"),
    ))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--enable-controlled-mutations",
        "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    composite_command_ids: list[str] = []
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        await websocket.send(json.dumps(_hello(material)))
        await websocket.recv()
        await _rpc(process, 1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "composite-e2e", "version": "1"},
        })
        listed = await _rpc(process, 2, "tools/list")
        name = "skill:stardew.basic.navigate_and_interact@1.0.0"
        assert name in {tool["name"] for tool in listed["result"]["tools"]}
        arguments = {
            "destination": {"map_id": "Farm", "x": 4, "y": 5, "arrival_mode": "adjacent", "stand_side": "down"},
            "interaction": {"x": 4, "y": 5},
        }

        async def complete(capability: str, call_task, *, state: str = "SUCCEEDED") -> dict:
            receive = asyncio.create_task(websocket.recv())
            done, _ = await asyncio.wait(
                {receive, call_task}, timeout=5, return_when=asyncio.FIRST_COMPLETED,
            )
            assert receive in done, (
                "Skill terminated before its expected capability dispatch",
                call_task.result() if call_task.done() else None,
            )
            dispatched = json.loads(receive.result())
            assert dispatched["type"] == "local_command"
            assert dispatched["wire_format"] == "protocol-2.4.5"
            command_id = dispatched["command_id"]
            composite_command_ids.append(command_id)
            output = _source_result(capability, command_id)[0] if state == "SUCCEEDED" else {}
            terminal = json.dumps({
                "type": "local_command_result", "command_id": command_id,
                "state": state, "output": output,
                "error_code": None if state == "SUCCEEDED" else "test_failure",
            })
            # The synthetic peer can observe the frame before the route's
            # send coroutine records the command as dispatched.  Yield once
            # so the terminal push models a real Mod execution rather than
            # racing the server's post-send bookkeeping.
            await asyncio.sleep(0.01)
            await websocket.send(terminal)
            return dispatched

        success = asyncio.create_task(_rpc(process, 3, "tools/call", {
            "name": name, "arguments": arguments,
            "_meta": {"toolCallId": "composite-ok"},
        }))
        for step, capability in enumerate(("query_world_region", "go_to", "interact")):
            await complete(capability, success)
            if step < 2:
                assert not success.done(), (
                    "composite terminated before dispatching its next capability",
                    success.result() if success.done() else None,
                )
        succeeded = await success
        assert succeeded["result"]["structuredContent"]["state"] == "SUCCEEDED"
        assert succeeded["result"]["structuredContent"]["output"]["status"] == "completed"

        failed = asyncio.create_task(_rpc(process, 4, "tools/call", {
            "name": name, "arguments": arguments,
            "_meta": {"toolCallId": "composite-failed"},
        }))
        await complete("query_world_region", failed)
        await complete("go_to", failed, state="FAILED")
        failure = await failed
        assert failure["result"]["structuredContent"]["state"] == "FAILED"
        assert failure["result"]["isError"] is True
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(websocket.recv(), 0.05)

        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0
        assert (await process.stderr.read()).decode() == ""
        _assert_transient_cleanup_and_persistent_audit(
            rendezvous,
            expected_tool_call_ids=("composite-ok", "composite-failed"),
            expected_command_ids=tuple(composite_command_ids),
        )
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_real_cli_wrong_mod_proof_fails_handshake_then_cleans_process_owner(tmp_path):
    rendezvous = tmp_path / "wrong-proof" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "0.2", "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        invalid = _hello(material)
        invalid["proof"] = "wrong-proof"
        await websocket.send(json.dumps(invalid))
        with pytest.raises(websockets.ConnectionClosed):
            await websocket.recv()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 1
        assert await process.stdout.read() == b""
        stderr = (await process.stderr.read()).decode()
        assert "authenticated local Mod readiness timed out" in stderr
        _assert_transient_cleanup_and_persistent_audit(rendezvous)
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_real_cli_standard_mcp_cancel_routes_to_core_and_ws_without_response(tmp_path):
    rendezvous = tmp_path / "cancel" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--enable-controlled-mutations",
        "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    responses = []
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        await websocket.send(json.dumps(_hello(material)))
        await websocket.recv()
        responses.append(await _rpc(process, 1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "cancel-e2e", "version": "1"},
        }))
        responses.append(await _rpc(process, 2, "tools/list"))

        request_id = "long-call"
        call = asyncio.create_task(_rpc(process, request_id, "tools/call", {
            "name": "skill:stardew.daily.water_crops@1.0.0",
            "arguments": {"targets": [{"map_id": "Farm", "x": 1, "y": 2, "stand_side": "down"}]},
            "_meta": {"toolCallId": "cancel-tool", "commandId": "cancel-command"},
        }))
        dispatched = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert dispatched["type"] == "local_command"
        assert dispatched["wire_format"] == "protocol-2.4.5"
        command_id = dispatched["command_id"]
        await _notify(process, "notifications/cancelled", {
            "requestId": request_id, "reason": "test requested cancellation",
        })
        cancel_query = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert cancel_query["type"] == "local_cancel"
        assert cancel_query["command_id"] == command_id
        await websocket.send(json.dumps({
            "type": "local_command_result",
            "control_id": cancel_query["control_id"],
            "command_id": command_id, "state": "RUNNING",
            "accepted": True, "output": {}, "error_code": None,
        }))
        status_query = json.loads(await asyncio.wait_for(websocket.recv(), 5))
        assert status_query["type"] == "local_status"
        assert status_query["command_id"] == command_id
        await websocket.send(json.dumps({
            "type": "local_command_result",
            "control_id": status_query["control_id"],
            "command_id": command_id, "state": "CANCELLED",
            "output": {}, "error_code": None,
        }))
        call.cancel()  # MCP sender stops waiting after issuing cancellation.
        with pytest.raises(asyncio.CancelledError):
            await call
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(process.stdout.readline(), 0.05)

        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await asyncio.wait_for(websocket.wait_closed(), 5)
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0 and stderr == ""
        _assert_transient_cleanup_and_persistent_audit(
            rendezvous, expected_tool_call_ids=("cancel-tool",),
            expected_command_ids=(command_id,),
        )
        assert material["proof"] not in json.dumps(responses) + stderr
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_real_cli_expired_authority_rejects_before_any_ws_execution(tmp_path):
    rendezvous = tmp_path / "expired" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--boot-lifetime", "0.5",
        "--rendezvous-path", str(rendezvous), cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    responses = []
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        await websocket.send(json.dumps(_hello(material)))
        await websocket.recv()
        responses.append(await _rpc(process, 1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "expiry-e2e", "version": "1"},
        }))
        responses.append(await _rpc(process, 2, "tools/list"))
        await asyncio.sleep(0.55)
        expired = await _rpc(process, 3, "tools/call", {
            "name": "skill:stardew.proto.say@1.0.0",
            "arguments": {"message": "must-not-execute"},
            "_meta": {"toolCallId": "expired-tool", "commandId": "expired-command"},
        })
        responses.append(expired)
        assert expired["error"]["data"]["code"] == "context_expired"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(websocket.recv(), 0.05)
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await asyncio.wait_for(websocket.wait_closed(), 5)
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0 and stderr == ""
        assert material["proof"] not in json.dumps(responses) + stderr
        _assert_transient_cleanup_and_persistent_audit(rendezvous)
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_real_cli_disconnect_never_reports_false_terminal_and_cleans(tmp_path):
    rendezvous = tmp_path / "disconnect" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    responses = []
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        await websocket.send(json.dumps(_hello(material)))
        await websocket.recv()
        responses.append(await _rpc(process, 1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "disconnect-e2e", "version": "1"},
        }))
        responses.append(await _rpc(process, 2, "tools/list"))
        call = asyncio.create_task(_rpc(process, 3, "tools/call", {
            "name": "skill:stardew.proto.say@1.0.0",
            "arguments": {"message": "uncertain"},
            "_meta": {"toolCallId": "unknown-tool", "commandId": "unknown-command"},
        }, timeout=20))
        assert json.loads(await websocket.recv())["type"] == "local_command"
        await websocket.close()
        # A dropped route must never fabricate SUCCEEDED/FAILED.  UNKNOWN is
        # deliberately converged until the command deadline; cancel the MCP
        # wait after proving no false terminal escaped immediately.  The
        # bounded UNKNOWN/EXPIRED_UNKNOWN state machine is covered directly
        # in test_skill_context.py.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(call), 0.2)
        await _notify(process, "notifications/cancelled", {
            "requestId": 3, "reason": "disconnect test cleanup",
        })
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0 and stderr == ""
        assert material["proof"] not in json.dumps(responses) + stderr
        _assert_transient_cleanup_and_persistent_audit(
            rendezvous, expected_tool_call_ids=("unknown-tool",),
        )
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,bad", [
    ("proof", "wrong-proof"),
    ("profile", "hosted"),
    ("generation", 999),
    ("binding_ref", "stale-binding"),
    ("binding_version", 999),
    ("game_session_ref", "stale-session"),
    ("game_instance_nonce", "stale-nonce"),
    ("lease_epoch", 999),
    ("capability_digest", "sha256:" + "0" * 64),
])
async def test_production_cli_rejects_every_transmitted_handshake_fence_and_cleans(
    tmp_path, field, bad,
):
    rendezvous = tmp_path / field / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "0.2", "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        invalid = _hello(material)
        invalid[field] = bad
        await websocket.send(json.dumps(invalid))
        with pytest.raises(websockets.ConnectionClosed):
            await websocket.recv()
        await asyncio.wait_for(process.wait(), 5)
        stdout = (await process.stdout.read()).decode()
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 1 and stdout == ""
        assert "authenticated local Mod readiness timed out" in stderr
        assert material["proof"] not in stdout + stderr
        _assert_transient_cleanup_and_persistent_audit(rendezvous)
    finally:
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_production_cli_rejects_second_owner_and_proof_replay_but_accepts_resume(tmp_path):
    rendezvous = tmp_path / "owner-replay" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    owner = second = replay = resumed = None
    try:
        material = await _wait_and_claim(rendezvous)
        owner = await websockets.connect(material["endpoint"])
        await owner.send(json.dumps(_hello(material)))
        ready = json.loads(await owner.recv())

        second = await websockets.connect(material["endpoint"])
        await second.send(json.dumps(_hello(material)))
        with pytest.raises(websockets.ConnectionClosed):
            await second.recv()

        await owner.close()
        await asyncio.sleep(0.02)
        replay = await websockets.connect(material["endpoint"])
        await replay.send(json.dumps(_hello(material)))
        with pytest.raises(websockets.ConnectionClosed):
            await replay.recv()

        resume_hello = _hello(material)
        resume_hello.pop("proof")
        resume_hello["reconnect_proof"] = ready["reconnect_proof"]
        resumed = await websockets.connect(material["endpoint"])
        await resumed.send(json.dumps(resume_hello))
        assert json.loads(await resumed.recv())["type"] == "local_direct_ready"

        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await asyncio.wait_for(resumed.wait_closed(), 5)
        stdout = (await process.stdout.read()).decode()
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0 and stdout == "" and stderr == ""
        assert material["proof"] not in stdout + stderr
        assert ready["reconnect_proof"] not in stdout + stderr
        _assert_transient_cleanup_and_persistent_audit(rendezvous)
    finally:
        for websocket in (owner, second, replay, resumed):
            if websocket is not None:
                await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()


@pytest.mark.asyncio
async def test_production_cli_eof_during_inflight_call_closes_ws_and_cleans_owner(tmp_path):
    rendezvous = tmp_path / "eof-inflight" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
        "--readiness-timeout", "5", "--enable-controlled-mutations",
        "--rendezvous-path", str(rendezvous),
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=2**20,
    )
    websocket = None
    call = None
    responses = []
    try:
        material = await _wait_and_claim(rendezvous)
        websocket = await websockets.connect(material["endpoint"])
        await websocket.send(json.dumps(_hello(material)))
        await websocket.recv()
        responses.append(await _rpc(process, 1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "eof-e2e", "version": "1"},
        }))
        await _rpc(process, 2, "tools/list")
        call = asyncio.create_task(_rpc(process, 3, "tools/call", {
            "name": "skill:stardew.daily.water_crops@1.0.0",
            "arguments": {"targets": [{"map_id": "Farm", "x": 1, "y": 2, "stand_side": "down"}]},
            "_meta": {"toolCallId": "eof-tool", "commandId": "eof-command"},
        }))
        assert json.loads(await websocket.recv())["type"] == "local_command"

        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        await asyncio.wait_for(websocket.wait_closed(), 5)
        assert websocket.close_code == 1001
        with pytest.raises(AssertionError, match="closed stdout"):
            await call
        stdout_tail = (await process.stdout.read()).decode()
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0 and stdout_tail == "" and stderr == ""
        assert material["proof"] not in json.dumps(responses) + stdout_tail + stderr
        _assert_transient_cleanup_and_persistent_audit(
            rendezvous, expected_tool_call_ids=("eof-tool",),
        )
    finally:
        if call is not None and not call.done():
            call.cancel()
        if websocket is not None:
            await websocket.close()
        if process.returncode is None:
            process.terminate()
            await process.wait()
