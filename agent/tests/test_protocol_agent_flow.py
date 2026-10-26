from __future__ import annotations

import time
import json
from types import SimpleNamespace

import pytest

from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import PlayerInput, TaskCompleted
from slow_loop.action_dispatcher import ActionDispatcher
from slow_loop.deliberative_loop import DeliberativeLoop
from tests.test_fc_migration import make_fc_llm_fn, make_fc_response

from agent.bridge.ws_bridge import WSBridge, _normalize_protocol_action
from agent.protocol.testing.fake_mod_bridge import FakeProtocolModBridge
from agent.protocol.tool_mapper import ProtocolMappingError


class L3ActionableQueryBridge(FakeProtocolModBridge):
    """Fake bridge with non-empty L1 query facts for L3 query-to-action flow."""

    def write_command(self, action: str, params: dict, target_player_id=None) -> str:
        if action not in {
            "query_runtime_snapshot",
            "query_world_region",
            "query_ui_snapshot",
        }:
            return super().write_command(action, params, target_player_id=target_player_id)

        self._seq += 1
        action_id = f"act_{self._seq:04d}_{action}"
        self._results[action_id] = {
            "success": True,
            "status": "ok",
            "data": {"protocol_data": {"fact": self._query_fact(action)}},
            "observation": "",
            "action_id": action_id,
            "seq": self._seq,
        }
        return action_id

    @staticmethod
    def _query_fact(action: str) -> dict:
        if action == "query_runtime_snapshot":
            return {
                "player_location": {"map_id": "Farm", "x": 0, "y": 0},
                "world_time": {"time": 900},
            }
        if action == "query_world_region":
            return {
                "map_id": "Farm",
                "entities": [
                    {
                        "ref": {"value": "world_entity:crop:farm:1:0#crop_1"},
                        "type": "crop",
                        "fact_kind": "crop",
                        "position": {"x": 1, "y": 0},
                        "crop": {
                            "ready_for_harvest": False,
                            "watered": False,
                            "dead": False,
                        },
                    }
                ],
                "characters": [],
            }
        if action == "query_ui_snapshot":
            return {"menu_open": False, "elements": []}
        raise AssertionError(f"unexpected query action: {action}")


def make_fc_response_with_finish(tool_calls=None, content="", finish_reason="tool_calls"):
    tc_objects = []
    for i, tc in enumerate(tool_calls or []):
        tc_objects.append(SimpleNamespace(
            id=tc.get("id", f"tc_{i}"),
            function=SimpleNamespace(
                name=tc["name"],
                arguments=json.dumps(tc.get("arguments", {})),
            ),
        ))
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=content,
                tool_calls=tc_objects if tc_objects else None,
            ),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        model="test-model",
        id="test-id",
    )


def test_action_dispatcher_round_trips_through_fake_protocol_mod() -> None:
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = FakeProtocolModBridge()
    dispatcher = ActionDispatcher(bridge, bus, memory, stop)

    dispatcher.start()
    try:
        job_id = dispatcher.submit(
            "use_tool",
            {"x": 10, "y": 11, "charge_level": 0},
            timeout_s=1.0,
            trigger_label="protocol-smoke",
            needs_followup=True,
        )
        assert job_id

        msg = bus.get_from_fast_blocking(timeout=2.0)
        assert isinstance(msg, TaskCompleted)
        assert msg.result["success"] is True
        assert msg.result["action"] == "use_tool"
        assert msg.result["needs_followup"] is True
        assert "fake mod completed use_tool" in msg.observation

        exchange = bridge.last_exchange
        assert exchange is not None
        assert exchange.action_id == msg.result["action_id"]
        assert len(exchange.command_envelopes) == 1

        command_envelope = exchange.command_envelopes[0]
        assert command_envelope.WhichOneof("body") == "command_request"
        assert command_envelope.command_request.capability == "use_tool"
        assert command_envelope.command_request.WhichOneof("params") == "use_tool"
        assert command_envelope.command_request.use_tool.target_tile.x == 10
        assert command_envelope.command_request.use_tool.target_tile.y == 11

        result_envelope = exchange.result_envelopes[0]
        assert result_envelope.WhichOneof("body") == "command_result"
        assert result_envelope.command_result.success is True
        assert result_envelope.command_result.capability == "use_tool"
        assert result_envelope.command_result.WhichOneof("data") == "use_tool"
    finally:
        dispatcher.stop()


