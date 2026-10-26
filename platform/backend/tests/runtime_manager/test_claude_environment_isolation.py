from __future__ import annotations

import os
from pathlib import Path

import pytest

from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
from modules.runtime_manager.adapters.claude.transport import ClaudeProcessTransport
from modules.runtime_manager.contracts import RuntimeSpec


@pytest.mark.asyncio
async def test_start_inherits_only_claude_runtime_environment(monkeypatch, tmp_path: Path):
    allowed = {
        "PATH": "/usr/local/bin:/usr/bin",
        "SystemRoot": r"C:\Windows",
        "HOME": "/ambient-home",
        "LANG": "en_US.UTF-8",
        "LC_CTYPE": "UTF-8",
        "HTTPS_PROXY": "http://proxy.example:8080",
        "no_proxy": "127.0.0.1,localhost",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
        "ANTHROPIC_BASE_URL": "https://newapi.example/v1",
        "ANTHROPIC_AUTH_TOKEN": "runtime-token",
        "ANTHROPIC_MODEL": "claude-sonnet-5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    }
    forbidden = {
        "DATABASE_URL": "postgresql://gateway-secret",
        "SUPABASE_SERVICE_ROLE_KEY": "supabase-secret",
        "NEW_API_SYS_KEY": "newapi-admin-secret",
        "UNRELATED_GATEWAY_SECRET": "must-not-leak",
        "USERPROFILE": r"C:\Users\developer",
        "APPDATA": r"C:\Users\developer\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\developer\AppData\Local",
    }
    for key, value in {**allowed, **forbidden}.items():
        monkeypatch.setenv(key, value)

    captured: dict[str, str] = {}

    async def capture_start(self, *, command, cwd, env):
        captured.update(env)

    monkeypatch.setattr(ClaudeProcessTransport, "start", capture_start)

    spec = RuntimeSpec(
        runtime_id="rt-env-isolation",
        generation=1,
        user_id="user-1",
        agentee_id="agentee-1",
        provider="claude",
        workspace_root=tmp_path,
        env={
            "HOME": "/runtime-home",
            "ANTHROPIC_AUTH_TOKEN": "explicit-runtime-token",
            "DATABASE_URL": "postgresql://explicit-secret",
            "LD_PRELOAD": "/tmp/evil.so",
            "NODE_OPTIONS": "--require=/tmp/evil.js",
            "EXPLICIT_RUNTIME_VALUE": "must-not-pass",
        },
    )
    adapter = ClaudeAgentRuntimeAdapter()
    prepared = await adapter.prepare(spec)
    await adapter.start(prepared)

    for key, value in allowed.items():
        expected = {
            "HOME": "/runtime-home",
            "ANTHROPIC_AUTH_TOKEN": "explicit-runtime-token",
        }.get(key, value)
        actual = (
            next(value for name, value in captured.items() if name.casefold() == key.casefold())
            if os.name == "nt" else captured[key]
        )
        assert actual == expected
    captured_keys = {key.casefold() if os.name == "nt" else key for key in captured}
    for key in forbidden:
        assert (key.casefold() if os.name == "nt" else key) not in captured_keys
    assert captured["ANTHROPIC_AUTH_TOKEN"] == "explicit-runtime-token"
    for key in ("DATABASE_URL", "LD_PRELOAD", "NODE_OPTIONS", "EXPLICIT_RUNTIME_VALUE"):
        assert key not in captured
    assert captured["STARCOPLAY_RUNTIME_OWNER_NONCE"] == prepared.provider_private_metadata["nonce"]


@pytest.mark.asyncio
async def test_non_hosted_runtime_cannot_inject_claude_tool_grants(monkeypatch, tmp_path: Path):
    captured: list[str] = []

    async def capture_start(self, *, command, cwd, env):
        captured.extend(command)

    monkeypatch.setattr(ClaudeProcessTransport, "start", capture_start)
    spec = RuntimeSpec(
        runtime_id="rt-no-tool-grant",
        generation=1,
        user_id="user-1",
        agentee_id="agentee-1",
        provider="claude",
        workspace_root=tmp_path,
        provider_private_metadata={
            "claude_allowed_tools": ("Bash", "Edit", "mcp__attacker__*"),
        },
    )
    adapter = ClaudeAgentRuntimeAdapter()
    await adapter.start(await adapter.prepare(spec))

    assert "--allowedTools" not in captured
    assert not any(tool in captured for tool in ("Bash", "Edit", "mcp__attacker__*"))
