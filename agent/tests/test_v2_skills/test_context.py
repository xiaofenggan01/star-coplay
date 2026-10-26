"""test_context — SkillContext 单元测试。

测试 SkillContext 与 FileBridge 的交互，使用 mock 替代真实 bridge。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保 v2/agent 在 sys.path 中
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from v2_skills.context import SkillContext
from v2_skills.errors import BridgeError, SkillCancelled
from v2_skills.models import GameState
from bridge.bridge_result import BridgeResult


# ── 测试数据 ─────────────────────────────────────────────────────────────────

SAMPLE_GAME_STATE = {
    "status": "running",
    "player": {
        "name": "TestPlayer",
        "location": "Farm",
        "tile": {"x": 10, "y": 20},
        "energy": {"current": 200, "max": 270},
        "gold": 5000,
        "equipped_item": {"name": "Watering Can", "type": "Tool"},
    },
    "world": {
        "time": 900,
        "day": 15,
        "season": "Spring",
        "day_of_week": "Monday",
    },
}


def _make_bridge_result(success: bool, data: dict = None, error_code: str = None, error_msg: str = None) -> BridgeResult:
    """构造测试用 BridgeResult。"""
    return BridgeResult(
        success=success,
        status="ok" if success else "failed",
        data=data or {},
        observation="",
        side_effects=[],
        error_code=error_code,
        message=error_msg,
        action_id="act_0001_test",
        seq=1,
    )


def _make_mock_bridge(result: BridgeResult = None) -> MagicMock:
    """构造 mock bridge。write_command 返回 action_id，read_command_result 返回指定结果。"""
    bridge = MagicMock()
    bridge.write_command.return_value = "act_0001_test"
    bridge.read_command_result.return_value = result
    bridge.cancel_command.return_value = None
    return bridge


# ── find_targets 测试 ────────────────────────────────────────────────────────

class TestFindTargets:
    """测试 find_targets 的成功和失败路径。"""

    def test_find_targets_success(self):
        """成功查找目标时返回 animals 列表。"""
        animals = [
            {"name": "Daisy", "display_name": "Daisy", "location_name": "Farm"},
            {"name": "Bessie", "display_name": "Bessie", "location_name": "Farm"},
        ]
        result = _make_bridge_result(success=True, data={"animals": animals})
        bridge = _make_mock_bridge(result)
        ctx = SkillContext(bridge, Path("/tmp/game_state.json"))

        targets = asyncio.run(ctx.find_targets("animal", filter={"ungreeted": True}))

        assert targets == animals
        # 参数键名对齐 Mod 侧：target_type（非 type），filter 展开到顶层
        bridge.write_command.assert_called_once_with("find_targets", {
            "target_type": "animal", "ungreeted": True, "sort": "nearest", "max": 50,
        })

    def test_find_targets_failure(self):
        """查找失败时抛出 BridgeError。"""
        result = _make_bridge_result(
            success=False,
            error_code="NOT_SUPPORTED",
            error_msg="find_targets not available",
        )
        bridge = _make_mock_bridge(result)
        ctx = SkillContext(bridge, Path("/tmp/game_state.json"))

        with pytest.raises(BridgeError) as exc_info:
            asyncio.run(ctx.find_targets("animal"))

        assert exc_info.value.code == "NOT_SUPPORTED"
        # 验证参数键名正确
        bridge.write_command.assert_called_once_with("find_targets", {
            "target_type": "animal", "sort": "nearest", "max": 50,
        })

    def test_find_targets_empty(self):
        """查找无结果时返回空列表。"""
        # find_targets 返回 data.animals，空结果时 Mod 返回 {"animals": []}
        result = _make_bridge_result(success=True, data={"animals": []})
        bridge = _make_mock_bridge(result)
        ctx = SkillContext(bridge, Path("/tmp/game_state.json"))

        targets = asyncio.run(ctx.find_targets("animal"))
        assert targets == []


# ── do / go_to 测试 ──────────────────────────────────────────────────────────

class TestActions:
    """测试 do 和 go_to 的交互方法。"""

    def test_do_success(self):
        """do 成功时返回结果 dict。"""
        result = _make_bridge_result(success=True, data={"effect": "petted"})
        bridge = _make_mock_bridge(result)
        ctx = SkillContext(bridge, Path("/tmp/game_state.json"))

        res = asyncio.run(ctx.do(intent="pet", target="Farm:animal_Daisy"))

        assert res["success"] is True
        assert res["data"]["effect"] == "petted"
        bridge.write_command.assert_called_once_with("do", {
            "intent": "pet", "target": "Farm:animal_Daisy",
        })

    def test_go_to_success(self):
        """go_to 成功时返回结果。v2.4.x: ctx.go_to() 发送 go_to 命令（非 move_to）。"""
        result = _make_bridge_result(success=True, data={"arrived": True})
        bridge = _make_mock_bridge(result)
        ctx = SkillContext(bridge, Path("/tmp/game_state.json"))

        res = asyncio.run(ctx.go_to("Farm:animal_Daisy", arrive_distance=1))

        assert res["success"] is True
        bridge.write_command.assert_called_once_with("go_to", {
            "target": "Farm:animal_Daisy", "arrive_distance": 1,
        })

    def test_go_to_timeout(self):
        """go_to 超时时返回 TIMEOUT 错误。"""
        bridge = MagicMock()
        bridge.write_command.return_value = "act_0002_test"
        bridge.read_command_result.return_value = None  # 永不返回结果
        bridge.cancel_command.return_value = None
        ctx = SkillContext(bridge, Path("/tmp/game_state.json"))

        # 使用极短超时触发超时
        res = asyncio.run(ctx._execute_and_wait("move_to", {"target": "test"}, timeout=0.5))

        assert res["success"] is False
        assert res["error"]["code"] == "TIMEOUT"
        bridge.cancel_command.assert_called_once_with("act_0002_test")


# ── get_state 测试 ───────────────────────────────────────────────────────────

class TestGetState:
    """测试 get_state 读取 game_state.json。"""

    def test_get_state_success(self, tmp_path):
        """正确读取并解析 game_state.json。"""
        state_path = tmp_path / "game_state.json"
        state_path.write_text(json.dumps(SAMPLE_GAME_STATE), encoding="utf-8")

        bridge = MagicMock()
        ctx = SkillContext(bridge, state_path)

        state = asyncio.run(ctx.get_state())

        assert isinstance(state, GameState)
        assert state.stamina == 200.0
        assert state.max_stamina == 270.0
        assert state.gold == 5000
        assert state.location == "Farm"
        assert state.x == 10.0
        assert state.y == 20.0
        assert state.current_tool == "Watering Can"
        assert state.time == 900
        assert state.day == 15
        assert state.season == "Spring"
        assert state.day_of_week == "Monday"

    def test_get_state_file_not_found(self, tmp_path):
        """game_state.json 不存在时抛出 FileNotFoundError。"""
        state_path = tmp_path / "nonexistent.json"
        bridge = MagicMock()
        ctx = SkillContext(bridge, state_path)

        with pytest.raises(FileNotFoundError):
            asyncio.run(ctx.get_state())


# ── check_cancelled 测试 ─────────────────────────────────────────────────────

class TestCancellation:
    """测试取消机制。"""

    def test_check_cancelled_not_set(self):
        """未取消时不抛异常。"""
        ctx = SkillContext(MagicMock(), Path("/tmp/test"))
        ctx.check_cancelled()  # 不应抛异常

    def test_check_cancelled_raises(self):
        """取消后抛出 SkillCancelled。"""
        ctx = SkillContext(MagicMock(), Path("/tmp/test"))
        ctx.cancel()
        assert ctx.is_cancelled is True

        with pytest.raises(SkillCancelled):
            ctx.check_cancelled()

    def test_cancel_during_wait(self):
        """等待结果期间被取消时，应取消 Mod 命令并抛出 SkillCancelled。"""
        bridge = MagicMock()
        bridge.write_command.return_value = "act_0003_test"
        bridge.read_command_result.return_value = None  # 持续无结果
        bridge.cancel_command.return_value = None

        ctx = SkillContext(bridge, Path("/tmp/test"))

        async def cancel_after_delay():
            """在短暂延迟后取消 ctx。"""
            await asyncio.sleep(0.2)
            ctx.cancel()

        async def run_test():
            task = asyncio.create_task(cancel_after_delay())
            with pytest.raises(SkillCancelled):
                await ctx._execute_and_wait("move_to", {"target": "test"}, timeout=5.0)
            await task

        asyncio.run(run_test())
        bridge.cancel_command.assert_called_once_with("act_0003_test")

    def test_cancel_during_wait_cancels_once(self):
        bridge = MagicMock()
        bridge.write_command.return_value = "act_0004_test"
        bridge.read_command_result.return_value = None
        bridge.cancel_command.return_value = None
        ctx = SkillContext(bridge, Path("/tmp/test"))

        async def cancel_after_delay():
            await asyncio.sleep(0.2)
            ctx.cancel()
            ctx.cancel()

        async def run_test():
            task = asyncio.create_task(cancel_after_delay())
            with pytest.raises(SkillCancelled):
                await ctx._execute_and_wait("move_to", {"target": "test"}, timeout=5.0)
            await task

        asyncio.run(run_test())
        bridge.cancel_command.assert_called_once_with("act_0004_test")

    def test_cancel_after_write_race_cancels_new_action(self):
        bridge = MagicMock()
        bridge.read_command_result.return_value = None
        bridge.cancel_command.return_value = None
        ctx_holder = {}

        def write_command(_capability, _params):
            ctx_holder["ctx"].cancel()
            return "act_0005_test"

        bridge.write_command.side_effect = write_command
        ctx = SkillContext(bridge, Path("/tmp/test"))
        ctx_holder["ctx"] = ctx

        with pytest.raises(SkillCancelled):
            asyncio.run(ctx._execute_and_wait("move_to", {"target": "test"}, timeout=5.0))

        bridge.cancel_command.assert_called_once_with("act_0005_test")
