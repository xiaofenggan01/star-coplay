from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.gateway.server import Session
from modules.runtime.text_channel_fanout import (
    TextChannelBinding,
    TextChannelOutputFanout,
)


@pytest.mark.asyncio
async def test_visible_output_fanout_writes_say_event():
    writer = SimpleNamespace(write_event=AsyncMock())
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=[
        TextChannelBinding(
            source="roadshow",
            channel_key="main",
            user_id="user_1",
            agent_instance_id="ai_1",
        )
    ]))
    fanout = TextChannelOutputFanout(writer=writer, resolver=resolver)
    inst = SimpleNamespace(agent_instance_id="ai_1", farm_session_id="farm_1")
    session = Session(connection_id="conn_1", instance=inst, ws=AsyncMock())

    await fanout.publish_agent_visible_output(session, {
        "type": "command",
        "capability": "say",
        "action_id": "act_1",
        "seq": 7,
        "params": {"message": "你好"},
    })

    writer.write_event.assert_awaited_once()
    kwargs = writer.write_event.await_args.kwargs
    assert kwargs["user_id"] == "user_1"
    assert kwargs["event_type"] == "external_text.agent_visible_output"
    assert kwargs["session_id"] == "farm_1"
    assert kwargs["client_event_id"] == "external_text:visible_output:roadshow:main:ai_1:act_1"
    assert kwargs["data"]["source"] == "roadshow"
    assert kwargs["data"]["channel_key"] == "main"
    assert kwargs["data"]["text"] == "你好"


@pytest.mark.asyncio
async def test_visible_output_fanout_ignores_non_say():
    writer = SimpleNamespace(write_event=AsyncMock())
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=[
        TextChannelBinding(
            source="roadshow",
            channel_key="main",
            user_id="user_1",
            agent_instance_id="ai_1",
        )
    ]))
    fanout = TextChannelOutputFanout(writer=writer, resolver=resolver)
    inst = SimpleNamespace(agent_instance_id="ai_1", farm_session_id="farm_1")
    session = Session(connection_id="conn_1", instance=inst, ws=AsyncMock())

    await fanout.publish_agent_visible_output(session, {
        "type": "command",
        "capability": "move_to",
        "params": {"location": "Farm"},
    })

    resolver.resolve.assert_not_awaited()
    writer.write_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_visible_output_fanout_ignores_empty_message():
    writer = SimpleNamespace(write_event=AsyncMock())
    resolver = SimpleNamespace(resolve=AsyncMock())
    fanout = TextChannelOutputFanout(writer=writer, resolver=resolver)
    inst = SimpleNamespace(agent_instance_id="ai_1", farm_session_id="farm_1")
    session = Session(connection_id="conn_1", instance=inst, ws=AsyncMock())

    await fanout.publish_agent_visible_output(session, {
        "type": "command",
        "capability": "say",
        "params": {"message": "  "},
    })

    resolver.resolve.assert_not_awaited()
    writer.write_event.assert_not_awaited()
