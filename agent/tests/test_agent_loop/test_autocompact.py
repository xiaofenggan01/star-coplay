"""
测试 DeliberativeLoop 的 Autocompact 逻辑。

覆盖：
- 动态阈值计算
- 压缩触发条件
- 消息切片与 boundary 处理
- 多次压缩的正确性
- 压缩与重置的语义分离
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import Any
from types import SimpleNamespace


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
    loop._ensure_session()
    return loop


class TestAutocompactThresholdCalculation:
    """D1: 动态阈值计算正确"""

    def test_d1_threshold_calculation(self, deliberative_loop):
        """
        Arrange:
            - context_window=128000, reserved_output=4096, compact_ratio=0.7
        Act:
            - 调用 _compute_compact_threshold()
        Assert:
            - threshold 基于公式 (context_window - reserved_output - system_tokens - tools_tokens) * ratio
        """
        threshold = deliberative_loop._compute_compact_threshold()

        # system_tokens 和 tools_tokens 是动态的，验证合理范围
        assert threshold > 0
        # 上限：(128000 - 4096 - 0 - 1500) * 0.7 = 85682
        assert threshold <= 85683
        # 下限：至少应该有一些空间
        assert threshold > 50000


class TestAutocompactTrigger:
    """D2: 未超阈值不触发 compact"""

    def test_d2_no_compact_below_threshold(self, deliberative_loop):
        """
        Arrange: 添加少量 messages（总 token 远低于阈值）
        Act: 调用 _maybe_compact()
        Assert: 无 compact_boundary 消息
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        deliberative_loop._maybe_compact()

        assert not any(
            msg.get("metadata", {}).get("type") == "compact_boundary"
            for msg in deliberative_loop._session_messages
        )


class TestAutocompactBoundaryInsertion:
    """D3: 超阈值插入 boundary"""

    def test_d3_boundary_insertion_on_threshold_exceeded(self, deliberative_loop):
        """
        Arrange: Mock _estimate_tokens 返回超阈值的值
        Act: 调用 _maybe_compact()
        Assert: 存在 compact_boundary 消息
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "x" * 1000},
            {"role": "assistant", "content": "y" * 1000},
        ]

        with patch.object(deliberative_loop._compactor, "estimate_tokens", return_value=100000):
            deliberative_loop._maybe_compact()

        boundaries = [
            msg for msg in deliberative_loop._session_messages
            if msg.get("metadata", {}).get("type") == "compact_boundary"
        ]
        assert len(boundaries) == 1

        boundary = boundaries[0]
        assert boundary["role"] == "user"
        assert "[对话摘要]" in boundary["content"]
        assert boundary["metadata"]["type"] == "compact_boundary"

        # boundary 后应有 assistant ack
        boundary_idx = deliberative_loop._session_messages.index(boundary)
        ack = deliberative_loop._session_messages[boundary_idx + 1]
        assert ack["role"] == "assistant"
        assert "已了解" in ack["content"]


class TestGetMessagesForAPI:
    """D4: _get_messages_for_api 切片正确"""

    def test_d4_get_messages_for_api_slicing(self, deliberative_loop):
        """
        Arrange: 手动构造含 boundary 的 messages
        Act: 调用 _get_messages_for_api()
        Assert: 返回从 boundary 开始的消息（不含 boundary 之前的）
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {
                "role": "user",
                "content": "[对话摘要] summary here",
                "metadata": {"type": "compact_boundary"},
            },
            {"role": "assistant", "content": "已了解之前的上下文，继续。"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]

        result = deliberative_loop._get_messages_for_api()

        # system + boundary 后的 4 条消息（metadata stripped for API）
        assert result[0]["role"] == "system"
        assert "[对话摘要]" in result[1]["content"]
        assert "metadata" not in result[1]  # metadata stripped
        assert result[2]["content"] == "已了解之前的上下文，继续。"
        assert result[3]["content"] == "msg5"
        assert result[4]["content"] == "msg6"
        assert len(result) == 5


