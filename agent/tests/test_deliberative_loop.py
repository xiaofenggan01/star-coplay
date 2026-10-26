"""
tests.test_deliberative_loop — DeliberativeLoop 单元测试（FC 模式）。
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import EscalateRequest, PlayerInput, ReportEvent
from slow_loop.deliberative_loop import DeliberativeLoop
from tests.helpers import make_llm_fn
from tests.test_fc_migration import make_fc_llm_fn, make_fc_response


def _make_loop(
    bus: MessageBus,
    bridge: MagicMock,
    memory: WorkingMemory,
    llm_responses=None,
    fc_responses=None,
    min_call_interval: float = 0.0,
    debounce_s: float = 0.5,
) -> DeliberativeLoop:
    config = {
        "slow_loop": {
            "min_call_interval_s": min_call_interval,
            "player_input_debounce_s": debounce_s,
            "max_bt_depth": 3,
            "max_bt_nodes": 15,
        }
    }
    loop = DeliberativeLoop(
        message_bus=bus,
        bridge=bridge,
        working_memory=memory,
        config=config,
    )
    if fc_responses is not None:
        loop._llm_fn = make_fc_llm_fn(fc_responses)
    elif llm_responses is not None:
        loop._llm_fn = make_llm_fn(llm_responses)
    return loop


class TestStartStop:
    def test_start_stop(self, mock_bridge, working_memory):
        bus = MessageBus()
        loop = _make_loop(bus, mock_bridge, working_memory)

        loop.start()
        assert loop._thread is not None
        assert loop._thread.is_alive()

        loop.stop()
        assert loop._thread is None


class TestEscalateTrigger:
    def test_escalate_triggers_planner(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[{"name": "move_to", "arguments": {"location": "Farm"}}],
            content="去农场",
        )
        loop = _make_loop(bus, mock_bridge, working_memory, fc_responses=[resp])

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="不知道做什么", correlation_id="test-esc-001"),
                priority=1,
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if mock_bridge.write_command.called:
                    break
                time.sleep(0.05)
            assert mock_bridge.write_command.called
            names = [c[0][0] for c in mock_bridge.write_command.call_args_list]
            assert "move_to" in names
        finally:
            loop.stop()


class TestPlayerInputDebounce:
    def test_player_input_debounce(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[{"name": "say", "arguments": {"message": "合并后回复"}}],
            content="ok",
        )
        loop = _make_loop(
            bus, mock_bridge, working_memory,
            fc_responses=[resp],
            debounce_s=0.5,
        )

        loop.start()
        try:
            bus.send_to_slow(PlayerInput(text="你好"), priority=5)
            bus.send_to_slow(PlayerInput(text="今天天气好"), priority=5)
            bus.send_to_slow(PlayerInput(text="一起去钓鱼吧"), priority=5)

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if mock_bridge.write_command.called:
                    break
                time.sleep(0.05)
            assert mock_bridge.write_command.called
            names = [c[0][0] for c in mock_bridge.write_command.call_args_list]
            assert "say" in names
        finally:
            loop.stop()


class TestFallback:
    def test_fallback_l1(self, mock_bridge, working_memory):
        bus = MessageBus()
        loop = _make_loop(
            bus, mock_bridge, working_memory,
            llm_responses=["not valid json"] * 4,
        )
        loop._current_bt_json = {"type": "action", "call": "observe"}

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="test", correlation_id="test-f1"),
                priority=1,
            )
            time.sleep(2.0)
            assert bus.try_get_from_slow() is None
            mock_bridge.write_command.assert_not_called()
            assert any(
                event["type"] == "fallback_suppressed"
                for event in working_memory.recent_events(10)
            )
        finally:
            loop.stop()

    def test_fallback_l2(self, mock_bridge, working_memory):
        bus = MessageBus()
        loop = _make_loop(
            bus, mock_bridge, working_memory,
            llm_responses=["not valid json"] * 4,
        )
        assert loop._current_bt_json is None

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="test", correlation_id="test-f2"),
                priority=1,
            )
            time.sleep(2.0)
            assert bus.try_get_from_slow() is None
            mock_bridge.write_command.assert_not_called()
            assert any(
                event["type"] == "fallback_suppressed"
                for event in working_memory.recent_events(10)
            )
        finally:
            loop.stop()


class TestDispatchDialogue:
    def test_dispatch_dialogue(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[
                {"name": "say", "arguments": {"message": "你好呀！"}},
                {"name": "emote", "arguments": {"type": "happy"}},
            ],
            content="打招呼",
        )
        loop = _make_loop(bus, mock_bridge, working_memory, fc_responses=[resp])

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="test", correlation_id="test-dial"),
                priority=1,
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if mock_bridge.write_command.call_count >= 2:
                    break
                time.sleep(0.05)
            names = [c[0][0] for c in mock_bridge.write_command.call_args_list]
            assert "say" in names
            assert "emote" in names
            say_call = next(c for c in mock_bridge.write_command.call_args_list if c[0][0] == "say")
            assert "你好呀" in say_call[0][1].get("message", "")
        finally:
            loop.stop()

    def test_wiki_only_player_input_gets_auto_reply(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[{"name": "search_wiki", "arguments": {"query": "spring crops profit"}}],
            content="查资料",
        )
        loop = _make_loop(bus, mock_bridge, working_memory, fc_responses=[resp], debounce_s=0.0)

        loop._process_trigger(PlayerInput(text="春天种什么赚钱"))

        names = [c[0][0] for c in mock_bridge.write_command.call_args_list]
        assert "say" in names
        say_call = next(c for c in mock_bridge.write_command.call_args_list if c[0][0] == "say")
        assert "查到" in say_call[0][1].get("message", "")


class TestReportEvent:
    def test_report_event_low_significance(self, mock_bridge, working_memory):
        bus = MessageBus()
        loop = _make_loop(bus, mock_bridge, working_memory, llm_responses=[])

        loop.start()
        try:
            bus.send_to_slow(
                ReportEvent(
                    event_type="crop_ready",
                    data={"crop": "南瓜"},
                    significance=3,
                ),
                priority=7,
            )
            time.sleep(2.0)
            assert bus.try_get_from_slow() is None
            assert working_memory.event_count > 0
        finally:
            loop.stop()
