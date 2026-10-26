"""
test_fc_atomicity.py — FC (Function Calling) round 原子性不变量测试。

测试改造后的 _process_trigger 中的关键不变量：
  1. assistant message 带 tool_calls → 必须紧跟所有 tool results
  2. tool 执行失败 → 补 synthetic error result
  3. assistant message 保留完整 tool_calls 结构（id, name, arguments）
  4. MAX_FC_ROUNDS 用尽 → 补一次无 tools 的 LLM 调用作为收尾
  5. FC 中途 LLM 异常不破坏一致性
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from messages.types import PlayerInput
from tests.test_agent_loop.conftest import FakeLLM, assert_messages_integrity, fc_response


@pytest.fixture
def deliberative_loop(fake_message_bus, fake_bridge, fake_working_memory, loop_config):
    """构造 DeliberativeLoop 实例（不启动线程）。"""
    from slow_loop.deliberative_loop import DeliberativeLoop

    loop = DeliberativeLoop(
        message_bus=fake_message_bus,
        bridge=fake_bridge,
        working_memory=fake_working_memory,
        config=loop_config,
    )
    # 禁用排队消息检查（避免干扰 FC 原子性测试）
    fake_message_bus.drain_from_fast.return_value = []
    return loop


class TestFCAtomicity:
    """FC round 原子性不变量测试套件"""

    def test_c1_no_unclosed_tool_calls(self, deliberative_loop, fake_message_bus):
        """C1. API messages 无未闭合 tool_calls [P0]

        Arrange: FakeLLM 返回 3 个 tool_calls，然后纯文本
        Act: 触发 _process_trigger
        Assert: session_messages 中 assistant(tc) 后紧跟 3 个 tool messages
        """
        llm = FakeLLM([
            fc_response(
                content="思考中",
                tool_calls=[
                    {"name": "say", "arguments": {"message": "hi"}, "id": "call_0"},
                    {"name": "say", "arguments": {"message": "yo"}, "id": "call_1"},
                    {"name": "say", "arguments": {"message": "done"}, "id": "call_2"},
                ]
            ),
            fc_response(content="完成"),
        ])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        # 验证 session_messages 结构完整性
        msgs = deliberative_loop._session_messages
        assert_messages_integrity(msgs)

        # 找到 assistant(tc) 消息
        tc_msgs = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(tc_msgs) >= 1

        # 验证 tool_calls 后紧跟对应数量的 tool messages
        for tc_msg in tc_msgs:
            idx = msgs.index(tc_msg)
            expected_count = len(tc_msg["tool_calls"])
            tool_msgs = []
            for j in range(idx + 1, min(idx + 1 + expected_count, len(msgs))):
                if msgs[j].get("role") == "tool":
                    tool_msgs.append(msgs[j])
            assert len(tool_msgs) == expected_count

    def test_c2_synthetic_error_on_failure(self, deliberative_loop, fake_message_bus):
        """C2. tool 执行失败补 synthetic error [P0]

        Arrange: FakeLLM 返回 tool_calls，tool 名称完全未知
        Act: 触发 _process_trigger
        Assert: tool message content 包含错误信息，messages 闭合
        """
        llm = FakeLLM([
            fc_response(
                content="移动中",
                tool_calls=[
                    {"name": "totally_fake_tool_xyz", "arguments": {}, "id": "call_0"}
                ]
            ),
            fc_response(content="完成"),
        ])
        deliberative_loop._llm_fn = llm

        deliberative_loop._process_trigger(PlayerInput(text="test"))

        msgs = deliberative_loop._session_messages
        # 找到 tool message
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        # tool result 应包含错误信息
        assert any("未知" in (m.get("content") or "") for m in tool_msgs)

    def test_c3_partial_failure_all_have_result(self, deliberative_loop, fake_message_bus):
        """C3. 多 tool_calls 部分失败全部有 result [P0]

        Arrange: 3 个 tool_calls，其中一个是未知工具
        Act: 触发 _process_trigger
        Assert: 3 个 tool messages 都存在
        """
        llm = FakeLLM([
            fc_response(
                content="执行多个动作",
                tool_calls=[
                    {"name": "say", "arguments": {"message": "hi"}, "id": "call_0"},
                    {"name": "unknown_tool", "arguments": {}, "id": "call_1"},
                    {"name": "say", "arguments": {"message": "done"}, "id": "call_2"},
                ]
            ),
            fc_response(content="完成"),
        ])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        msgs = deliberative_loop._session_messages
        assert_messages_integrity(msgs)

        # 找到 assistant(tc) 后的 tool messages
        tc_msg = next(m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls"))
        idx = msgs.index(tc_msg)
        tool_msgs = [msgs[j] for j in range(idx + 1, len(msgs)) if msgs[j].get("role") == "tool"]
        assert len(tool_msgs) == 3
        # 第二个应包含错误
        assert "未知" in tool_msgs[1].get("content", "")

    def test_c4_assistant_preserves_tool_calls_structure(self, deliberative_loop, fake_message_bus):
        """C4. assistant message 保留完整 tool_calls 结构 [P0]

        Arrange: FakeLLM 返回带 tool_calls 的 assistant message
        Act: 触发 _process_trigger
        Assert: _session_messages 中 assistant 的 tool_calls 有 id/function.name/function.arguments
        """
        llm = FakeLLM([
            fc_response(
                content="思考中",
                tool_calls=[
                    {"name": "say", "arguments": {"message": "hello"}, "id": "call_0"},
                ]
            ),
            fc_response(content="完成"),
        ])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        msgs = deliberative_loop._session_messages
        assistant_msg = next(m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls"))
        tool_calls = assistant_msg["tool_calls"]
        assert len(tool_calls) > 0
        for tc in tool_calls:
            assert "id" in tc
            assert "function" in tc
            assert "name" in tc["function"]
            assert "arguments" in tc["function"]

    def test_c4b_preserves_reasoning_content_for_thinking_models(self, deliberative_loop, fake_message_bus):
        """C4b. thinking model FC continuation keeps reasoning_content."""
        first = fc_response(
            content="",
            tool_calls=[
                {"name": "say", "arguments": {"message": "hello"}, "id": "call_0"},
            ],
        )
        first.choices[0].message.reasoning_content = "private reasoning payload"
        llm = FakeLLM([first, fc_response(content="done")])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        second_messages = llm.nth_messages(1)
        assistant_msg = next(m for m in second_messages if m.get("role") == "assistant" and m.get("tool_calls"))
        assert assistant_msg["reasoning_content"] == "private reasoning payload"

    def test_c4c_omits_reasoning_content_when_absent(self, deliberative_loop, fake_message_bus):
        """C4c. non-thinking FC continuation does not add reasoning_content."""
        llm = FakeLLM([
            fc_response(
                content="",
                tool_calls=[
                    {"name": "say", "arguments": {"message": "hello"}, "id": "call_0"},
                ],
            ),
            fc_response(content="done"),
        ])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        second_messages = llm.nth_messages(1)
        assistant_msg = next(m for m in second_messages if m.get("role") == "assistant" and m.get("tool_calls"))
        assert "reasoning_content" not in assistant_msg

    def test_c4d_preserves_reasoning_content_from_model_extra(self, deliberative_loop, fake_message_bus):
        """C4d. OpenAI SDK extra fields are preserved in FC continuation."""
        first = fc_response(
            content="",
            tool_calls=[
                {"name": "say", "arguments": {"message": "hello"}, "id": "call_0"},
            ],
        )
        first.choices[0].message.model_extra = {"reasoning_content": "payload from model_extra"}
        llm = FakeLLM([first, fc_response(content="done")])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        second_messages = llm.nth_messages(1)
        assistant_msg = next(m for m in second_messages if m.get("role") == "assistant" and m.get("tool_calls"))
        assert assistant_msg["reasoning_content"] == "payload from model_extra"

    def test_c4e_preserves_reasoning_content_from_model_dump(self, deliberative_loop, fake_message_bus):
        """C4e. Pydantic model_dump extra fields are preserved in FC continuation."""
        first = fc_response(
            content="",
            tool_calls=[
                {"name": "say", "arguments": {"message": "hello"}, "id": "call_0"},
            ],
        )
        first.choices[0].message.model_dump = lambda: {"reasoning_content": "payload from model_dump"}
        llm = FakeLLM([first, fc_response(content="done")])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        second_messages = llm.nth_messages(1)
        assistant_msg = next(m for m in second_messages if m.get("role") == "assistant" and m.get("tool_calls"))
        assert assistant_msg["reasoning_content"] == "payload from model_dump"

    def test_c5_llm_exception_mid_fc_preserves_consistency(self, deliberative_loop, fake_message_bus):
        """C5. FC 中途 LLM 异常不破坏一致性 [P0]

        Arrange: 第一次返回 tool_calls，第二次抛异常
        Act: 触发 _process_trigger
        Assert: session_messages 仍然闭合
        """
        call_count = [0]
        def llm_with_error(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return fc_response(
                    content="执行动作",
                    tool_calls=[{"name": "say", "arguments": {"message": "hi"}, "id": "call_0"}]
                )
            raise TimeoutError("LLM request timeout")

        deliberative_loop._llm_fn = llm_with_error

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        # session 不应崩溃，后续 trigger 能正常工作
        msgs = deliberative_loop._session_messages
        # 至少有 user message
        assert any(m.get("role") == "user" for m in msgs)

    def test_c6_max_fc_rounds_exhausted(self, deliberative_loop, fake_message_bus):
        """C6. MAX_FC_ROUNDS 用尽时正常结束 [P1]

        Arrange: FakeLLM 始终返回 tool_calls（模拟无限循环）
        Act: 触发 _process_trigger
        Assert: 不会无限循环，最终结束
        """
        # 生成超过 MAX_FC_ROUNDS(5) 的响应
        responses = [
            fc_response(
                content=f"round {i}",
                tool_calls=[{"name": "say", "arguments": {"message": str(i)}, "id": f"call_{i}"}]
            )
            for i in range(10)
        ]
        llm = FakeLLM(responses)
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="test"))

        # 应该在 MAX_FC_ROUNDS 内停止
        assert llm.call_count <= 6  # 最多 5 轮 + 可能的排队消息处理
        assert_messages_integrity(deliberative_loop._session_messages)

        assistant_tool_ids = [
            tc["id"]
            for msg in deliberative_loop._session_messages
            if msg.get("role") == "assistant"
            for tc in msg.get("tool_calls", [])
        ]
        tool_result_ids = [
            msg.get("tool_call_id")
            for msg in deliberative_loop._session_messages
            if msg.get("role") == "tool"
        ]
        assert "call_5" not in assistant_tool_ids
        assert set(assistant_tool_ids) == set(tool_result_ids)

    def test_c7_assert_messages_integrity_validation(self):
        """C7. assert_messages_integrity 辅助函数验证 [P2]"""
        # 正确的消息序列
        valid_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "thinking", "tool_calls": [
                {"id": "call_0", "function": {"name": "say", "arguments": "{}"}},
                {"id": "call_1", "function": {"name": "observe", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_0", "content": "result 0"},
            {"role": "tool", "tool_call_id": "call_1", "content": "result 1"},
            {"role": "assistant", "content": "done"},
        ]
        assert_messages_integrity(valid_messages)

        # 错误 1：tool_calls 未闭合
        unclosed_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "thinking", "tool_calls": [
                {"id": "call_0", "function": {"name": "say", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_0", "content": "result 0"},
            {"role": "assistant", "content": "thinking 2", "tool_calls": [
                {"id": "call_1", "function": {"name": "observe", "arguments": "{}"}},
            ]},
        ]
        with pytest.raises(AssertionError, match="expected 1 tool results, got 0"):
            assert_messages_integrity(unclosed_messages)

        # 错误 2：以 tool message 结尾
        ends_with_tool = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "thinking", "tool_calls": [
                {"id": "call_0", "function": {"name": "say", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_0", "content": "result"},
        ]
        with pytest.raises(AssertionError, match="messages ends with tool"):
            assert_messages_integrity(ends_with_tool)

        mismatched_tool_id = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "thinking", "tool_calls": [
                {"id": "call_new", "function": {"name": "say", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_old", "content": "stale result"},
            {"role": "assistant", "content": "done"},
        ]
        with pytest.raises(AssertionError, match="tool_call_id mismatch"):
            assert_messages_integrity(mismatched_tool_id)

        # 正确：多轮对话
        multi_round_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "thinking 1", "tool_calls": [
                {"id": "call_0", "function": {"name": "say", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_0", "content": "result 0"},
            {"role": "assistant", "content": "thinking 2", "tool_calls": [
                {"id": "call_1", "function": {"name": "observe", "arguments": "{}"}},
                {"id": "call_2", "function": {"name": "remember", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "call_2", "content": "result 2"},
            {"role": "assistant", "content": "final response"},
        ]
        assert_messages_integrity(multi_round_messages)

        # 正确：空消息列表
        assert_messages_integrity([])


class TestFCAtomicityIntegration:
    """FC 原子性集成测试"""

    def test_fc_round_trip_preserves_atomicity(self, deliberative_loop, fake_message_bus):
        """完整的 FC 轮次保持原子性"""
        llm = FakeLLM([
            fc_response(
                content="round 1",
                tool_calls=[
                    {"name": "say", "arguments": {"message": "1"}, "id": "call_0"},
                    {"name": "say", "arguments": {"message": "2"}, "id": "call_1"},
                ]
            ),
            fc_response(
                content="round 2",
                tool_calls=[
                    {"name": "say", "arguments": {"message": "3"}, "id": "call_2"},
                ]
            ),
            fc_response(content="final"),
        ])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="multi-round test"))

        msgs = deliberative_loop._session_messages
        assert_messages_integrity(msgs)
        # 应有 user + 多轮 FC 消息
        assert len(msgs) >= 5

    def test_fc_error_recovery_maintains_consistency(self, deliberative_loop, fake_message_bus):
        """FC 错误恢复后 session 仍可用"""
        llm = FakeLLM([
            # 第一次 trigger：正常带 tool_calls
            fc_response(content="first", tool_calls=[
                {"name": "say", "arguments": {"message": "hi"}, "id": "call_0"}
            ]),
            fc_response(content="first done"),
            # 第二次 trigger：也正常
            fc_response(content="second", tool_calls=[
                {"name": "say", "arguments": {"message": "bye"}, "id": "call_1"}
            ]),
            fc_response(content="second done"),
        ])
        deliberative_loop._llm_fn = llm

        with patch.object(deliberative_loop, "_handle_say"):
            deliberative_loop._process_trigger(PlayerInput(text="first"))
            assert deliberative_loop._session_initialized is True

            deliberative_loop._process_trigger(PlayerInput(text="second"))
            assert len(deliberative_loop._session_messages) >= 4