class TestMultipleCompacts:
    """D5: 多次 compact 取最后 boundary"""

    def test_d5_multiple_compacts_use_latest_boundary(self, deliberative_loop):
        """
        Arrange: 手动构造两个 boundary
        Act: 调用 _get_messages_for_api()
        Assert: 返回从第二个 boundary 开始的消息
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {
                "role": "user",
                "content": "[对话摘要] first",
                "metadata": {"type": "compact_boundary"},
            },
            {"role": "assistant", "content": "ack1"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
            {
                "role": "user",
                "content": "[对话摘要] second",
                "metadata": {"type": "compact_boundary"},
            },
            {"role": "assistant", "content": "ack2"},
            {"role": "user", "content": "msg9"},
        ]

        result = deliberative_loop._get_messages_for_api()

        # system + 从第二个 boundary 开始的 3 条
        assert result[0]["role"] == "system"
        assert "[对话摘要] second" in result[1]["content"]
        assert result[2]["content"] == "ack2"
        assert result[3]["content"] == "msg9"
        assert len(result) == 4


class TestCompactPreservesSystemMessage:
    """D6: compact 不改变 system message"""

    def test_d6_compact_preserves_system_message(self, deliberative_loop):
        """
        Arrange: 设置 system_message，填充超阈值 messages
        Act: 调用 _maybe_compact()
        Assert: system_message 不变
        """
        deliberative_loop._system_message = "You are a helpful assistant."
        original_system = deliberative_loop._system_message

        deliberative_loop._session_messages = [
            {"role": "user", "content": "x" * 1000},
            {"role": "assistant", "content": "y" * 1000},
        ]

        with patch.object(deliberative_loop._compactor, "estimate_tokens", return_value=100000):
            deliberative_loop._maybe_compact()

        assert deliberative_loop._system_message == original_system


class TestCompactSafetyBoundary:
    """D7: compact 只在安全边界触发（不变量 5）"""

    def test_d7_compact_respects_safety_boundary(self, deliberative_loop):
        """
        Arrange: messages 最后是未闭合的 assistant(tool_calls)
        Act: 调用 _maybe_compact()
        Assert: 不触发 compact
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "x" * 1000},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "tool1", "arguments": "{}"}}],
            },
        ]

        original_messages = [msg.copy() for msg in deliberative_loop._session_messages]

        with patch.object(deliberative_loop._compactor, "estimate_tokens", return_value=100000):
            deliberative_loop._maybe_compact()

        # 无 boundary 被插入
        assert not any(
            msg.get("metadata", {}).get("type") == "compact_boundary"
            for msg in deliberative_loop._session_messages
        )
        assert len(deliberative_loop._session_messages) == len(original_messages)


class TestCompactFollowedByTrigger:
    """D8: compact 后下一次 trigger 正常工作"""

    def test_d8_compact_then_trigger_works_normally(self, deliberative_loop):
        """
        Arrange: 模拟 compact 后的状态
        Act: 调用 _get_messages_for_api()
        Assert: 返回从 boundary 开始的消息
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "old msg"},
            {"role": "assistant", "content": "old reply"},
            {
                "role": "user",
                "content": "[对话摘要] previous context",
                "metadata": {"type": "compact_boundary"},
            },
            {"role": "assistant", "content": "已了解之前的上下文，继续。"},
            {"role": "user", "content": "new question"},
        ]

        messages_for_api = deliberative_loop._get_messages_for_api()

        # system + boundary + ack + new question
        assert messages_for_api[0]["role"] == "system"
        assert "[对话摘要]" in messages_for_api[1]["content"]
        assert "metadata" not in messages_for_api[1]  # stripped for API
        assert messages_for_api[3]["content"] == "new question"
        assert len(messages_for_api) == 4


class TestCompactVsResetSemantics:
    """D9: compact vs reset 语义分离（不变量 6）"""

    def test_d9_compact_vs_reset_semantics(self, deliberative_loop):
        """
        Arrange: 填充 messages
        Act: compact → 检查 → reset → 检查
        Assert: compact 保留 session，reset 清空 session
        """
        deliberative_loop._session_messages = [
            {"role": "user", "content": "x" * 1000},
            {"role": "assistant", "content": "y" * 1000},
        ]

        # 执行 compact
        with patch.object(deliberative_loop._compactor, "estimate_tokens", return_value=100000):
            deliberative_loop._maybe_compact()

        # compact 后：session 继续，messages 非空，包含 boundary
        assert len(deliberative_loop._session_messages) > 0
        assert any(
            msg.get("metadata", {}).get("type") == "compact_boundary"
            for msg in deliberative_loop._session_messages
        )
        assert deliberative_loop._session_initialized is True

        # 执行 reset
        deliberative_loop._reset_session()

        # reset 后：session 清空
        assert len(deliberative_loop._session_messages) == 0
        assert deliberative_loop._session_initialized is False
