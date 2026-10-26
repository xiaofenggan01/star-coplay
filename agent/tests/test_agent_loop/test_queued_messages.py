"""
测试 LLM 工作期间排队消息的处理。

改造后的行为：
- _drain_queued_messages() -> list[str]：从 MessageBus 提取排队的用户消息
- FC 循环结束后检查排队消息
- 排队消息合并为一个新 trigger，递归调用 _process_trigger
- 合并格式：前缀 + 排队消息列表
"""

import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from typing import Any

from messages.types import PlayerInput
from tests.test_agent_loop.conftest import FakeLLM, fc_response


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
    loop._llm_fn = FakeLLM([fc_response(content="default")])
    return loop


class TestQueuedMessagesProcessing:
    """排队消息处理测试套件"""

    def test_e1_queued_messages_processed_after_fc_ends(
        self, deliberative_loop, fake_message_bus
    ):
        """
        E1. FC 结束后处理排队消息 [P0]

        场景：
        - FC 第一次调用完成后，MessageBus 中有排队消息
        - 排队消息被合并为新 trigger，递归调用 _process_trigger

        断言：
        - FakeLLM 被调用 >= 2 次
        - 第二个 user message 包含 "在你刚才工作的时候"
        """
        llm = FakeLLM([
            fc_response(content="First response"),
            fc_response(content="Processed queued messages"),
        ])
        deliberative_loop._llm_fn = llm

        # 配置 MessageBus：第一次 drain 返回排队消息，第二次返回空
        call_count = [0]
        def mock_drain(predicate, limit=10):
            call_count[0] += 1
            if call_count[0] == 1:
                return [PlayerInput(text="用户消息1"), PlayerInput(text="用户消息2")]
            return []
        fake_message_bus.drain_from_fast.side_effect = mock_drain

        # Act
        trigger = PlayerInput(text="开始工作")
        deliberative_loop._process_trigger(trigger)

        # Assert
        assert llm.call_count >= 2

        # 第二次调用的 messages 应包含排队消息前缀
        second_call_messages = llm.nth_messages(1)
        user_messages = [m for m in second_call_messages if m.get("role") == "user"]
        assert any(
            "在你刚才工作的时候" in (msg.get("content") or "")
            for msg in user_messages
        )

    def test_e2_queued_messages_full_fc_loop(
        self, deliberative_loop, fake_message_bus
    ):
        """
        E2. 排队消息走完整 FC 循环 [P0]

        场景：
        - 排队消息的响应包含 tool_calls
        - 需要执行工具调用，然后继续 FC round

        断言：
        - FakeLLM 被调用 >= 3 次
        """
        llm = FakeLLM([
            fc_response(content="First response"),
            # 排队消息触发的 FC 有 tool_calls
            fc_response(content="thinking", tool_calls=[
                {"name": "say", "arguments": {"message": "hi"}, "id": "call_q1"}
            ]),
            fc_response(content="Done with queued"),
        ])
        deliberative_loop._llm_fn = llm

        # Mock tool 执行
        with patch.object(deliberative_loop, "_handle_say"):
            call_count = [0]
            def mock_drain(predicate, limit=10):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [PlayerInput(text="新消息")]
                return []
            fake_message_bus.drain_from_fast.side_effect = mock_drain

            trigger = PlayerInput(text="开始")
            deliberative_loop._process_trigger(trigger)

        assert llm.call_count >= 3

    def test_e3_no_queued_messages_no_recursion(
        self, deliberative_loop, fake_message_bus
    ):
        """
        E3. 无排队消息时不递归 [P1]

        场景：
        - MessageBus 为空，无排队消息
        - FC 循环正常结束

        断言：
        - FakeLLM 只被调用 1 次
        """
        llm = FakeLLM([fc_response(content="Response")])
        deliberative_loop._llm_fn = llm

        # MessageBus 始终为空
        fake_message_bus.drain_from_fast.return_value = []

        trigger = PlayerInput(text="简单请求")
        deliberative_loop._process_trigger(trigger)

        assert llm.call_count == 1

    def test_e4_multiple_queued_messages_merged_into_one_trigger(
        self, deliberative_loop, fake_message_bus
    ):
        """
        E4. 多条排队消息合并为一个 trigger [P1]

        场景：
        - 3 条排队消息
        - 应合并为单个 trigger，只产生一次额外调用

        断言：
        - 合并后的 user message 包含所有 3 条文本
        - 只产生一次额外的 LLM 调用（不是 3 次）
        """
        llm = FakeLLM([
            fc_response(content="First response"),
            fc_response(content="Processed all queued"),
        ])
        deliberative_loop._llm_fn = llm

        call_count = [0]
        def mock_drain(predicate, limit=10):
            call_count[0] += 1
            if call_count[0] == 1:
                return [
                    PlayerInput(text="消息A"),
                    PlayerInput(text="消息B"),
                    PlayerInput(text="消息C"),
                ]
            return []
        fake_message_bus.drain_from_fast.side_effect = mock_drain

        trigger = PlayerInput(text="开始")
        deliberative_loop._process_trigger(trigger)

        # 应只有 2 次调用（初始 + 合并排队消息）
        assert llm.call_count == 2

        # 第二个调用的消息应包含所有 3 条排队消息
        second_call_messages = llm.nth_messages(1)
        merged_text = str(second_call_messages)
        for msg in ["消息A", "消息B", "消息C"]:
            assert msg in merged_text

    def test_e5_queued_messages_trigger_compact_check(
        self, deliberative_loop, fake_message_bus
    ):
        """
        E5. 排队消息也触发 compact 检查 [P2]

        场景：
        - 排队消息处理时也会调用 _maybe_compact

        断言：
        - _maybe_compact 在排队消息处理中被调用
        """
        llm = FakeLLM([
            fc_response(content="First response"),
            fc_response(content="Processed queued"),
        ])
        deliberative_loop._llm_fn = llm

        call_count = [0]
        def mock_drain(predicate, limit=10):
            call_count[0] += 1
            if call_count[0] == 1:
                return [PlayerInput(text="新消息")]
            return []
        fake_message_bus.drain_from_fast.side_effect = mock_drain

        with patch.object(deliberative_loop, "_maybe_compact", wraps=deliberative_loop._maybe_compact) as mock_compact:
            trigger = PlayerInput(text="开始")
            deliberative_loop._process_trigger(trigger)

            # _maybe_compact 应被调用 >= 2 次（初始 trigger + 排队 trigger）
            assert mock_compact.call_count >= 2
