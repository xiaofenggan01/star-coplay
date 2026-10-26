from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modules.http_api.server import HttpApiConfig, HttpApiServer
from modules.llm.runtime_relay import (
    DbRuntimeLLMEnvironmentResolver,
    RuntimeLLMTokenSigner,
)
from modules.llm.system_router import SystemLLMResult
from modules.runtime_manager.adapters.claude.adapter import _runtime_environment
from modules.runtime_manager.contracts import RuntimeSpec


SECRET = "runtime-llm-test-secret-that-is-long-enough"


def test_runtime_llm_token_is_generation_scoped_and_tamper_evident():
    signer = RuntimeLLMTokenSigner(SECRET, clock=lambda: 1000)
    token = signer.issue(
        user_id="tenant-1", runtime_id="runtime-1", generation=4,
        model_tier="balanced",
    )
    claims = signer.verify(token)
    assert (claims.user_id, claims.runtime_id, claims.generation, claims.model_tier) == (
        "tenant-1", "runtime-1", 4, "balanced",
    )
    with pytest.raises(ValueError):
        signer.verify(token[:-1] + ("A" if token[-1] != "A" else "B"))


@pytest.mark.asyncio
async def test_runtime_environment_contains_only_relay_credential_and_model_tier():
    class Pool:
        async def fetchrow(self, _query, user_id):
            assert user_id == "tenant-1"
            return {"preferences": {"ai_relay": {"default_model_tier": "fast"}}}

    signer = RuntimeLLMTokenSigner(SECRET, clock=lambda: 1000)
    resolver = DbRuntimeLLMEnvironmentResolver(
        Pool(), signer, "http://127.0.0.1:8766/internal/runtime-llm",
    )
    env = await resolver.resolve(
        runtime_id="runtime-1", generation=2, user_id="tenant-1",
        agentee_id="agentee-1", provider="claude", adapter_config=SimpleNamespace(),
    )
    assert env["ANTHROPIC_BASE_URL"].endswith("/internal/runtime-llm")
    assert env["ANTHROPIC_MODEL"] == "fast"
    assert "NEWAPI" not in " ".join(env)
    claims = signer.verify(env["ANTHROPIC_AUTH_TOKEN"])
    assert claims.generation == 2 and claims.model_tier == "fast"


@pytest.mark.asyncio
async def test_dsh_runtime_environment_uses_deepseek_relay_and_credential():
    class Pool:
        async def fetchrow(self, _query, user_id):
            assert user_id == "tenant-1"
            return {"preferences": {"ai_relay": {"default_model_tier": "fast"}}}

    signer = RuntimeLLMTokenSigner(SECRET, clock=lambda: 1000)
    resolver = DbRuntimeLLMEnvironmentResolver(
        Pool(), signer, "http://127.0.0.1:8766/internal/runtime-llm",
    )
    env = await resolver.resolve(
        runtime_id="runtime-1", generation=2, user_id="tenant-1",
        agentee_id="agentee-1", provider="dsh", adapter_config=SimpleNamespace(),
    )
    assert set(env) == {"DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY"}
    assert env["DEEPSEEK_BASE_URL"] == "http://127.0.0.1:8766/internal/runtime-llm"
    assert "ANTHROPIC_BASE_URL" not in env
    claims = signer.verify(env["DEEPSEEK_API_KEY"])
    assert claims.generation == 2 and claims.model_tier == "fast"


def test_runtime_relay_token_removes_ambient_official_anthropic_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "official-global-key")
    spec = RuntimeSpec(
        runtime_id="runtime-1", generation=1, user_id="tenant-1",
        agentee_id="agentee-1", provider="claude", workspace_root=Path("."),
        env={
            "ANTHROPIC_BASE_URL": "http://relay/internal/runtime-llm",
            "ANTHROPIC_AUTH_TOKEN": "runtime-scoped-token",
        },
    )
    env = _runtime_environment(spec, "nonce")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "runtime-scoped-token"
    assert "ANTHROPIC_API_KEY" not in env


class RuntimeManager:
    async def get(self, runtime_id):
        if runtime_id != "runtime-1":
            return None
        return SimpleNamespace(
            generation=3,
            spec=SimpleNamespace(user_id="tenant-1"),
        )


class Router:
    def __init__(self):
        self.calls = []

    async def anthropic_messages(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        response = httpx.Response(
            200,
            json={"type": "message", "content": [{"type": "text", "text": "ok"}]},
            headers={"content-type": "application/json"},
        )
        return SystemLLMResult(response, "claude-routed", "balanced", "system_config")

    async def stream_response(self, response):
        async for chunk in response.aiter_bytes():
            yield chunk


async def relay_client(signer, router):
    server = HttpApiServer(
        gateway=SimpleNamespace(), authenticator=SimpleNamespace(), config=HttpApiConfig(),
        runtime_manager=RuntimeManager(), runtime_manager_enabled=True,
        system_llm_router=router, runtime_llm_token_signer=signer,
    )
    app = web.Application()
    app.router.add_post("/internal/runtime-llm/v1/messages", server._runtime_llm_anthropic_messages)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_runtime_anthropic_relay_uses_claim_owner_and_model_tier():
    signer = RuntimeLLMTokenSigner(SECRET)
    token = signer.issue(
        user_id="tenant-1", runtime_id="runtime-1", generation=3,
        model_tier="balanced",
    )
    router = Router()
    client = await relay_client(signer, router)
    try:
        response = await client.post(
            "/internal/runtime-llm/v1/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
            },
            json={"model": "attempted-override", "max_tokens": 10, "messages": []},
        )
        assert response.status == 200
        payload, kwargs = router.calls[0]
        assert payload["model"] == "attempted-override"
        assert kwargs["context"].user_id == "tenant-1"
        assert kwargs["context"].model_tier == "balanced"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_runtime_anthropic_relay_rejects_stale_generation_before_newapi():
    signer = RuntimeLLMTokenSigner(SECRET)
    token = signer.issue(
        user_id="tenant-1", runtime_id="runtime-1", generation=2,
        model_tier="balanced",
    )
    router = Router()
    client = await relay_client(signer, router)
    try:
        response = await client.post(
            "/internal/runtime-llm/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"max_tokens": 10, "messages": []},
        )
        assert response.status == 401
        assert router.calls == []
    finally:
        await client.close()
