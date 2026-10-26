"""
tests.test_action_plan — action_plan 迁移相关的单元测试。

覆盖：
  - Planner: action_plan 格式解析、校验、重试、白名单
  - DeliberativeLoop: action_plan → BT 转换、dialogue 去重、monologue 记录
  - BtValidator: validate_action_plan 函数
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from slow_loop.planner import Planner
from slow_loop.bt_validator import validate_action_plan
from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import EscalateRequest
from slow_loop.deliberative_loop import DeliberativeLoop
from tests.helpers import make_llm_fn
from tests.test_fc_migration import make_fc_llm_fn, make_fc_response


# ── 测试常量 ──────────────────────────────────────────────────────────────

VALID_ACTION_PLAN_SIMPLE = {
    "monologue": "我去农场找玩家。",
    "action_plan": [
        {"action": "move_to", "params": {"location": "Farm"}},
    ],
}

VALID_ACTION_PLAN_MULTI = {
    "monologue": "先说话再走。",
    "action_plan": [
        {"action": "say", "params": {"message": "马上到！"}},
        {"action": "move_to", "params": {"location": "Farm"}},
        {"action": "emote", "params": {"type": "happy"}},
    ],
}

VALID_ACTION_PLAN_EMPTY = {
    "monologue": "暂时不需要行动，等等看。",
    "action_plan": [],
}

VALID_ACTION_PLAN_WITH_DIALOGUE = {
    "monologue": "玩家打招呼了。",
    "dialogue": {"content": "你好呀！", "tone": "casual"},
    "action_plan": [
        {"action": "say", "params": {"message": "你好呀！"}},
        {"action": "emote", "params": {"type": "happy"}},
    ],
}

VALID_ACTION_PLAN_DIALOGUE_NO_SAY = {
    "monologue": "跟玩家聊聊天。",
    "dialogue": {"content": "今天天气真好！", "tone": "casual"},
    "action_plan": [
        {"action": "move_to", "params": {"location": "Farm"}},
    ],
}

MESSAGES_STUB = [
    {"role": "system", "content": "你是搭子。"},
    {"role": "user", "content": "请做计划。"},
]


# ═══════════════════════════════════════════════════════════════════════════
# Part 1: validate_action_plan (bt_validator.py)
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateActionPlan:
    """V1-V4: validate_action_plan 函数测试。"""

    def test_v1_valid_plan(self):
        """V1: 合法 action_plan → (True, "")。"""
        actions = [
            {"action": "move_to", "params": {"location": "Farm"}},
            {"action": "say", "params": {"message": "hi"}},
        ]
        ok, err = validate_action_plan(actions, frozenset({"move_to", "say"}))
        assert ok is True
        assert err == ""

    def test_v2_unknown_capability(self):
        """V2: 未知能力 → (False, 含 '未知能力')。"""
        actions = [{"action": "fly", "params": {}}]
        ok, err = validate_action_plan(actions, frozenset({"move_to", "say"}))
        assert ok is False
        assert "fly" in err

    def test_v3_missing_action_field(self):
        """V3: 缺少 action 字段 → (False, 含 'action')。"""
        actions = [{"params": {"location": "Farm"}}]
        ok, err = validate_action_plan(actions, frozenset({"move_to"}))
        assert ok is False
        assert "action" in err

    def test_v4_empty_list(self):
        """V4: 空列表 → (True, "")。"""
        ok, err = validate_action_plan([], frozenset({"move_to"}))
        assert ok is True

    def test_no_whitelist(self):
        """无白名单时不校验能力名。"""
        actions = [{"action": "anything", "params": {}}]
        ok, err = validate_action_plan(actions, None)
        assert ok is True


# ═══════════════════════════════════════════════════════════════════════════
# Part 2: Planner + action_plan (planner.py)
# ═══════════════════════════════════════════════════════════════════════════


class TestPlannerActionPlan:
    """P1-P10: Planner action_plan 测试。"""

    def test_p1_valid_simple(self):
        """P1: 简单合法 action_plan → 返回 dict。"""
        llm_fn = make_llm_fn([json.dumps(VALID_ACTION_PLAN_SIMPLE)])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None
        assert result["monologue"] == VALID_ACTION_PLAN_SIMPLE["monologue"]
        assert "action_plan" in result
        assert len(result["action_plan"]) == 1

    def test_p2_multi_actions(self):
        """P2: 多动作 action_plan → 返回 dict，长度 3。"""
        llm_fn = make_llm_fn([json.dumps(VALID_ACTION_PLAN_MULTI)])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None
        assert len(result["action_plan"]) == 3

    def test_p3_empty_action_plan(self):
        """P3: 空 action_plan → 返回 dict（允许空计划）。"""
        llm_fn = make_llm_fn([json.dumps(VALID_ACTION_PLAN_EMPTY)])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None
        assert result["action_plan"] == []

    def test_p4_missing_monologue(self):
        """P4: 缺少 monologue → 返回 None。"""
        bad = {"action_plan": [{"action": "move_to", "params": {}}]}
        llm_fn = make_llm_fn([json.dumps(bad)] * 2)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is None

    def test_p5_missing_action_plan(self):
        """P5: 缺少 action_plan → 返回 None。"""
        bad = {"monologue": "想想"}
        llm_fn = make_llm_fn([json.dumps(bad)] * 2)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is None

    def test_p6_unknown_action(self):
        """P6: action 不在白名单 → 返回 None。"""
        bad = {
            "monologue": "飞吧",
            "action_plan": [{"action": "fly", "params": {}}],
        }
        llm_fn = make_llm_fn([json.dumps(bad)] * 2)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(
            MESSAGES_STUB,
            allowed_capabilities=frozenset({"move_to", "say"}),
        )
        assert result is None

    def test_p7_action_plan_not_list(self):
        """P7: action_plan 非数组 → 返回 None。"""
        bad = {"monologue": "走", "action_plan": "go"}
        llm_fn = make_llm_fn([json.dumps(bad)] * 2)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is None

    def test_p8_retry_success(self):
        """P8: 首次失败，重试成功。"""
        responses = [
            "not json {{{",
            json.dumps(VALID_ACTION_PLAN_SIMPLE),
        ]
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None

    def test_p9_all_fail(self):
        """P9: 两次都失败 → 返回 None。"""
        llm_fn = make_llm_fn(["bad1", "bad2"])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is None

    def test_p10_custom_capabilities(self):
        """P10: 自定义白名单含 fish → 通过。"""
        gsp = {
            "monologue": "去钓鱼",
            "action_plan": [{"action": "fish", "params": {}}],
        }
        llm_fn = make_llm_fn([json.dumps(gsp)])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(
            MESSAGES_STUB,
            allowed_capabilities=frozenset({"fish", "move_to"}),
        )
        assert result is not None
        assert result["action_plan"][0]["action"] == "fish"


# ═══════════════════════════════════════════════════════════════════════════
# Part 3: DeliberativeLoop dispatch (deliberative_loop.py)
# ═══════════════════════════════════════════════════════════════════════════


def _make_loop(
    bus: MessageBus,
    bridge: MagicMock,
    memory: WorkingMemory,
    llm_responses: list[str] | None = None,
    fc_responses=None,
) -> DeliberativeLoop:
    config = {
        "slow_loop": {
            "min_call_interval_s": 0.0,
            "player_input_debounce_s": 0.5,
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


class TestActionPlanDispatch:
    """D1-D5: DeliberativeLoop FC 工具执行。"""

    def test_d1_action_plan_to_replace_bt(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[
                {"name": "say", "arguments": {"message": "马上到！"}},
                {"name": "move_to", "arguments": {"location": "Farm"}},
                {"name": "emote", "arguments": {"type": "happy"}},
            ],
            content="先说话再走",
        )
        loop = _make_loop(bus, mock_bridge, working_memory, fc_responses=[resp])

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="test", correlation_id="test-d1"),
                priority=1,
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if mock_bridge.write_command.call_count >= 3:
                    break
                time.sleep(0.05)
            names = [c[0][0] for c in mock_bridge.write_command.call_args_list]
            assert names.count("say") >= 1
            assert "move_to" in names
            assert "emote" in names
        finally:
            loop.stop()

    def test_d2_dialogue_dedup_with_say(self, mock_bridge, working_memory):
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
                EscalateRequest(reason="test", correlation_id="test-d2"),
                priority=1,
            )
            time.sleep(2.0)
            say_msgs = [
                c[0][1].get("message", "")
                for c in mock_bridge.write_command.call_args_list
                if c[0][0] == "say"
            ]
            assert len(say_msgs) == 1
            assert "你好呀" in say_msgs[0]
        finally:
            loop.stop()

    def test_d3_dialogue_no_dedup_without_say(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[
                {"name": "move_to", "arguments": {"location": "Farm"}},
            ],
            content="先走过去",
        )
        loop = _make_loop(bus, mock_bridge, working_memory, fc_responses=[resp])

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="test", correlation_id="test-d3"),
                priority=1,
            )
            time.sleep(2.0)
            names = [c[0][0] for c in mock_bridge.write_command.call_args_list]
            assert "move_to" in names
            assert all(c[0][0] != "say" for c in mock_bridge.write_command.call_args_list)
        finally:
            loop.stop()

    def test_d5_empty_action_plan(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(tool_calls=[], content="等等看")
        loop = _make_loop(bus, mock_bridge, working_memory, fc_responses=[resp])

        loop.start()
        try:
            bus.send_to_slow(
                EscalateRequest(reason="test", correlation_id="test-d5"),
                priority=1,
            )
            time.sleep(2.0)
            assert bus.try_get_from_slow() is None
            mock_bridge.write_command.assert_called()
        finally:
            loop.stop()