def test_query_location_returns_protocol_result_as_bridge_result() -> None:
    bridge = FakeProtocolModBridge(
        game_state={"player": {"location": "FarmHouse", "x": 6, "y": 8}}
    )

    action_id = bridge.write_command("query_location", {})
    result = bridge.read_command_result(action_id)

    assert result is not None
    assert result.success is True
    assert result.data["protocol_capability"] == "query_location"
    assert result.data["protocol_data_field"] == "query_location"
    assert result.data["protocol_data"]["location"]["map_token"] == "FarmHouse"

    summary = bridge.protocol_summary()
    assert summary["capabilities"] == ["query_location"]
    assert summary["oneof_params"] == ["query_location"]
    assert summary["results"][0]["status"] == "COMMAND_STATUS_COMPLETED"


def test_say_with_emote_produces_two_protocol_commands() -> None:
    bridge = FakeProtocolModBridge()

    action_id = bridge.write_command("say", {"message": "Hi", "emote": "happy"})
    result = bridge.read_command_result(action_id)

    assert result is not None
    assert result.success is True
    summary = bridge.protocol_summary()
    assert summary["capabilities"] == ["say", "emote"]
    assert summary["oneof_params"] == ["say", "emote"]
    assert len(summary["envelope_bytes"]) == 2


def test_protocol_action_normalization_rejects_unstructured_legacy_target() -> None:
    action, params = _normalize_protocol_action("move_to", {"x": 1, "y": 2})
    assert action == "move_to"
    assert params == {"x": 1, "y": 2, "mode": "exact"}

    action, params = _normalize_protocol_action("go_to", {"target": "Farm:64,15"})
    assert action == "go_to"
    assert params == {"map_id": "Farm", "x": 64, "y": 15, "arrival_mode": "exact"}

    with pytest.raises(ProtocolMappingError, match="structured map/tile"):
        _normalize_protocol_action("go_to", {"target": "Abigail"})


def test_protocol_ws_bridge_logs_ignored_target_player_id(caplog) -> None:
    sent: list[dict] = []
    bridge = WSBridge(
        sender=sent.append,
        target_player_id=42,
    )

    with caplog.at_level("WARNING", logger="bridge.ws_bridge"):
        action_id = bridge.write_command("query_location", {})

    assert action_id
    assert sent[0]["type"] == "protocol_envelope"
    assert "protocol transport ignores target_player_id=42" in caplog.text


def test_default_tool_schema_uses_protocol_core_tools() -> None:
    loop = DeliberativeLoop(
        message_bus=MessageBus(),
        bridge=FakeProtocolModBridge(),
        working_memory=WorkingMemory(),
        config={"slow_loop": {"min_call_interval_s": 0.0}},
    )

    names = {
        schema["function"]["name"]
        for schema in loop._build_tool_schemas(loop._bridge.capabilities())
    }
    assert {
        "say",
        "use_tool",
    }.issubset(names)
    assert {"observe", "search_facts", "inspect", "find_interactions", "search_wiki", "read_wiki", "remember"}.issubset(names)
    assert {"cancel_active_action", "query_action_jobs"}.issubset(names)
    assert {
        "query_runtime_snapshot",
        "query_world_region",
        "query_inventory_snapshot",
        "query_ui_snapshot",
        "query_inspect_refs",
    }.isdisjoint(names)


def test_protocol_tool_schema_ignores_missing_runtime_capabilities() -> None:
    loop = DeliberativeLoop(
        message_bus=MessageBus(),
        bridge=FakeProtocolModBridge(),
        working_memory=WorkingMemory(),
        config={"slow_loop": {"min_call_interval_s": 0.0}},
    )

    names = {
        schema["function"]["name"]
        for schema in loop._build_tool_schemas([])
    }

    assert {
        "say",
        "go_to",
        "observe",
        "search_facts",
        "inspect",
        "find_interactions",
        "search_wiki",
        "read_wiki",
        "remember",
        "cancel_active_action",
        "query_action_jobs",
    }.issubset(names)
    assert {"query_runtime_snapshot", "query_world_region", "query_inspect_refs"}.isdisjoint(names)


