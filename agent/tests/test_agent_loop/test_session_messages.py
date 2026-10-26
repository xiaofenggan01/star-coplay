"""
测试 Agent Loop 的累积式 messages 行为。

TODO: 这些测试需要在 Step 2（DeliberativeLoop 改造完成）后去掉 @pytest.mark.skip。
当前标记为 skip 是因为 DeliberativeLoop 的以下部分尚未实现：
  - _session_messages: list[dict] 跨 trigger 累积
  - _system_message: str session 内锁定
  - _ensure_session() 懒初始化
  - _build_user_turn(trigger_msg) -> str
  - _get_messages_for_api() -> list[dict]

测试覆盖的核心行为：
  1. 单次 trigger 产生正确 messages 结构
  2. 跨 trigger 消息累积
  3. FC 多轮自然追加
  4. API 调用始终以 system message 开头
  5. system message 不随 trigger 变化
  6. _build_user_turn 只含本次触发信息
  7. 不同触发类型的 user turn 格式
  8. EpisodeBoundaryReport 不触发 session reset
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

# 假设这些会从实际模块导入，当前用 mock 替代
# from agent.slow_loop.deliberative_loop import DeliberativeLoop
# from agent.slow_loop.triggers import PlayerInput, ReportEvent, EscalateRequest, EpisodeBoundaryReport, TaskCompleted


@dataclass
class PlayerInput:
    """玩家输入触发"""
    text: str


@dataclass
class ReportEvent:
    """事件报告触发"""
    event_type: str
    significance: int = 5
    description: str = ""


@dataclass
class EscalateRequest:
    """升级请求触发"""
    reason: str
    context: str = ""


@dataclass
class EpisodeBoundaryReport:
    """日结事件触发（不应重置 session）"""
    trigger: str
    summary: str = ""


@dataclass
class TaskCompleted:
    """任务完成触发"""
    task_name: str
    result: str = ""


class MockDeliberativeLoop:
    """
    Mock DeliberativeLoop，模拟改造后的行为。

    实际实现会在 Step 2 中完成，这里用 mock 来验证测试逻辑。
    """

    def __init__(self, config, memory_system, bridge, message_bus, working_memory, fake_llm):
        self.config = config
        self.memory_system = memory_system
        self.bridge = bridge
        self.message_bus = message_bus
        self.working_memory = working_memory
        self.fake_llm = fake_llm

        # 改造后的属性
        self._session_messages: list[dict] = []
        self._system_message: Optional[str] = None
        self._session_active = False

    def _ensure_session(self) -> None:
        """懒初始化 session"""
        if not self._session_active:
            self._system_message = "You are a helpful Stardew Valley agent."
            self._session_messages = []
            self._session_active = True

    def _build_user_turn(self, trigger_msg: str) -> str:
        """构造本次触发的 user message 内容（不含历史）"""
        return f"User trigger: {trigger_msg}"

    def _get_messages_for_api(self) -> list[dict]:
        """返回 boundary 后的所有消息（包括 system）"""
        if not self._session_active:
            return []
        messages = [{"role": "system", "content": self._system_message}]
        messages.extend(self._session_messages)
        return messages

    def _process_trigger(self, trigger) -> str:
        """处理单个触发，返回 LLM 响应内容"""
        self._ensure_session()

        # 构造本次 user message
        if isinstance(trigger, PlayerInput):
            user_content = self._build_user_turn(trigger.text)
        elif isinstance(trigger, ReportEvent):
            user_content = self._build_user_turn(f"Event: {trigger.event_type} (sig={trigger.significance})")
        elif isinstance(trigger, EscalateRequest):
            user_content = self._build_user_turn(f"Escalate: {trigger.reason}")
        elif isinstance(trigger, EpisodeBoundaryReport):
            user_content = self._build_user_turn(f"Episode boundary: {trigger.trigger}")
        elif isinstance(trigger, TaskCompleted):
            user_content = self._build_user_turn(f"Task done: {trigger.task_name}")
        else:
            user_content = self._build_user_turn(str(trigger))

        # 追加 user message
        self._session_messages.append({"role": "user", "content": user_content})

        # 调用 LLM
        messages_for_api = self._get_messages_for_api()
        raw_response = self.fake_llm(messages=messages_for_api)

        # 解析响应（兼容 fc_response 格式）
        message_obj = raw_response.choices[0].message
        content = message_obj.content or ""
        tool_calls = message_obj.tool_calls

        # 处理响应
        if tool_calls:
            # 有 tool_calls，追加 assistant + tool 消息
            self._session_messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls
            })
            # 这里简化处理，实际会有 tool result 消息
            tool_result = {"role": "tool", "content": "tool executed"}
            self._session_messages.append(tool_result)
            # 再次调用 LLM 获取最终响应
            messages_for_api = self._get_messages_for_api()
            final_raw = self.fake_llm(messages=messages_for_api)
            final_content = final_raw.choices[0].message.content or ""
            self._session_messages.append({
                "role": "assistant",
                "content": final_content
            })
            return final_content
        else:
            # 纯文本响应
            self._session_messages.append({
                "role": "assistant",
                "content": content
            })
            return content


# ============================================================================
# 测试用例
# ============================================================================



def test_b1_single_trigger_messages(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B1. 单次 trigger 产生正确 messages 结构 [P0]

    Arrange: 创建 loop，FakeLLM 返回纯文本
    Act: 处理一个 PlayerInput trigger
    Assert: _session_messages 有 user + assistant，user content 包含触发文本
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([fc_response(content="I understand your input.")])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    trigger = PlayerInput(text="Hello, agent!")
    response = loop._process_trigger(trigger)

    # Assert
    assert response == "I understand your input."
    assert len(loop._session_messages) == 2  # user + assistant
    assert loop._session_messages[0]["role"] == "user"
    assert "Hello, agent!" in loop._session_messages[0]["content"]
    assert loop._session_messages[1]["role"] == "assistant"
    assert loop._session_messages[1]["content"] == "I understand your input."



def test_b2_cross_trigger_accumulation(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B2. 跨 trigger 消息累积 [P0]

    Arrange: 创建 loop，FakeLLM 返回两个不同响应
    Act: 处理两个不同的 trigger
    Assert: _session_messages >= 4 条，第二次 LLM 调用的 messages 包含第一次历史
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="First response."),
        fc_response(content="Second response.")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    trigger1 = PlayerInput(text="First input")
    response1 = loop._process_trigger(trigger1)

    trigger2 = ReportEvent(event_type="test_event", significance=7)
    response2 = loop._process_trigger(trigger2)

    # Assert
    assert response1 == "First response."
    assert response2 == "Second response."
    assert len(loop._session_messages) >= 4  # user1 + asst1 + user2 + asst2

    # 第二次 LLM 调用应包含第一次的历史
    second_call_messages = fake_llm.nth_messages(1)
    assert len(second_call_messages) >= 4  # system + user1 + asst1 + user2
    assert second_call_messages[0]["role"] == "system"
    assert any("First input" in msg.get("content", "") for msg in second_call_messages if msg["role"] == "user")



def test_b3_fc_multi_turn_natural_append(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B3. FC 多轮自然追加 [P0]

    Arrange: FakeLLM 第一次返回 tool_calls，第二次返回纯文本
    Act: 处理一个 trigger
    Assert: messages 包含 user → assistant(tc) → tool → assistant
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="Calling tool", tool_calls=[{"name": "test_tool", "arguments": {}, "id": "call_1"}]),
        fc_response(content="Tool result processed.")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    trigger = PlayerInput(text="Use a tool")
    response = loop._process_trigger(trigger)

    # Assert
    assert response == "Tool result processed."
    # 应该有 user + assistant(with tc) + tool + assistant
    assert len(loop._session_messages) >= 4
    assert loop._session_messages[0]["role"] == "user"
    assert loop._session_messages[1]["role"] == "assistant"
    assert "tool_calls" in loop._session_messages[1]
    assert loop._session_messages[2]["role"] == "tool"
    assert loop._session_messages[3]["role"] == "assistant"



def test_b4_api_call_always_starts_with_system(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B4. API 调用始终以 system message 开头 [P0]

    Arrange: 创建 loop，处理多个 trigger
    Act: 检查每次 LLM 调用的 messages
    Assert: 每次调用的 messages[0]["role"] == "system"
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="Response 1"),
        fc_response(content="Response 2"),
        fc_response(content="Response 3")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    loop._process_trigger(PlayerInput(text="Input 1"))
    loop._process_trigger(ReportEvent(event_type="event_1"))
    loop._process_trigger(EscalateRequest(reason="Need help"))

    # Assert
    for i in range(3):
        messages = fake_llm.nth_messages(i)
        assert len(messages) > 0
        assert messages[0]["role"] == "system"



def test_b5_system_message_unchanged_across_triggers(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B5. system message 不随 trigger 变化 [P0]

    Arrange: 创建 loop，处理不同类型的 trigger
    Act: 记录每次 LLM 调用的 system message
    Assert: 所有调用的 system message 内容完全相同
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="Response 1"),
        fc_response(content="Response 2")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    loop._process_trigger(PlayerInput(text="Input 1"))
    first_system = fake_llm.nth_messages(0)[0]["content"]

    loop._process_trigger(ReportEvent(event_type="event_1", significance=10))
    second_system = fake_llm.nth_messages(1)[0]["content"]

    # Assert
    assert first_system == second_system
    assert first_system == "You are a helpful Stardew Valley agent."



def test_b6_build_user_turn_only_contains_current_trigger(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B6. _build_user_turn 只含本次触发信息 [P1]

    Arrange: 创建 loop，处理第一个 trigger
    Act: 处理第二个 trigger，检查第二次的 user message
    Assert: 第二次 trigger 的 user message 不包含第一次的内容
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="Response 1"),
        fc_response(content="Response 2")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    loop._process_trigger(PlayerInput(text="First unique input"))
    loop._process_trigger(PlayerInput(text="Second unique input"))

    # Assert
    # 第二次的 user message 应该只包含 "Second unique input"，不包含 "First unique input"
    second_user_message = None
    for msg in loop._session_messages:
        if msg["role"] == "user" and "Second unique input" in msg["content"]:
            second_user_message = msg["content"]
            break

    assert second_user_message is not None
    assert "First unique input" not in second_user_message
    assert "Second unique input" in second_user_message



def test_b7_different_trigger_types_format(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B7. 不同触发类型的 user turn 格式 [P1]

    Arrange: 创建 loop
    Act: 处理各种类型的 trigger
    Assert: 各类型触发产生正确格式的 user message
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="Response"),
        fc_response(content="Response"),
        fc_response(content="Response"),
        fc_response(content="Response"),
        fc_response(content="Response")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    triggers = [
        PlayerInput(text="Hello"),
        ReportEvent(event_type="combat", significance=8),
        EscalateRequest(reason="Need immediate help"),
        TaskCompleted(task_name="gather_wood", result="success"),
        EpisodeBoundaryReport(trigger="day_end", summary="Day completed")
    ]

    for trigger in triggers:
        loop._process_trigger(trigger)

    # Assert
    user_messages = [msg for msg in loop._session_messages if msg["role"] == "user"]
    assert len(user_messages) == 5

    # 检查各类型的格式
    assert "Hello" in user_messages[0]["content"]
    assert "combat" in user_messages[1]["content"]
    assert "Escalate" in user_messages[2]["content"]
    assert "Task done" in user_messages[3]["content"]
    assert "Episode boundary" in user_messages[4]["content"]



def test_b8_episode_boundary_does_not_reset_session(loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory):
    """
    B8. EpisodeBoundaryReport 不触发 session reset [P0]

    Arrange: 创建 loop，处理一个 trigger
    Act: 处理 EpisodeBoundaryReport，再处理另一个 trigger
    Assert: _session_messages 未被清空，包含所有历史消息
    """
    from tests.test_agent_loop.conftest import FakeLLM, fc_response

    fake_llm = FakeLLM([
        fc_response(content="Response 1"),
        fc_response(content="Boundary processed"),
        fc_response(content="Response 3")
    ])
    loop = MockDeliberativeLoop(
        loop_config, fake_memory_system, fake_bridge, fake_message_bus, fake_working_memory, fake_llm
    )

    # Act
    loop._process_trigger(PlayerInput(text="Before boundary"))
    messages_before = len(loop._session_messages)

    loop._process_trigger(EpisodeBoundaryReport(trigger="day_end", summary="Day 1 complete"))
    messages_after_boundary = len(loop._session_messages)

    loop._process_trigger(PlayerInput(text="After boundary"))
    messages_final = len(loop._session_messages)

    # Assert
    # 日结事件不应清空 session
    assert messages_after_boundary > messages_before  # 日结事件本身产生消息
    assert messages_final > messages_after_boundary  # 日结后还能继续累积

    # 验证所有消息都在 _session_messages 中
    all_user_contents = [msg["content"] for msg in loop._session_messages if msg["role"] == "user"]
    assert any("Before boundary" in content for content in all_user_contents)
    assert any("day_end" in content for content in all_user_contents)
    assert any("After boundary" in content for content in all_user_contents)
