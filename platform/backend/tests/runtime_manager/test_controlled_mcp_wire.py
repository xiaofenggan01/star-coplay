from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from modules.runtime_manager.adapters.claude.adapter import (
    ClaudeAgentRuntimeAdapter, _PROTOBUF_IMPORT_ROOT,
)
from modules.runtime_manager.adapters.claude.protocol import resolve_launch_config
from modules.runtime_manager.contracts import RuntimeSpec


SERVER = Path(__file__).with_name("controlled_mcp_server.py")


async def _rpc(proc: asyncio.subprocess.Process, request: dict, timeout: float = 2.0) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((json.dumps(request) + "\n").encode())
    await proc.stdin.drain()
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not line:
        await proc.wait()
        stderr = ""
        if proc.stderr is not None:
            stderr = (await proc.stderr.read()).decode(errors="replace").strip()
        pytest.fail(
            f"controlled MCP exited before response (code={proc.returncode}, stderr={stderr!r})"
        )
    assert line.startswith(b"{"), "MCP stdio must be newline-delimited JSON, not Content-Length framed"
    return json.loads(line)


@pytest.mark.asyncio
async def test_controlled_mcp_wire_success_failure_revoke_cross_runtime_and_audit(tmp_path: Path):
    secret = "test-only-secret-never-a-platform-credential"
    credential = tmp_path / "credential"
    credential.write_text(secret)
    credential.chmod(0o600)
    audit = tmp_path / "audit.jsonl"
    revoke = tmp_path / "revoked"
    env = {
        **os.environ,
        "CONTROLLED_MCP_CREDENTIAL_PATH": str(credential),
        "CONTROLLED_MCP_REVOKE_MARKER_PATH": str(revoke),
        "CONTROLLED_MCP_AUDIT_LOG_PATH": str(audit),
        "CONTROLLED_MCP_RUNTIME_ID": "runtime-a",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SERVER), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        # This timeout is the total bootstrap deadline: an early process exit
        # is reported above with its stderr rather than looking like a hang.
        init = await _rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            timeout=10.0,
        )
        assert init["result"]["capabilities"] == {"tools": {}}
        tools = await _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert {tool["name"] for tool in tools["result"]["tools"]} == {"controlled_echo", "controlled_fail"}

        ok = await _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "controlled_echo", "arguments": {"message": "hello", "runtime_id": "runtime-a"}}})
        fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:16]
        assert ok["result"]["isError"] is False
        assert fingerprint in ok["result"]["content"][0]["text"]

        failed = await _rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "controlled_fail", "arguments": {"message": "x"}}})
        assert failed["result"]["isError"] is True
        cross = await _rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "controlled_echo", "arguments": {"message": "x", "runtime_id": "runtime-b"}}})
        assert cross["result"]["isError"] is True
        revoke.touch()
        revoked = await _rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "controlled_echo", "arguments": {"message": "x", "runtime_id": "runtime-a"}}})
        assert revoked["result"]["isError"] is True

        entries = [json.loads(line) for line in audit.read_text().splitlines()]
        assert [e["note"] for e in entries] == [
            "protocol-init", "protocol-tools-list", "ok", "deliberate-failure", "runtime-mismatch", "revoked"
        ]
        assert secret not in audit.read_text()
        assert all(set(e) == {"timestamp", "tool", "is_error", "fingerprint", "note"} for e in entries)
    finally:
        if proc.stdin:
            proc.stdin.close()
        await asyncio.wait_for(proc.wait(), timeout=2)


@pytest.mark.asyncio
async def test_claude_adapter_materializes_strict_provider_private_mcp_config(tmp_path: Path):
    spec = RuntimeSpec(
        runtime_id="runtime-config", generation=1, user_id="u", agentee_id="a",
        provider="claude", workspace_root=tmp_path, idle_ttl_seconds=600,
        provider_private_metadata={"controlled_mcp_server": str(SERVER)},
    )
    prepared = await ClaudeAgentRuntimeAdapter().prepare(spec)
    metadata = dict(spec.provider_private_metadata)
    metadata.update(prepared.provider_private_metadata)
    config = Path(str(metadata["mcp_config_path"]))
    command = resolve_launch_config(metadata).command()
    assert command[command.index("--mcp-config") + 1] == str(config)
    assert "--strict-mcp-config" in command
    assert "--allowedTools" not in command
    if os.name == "nt":
        assert config.is_file() and config.stat().st_size > 0
    else:
        assert config.stat().st_mode & 0o777 == 0o600
    credential = prepared.workdir / "mcp-credential"
    assert credential.stat().st_mode & 0o777 == 0o600
    raw = config.read_text()
    assert credential.read_text() not in raw
    assert metadata["credential_fingerprint"] == hashlib.sha256(credential.read_bytes()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_claude_adapter_hosted_proxy_imports_from_unfamiliar_runtime_cwd(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("PYTHON", sys.executable)
    spec = RuntimeSpec(
        runtime_id="runtime-hosted", generation=1, user_id="u", agentee_id="a",
        provider="claude", workspace_root=tmp_path, idle_ttl_seconds=600,
        provider_private_metadata={"hosted_mcp_endpoint": "ws://127.0.0.1:8766/mcp/hosted/ws"},
    )
    prepared = await ClaudeAgentRuntimeAdapter().prepare(spec)
    config_path = Path(prepared.provider_private_metadata["mcp_config_path"])
    config = json.loads(config_path.read_text())
    server = config["mcpServers"]["starcoplay-hosted"]
    assert server["alwaysLoad"] is True
    assert server["args"] == ["-m", "modules.mcp.transports.stdio_proxy"]
    assert server["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(Path(__file__).resolve().parents[4]),
        str(Path(__file__).resolve().parents[2]),
        str(_PROTOBUF_IMPORT_ROOT),
    ]
    assert server["env"]["PYTHONIOENCODING"] == "utf-8"
    assert server["env"]["PYTHONUTF8"] == "1"
    assert server["env"]["STARCOPLAY_HOSTED_MCP_ENDPOINT"].startswith("ws://")
    credential_path = Path(server["env"]["STARCOPLAY_MCP_CREDENTIAL_PATH"])
    assert credential_path.name == "mcp-credential"
    assert not credential_path.exists()  # READY_UNBOUND: no authority material yet.
    raw = config_path.read_text()
    assert "Bearer " not in raw and "credential_fingerprint" not in raw
    if os.name == "nt":
        assert config_path.is_file() and config_path.stat().st_size > 0
    else:
        assert config_path.stat().st_mode & 0o777 == 0o600

    metadata = {**spec.provider_private_metadata, **prepared.provider_private_metadata}
    command = resolve_launch_config(metadata).command()
    allowed_index = command.index("--allowedTools")
    assert command[allowed_index + 1:] == ["mcp__starcoplay-hosted__*"]
    assert not any(tool in command[allowed_index + 1:] for tool in ("Bash", "Edit", "Write", "Read"))

    unfamiliar_cwd = tmp_path / "not-the-repository-or-backend"
    unfamiliar_cwd.mkdir()
    process = await asyncio.create_subprocess_exec(
        server["command"], *server["args"], cwd=unfamiliar_cwd,
            env={
                "PATH": os.environ.get("PATH", ""),
                "SystemRoot": os.environ.get("SystemRoot", os.environ.get("SYSTEMROOT", "")),
                **server["env"],
            },
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        initialized = await _rpc(process, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        assert initialized["result"]["serverInfo"]["name"] == "starcoplay-hosted-proxy"
        assert process.returncode is None
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        stderr = (await process.stderr.read()).decode()
        assert process.returncode == 0
        assert "ModuleNotFoundError" not in stderr
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
