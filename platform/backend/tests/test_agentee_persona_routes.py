from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer

from modules.gateway.server import Gateway, GatewayConfig
from modules.http_api.server import HttpApiConfig, HttpApiServer


class FakePersonaService:
    def __init__(self) -> None:
        self.parse_calls = []
        self.compile_calls = []

    async def parse_persona(self, payload):
        self.parse_calls.append(payload)
        return {"core_preferences": {"warmth": 3, "expressiveness": 3, "playfulness": 3, "initiative": 3, "carefulness": 3, "goal_focus": 3}, "custom_flavor": [], "generated_by_model": "gpt-5.4", "persona_schema_version": 1, "generation_status": "fallback"}

    async def compile_persona(self, payload):
        self.compile_calls.append(payload)
        return {
            "style_summary": "温柔可靠",
            "speech_guidelines": ["语气亲切"],
            "collaboration_guidelines": ["主动给建议"],
            "gameplay_guidelines": ["优先完成目标"],
            "persona_card": "card",
            "generated_by_model": "gpt-5.4",
            "persona_schema_version": 1,
            "generation_status": "generated",
            "generation_error": None,
        }


async def _client(service=None, *, internal_api_token="internal-token"):
    gateway = Gateway(GatewayConfig(admin_secret="admin-secret"), AsyncMock())
    server = HttpApiServer(
        gateway,
        gateway.authenticator,
        HttpApiConfig(),
        persona_service=service,
        internal_api_token=internal_api_token,
    )
    test_server = TestServer(server.build_app())
    client = TestClient(test_server)
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_persona_internal_routes_require_token():
    client = await _client(FakePersonaService())
    try:
        resp = await client.post("/internal/persona/parse", json={})
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "CONFIG_UNAUTHORIZED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_persona_parse_route_forwards_body():
    service = FakePersonaService()
    client = await _client(service)
    try:
        resp = await client.post(
            "/internal/persona/parse",
            headers={"Authorization": "Bearer internal-token"},
            json={
                "custom_description": "想要一个温柔但会主动提醒我的搭子",
                "selected_tags": ["quiet_companion"],
                "persona_schema_version": 1,
            },
        )

        assert resp.status == 200
        body = await resp.json()
        assert body["generation_status"] == "fallback"
        assert service.parse_calls[0]["custom_description"] == "想要一个温柔但会主动提醒我的搭子"
        assert service.parse_calls[0]["selected_tags"] == ["quiet_companion"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_persona_parse_route_accepts_api_prefixed_internal_path():
    service = FakePersonaService()
    client = await _client(service)
    try:
        resp = await client.post(
            "/api/internal/persona/parse",
            headers={"Authorization": "Bearer internal-token"},
            json={
                "custom_description": "想要一个温柔但会主动提醒我的搭子",
                "persona_schema_version": 1,
            },
        )

        assert resp.status == 200
        assert service.parse_calls[0]["custom_description"] == "想要一个温柔但会主动提醒我的搭子"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_persona_compile_route_forwards_body():
    service = FakePersonaService()
    client = await _client(service)
    try:
        resp = await client.post(
            "/internal/persona/compile",
            headers={"Authorization": "Bearer internal-token"},
            json={
                "profile": {
                    "display_name": "小枫",
                    "user_call_name": "主人",
                    "relationship_type": "游戏搭子",
                    "short_bio": "一起种田",
                },
                "persona": {
                    "custom_description": "平时安静，关键时刻主动提醒我",
                    "core_preferences": {
                        "warmth": 4,
                        "expressiveness": 3,
                        "playfulness": 2,
                        "initiative": 4,
                        "carefulness": 4,
                        "goal_focus": 5,
                    },
                    "custom_flavor": [],
                },
                "persona_schema_version": 1,
            },
        )

        assert resp.status == 200
        body = await resp.json()
        assert body["persona_card"] == "card"
        assert service.compile_calls[0]["profile"]["display_name"] == "小枫"
        assert service.compile_calls[0]["persona"]["core_preferences"]["goal_focus"] == 5
    finally:
        await client.close()