def test_deliberative_loop_user_input_to_fake_protocol_mod_completion() -> None:
    bus = MessageBus()
    bridge = FakeProtocolModBridge()
    memory = WorkingMemory()
    response = make_fc_response(
        tool_calls=[
            {
                "name": "use_tool",
                "arguments": {"x": 10, "y": 11, "charge_level": 0},
            }
        ],
        content="I will use the tool.",
    )
    loop = DeliberativeLoop(
        message_bus=bus,
        bridge=bridge,
        working_memory=memory,
        config={
            "slow_loop": {
                "min_call_interval_s": 0.0,
                "player_input_debounce_s": 0.0,
            }
        },
    )
    loop._llm_fn = make_fc_llm_fn([response])

    try:
        loop._process_trigger(PlayerInput(text="water this tile"))
        msg = _wait_for_task_completed(bus)
        assert msg is not None
        assert msg.result["success"] is True
        assert msg.result["action"] == "use_tool"
        assert bridge.last_exchange is not None
        assert bridge.last_exchange.command_envelopes[0].command_request.capability == "use_tool"
    finally:
        loop.stop()


def test_deliberative_loop_can_act_from_l3_find_interactions_result() -> None:
    bus = MessageBus()
    bridge = L3ActionableQueryBridge()
    memory = WorkingMemory()
    first_response = make_fc_response_with_finish(
        tool_calls=[
            {
                "name": "find_interactions",
                "arguments": {"radius": 8, "limit": 5},
            }
        ],
        content="先观察当前可交互对象。",
        finish_reason="tool_calls",
    )
    second_response = make_fc_response_with_finish(
        tool_calls=[
            {
                "name": "move_to",
                "arguments": {"mode": "adjacent", "x": 1, "y": 0, "adjacent_side": "left"},
            },
            {
                "name": "use_tool",
                "arguments": {"x": 1, "y": 0, "charge_level": 0},
            },
        ],
        content="按推荐步骤靠近并浇水。",
        finish_reason="tool_calls",
    )
    loop = DeliberativeLoop(
        message_bus=bus,
        bridge=bridge,
        working_memory=memory,
        config={
            "slow_loop": {
                "min_call_interval_s": 0.0,
                "player_input_debounce_s": 0.0,
            }
        },
    )
    loop._llm_fn = make_fc_llm_fn([first_response, second_response])

    try:
        loop._process_trigger(PlayerInput(text="给眼前没浇水的作物浇水"))
        completed_actions: list[str] = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and {"move_to", "use_tool"} - set(completed_actions):
            msg = bus.get_from_fast_blocking(timeout=0.1)
            if isinstance(msg, TaskCompleted):
                completed_actions.append(msg.result["action"])

        assert {"move_to", "use_tool"}.issubset(completed_actions)
        assert [
            exchange.action
            for exchange in bridge.exchanges
            if exchange.action in {"move_to", "use_tool"}
        ] == ["move_to", "use_tool"]
        move_request = bridge.exchanges[-2].command_envelopes[0].command_request
        use_tool_request = bridge.exchanges[-1].command_envelopes[0].command_request
        assert move_request.capability == "move_to"
        assert move_request.move_to.mode
        assert move_request.move_to.x == 1
        assert move_request.move_to.y == 0
        assert move_request.move_to.adjacent_side
        assert use_tool_request.capability == "use_tool"
        assert use_tool_request.use_tool.target_tile.x == 1
        assert use_tool_request.use_tool.target_tile.y == 0

        tool_messages = [
            message
            for message in loop._session_messages
            if message.get("role") == "tool"
        ]
        assert any("recommended_steps" in message.get("content", "") for message in tool_messages)
    finally:
        loop.stop()


def _wait_for_task_completed(bus: MessageBus, timeout_s: float = 2.0) -> TaskCompleted | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = bus.get_from_fast_blocking(timeout=0.1)
        if isinstance(msg, TaskCompleted):
            return msg
    return None
