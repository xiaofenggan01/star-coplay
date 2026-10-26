from __future__ import annotations

import json

import httpx
import pytest

from modules.ai_relay_account import RelayAccount
from modules.ai_relay_account.config import AIRelayConfigProvider
from modules.llm.system_router import (
    SystemLLMContext,
    SystemLLMRouter,
    SystemLLMRouterError,
)


class FakeAccountReader:
    def __init__(self, account: RelayAccount | None) -> None:
        self.account = account

    async def get_account(self, user_id: str) -> RelayAccount | None:
        if self.account is not None and self.account.user_id == user_id:
            return self.account
        return None


class StaticConfigClient:
    def __init__(self, mapping) -> None:
        self.mapping = mapping

    async def get_string(self, domain, config_key, environment=None):
        if config_key == "default_group":
            return "default"
        raise AssertionError(f"unexpected string config: {domain}/{config_key}")

    async def get_json(self, domain, config_key, environment=None):
        if config_key == "allowed_groups":
            return ["default"]
        if config_key == "default_preferences":
            return {}
        if config_key == "model_tier_mapping":
            return self.mapping
        raise AssertionError(f"unexpected json config: {domain}/{config_key}")

    async def get_decimal(self, domain, config_key, environment=None):
        if config_key == "initial_user_quota_usd":
            return 100.0
        raise AssertionError(f"unexpected decimal config: {domain}/{config_key}")


def _account(**kwargs) -> RelayAccount:
    values = {
        "user_id": "u1",
        "newapi_user_id": 10,
        "newapi_token_id": 20,
        "newapi_token_key": "sk-newapi-user-token",
        "has_newapi_user_access_token": True,
        "group": "default",
    }
    values.update(kwargs)
    return RelayAccount(**values)


def _router(account: RelayAccount | None, handler, mapping=None) -> SystemLLMRouter:
    client = httpx.AsyncClient(
        base_url="http://newapi.local",
        transport=httpx.MockTransport(handler),
    )
    return SystemLLMRouter(
        account_reader=FakeAccountReader(account),
        config_provider=AIRelayConfigProvider(
            config_client=StaticConfigClient(mapping or {"balanced": ["gpt-4o"]}),
            environment="test",
        ),
        newapi_base_url="http://newapi.local",
        client=client,
    )


@pytest.mark.asyncio
async def test_system_llm_router_injects_newapi_token_and_resolved_model():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
            headers={"content-type": "application/json"},
        )

    router = _router(
        _account(preferred_models={"balanced": "gpt-4o-mini"}),
        handler,
        mapping={"balanced": {"default": "gpt-4o", "models": ["gpt-4o", "gpt-4o-mini"]}},
    )

    result = await router.chat_completions(
        {"model": "caller-choice", "messages": [{"role": "user", "content": "hi"}]},
        context=SystemLLMContext(user_id="u1", model_tier="balanced"),
        stream=False,
    )

    assert result.resolved_model == "gpt-4o-mini"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-newapi-user-token"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    await result.response.aclose()
    await router.aclose()


@pytest.mark.asyncio
async def test_system_llm_router_relays_anthropic_messages_to_tenant_newapi_token():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["version"] = request.headers.get("anthropic-version")
        captured["beta"] = request.headers.get("anthropic-beta")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"type": "message", "content": []})

    router = _router(
        _account(preferred_models={"balanced": "claude-sonnet-routed"}),
        handler,
        mapping={"balanced": ["claude-sonnet-routed"]},
    )
    result = await router.anthropic_messages(
        {"model": "caller-model", "max_tokens": 64, "messages": []},
        context=SystemLLMContext(user_id="u1", model_tier="balanced"),
        stream=False,
        anthropic_version="2023-06-01",
        anthropic_beta="tools-2025-01-01",
    )

    assert captured == {
        "path": "/v1/messages",
        "auth": "Bearer sk-newapi-user-token",
        "version": "2023-06-01",
        "beta": "tools-2025-01-01",
        "body": {"model": "claude-sonnet-routed", "max_tokens": 64, "messages": []},
    }
    await result.response.aclose()
    await router.aclose()


@pytest.mark.asyncio
async def test_system_llm_router_rejects_missing_account():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    router = _router(None, handler)

    with pytest.raises(SystemLLMRouterError) as exc:
        await router.chat_completions(
            {"messages": []},
            context=SystemLLMContext(user_id="missing", model_tier="balanced"),
            stream=False,
        )

    assert exc.value.code == "AI_ACCOUNT_NOT_FOUND"
    assert exc.value.status == 404
    await router.aclose()


@pytest.mark.asyncio
async def test_system_llm_router_rejects_disabled_account():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    router = _router(_account(status="disabled"), handler)

    with pytest.raises(SystemLLMRouterError) as exc:
        await router.chat_completions(
            {"messages": []},
            context=SystemLLMContext(user_id="u1", model_tier="balanced"),
            stream=False,
        )

    assert exc.value.code == "AI_ACCOUNT_DISABLED"
    assert exc.value.status == 409
    await router.aclose()


@pytest.mark.asyncio
async def test_system_llm_router_normalizes_upstream_rate_limit():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limited"}},
            headers={"content-type": "application/json"},
        )

    router = _router(_account(), handler)

    with pytest.raises(SystemLLMRouterError) as exc:
        await router.chat_completions(
            {"messages": []},
            context=SystemLLMContext(user_id="u1", model_tier="balanced"),
            stream=False,
        )

    assert exc.value.code == "NEWAPI_RATE_LIMITED"
    assert exc.value.status == 429
    assert exc.value.upstream_status == 429
    await router.aclose()


@pytest.mark.asyncio
async def test_system_llm_router_streams_chunks_and_closes_response():
    class IteratorStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: one\n\n"
            yield b"data: [DONE]\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            stream=IteratorStream(),
            headers={"content-type": "text/event-stream"},
        )

    router = _router(_account(), handler)
    result = await router.chat_completions(
        {"messages": [{"role": "user", "content": "hi"}]},
        context=SystemLLMContext(user_id="u1", model_tier="balanced"),
        stream=True,
    )

    chunks = [chunk async for chunk in router.stream_response(result.response)]

    assert chunks == [b"data: one\n\n", b"data: [DONE]\n\n"]
    await router.aclose()
