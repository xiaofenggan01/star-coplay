from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from modules.gateway.server import Gateway, GatewayConfig
from modules.http_api.server import HttpApiConfig, HttpApiServer
from modules.runtime.instance_registry import AgentInstance, InstanceState


class FakeSystemLLMRouter:
    def __init__(self) -> None:
        self.calls = []
        self.response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
            headers={"content-type": "application/json"},
        )

    async def chat_completions(self, payload, *, context, stream):
        self.calls.append({"payload": payload, "context": context, "stream": stream})

        class Result:
            def __init__(self, response):
                self.response = response
                self.resolved_model = "gpt-4o"
                self.model_tier = "balanced"
                self.source = "system_config"

        return Result(self.response)

    async def stream_response(self, response):
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()


async def _client(router=None, *, internal_api_token="internal-token"):
    gateway = Gateway(GatewayConfig(admin_secret="admin-secret"), AsyncMock())
    server = HttpApiServer(
        gateway,
        gateway.authenticator,
        HttpApiConfig(),
        system_llm_router=router,
        internal_api_token=internal_api_token,
    )
    test_server = TestServer(server.build_app())
    client = TestClient(test_server)
    await client.start_server()
    return client, gateway


@pytest.fixture(autouse=True)
def _auth_bypass(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_WS_AUTH_BYPASS", "1")
    monkeypatch.setenv("STARCOPLAY_WS_AUTH_BYPASS_USER_ID", "u-auth")


@pytest.mark.asyncio
async def test_http_api_returns_request_id_header():
    client, _gateway = await _client(FakeSystemLLMRouter())
    try:
        resp = await client.get("/api/ping", headers={"x-request-id": "req-test-1"})
        assert resp.status == 200
        assert resp.headers["x-request-id"] == "req-test-1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_internal_llm_route_requires_internal_token():
    client, _gateway = await _client(FakeSystemLLMRouter())
    try:
        resp = await client.post("/internal/llm/chat/completions", json={})
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "CONFIG_UNAUTHORIZED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_internal_llm_route_forwards_payload_without_token_leak():
    router = FakeSystemLLMRouter()
    client, _gateway = await _client(router)
    try:
        resp = await client.post(
            "/internal/llm/chat/completions",
            headers={"Authorization": "Bearer internal-token"},
            json={
                "user_id": "u1",
                "model_tier": "balanced",
                "capability": "chat",
                "payload": {"messages": [{"role": "user", "content": "hi"}]},
            },
        )

        assert resp.status == 200
        body = await resp.json()
        assert body["choices"][0]["message"]["content"] == "ok"
        assert "sk-" not in str(body)
        assert resp.headers["X-Resolved-Model"] == "gpt-4o"
        assert router.calls[0]["context"].user_id == "u1"
        assert router.calls[0]["context"].model_tier == "balanced"
        assert router.calls[0]["context"].capability == "chat"
        assert router.calls[0]["payload"] == {"messages": [{"role": "user", "content": "hi"}]}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_internal_llm_route_checks_agent_session_scope():
    router = FakeSystemLLMRouter()
    client, gateway = await _client(router)
    inst = AgentInstance(
        agent_instance_id="ai_session_1",
        connection_id="conn",
        user_id="u-real",
        state=InstanceState.RUNNING,
    )
    gateway.registry._by_id[inst.agent_instance_id] = inst
    try:
        resp = await client.post(
            "/internal/llm/chat/completions",
            headers={"x-internal-api-token": "internal-token"},
            json={
                "user_id": "u-attacker",
                "agent_session_id": "ai_session_1",
                "model_tier": "balanced",
                "payload": {"messages": []},
            },
        )

        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "AGENT_SESSION_FORBIDDEN"
        assert router.calls == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_internal_llm_route_streams_response():
    class IteratorStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: one\n\n"
            yield b"data: [DONE]\n\n"

    router = FakeSystemLLMRouter()
    router.response = httpx.Response(
        200,
        stream=IteratorStream(),
        headers={"content-type": "text/event-stream"},
    )
    client, _gateway = await _client(router)
    try:
        resp = await client.post(
            "/internal/llm/chat/completions",
            headers={"Authorization": "Bearer internal-token"},
            json={
                "user_id": "u1",
                "model_tier": "balanced",
                "stream": True,
                "payload": {"messages": []},
            },
        )

        assert resp.status == 200
        assert resp.headers["X-Resolved-Model"] == "gpt-4o"
        text = await resp.text()
        assert "data: one" in text
        assert "data: [DONE]" in text
        assert router.calls[0]["stream"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_compat_system_llm_route_uses_platform_token_user_and_model_as_tier():
    router = FakeSystemLLMRouter()
    client, _gateway = await _client(router)
    try:
        resp = await client.post(
            "/api/v1/system/chat/completions",
            headers={"Authorization": "Bearer platform-token", "x-agent-session-id": "agent-1"},
            json={"model": "balanced", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert resp.status == 200
        body = await resp.json()
        assert body["choices"][0]["message"]["content"] == "ok"
        assert resp.headers["X-Resolved-Model"] == "gpt-4o"
        assert router.calls[0]["context"].user_id == "u-auth"
        assert router.calls[0]["context"].agent_session_id == "agent-1"
        assert router.calls[0]["context"].model_tier == "balanced"
        assert router.calls[0]["context"].capability == "agent_openai_compat"
        assert router.calls[0]["payload"]["messages"][0]["content"] == "hi"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_compat_system_llm_route_requires_platform_token():
    router = FakeSystemLLMRouter()
    client, _gateway = await _client(router)
    try:
        resp = await client.post(
            "/api/v1/system/chat/completions",
            json={"model": "balanced", "messages": []},
        )

        assert resp.status == 401
        body = await resp.json()
        assert body["error"] == "missing token"
        assert router.calls == []
    finally:
        await client.close()
