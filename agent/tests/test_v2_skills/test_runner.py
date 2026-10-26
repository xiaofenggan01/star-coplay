"""test_runner — SkillRunner 单元测试。

测试 SkillRunner 的四种执行路径：正常完成、超时、取消、异常。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from v2_skills.runner import SkillRunner
from v2_skills.errors import BridgeError, SkillCancelled


@dataclass
class FakeSkillEntry:
    """测试用的 SkillEntry 替身。"""
    name: str
    func: Callable
    timeout: float = 120.0


class TestSkillRunnerNormal:
    """正常执行路径。"""

    def test_normal_dict_result(self):
        """技能返回 dict 时直接作为结果。"""
        async def my_skill(ctx):
            return {"success": True, "status": "ok", "data": {"count": 3}}

        bridge = MagicMock()
        runner = SkillRunner(bridge, Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="my_skill", func=my_skill)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert result["status"] == "ok"
        assert result["data"]["count"] == 3

    def test_normal_non_dict_result(self):
        """技能返回非 dict 时包装为标准结构。"""
        async def my_skill(ctx):
            return "done"

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="my_skill", func=my_skill)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert result["data"] == "done"

    def test_params_passed_to_skill(self):
        """参数正确传递给技能函数。"""
        received_params = {}

        async def my_skill(ctx, max_count: int = 0, name: str = ""):
            received_params["max_count"] = max_count
            received_params["name"] = name
            return {"success": True, "status": "ok"}

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="my_skill", func=my_skill)

        asyncio.run(runner.execute(entry, {"max_count": 5, "name": "test"}))

        assert received_params["max_count"] == 5
        assert received_params["name"] == "test"

    def test_on_context_callback_receives_context(self):
        received = []

        async def my_skill(ctx):
            return {"success": True, "status": "ok"}

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="my_skill", func=my_skill)

        result = asyncio.run(runner.execute(entry, {}, on_context=received.append))

        assert result["success"] is True
        assert len(received) == 1
        assert received[0].is_cancelled is False

    def test_set_bridge_replaces_context_bridge(self):
        received = []

        async def my_skill(ctx):
            received.append(ctx._bridge)
            return {"success": True, "status": "ok"}

        bridge = MagicMock()
        new_bridge = MagicMock()
        runner = SkillRunner(bridge, Path("/tmp/game_state.json"))
        runner.set_bridge(new_bridge)
        entry = FakeSkillEntry(name="my_skill", func=my_skill)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert received == [new_bridge]


class TestSkillRunnerTimeout:
    """超时路径。"""

    def test_timeout(self):
        """技能执行超时返回 TIMEOUT 错误。"""
        async def slow_skill(ctx):
            await asyncio.sleep(10.0)  # 永远不会完成
            return {"success": True}

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="slow_skill", func=slow_skill, timeout=0.3)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is False
        assert result["status"] == "failed"
        assert result["error"]["code"] == "TIMEOUT"

    def test_max_timeout_cap(self):
        """timeout 被 MAX_TIMEOUT 上限截断。"""
        async def fast_skill(ctx):
            return {"success": True, "status": "ok"}

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="fast_skill", func=fast_skill, timeout=999.0)

        # 不会真的等 300 秒，因为技能立即返回
        result = asyncio.run(runner.execute(entry, {}))
        assert result["success"] is True


class TestSkillRunnerCancelled:
    """取消路径。"""

    def test_cancelled(self):
        """技能抛出 SkillCancelled 时返回 partial 状态。"""
        async def cancel_skill(ctx):
            raise SkillCancelled()

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="cancel_skill", func=cancel_skill)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert result["status"] == "partial"
        assert "被取消" in result["observation"]
        assert result["error"]["code"] == "CANCELLED"


class TestSkillRunnerError:
    """异常路径。"""

    def test_bridge_error(self):
        """BridgeError 被正确捕获并转为结果。"""
        async def failing_skill(ctx):
            raise BridgeError("MOVE_FAILED", "路径不可达")

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="failing_skill", func=failing_skill)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is False
        assert result["error"]["code"] == "MOVE_FAILED"
        assert "路径不可达" in result["error"]["message"]

    def test_unexpected_exception(self):
        """未预期异常被捕获为 INTERNAL_ERROR。"""
        async def broken_skill(ctx):
            raise ValueError("unexpected error")

        runner = SkillRunner(MagicMock(), Path("/tmp/game_state.json"))
        entry = FakeSkillEntry(name="broken_skill", func=broken_skill)

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is False
        assert result["error"]["code"] == "INTERNAL_ERROR"
        assert "unexpected error" in result["error"]["message"]
