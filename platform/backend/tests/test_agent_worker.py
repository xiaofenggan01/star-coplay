from __future__ import annotations

import json

import pytest

from modules.runtime.agent_worker import AgentSessionWorker


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, preferences):
        self._preferences = preferences

    async def fetchrow(self, query, user_id):
        return {"preferences": self._preferences}


class FakePool:
    def __init__(self, preferences):
        self._preferences = preferences

    def acquire(self):
        return FakeAcquire(FakeConn(self._preferences))


@pytest.mark.asyncio
async def test_agent_worker_reads_default_model_tier_from_json_string_preferences():
    worker = AgentSessionWorker(
        db_pool=FakePool(json.dumps({"ai_relay": {"default_model_tier": "fast"}})),
    )

    assert await worker._get_user_default_model_tier("user-1") == "fast"


@pytest.mark.asyncio
async def test_agent_worker_reads_default_model_tier_from_dict_preferences():
    worker = AgentSessionWorker(
        db_pool=FakePool({"ai_relay": {"default_model_tier": "balanced"}}),
    )

    assert await worker._get_user_default_model_tier("user-1") == "balanced"


@pytest.mark.asyncio
async def test_agent_worker_ignores_invalid_preferences_json():
    worker = AgentSessionWorker(db_pool=FakePool("{not-json"))

    assert await worker._get_user_default_model_tier("user-1") == ""
