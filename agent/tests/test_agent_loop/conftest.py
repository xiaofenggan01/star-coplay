"""
test_agent_loop.conftest — Agent Loop 模式测试专用 fixtures。

提供 FakeLLM、fc_response 工厂、标准配置等共享基础设施。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── FakeLLM ─────────────────────────────────────────────────────────────────


class FakeLLM:
    """可编程的 LLM mock，支持多轮 FC 响应序列。

    用法：
        llm = FakeLLM([
            fc_response(content="思考中", tool_calls=[{"name": "say", "arguments": {"message": "hi"}}]),
            fc_response(content="完成"),
        ])
        loop._llm_fn = llm
    """

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = responses
        self._call_idx = 0
        self.call_history: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        self.call_history.append(kwargs)
        if self._call_idx >= len(self._responses):
            return self._responses[-1]
        resp = self._responses[self._call_idx]
        self._call_idx += 1
        return resp

    @property
    def call_count(self) -> int:
        return len(self.call_history)

    def last_messages(self) -> list[dict]:
        if not self.call_history:
            return []
        return self.call_history[-1].get("messages", [])

    def nth_messages(self, n: int) -> list[dict]:
        """第 n 次调用（0-indexed）传入的 messages"""
        if n >= len(self.call_history):
            return []
        return self.call_history[n].get("messages", [])

    def nth_tools(self, n: int) -> list[dict]:
        """第 n 次调用传入的 tools 参数"""
        if n >= len(self.call_history):
            return []
        return self.call_history[n].get("tools", [])


# ── fc_response 工厂 ────────────────────────────────────────────────────────


def fc_response(
    content: str = "",
    tool_calls: list[dict] | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    """构造 mock FC response（兼容 OpenAI ChatCompletion 结构）。

    Args:
        content: assistant 回复文本
        tool_calls: [{"name": "say", "arguments": {"message": "hi"}, "id": "optional"}]
        finish_reason: 显式指定。未指定时：有 tool_calls → "tool_calls"，否则 → "stop"
    """
    if finish_reason is None:
        finish_reason = "tool_calls" if tool_calls else "stop"

    tc_objects = None
    if tool_calls:
        tc_objects = []
        for i, tc in enumerate(tool_calls):
            tc_objects.append(SimpleNamespace(
                id=tc.get("id", f"call_{i}"),
                type="function",
                function=SimpleNamespace(
                    name=tc["name"],
                    arguments=json.dumps(tc.get("arguments", {})),
                ),
            ))

    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=content,
                tool_calls=tc_objects,
            ),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        model="test-model",
        id="test-resp-id",
    )


# ── 结构完整性断言 ──────────────────────────────────────────────────────────


def assert_messages_integrity(messages: list[dict]) -> None:
    """验证 messages 数组满足所有结构不变量。

    检查项：
    1. 每个 assistant(tool_calls) 后紧跟对应数量的 tool messages
    2. tool message 的 tool_call_id 与前面 assistant 的 tool_calls id 匹配
    3. 不以 tool message 结尾
    """
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tc_list = msg["tool_calls"]
            expected_count = len(tc_list)
            expected_ids = set()
            for tc in tc_list:
                if isinstance(tc, dict):
                    expected_ids.add(tc.get("id") or tc.get("function", {}).get("name"))
                else:
                    expected_ids.add(getattr(tc, "id", None))

            actual_count = 0
            actual_ids = set()
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                actual_count += 1
                actual_ids.add(messages[j].get("tool_call_id"))
                j += 1
            assert actual_count == expected_count, (
                f"Position {i}: expected {expected_count} tool results, got {actual_count}"
            )
            assert actual_ids == expected_ids, (
                f"Position {i}: tool_call_id mismatch, expected {sorted(expected_ids)}, "
                f"got {sorted(actual_ids)}"
            )
            i = j
        else:
            i += 1

    if messages:
        assert messages[-1].get("role") != "tool", "messages ends with tool (unclosed)"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def loop_config() -> dict:
    """Agent Loop 模式的标准测试配置"""
    return {
        "slow_loop": {
            "min_call_interval_s": 0.0,
            "player_input_debounce_s": 0.0,
            "context_window": 128000,
            "reserved_output": 4096,
            "compact_ratio": 0.7,
        }
    }


@pytest.fixture
def fake_memory_system() -> MagicMock:
    """Mock MemorySystem"""
    ms = MagicMock()
    ms.get_facts.return_value = "玩家名: 测试玩家\n好感度: Sebastian 6心"
    ms.get_recent_summaries.return_value = ["第1天：种了土豆，认识了Sebastian"]
    ms.get_context_for_prompt.return_value = "记忆上下文摘要"
    return ms


@pytest.fixture
def fake_bridge() -> MagicMock:
    """Mock BridgeBackend"""
    bridge = MagicMock()
    bridge.read_game_state.return_value = {
        "player": {"location": "Farm", "energy": 200, "max_energy": 270, "gold": 500},
        "time": "Spring 1, 6:00 AM",
        "world": {},
    }
    bridge.capabilities.return_value = []
    bridge.write_command.return_value = "action_001"
    bridge.read_command_result.return_value = SimpleNamespace(
        success=True, data={"status": "done"}, error=None
    )
    return bridge


@pytest.fixture
def fake_message_bus() -> MagicMock:
    """Mock MessageBus"""
    bus = MagicMock()
    bus.drain_from_fast.return_value = []
    bus.try_get_from_fast.return_value = None
    return bus


@pytest.fixture
def fake_working_memory() -> MagicMock:
    """Mock WorkingMemory"""
    wm = MagicMock()
    wm.recent_events.return_value = []
    wm.last_state = None
    wm.summarize.return_value = "Game state: not yet loaded"
    return wm
