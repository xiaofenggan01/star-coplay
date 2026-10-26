from __future__ import annotations

import json

import httpx
import pytest

from common.relay import NewAPIClient, NewAPIClientConfig
from modules.agentee.persona_service import PersonaInternalService


def _client(handler) -> NewAPIClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://newapi.test", transport=transport)
    return NewAPIClient(
        NewAPIClientConfig(
            base_url="http://newapi.test",
            access_token="admin-token",
        ),
        client=http_client,
    )


@pytest.mark.asyncio
async def test_parse_persona_calls_newapi_chat_completions():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "core_preferences": {
                                        "warmth": 5,
                                        "expressiveness": 4,
                                        "playfulness": 3,
                                        "initiative": 2,
                                        "carefulness": 4,
                                        "goal_focus": 5,
                                    },
                                    "custom_flavor": [
                                        {
                                            "flavor_name": "嘴硬式关心",
                                            "flavor_description": "常用吐槽和提醒表达关心，不真的贬低用户。",
                                        },
                                    ],
                                }
                            )
                        }
                    }
                ]
            },
            headers={"content-type": "application/json"},
        )

    client = _client(handler)
    service = PersonaInternalService(newapi_client=client)

    try:
        result = await service.parse_persona(
            {
                "custom_description": "嘴上嫌弃我但很靠谱。",
                "tags": ["quiet_companion"],
                "persona_schema_version": 2,
            }
        )
    finally:
        await client.aclose()

    assert captured["path"] == "/v1/chat/completions"
    assert captured["auth"] == "admin-token"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-5.4"
    user_message = body["messages"][1]["content"]
    assert json.loads(user_message)["persona_tags"] == ["quiet_companion"]
    assert result["core_preferences"]["warmth"] == 5
    assert result["custom_flavor"][0]["label"] == "嘴硬式关心"
    assert result["persona_schema_version"] == 2
    assert result["generation_status"] == "generated"


@pytest.mark.asyncio
async def test_compile_persona_falls_back_without_newapi_client():
    service = PersonaInternalService(newapi_client=None)

    result = await service.compile_persona(
        {
            "profile": {
                "display_name": "小枫",
                "user_call_name": "主人",
                "relationship_type": "游戏搭子",
                "short_bio": "一起种田",
            },
            "persona": {
                "custom_description": "平时安静，关键时刻主动提醒我。",
                "core_preferences": {
                    "warmth": 4,
                    "expressiveness": 3,
                    "playfulness": 2,
                    "initiative": 4,
                    "carefulness": 4,
                    "goal_focus": 5,
                },
                "custom_flavor": [
                    {"label": "细心", "description": "会盯着细节"},
                ],
            },
        }
    )

    assert result["generation_status"] == "fallback"
    assert result["generation_error"] == "persona_llm_not_configured"
    assert "小枫" in result["persona_card"]
    assert "通用边界：" in result["persona_card"]
    assert "你不是星露谷 NPC、客服或工具人" in result["persona_card"]
    assert "重大决策、关键资源、不可逆操作" in result["persona_card"]
