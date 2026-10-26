from __future__ import annotations

import asyncio

import pytest

from modules.runtime.subprocess_handle import AgentSubprocessHandle


class FakeProcess:
    pid = 12345


@pytest.mark.asyncio
async def test_agent_subprocess_injects_system_llm_router_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        captured["stderr"] = kwargs["stderr"]
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setenv("WS_HTTP_PORT", "9876")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("AI_RELAY_AGENT_MODEL_TIER", raising=False)
    monkeypatch.delenv("AI_RELAY_AGENT_BASE_URL", raising=False)

    await AgentSubprocessHandle.spawn(
        agent_instance_id="agent-1",
        platform_token="platform-token",
        user_id="user-1",
        target_player_id=None,
        platform_url="http://platform.local",
    )

    env = captured["env"]
    cmd = captured["cmd"]
    assert "--platform-token" in cmd
    assert captured["stderr"] is asyncio.subprocess.PIPE
    assert env["LLM_RELAY_BASE_URL"] == "http://127.0.0.1:9876/api/v1/system"
    assert env["LLM_RELAY_API_KEY"] == "platform-token"
    assert env["STARCOPLAY_STRUCTURED_LOGGING"] == "1"
    assert env["LLM_MODEL"] == "balanced"


@pytest.mark.asyncio
async def test_agent_subprocess_allows_system_router_env_overrides(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setenv("AI_RELAY_AGENT_BASE_URL", "http://router.local/api/v1/system")
    monkeypatch.setenv("AI_RELAY_AGENT_MODEL_TIER", "fast")

    await AgentSubprocessHandle.spawn(
        agent_instance_id="agent-1",
        platform_token="platform-token",
        user_id="user-1",
        target_player_id=None,
        platform_url="http://platform.local",
    )

    env = captured["env"]
    assert env["LLM_RELAY_BASE_URL"] == "http://router.local/api/v1/system"
    assert env["LLM_MODEL"] == "fast"


@pytest.mark.asyncio
async def test_agent_subprocess_uses_user_model_tier_before_env_default(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setenv("AI_RELAY_AGENT_MODEL_TIER", "balanced")

    await AgentSubprocessHandle.spawn(
        agent_instance_id="agent-1",
        platform_token="platform-token",
        user_id="user-1",
        target_player_id=None,
        platform_url="http://platform.local",
        model_tier="fast",
    )

    env = captured["env"]
    assert env["LLM_MODEL"] == "fast"
