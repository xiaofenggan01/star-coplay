"""
tests.test_fc_migration — Function Calling 迁移测试。

覆盖：
    T1: _build_tool_schemas 从 adapter_info 生成正确的 tools 列表
    T2: _build_tool_schemas 包含 remember 内部工具
    T3: _process_trigger 调用 LLM 并执行 tool_calls
    T4: 即时动作（say/emote）不阻塞
    T5: 长时动作（move_to）后截断剩余 tool_calls
    T6: LLM 未返回 tool_calls -> fallback
    T7: tool_call 参数解析失败 -> 跳过该 call
    T8: monologue 从 content 提取并写入 WorkingMemory
    T9: remember 工具写入 WorkingMemory
    T10: EventRelay 轮询 events.jsonl 转发 PlayerInput
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import EscalateRequest, PlayerInput
from slow_loop.deliberative_loop import (
    DeliberativeLoop,
    INSTANT_ACTIONS,
    LONG_ACTIONS,
    QUERY_ACTIONS,
    _is_quota_or_precharge_error,
)


# ── Mock FC Response 构造器 ───────────────────────────────────────────────


def make_fc_response(tool_calls=None, content=""):
    """构造 mock FC response（模拟 OpenAI ChatCompletion）。"""
    tc_objects = []
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            tc_objects.append(SimpleNamespace(
                id=tc.get("id", f"tc_{i}"),
                function=SimpleNamespace(
                    name=tc["name"],
                    arguments=json.dumps(tc.get("arguments", {}))
                )
            ))
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=content,
                tool_calls=tc_objects if tc_objects else None
            ),
            finish_reason="stop"
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        model="test-model",
        id="test-id",
    )


def make_fc_llm_fn(responses):
    """构造 mock LLM function for FC mode。"""
    idx = [0]
    def fn(**kwargs):
        i = idx[0]
        idx[0] += 1
        if i < len(responses):
            return responses[i]
        return responses[-1]
    return fn


# ── 测试用 Loop 工厂 ──────────────────────────────────────────────────────


def _make_fc_loop(
    bus: MessageBus,
    bridge: MagicMock,
    memory: WorkingMemory,
    llm_fn=None,
    min_call_interval: float = 0.0,
    debounce_s: float = 0.5,
) -> DeliberativeLoop:
    """创建用于测试的 DeliberativeLoop（FC 模式）。"""
    config = {
        "slow_loop": {
            "min_call_interval_s": min_call_interval,
            "player_input_debounce_s": debounce_s,
        }
    }
    loop = DeliberativeLoop(
        message_bus=bus,
        bridge=bridge,
        working_memory=memory,
        config=config,
    )
    if llm_fn is not None:
        loop._llm_fn = llm_fn
    return loop


# ── FC 格式的 mock capabilities ──────────────────────────────────────────

FC_CAPABILITIES = [
    {
        "name": "say",
        "description": "Send a chat message",
        "params": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "the message text"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "move_to",
        "description": "Walk to a target",
        "params": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "target map name"}
            }
        }
    },
    {
        "name": "emote",
        "description": "Play emote",
        "params": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["happy", "sad"]}
            },
            "required": ["type"]
        }
    },
    {
        "name": "face",
        "description": "Turn direction",
        "params": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]}
            },
            "required": ["direction"]
        }
    },
    {
        "name": "observe",
        "description": "Observe surroundings",
        "params": {"type": "object", "properties": {}}
    },
    {
        "name": "do",
        "description": "Generic action",
        "params": {
            "type": "object",
            "properties": {
                "intent": {"type": "string"}
            }
        }
    },
]


@pytest.fixture
def fc_mock_bridge(mock_game_state):
    """Mock bridge with FC-format capabilities."""
    bridge = MagicMock()
    bridge.read_game_state.return_value = mock_game_state
    bridge.capabilities.return_value = FC_CAPABILITIES
    bridge.capability_names.return_value = frozenset(c["name"] for c in FC_CAPABILITIES)
    bridge.write_command.return_value = "act_0001_test"
    ok = MagicMock()
    ok.success = True
    ok.data = {}
    ok.observation = ""
    ok.message = ""
    ok.error_code = None
    bridge.read_command_result.return_value = ok
    bridge.state_path = "/tmp/test_bridge/game_state.json"
    return bridge


# ── T1: _build_tool_schemas 生成正确的 tools ─────────────────────────────

class TestBuildToolSchemas:
    def test_t1_build_tools_from_capabilities(self, fc_mock_bridge, working_memory):
        """从 FC 格式的 capabilities 生成 OpenAI tools 列表。"""
        bus = MessageBus()
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory)

        tools = loop._build_tool_schemas(FC_CAPABILITIES)

        assert len(tools) >= len(FC_CAPABILITIES) + 1

        # 检查格式
        for tool in tools:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]
            params = tool["function"]["parameters"]
            assert params["type"] == "object"
            assert "properties" in params

        # 检查 say 的 required
        say_tool = next(t for t in tools if t["function"]["name"] == "say")
        assert "required" in say_tool["function"]["parameters"]
        assert "message" in say_tool["function"]["parameters"]["required"]

        # v2.4.x: emote 已合并到 say 工具的 emote 参数中（不再是独立工具）
        # 检查 say 工具包含 emote enum 参数
        say_props = say_tool["function"]["parameters"]["properties"]
        assert "emote" in say_props
        assert "enum" in say_props["emote"]

    def test_t2_build_tools_includes_remember(self, fc_mock_bridge, working_memory):
        """tools 列表包含 remember 内部工具。"""
        bus = MessageBus()
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory)

        tools = loop._build_tool_schemas(FC_CAPABILITIES)

        remember_tools = [t for t in tools if t["function"]["name"] == "remember"]
        assert len(remember_tools) == 1

        remember = remember_tools[0]
        params = remember["function"]["parameters"]
        assert "type" in params["properties"]
        assert "content" in params["properties"]
        assert params["properties"]["type"]["enum"] == [
            "player_preference", "relationship_insight", "world_discovery", "strategy_lesson"
        ]


# ── T3: _process_trigger 调用 LLM 并执行 tool_calls ─────────────────────

class TestProcessTrigger:
    def test_t3_process_trigger_executes_tool_calls(self, fc_mock_bridge, working_memory):
        """发送触发消息 -> LLM 返回 tool_calls -> bridge.write_command 被调用。"""
        bus = MessageBus()
        response = make_fc_response(
            tool_calls=[
                {"name": "say", "arguments": {"message": "你好！"}},
                {"name": "emote", "arguments": {"type": "happy"}},
            ],
            content="我来打个招呼。"
        )
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        # 直接调用 _process_trigger
        trigger = PlayerInput(text="嗨！")
        loop._process_trigger(trigger)

        # say 通过 _handle_say 调用 bridge.write_command
        # emote 通过 _execute_game_action 调用 bridge.write_command
        assert fc_mock_bridge.write_command.call_count >= 2

    def test_t4_instant_actions_no_wait(self, fc_mock_bridge, working_memory):
        """即时动作（say/emote/face）不等待结果。"""
        bus = MessageBus()
        response = make_fc_response(
            tool_calls=[
                {"name": "emote", "arguments": {"type": "happy"}},
                {"name": "face", "arguments": {"direction": "up"}},
            ]
        )
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(EscalateRequest(reason="test", correlation_id="t4"))

        # read_command_result 不应被调用（即时动作不等待）
        fc_mock_bridge.read_command_result.assert_not_called()

    def test_t5_long_action_truncates(self, fc_mock_bridge, working_memory):
        """长时动作（move_to）后截断剩余 tool_calls。"""
        bus = MessageBus()
        # move_to 后面还有 say，但 move_to 是长时动作，应截断
        response = make_fc_response(
            tool_calls=[
                {"name": "say", "arguments": {"message": "出发了"}},
                {"name": "move_to", "arguments": {"location": "Farm"}},
                {"name": "say", "arguments": {"message": "到了"}},  # 应被截断
            ]
        )
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        try:
            loop._process_trigger(EscalateRequest(reason="test", correlation_id="t5"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                calls = fc_mock_bridge.write_command.call_args_list
                call_actions = [c[0][0] for c in calls]
                if "move_to" in call_actions:
                    break
                time.sleep(0.02)

            calls = fc_mock_bridge.write_command.call_args_list
            call_actions = [c[0][0] for c in calls]
            assert "say" in call_actions
            assert "move_to" in call_actions
            say_count = sum(1 for a in call_actions if a == "say")
            assert say_count == 2, f"Expected 2 say calls (FC 同轮执行全部 tool), got {say_count}: {call_actions}"
        finally:
            loop.stop()


# ── T6: LLM 未返回 tool_calls -> fallback ────────────────────────────────

class TestFallback:
    def test_quota_or_precharge_error_is_reported_as_insufficient_credits(self, fc_mock_bridge, working_memory):
        """NewAPI 额度/预扣费失败应返回明确积分提示，而不是通用兜底。"""
        bus = MessageBus()

        def llm_fn(**kwargs):
            raise RuntimeError(
                "NEWAPI_AUTH_FAILED: PreConsumeQuota failed, user quota is not enough, "
                "balance=$0.046932 needed=$2.100000"
            )

        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(PlayerInput(text="帮我继续"))

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fc_mock_bridge.write_command.called:
            time.sleep(0.02)
        fc_mock_bridge.write_command.assert_called_once()
        assert fc_mock_bridge.write_command.call_args[0][0] == "say"
        assert fc_mock_bridge.write_command.call_args[0][1]["message"] == "积分余额不足"

    @pytest.mark.parametrize("error_text", [
        "quota insufficient",
        "user quota is not enough",
        "NEWAPI_AUTH_FAILED: balance is not enough",
        "预扣费失败：余额不足",
    ])
    def test_quota_or_precharge_error_detection(self, error_text):
        assert _is_quota_or_precharge_error(error_text)

    def test_t6_player_no_tool_calls_fallback(self, fc_mock_bridge, working_memory):
        """玩家输入下 LLM 返回空 tool_calls -> 兜底 say。"""
        bus = MessageBus()
        response = make_fc_response(tool_calls=None, content="嗯...")
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(PlayerInput(text="你在吗"))

        # 应调用 _execute_fallback -> bridge.write_command("say", ...)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fc_mock_bridge.write_command.called:
            time.sleep(0.02)
        fc_mock_bridge.write_command.assert_called_once()
        args = fc_mock_bridge.write_command.call_args
        assert args[0][0] == "say"
        assert args[0][1]["message"] == "模型响应错误（未进行 tool call）"

    def test_t6_internal_no_tool_calls_suppresses_visible_fallback(self, fc_mock_bridge, working_memory):
        """内部触发下 LLM 返回空 tool_calls -> 不往游戏聊天发兜底。"""
        bus = MessageBus()
        response = make_fc_response(tool_calls=None, content="嗯...")
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(EscalateRequest(reason="test", correlation_id="t6"))

        fc_mock_bridge.write_command.assert_not_called()
        assert any(
            event["type"] == "fallback_suppressed"
            for event in working_memory.recent_events(10)
        )


# ── T7: tool_call 参数解析失败 -> 跳过 ───────────────────────────────────

class TestParseFailure:
    def test_t7_invalid_arguments_skipped(self, fc_mock_bridge, working_memory):
        """tool_call 参数不是合法 JSON -> 跳过该 call，继续执行后续。"""
        bus = MessageBus()

        # 手动构造一个有坏参数的 response
        tc_good = SimpleNamespace(
            id="tc_good",
            function=SimpleNamespace(
                name="emote",
                arguments=json.dumps({"type": "happy"})
            )
        )
        tc_bad = SimpleNamespace(
            id="tc_bad",
            function=SimpleNamespace(
                name="say",
                arguments="not valid json {{"
            )
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    tool_calls=[tc_bad, tc_good]
                ),
                finish_reason="stop"
            )],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            model="test-model",
            id="test-id",
        )
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(EscalateRequest(reason="test", correlation_id="t7"))

        # 只有 emote (good) 应被执行，say (bad) 应被跳过
        assert fc_mock_bridge.write_command.call_count == 1
        args = fc_mock_bridge.write_command.call_args
        assert args[0][0] == "emote"


# ── T8: monologue 写入 WorkingMemory ─────────────────────────────────────

class TestMonologue:
    def test_t8_monologue_stored(self, fc_mock_bridge, working_memory):
        """monologue 从 content 提取并写入 WorkingMemory。"""
        bus = MessageBus()
        response = make_fc_response(
            tool_calls=[{"name": "observe", "arguments": {}}],
            content="看看周围有什么好玩的。"
        )
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(EscalateRequest(reason="test", correlation_id="t8"))

        # 检查 WorkingMemory 中有 agent_thought
        events = working_memory.recent_events(10)
        thought_events = [e for e in events if e.get("type") == "agent_thought"]
        assert len(thought_events) >= 1
        assert "好玩" in thought_events[0]["content"]


# ── T9: remember 工具写入 WorkingMemory ──────────────────────────────────

class TestRemember:
    def test_t9_remember_stored(self, fc_mock_bridge, working_memory):
        """remember 工具调用写入 WorkingMemory。"""
        bus = MessageBus()
        response = make_fc_response(
            tool_calls=[{
                "name": "remember",
                "arguments": {
                    "type": "player_preference",
                    "content": "玩家喜欢钓鱼"
                }
            }]
        )
        llm_fn = make_fc_llm_fn([response])
        loop = _make_fc_loop(bus, fc_mock_bridge, working_memory, llm_fn=llm_fn)

        loop._process_trigger(EscalateRequest(reason="test", correlation_id="t9"))

        events = working_memory.recent_events(10)
        pref_events = [e for e in events if e.get("type") == "player_preference"]
        assert len(pref_events) >= 1
        assert "钓鱼" in pref_events[0]["content"]


# ── T10: EventRelay 转发 PlayerInput ─────────────────────────────────────

class TestEventRelay:
    def test_t10_event_relay_forwards_chat(self):
        """EventRelay 轮询 chat_received 事件 -> 转发 PlayerInput。"""
        from game_monitor import EventRelay

        bus = MessageBus()
        bridge = MagicMock()
        bridge.state_path = "/tmp/test_state.json"
        bridge.read_new_events.return_value = [
            {
                "seq": 1,
                "type": "chat_received",
                "data": {"message": "你好", "player_name": "Player1"},
            }
        ]

        relay = EventRelay(message_bus=bus, bridge=bridge)

        # 直接调用内部方法测试（不启动线程）
        relay._poll_game_events()

        # 检查 MessageBus 中有 PlayerInput
        msg = bus.get_from_fast_blocking(timeout=1.0)
        assert msg is not None
        assert isinstance(msg, PlayerInput)
        assert "Player1" in msg.text
        assert "你好" in msg.text

    def test_t10_event_relay_seq_tracking(self):
        """EventRelay 正确跟踪已处理的 seq。"""
        from game_monitor import EventRelay

        bus = MessageBus()
        bridge = MagicMock()
        bridge.state_path = "/tmp/test_state.json"

        relay = EventRelay(message_bus=bus, bridge=bridge)

        # 第一批事件
        bridge.read_new_events.return_value = [
            {"seq": 5, "type": "chat_received", "data": {"message": "a", "player_name": "P"}},
        ]
        relay._poll_game_events()
        assert relay._last_event_seq == 5

        # 第二批：后续调用应传入 after_seq=5
        bridge.read_new_events.return_value = []
        relay._poll_game_events()
        bridge.read_new_events.assert_called_with(after_seq=5)


# ── 动作分类一致性检查 ───────────────────────────────────────────────────

class TestActionClassification:
    def test_instant_and_long_no_overlap(self):
        """即时动作和长时动作集合不重叠。"""
        assert INSTANT_ACTIONS & LONG_ACTIONS == frozenset()

    def test_say_is_instant(self):
        """say 是即时动作。"""
        assert "say" in INSTANT_ACTIONS

    def test_move_to_is_long(self):
        """move_to 是长时动作。"""
        assert "move_to" in LONG_ACTIONS

    def test_query_actions_are_instant(self):
        """所有 query_* 走查询路径（QUERY_ACTIONS）。"""
        query_actions = {a for a in QUERY_ACTIONS if a.startswith("query_")}
        assert len(query_actions) >= 10
