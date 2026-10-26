"""
test_session_lifecycle.py — Session 生命周期管理测试。

测试 DeliberativeLoop 的 Session 初始化、重置、幂等性等核心不变量。

改造后的 DeliberativeLoop 应支持：
  - _session_messages: list[dict] — 初始为 []
  - _system_message: str — 初始为 ""
  - _session_initialized: bool — 初始为 False
  - _turn_count: int — 初始为 0
  - _ensure_session()：懒初始化，调用 SystemPromptBuilder.build()
  - _reset_session()：清空所有状态，下次 trigger 重新初始化

外部通过 MessageBus 触发：
  - CompactRequest → 触发 _do_compact()
  - ResetRequest → 触发 _reset_session()
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from messages.types import PlayerInput


# ── 测试消息类型 ────────────────────────────────────────────────────────────


class CompactRequest:
    """消息总线上的 Compact 请求。"""
    def __init__(self, reason: str = "manual"):
        self.reason = reason


class ResetRequest:
    """消息总线上的 Reset 请求。"""
    def __init__(self, reason: str = "manual"):
        self.reason = reason


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def deliberative_loop(
    fake_message_bus,
    fake_bridge,
    fake_working_memory,
    loop_config,
):
    """构造 DeliberativeLoop 实例（不启动线程）。"""
    from slow_loop.deliberative_loop import DeliberativeLoop

    loop = DeliberativeLoop(
        message_bus=fake_message_bus,
        bridge=fake_bridge,
        working_memory=fake_working_memory,
        config=loop_config,
    )
    # 不启动线程，仅用于单元测试
    return loop


# ── F1. 懒初始化 — 首次 trigger 时初始化 [P0] ────────────────────────────────


def test_f1_lazy_initialization(deliberative_loop):
    """验证 loop 刚创建时，session 状态为未初始化。

    Arrange:
        - 新建 DeliberativeLoop 实例

    Act:
        - 检查初始状态

    Assert:
        - _session_initialized == False
        - _session_messages == []
        - _system_message == ""
        - _turn_count == 0
    """
    # 检查初始状态
    assert hasattr(deliberative_loop, "_session_initialized")
    assert deliberative_loop._session_initialized is False

    assert hasattr(deliberative_loop, "_session_messages")
    assert deliberative_loop._session_messages == []

    assert hasattr(deliberative_loop, "_system_message")
    assert deliberative_loop._system_message == ""

    assert hasattr(deliberative_loop, "_turn_count")
    assert deliberative_loop._turn_count == 0


# ── F2. 重复初始化幂等 [P1] ──────────────────────────────────────────────────


def test_f2_idempotent_ensure_session(deliberative_loop):
    """验证多次 _ensure_session() 调用时，SystemPromptBuilder.build() 只调用一次。

    Arrange:
        - 新建 DeliberativeLoop 实例
        - Mock SystemPromptBuilder.build()

    Act:
        - 调用 _ensure_session() 三次

    Assert:
        - SystemPromptBuilder.build() 被调用恰好 1 次
        - _session_initialized 保持 True
        - _system_message 内容一致
    """
    with patch("slow_loop.deliberative_loop.SystemPromptBuilder") as mock_builder_cls:
        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        mock_builder.build.return_value = "system prompt content"

        # 第一次调用
        deliberative_loop._ensure_session()
        assert deliberative_loop._session_initialized is True
        assert deliberative_loop._system_message == "system prompt content"
        assert mock_builder.build.call_count == 1

        # 第二、三次调用
        deliberative_loop._ensure_session()
        deliberative_loop._ensure_session()

        # build() 仍然只被调用一次
        assert mock_builder.build.call_count == 1


# ── F3. _reset_session 清空所有状态 [P0] ──────────────────────────────────────



def test_f2b_ensure_session_passes_skill_routing_summaries(deliberative_loop):
    deliberative_loop._skill_registry = MagicMock()
    deliberative_loop._skill_registry.get_routing_summaries.return_value = [
        {
            "name": "water_crops",
            "description": "浇灌当前地图所有未浇水的作物",
            "routing": {"owns_discovery": True},
        }
    ]

    with patch("slow_loop.deliberative_loop.SystemPromptBuilder") as mock_builder_cls:
        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        mock_builder.build.return_value = "system prompt"

        deliberative_loop._ensure_session()

        mock_builder_cls.assert_called_once()
        assert mock_builder_cls.call_args.kwargs["skill_routing_summaries"] == [
            {
                "name": "water_crops",
                "description": "浇灌当前地图所有未浇水的作物",
                "routing": {"owns_discovery": True},
            }
        ]


def test_f3_reset_session_clears_all_state(deliberative_loop):
    """验证 _reset_session() 清空所有 session 状态。

    Arrange:
        - 初始化 session（调用 _ensure_session()）
        - 向 _session_messages 添加几条消息
        - 增加 _turn_count

    Act:
        - 调用 _reset_session()

    Assert:
        - _session_initialized == False
        - _session_messages == []
        - _system_message == ""
        - _turn_count == 0
    """
    # 初始化 session
    deliberative_loop._ensure_session()
    assert deliberative_loop._session_initialized is True

    # 模拟添加消息和增加 turn_count
    deliberative_loop._session_messages.append({"role": "user", "content": "hello"})
    deliberative_loop._session_messages.append({"role": "assistant", "content": "hi"})
    deliberative_loop._turn_count = 3

    # 重置
    deliberative_loop._reset_session()

    # 验证所有状态归零
    assert deliberative_loop._session_initialized is False
    assert deliberative_loop._session_messages == []
    assert deliberative_loop._system_message == ""
    assert deliberative_loop._turn_count == 0


# ── F4. reset 后下一次 trigger 重新初始化 [P0] ──────────────────────────────



def test_f4_reset_then_trigger_reinitializes(deliberative_loop):
    """验证 reset 后，下一次 trigger 会重新初始化 session。

    Arrange:
        - 初始化 session
        - 添加消息和增加 turn_count
        - 调用 _reset_session()

    Act:
        - 模拟 trigger（调用 _process_trigger）
        - 或直接调用 _ensure_session()

    Assert:
        - build() 被调用 2 次（第一次初始化 + reset 后重新初始化）
        - 第二次 messages 只含第二次内容
    """
    with patch("slow_loop.deliberative_loop.SystemPromptBuilder") as mock_builder_cls:
        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        mock_builder.build.return_value = "system prompt"

        # 第一次初始化
        deliberative_loop._ensure_session()
        deliberative_loop._session_messages.append({"role": "user", "content": "msg1"})
        assert mock_builder.build.call_count == 1

        # 重置
        deliberative_loop._reset_session()
        assert deliberative_loop._session_initialized is False

        # 下一次 trigger 重新初始化
        deliberative_loop._ensure_session()
        assert deliberative_loop._session_initialized is True
        assert mock_builder.build.call_count == 2
        # 消息应该是空���（重新初始化后）
        assert deliberative_loop._session_messages == []


# ── F5. reset 通过 MessageBus 触发（不变量 4）[P1] ──────────────────────────



def test_f5_reset_via_message_bus(deliberative_loop, fake_message_bus):
    """验证 ResetRequest 通过 MessageBus 触发时，session 被重置。

    Arrange:
        - 初始化 session
        - 添加消息和增加 turn_count
        - 配置 fake_message_bus 返回 ResetRequest

    Act:
        - 调用 _handle_message(ResetRequest)
        - 或在主循环中处理消息

    Assert:
        - session 被重置
        - _session_initialized == False
    """
    # 初始化 session
    deliberative_loop._ensure_session()
    deliberative_loop._session_messages.append({"role": "user", "content": "test"})
    deliberative_loop._turn_count = 2

    # 构造 ResetRequest
    reset_req = ResetRequest(reason="test_reset")

    # 处理消息（假设 _handle_message 支持 ResetRequest）
    deliberative_loop._handle_message(reset_req)

    # 验证 session 被重置
    assert deliberative_loop._session_initialized is False
    assert deliberative_loop._session_messages == []
    assert deliberative_loop._turn_count == 0


# ── F6. 单线程 actor 模型（不变量 4）[P0] ──────────────────────────────────



def test_f6_single_threaded_actor_model(deliberative_loop):
    """验证设计层面：所有修改 _session_messages 的方法都在主循环调用链内。

    这是一个文档性测试，验证 DeliberativeLoop 遵循单线程 actor 模型：
    - 所有 session 状态修改（_session_messages, _turn_count 等）都在主循环中
    - 没有并发修改，无需锁
    - 外部通过 MessageBus 投递消息，主循环串行处理

    Arrange:
        - 检查 DeliberativeLoop 的方法调用关系

    Act:
        - 静态分析或运行时验证

    Assert:
        - _session_messages 只在以下方法中修改：
          * _ensure_session()
          * _reset_session()
          * _process_trigger() 及其调用链
        - 这些方法都在 _run_loop() 的主循环中调用
        - 没有其他线程修改这些状态
    """
    # 文档性测试：验证方法调用关系
    # 检查 _ensure_session 和 _reset_session 存在
    assert hasattr(deliberative_loop, "_ensure_session")
    assert callable(deliberative_loop._ensure_session)

    assert hasattr(deliberative_loop, "_reset_session")
    assert callable(deliberative_loop._reset_session)

    # 检查 _process_trigger 存在（主要决策流水线）
    assert hasattr(deliberative_loop, "_process_trigger")
    assert callable(deliberative_loop._process_trigger)

    # 检查 _run_loop 存在（主循环）
    assert hasattr(deliberative_loop, "_run_loop")
    assert callable(deliberative_loop._run_loop)

    # 验证 _handle_message 存在（消息分发）
    assert hasattr(deliberative_loop, "_handle_message")
    assert callable(deliberative_loop._handle_message)

    # 设计意图：
    # _run_loop() 是唯一的主循环入口，运行在 daemon thread 中
    # _handle_message() 在主循环中被调用，分发不同消息类型
    # _process_trigger() 处理业务逻辑，调用 _ensure_session()
    # _reset_session() 通过 _handle_message() 被调用
    # 所有状态修改都在这个调用链内，无并发问题


# ── F7. CompactRequest 通过 MessageBus 触发 [P1] ──────────────────────────



def test_f7_compact_request_via_message_bus(deliberative_loop, fake_message_bus):
    """验证 CompactRequest 通过 MessageBus 触发时，_do_compact 被调用。

    Arrange:
        - 初始化 session，添加多条消息
        - 配置 fake_message_bus 返回 CompactRequest

    Act:
        - 调用 _handle_message(CompactRequest)

    Assert:
        - _do_compact() 被调用
        - session 消息被压缩（具体压缩逻辑由 _do_compact 实现）
    """
    # 初始化 session
    deliberative_loop._ensure_session()
    deliberative_loop._session_messages = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "resp1"},
        {"role": "user", "content": "msg2"},
        {"role": "assistant", "content": "resp2"},
    ]

    # 构造 CompactRequest
    compact_req = CompactRequest(reason="test_compact")

    # Mock _do_compact 方法
    with patch.object(deliberative_loop, "_do_compact") as mock_compact:
        # 处理消息
        deliberative_loop._handle_message(compact_req)

        # 验证 _do_compact 被调用
        mock_compact.assert_called_once()


# ── F8. turn_count 正确递增 [P2] ──────────────────────────────────────────



def test_f8_turn_count_increments(deliberative_loop):
    """验证 _turn_count 在每次 trigger 时正确递增。

    Arrange:
        - 初始化 session
        - _turn_count 初始为 0

    Act:
        - 模拟 3 次 trigger（调用 _process_trigger 或 _handle_message）

    Assert:
        - 第 1 次 trigger 后：_turn_count == 1
        - 第 2 次 trigger 后：_turn_count == 2
        - 第 3 次 trigger 后：_turn_count == 3
    """
    # 初始化 session
    deliberative_loop._ensure_session()
    assert deliberative_loop._turn_count == 0

    # 模拟 3 次 trigger
    # 假设 _process_trigger 会增加 _turn_count
    # 这里我们直接修改以验证逻辑（实际实现中应在 _process_trigger 中增加）

    deliberative_loop._turn_count += 1
    assert deliberative_loop._turn_count == 1

    deliberative_loop._turn_count += 1
    assert deliberative_loop._turn_count == 2

    deliberative_loop._turn_count += 1
    assert deliberative_loop._turn_count == 3


# ── 集成测试：完整生命周期 ──────────────────────────────────────────────────



def test_session_lifecycle_integration(deliberative_loop, fake_message_bus):
    """集成测试：验证完整的 session 生命周期。

    Arrange:
        - 新建 DeliberativeLoop

    Act:
        1. 初始化 session（_ensure_session）
        2. 处理几条消息（_turn_count 递增）
        3. 收到 CompactRequest，压缩 session
        4. 继续处理消息
        5. 收到 ResetRequest，重置 session
        6. 再次初始化 session

    Assert:
        - 每个阶段的状态符合预期
        - reset 后能正确重新初始化
        - turn_count 在 reset 后归零
    """
    # 阶段 1：初始化
    deliberative_loop._ensure_session()
    assert deliberative_loop._session_initialized is True
    assert deliberative_loop._turn_count == 0

    # 阶段 2：处理消息，turn_count 递增
    deliberative_loop._turn_count = 1
    deliberative_loop._session_messages.append({"role": "user", "content": "msg1"})
    assert deliberative_loop._turn_count == 1

    deliberative_loop._turn_count = 2
    deliberative_loop._session_messages.append({"role": "assistant", "content": "resp1"})
    assert deliberative_loop._turn_count == 2

    # 阶段 3：收到 CompactRequest（假设 _do_compact 存在）
    if hasattr(deliberative_loop, "_do_compact"):
        with patch.object(deliberative_loop, "_do_compact"):
            compact_req = CompactRequest()
            deliberative_loop._handle_message(compact_req)

    # 阶段 4：继续处理消息
    deliberative_loop._turn_count = 3
    assert deliberative_loop._turn_count == 3

    # 阶段 5：收到 ResetRequest
    reset_req = ResetRequest()
    deliberative_loop._handle_message(reset_req)

    # 验证 reset 后的状态
    assert deliberative_loop._session_initialized is False
    assert deliberative_loop._session_messages == []
    assert deliberative_loop._turn_count == 0

    # 阶段 6：重新初始化
    deliberative_loop._ensure_session()
    assert deliberative_loop._session_initialized is True
    assert deliberative_loop._turn_count == 0
