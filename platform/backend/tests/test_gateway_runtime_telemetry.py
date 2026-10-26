from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from modules.gateway.server import Gateway, GatewayConfig, Session
from modules.runtime.instance_registry import AgentInstance


class FakeWS:
    def __init__(self, frames: list[dict]):
        self._frames = [json.dumps(frame) for frame in frames]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def send(self, _data: str) -> None:
        return None


@pytest.mark.asyncio
async def test_gateway_skips_runtime_telemetry_db_write_by_default():
    gateway = Gateway(GatewayConfig(), worker_handle=AsyncMock())
    writer = AsyncMock()
    gateway._telemetry_writer.write = writer
    session = Session(
        connection_id="conn-1",
        instance=AgentInstance(
            agent_instance_id="agent-1",
            connection_id="conn-1",
            user_id="user-1",
        ),
        ws=FakeWS([
            {"type": "telemetry", "event_type": "error.llm_api"},
            {"type": "session.end"},
        ]),
    )

    await gateway._run_session(session)

    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_skips_runtime_telemetry_even_for_business_looking_frames():
    gateway = Gateway(GatewayConfig(), worker_handle=AsyncMock())
    writer = AsyncMock()
    gateway._telemetry_writer.write = writer
    session = Session(
        connection_id="conn-1",
        instance=AgentInstance(
            agent_instance_id="agent-1",
            connection_id="conn-1",
            user_id="user-1",
        ),
        ws=FakeWS([
            {"type": "telemetry", "event_type": "business.compat"},
            {"type": "session.end"},
        ]),
    )

    await gateway._run_session(session)

    writer.assert_not_awaited()
