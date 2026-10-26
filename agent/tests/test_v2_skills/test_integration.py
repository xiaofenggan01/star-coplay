"""test_integration — L3 SkillRuntime 与 FC 循环集成测试。

覆盖 12 个场景：
 1. FC 循环识别 skill: 工具
 2. 工具集包含 L3 技能
 3. 正常执行链路（完整 mock）
 4. 无动物时的行为
 5. 部分失败（go_to 失败）
 6. 技能超时
 7. 技能取消
 8. SkillRuntime 未加载时的降级
 9. 不存在的技能
10. 旧链路不受影响
11. _format_skill_result 格式化
12. asyncio.run 在同步线程中工作
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from v2_skills.registry import SkillRegistry, SkillEntry
from v2_skills.runner import SkillRunner
from v2_skills.context import SkillContext
from v2_skills.errors import BridgeError, SkillCancelled
from bridge.bridge_result import BridgeResult


# ── 测试数据 ─────────────────────────────────────────────────────────────────

MOCK_GAME_STATE = {
    "status": "running",
    "player": {
        "name": "TestPlayer",
        "location": "Farm",
        "tile": {"x": 10, "y": 20},
        "energy": {"current": 200, "max": 270},
        "gold": 5000,
        "equipped_item": {"name": "Scythe", "type": "Tool"},
    },
    "world": {
        "time": 900,
        "day": 15,
        "season": "Spring",
        "day_of_week": "Monday",
    },
}

MOCK_ANIMALS = [
    {
        "name": "Daisy", "display_name": "Daisy", "animal_type": "Cow",
        "location_name": "Farm", "x": 15, "y": 25, "was_petted": False,
        "target_ref": "Farm:animal_Daisy",
    },
    {
        "name": "Clucky", "display_name": "Clucky", "animal_type": "Chicken",
        "location_name": "Farm", "x": 20, "y": 30, "was_petted": False,
        "target_ref": "Farm:animal_Clucky",
    },
    {
        "name": "Woolly", "display_name": "Woolly", "animal_type": "Sheep",
        "location_name": "Farm", "x": 25, "y": 35, "was_petted": False,
        "target_ref": "Farm:animal_Woolly",
    },
]


def _runtime_fact() -> dict:
    return {
        "fact": {
            "player_location": {"map_token": "Farm", "map_id": "Farm", "x": 10, "y": 10},
            "stamina": 270,
            "time_of_day": 900,
        }
    }


def _animal_character(name: str, x: int, y: int, *, was_petted: bool = False) -> dict:
    return {
        "name": name,
        "display_name": name,
        "type": "farm_animal",
        "fact_kind": "farm_animal",
        "position": {"map_id": "Farm", "x": x, "y": y},
        "farm_animal": {"animal_type": "Chicken", "was_petted": was_petted},
    }


def _animal_world(characters: list[dict]) -> dict:
    tiles = []
    for x in range(8, 28):
        for y in range(8, 38):
            tiles.append({"position": {"map_id": "Farm", "x": x, "y": y}, "passable": True})
    return {
        "fact": {
            "map_token": "Farm",
            "map_id": "Farm",
            "tiles": tiles,
            "entities": [],
            "characters": characters,
        }
    }

MOCK_CAPABILITIES = [
    {"name": "move_to", "description": "移动到指定位置", "params": {"type": "object", "properties": {"target": {"type": "string"}}}},
    {"name": "say", "description": "说话", "params": {"type": "object", "properties": {"message": {"type": "string"}}}},
    {"name": "do", "description": "执行动作", "params": {"type": "object", "properties": {"intent": {"type": "string"}}}},
    {"name": "water_crops", "description": "浇水", "params": {"type": "object", "properties": {}}},
]


# ── 辅助工具 ─────────────────────────────────────────────────────────────────

def _make_bridge_result(success: bool, data: dict = None, error_code: str = None, error_msg: str = None) -> BridgeResult:
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


class MockBridge:
    """可编程的 FileBridge mock，支持按 capability 返回不同结果。"""

    def __init__(self, responses: dict[str, list] = None, game_state: dict = None):
        self._responses = responses or {}
        self._commands: list[tuple[str, dict, str]] = []
        self._call_counts: dict[str, int] = {}
        self._game_state = game_state or MOCK_GAME_STATE

    def write_command(self, capability: str, params: dict) -> str:
        action_id = f"act_{len(self._commands):04d}"
        self._commands.append((capability, params, action_id))
        return action_id

    def read_command_result(self, action_id: str) -> Any:
        # 从 _commands 找到对应的 capability
        for cap, params, aid in self._commands:
            if aid == action_id:
                idx = self._call_counts.get(cap, 0)
                self._call_counts[cap] = idx + 1
                responses = self._responses.get(cap, [])
                if idx < len(responses):
                    return responses[idx]
                return _make_bridge_result(success=True, data={})
        return None

    def cancel_command(self, action_id: str) -> None:
        pass

    def read_game_state(self) -> dict:
        return self._game_state

    def capabilities(self) -> list[dict]:
        return MOCK_CAPABILITIES

    def capability_names(self) -> frozenset:
        return frozenset(c["name"] for c in MOCK_CAPABILITIES)


def _make_fc_response(tool_calls: list[dict] = None, content: str = "", finish_reason: str = "tool_calls"):
    """构造模拟 LLM FC 返回值。"""
    tc_objects = []
    for i, tc in enumerate(tool_calls or []):
        tc_objects.append(SimpleNamespace(
            id=f"call_{i:04d}",
            function=SimpleNamespace(
                name=tc["name"],
                arguments=json.dumps(tc.get("arguments", {})),
            ),
        ))

    msg = SimpleNamespace(
        content=content,
        tool_calls=tc_objects if tc_objects else None,
    )
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _make_deliberative_loop(bridge=None, skill_registry=None, skill_runner=None):
    """构造最小化的 DeliberativeLoop 用于测试单个方法。

    不启动线程，直接测试内部方法。
    """
    from bt_engine.message_bus import MessageBus
    from memory.working_memory import WorkingMemory
    from slow_loop.deliberative_loop import DeliberativeLoop

    if bridge is None:
        bridge = MockBridge()

    bus = MessageBus()
    wm = WorkingMemory(max_events=50)
    config = {
        "slow_loop": {
            "min_call_interval_s": 0.0,
            "player_input_debounce_s": 0.5,
        }
    }

    # 构造 loop，但跳过 SkillRuntime 自动加载（通过 patch）
    with patch("slow_loop.deliberative_loop.SkillRegistry", None):
        loop = DeliberativeLoop(
            message_bus=bus,
            bridge=bridge,
            working_memory=wm,
            config=config,
        )

    # 手动注入 skill_registry 和 skill_runner
    loop._skill_registry = skill_registry
    loop._skill_runner = skill_runner

    return loop


def _load_real_registry():
    """加载真实的 SkillRegistry（含 pet_animals 技能）。"""
    skills_dir = Path(__file__).parent.parent.parent / "v2_skills"
    return SkillRegistry(skills_dir)


# ── 场景 1：FC 循环识别 skill: 工具 ──────────────────────────────────────────

class TestScenario01_FCRecognizesSkill:
    """验证 FC 循环正确路由 skill: 前缀的工具调用。"""

    def test_skill_tool_call_dispatched(self):
        """LLM 返回 skill:pet_animals → _execute_skill 同步调用 SkillRunner.execute 并返回真实结果。"""
        from slow_loop.deliberative_loop import DeliberativeLoop

        registry = _load_real_registry()
        assert registry.get("pet_animals") is not None

        # 构造 mock runner，验证 execute 被真实调用
        mock_runner = MagicMock()

        async def mock_execute(entry, params):
            return {
                "success": True, "status": "ok",
                "observation": "已抚摸 2/2 只动物",
                "data": {"petted": 2, "skipped": 0, "total": 2},
            }
        mock_runner.execute = mock_execute

        loop = _make_deliberative_loop(
            skill_registry=registry,
            skill_runner=mock_runner,
        )

        result_text = loop._execute_skill("skill:pet_animals", {})

        # 修复后：不再是 rejected，而是 SkillRunner 返回的真实观测文本
        assert "queued asynchronously" not in result_text
        assert "rejected" not in result_text
        assert "2/2" in result_text or "pet_animals" in result_text


# ── 场景 2：工具集包含 L3 技能 ───────────────────────────────────────────────

class TestScenario02_ToolSchemasIncludeSkills:
    """验证 _build_tool_schemas 返回包含 skill:pet_animals 的 tools 列表。"""

    def test_tools_include_skill(self):
        registry = _load_real_registry()
        loop = _make_deliberative_loop(skill_registry=registry)

        tools = loop._build_tool_schemas(MOCK_CAPABILITIES)

        tool_names = [t["function"]["name"] for t in tools]
        assert "skill-pet_animals" in tool_names

    def test_skill_schema_structure(self):
        registry = _load_real_registry()
        loop = _make_deliberative_loop(skill_registry=registry)

        tools = loop._build_tool_schemas(MOCK_CAPABILITIES)
        skill_tool = next(t for t in tools if t["function"]["name"] == "skill-pet_animals")

        assert skill_tool["type"] == "function"
        func = skill_tool["function"]
        assert "description" in func
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"

    def test_tools_count_reasonable(self):
        registry = _load_real_registry()
        loop = _make_deliberative_loop(skill_registry=registry)

        tools = loop._build_tool_schemas(MOCK_CAPABILITIES)

        # 4 capabilities + 1 remember + 1 skill:pet_animals = 6 (no wiki)
        assert len(tools) >= 6


# ── 场景 3：正常执行链路（完整 mock）─────────────────────────────────────────

class TestScenario03_NormalExecution:
    """完整 mock bridge + game_state，端到端验证 pet_animals 技能执行。"""

    def test_full_pet_animals_execution(self, tmp_path):
        """mock 2 只动物，全部抚摸成功。"""
        game_state_path = tmp_path / "game_state.json"
        game_state_path.write_text(json.dumps(MOCK_GAME_STATE), encoding="utf-8")

        runtime_result = _make_bridge_result(success=True, data=_runtime_fact())
        world_result = _make_bridge_result(success=True, data=_animal_world([
            _animal_character("Daisy", 15, 25),
            _animal_character("Clucky", 20, 30),
        ]))
        nav_result = _make_bridge_result(success=True, data={"arrived": True})
        interact_result = _make_bridge_result(success=True, data={"effect": "petted"})

        bridge = MockBridge(responses={
            "query_runtime_snapshot": [runtime_result, runtime_result, runtime_result],
            "query_world_region": [world_result],
            "move_to": [nav_result, nav_result],
            "interact": [interact_result, interact_result],
        })

        runner = SkillRunner(bridge, game_state_path)
        registry = _load_real_registry()
        entry = registry.get("pet_animals")

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert result["data"]["petted"] == 2
        assert result["data"]["total"] == 2
        assert result["data"]["skipped"] == 0

        commands = [c[0] for c in bridge._commands]
        assert "query_world_region" in commands
        assert "move_to" in commands
        assert "interact" in commands
        assert "find_targets" not in commands
        assert "do" not in commands
        assert "say" not in commands


# ── 场景 4：无动物时的行为 ───────────────────────────────────────────────────

class TestScenario04_NoAnimals:
    """find_targets 返回空列表时技能正确处理。"""

    def test_no_animals_returns_zero(self, tmp_path):
        game_state_path = tmp_path / "game_state.json"
        game_state_path.write_text(json.dumps(MOCK_GAME_STATE), encoding="utf-8")

        runtime_result = _make_bridge_result(success=True, data=_runtime_fact())
        world_result = _make_bridge_result(success=True, data=_animal_world([]))
        bridge = MockBridge(responses={
            "query_runtime_snapshot": [runtime_result],
            "query_world_region": [world_result],
        })

        runner = SkillRunner(bridge, game_state_path)
        registry = _load_real_registry()
        entry = registry.get("pet_animals")

        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert result["status"] == "ok"
        assert result["data"]["petted"] == 0
        assert "没有" in result["observation"]


# ── 场景 5：部分失败（go_to 失败）────────────────────────────────────────────

class TestScenario05_PartialFailure:
    """3 只动物，第 2 只导航失败，验证 partial 结果。"""

    def test_partial_failure_on_nav(self, tmp_path):
        game_state_path = tmp_path / "game_state.json"
        game_state_path.write_text(json.dumps(MOCK_GAME_STATE), encoding="utf-8")

        runtime_result = _make_bridge_result(success=True, data=_runtime_fact())
        world_result = _make_bridge_result(success=True, data=_animal_world([
            _animal_character("Daisy", 15, 25),
            _animal_character("Clucky", 20, 30),
            _animal_character("Woolly", 25, 35),
        ]))
        nav_ok = _make_bridge_result(success=True, data={"arrived": True})
        nav_fail = _make_bridge_result(success=False, error_code="PATH_BLOCKED", error_msg="Cannot reach target")
        interact_ok = _make_bridge_result(success=True, data={"effect": "petted"})

        bridge = MockBridge(responses={
            "query_runtime_snapshot": [runtime_result, runtime_result, runtime_result, runtime_result],
            "query_world_region": [world_result],
            "move_to": [nav_ok, nav_fail, nav_ok],
            "interact": [interact_ok, interact_ok],
        })

        runner = SkillRunner(bridge, game_state_path)
        entry = _load_real_registry().get("pet_animals")

        result = asyncio.run(runner.execute(entry, {}))

        assert result["data"]["petted"] == 2
        assert result["data"]["skipped"] == 1
        assert result["data"]["total"] == 3
        # petted < total → partial
        assert result["status"] == "partial"


# ── 场景 6：技能超时 ─────────────────────────────────────────────────────────

class TestScenario06_SkillTimeout:
    """SkillRunner 超时，验证 TIMEOUT 错误。"""

    def test_timeout_returns_error(self, tmp_path):
        game_state_path = tmp_path / "game_state.json"
        game_state_path.write_text(json.dumps(MOCK_GAME_STATE), encoding="utf-8")

        runtime_result = _make_bridge_result(success=True, data=_runtime_fact())
        world_result = _make_bridge_result(success=True, data=_animal_world([
            _animal_character("Daisy", 15, 25),
        ]))
        bridge = MockBridge(responses={
            "query_runtime_snapshot": [runtime_result, runtime_result],
            "query_world_region": [world_result],
        })
        # move_to 永不返回结果（read_command_result 返回 None）
        original_read = bridge.read_command_result
        bridge.read_command_result = lambda aid: (
            None if any(c[0] == "move_to" and c[2] == aid for c in bridge._commands)
            else original_read(aid)
        )

        runner = SkillRunner(bridge, game_state_path)
        entry = _load_real_registry().get("pet_animals")
        # 使用极短超时
        entry_short = SkillEntry(
            name=entry.name,
            func=entry.func,
            metadata=entry.metadata,
            fc_schema=entry.fc_schema,
            timeout=1.5,
        )

        result = asyncio.run(runner.execute(entry_short, {}))

        assert result["success"] is False
        assert result["status"] == "failed"
        assert result["error"]["code"] == "TIMEOUT"


# ── 场景 7：技能取消 ─────────────────────────────────────────────────────────

class TestScenario07_SkillCancelled:
    """技能执行中被取消，验证 partial 状态。"""

    def test_cancelled_returns_partial(self):
        async def slow_skill(ctx):
            ctx.check_cancelled()
            await asyncio.sleep(10)
            return {"success": True, "status": "ok"}

        bridge = MagicMock()
        runner = SkillRunner(bridge, Path("/tmp/game_state.json"))
        entry = SkillEntry(
            name="slow_skill", func=slow_skill, metadata={},
            fc_schema={}, timeout=5.0,
        )

        async def run_cancel():
            # 创建 context 并立即标记取消
            # SkillRunner 内部创建 ctx，我们需要通过另一种方式触发取消
            # 使用一个自身在第一次 check_cancelled 就抛异常的技能
            pass

        # 更直接的方式：技能函数自行抛 SkillCancelled
        async def auto_cancel_skill(ctx):
            raise SkillCancelled()

        entry2 = SkillEntry(
            name="auto_cancel", func=auto_cancel_skill, metadata={},
            fc_schema={}, timeout=5.0,
        )

        result = asyncio.run(runner.execute(entry2, {}))
        assert result["status"] == "partial"
        assert result["success"] is True
        assert "取消" in result.get("observation", "")


# ── 场景 8：SkillRuntime 未加载时的降级 ──────────────────────────────────────

class TestScenario08_RuntimeNotLoaded:
    """skill_registry=None 时返回错误文本。"""

    def test_not_loaded_returns_error(self):
        loop = _make_deliberative_loop(
            skill_registry=None,
            skill_runner=None,
        )

        result = loop._queue_skill("skill:pet_animals", {}, "", 100)
        assert "SkillRuntime 未加载" in result


# ── 场景 9：不存在的技能 ─────────────────────────────────────────────────────

class TestScenario09_NonexistentSkill:
    """调用不存在的技能时返回错误文本。"""

    def test_nonexistent_skill_returns_error(self):
        registry = _load_real_registry()
        loop = _make_deliberative_loop(
            skill_registry=registry,
            skill_runner=MagicMock(),
        )

        result = loop._queue_skill("skill:nonexistent_skill", {}, "", 100)
        assert "技能不存在" in result
        assert "nonexistent_skill" in result


# ── 场景 10：旧链路不受影响 ──────────────────────────────────────────────────

class TestScenario10_OldToolsUnaffected:
    """旧工具（water_crops）和新工具（skill:pet_animals）可共存。"""

    def test_both_old_and_new_tools_present(self):
        registry = _load_real_registry()
        loop = _make_deliberative_loop(skill_registry=registry)

        tools = loop._build_tool_schemas(MOCK_CAPABILITIES)
        tool_names = [t["function"]["name"] for t in tools]

        # v2.4.x: _build_tool_schemas 已重构为忽略 capabilities 参数，
        # 数据源改为 core_tools + skill_registry，不再包含 adapter_info 的旧工具名。
        # 旧工具（water_crops）以 skill:water_crops 形式出现在 tool_names 中。
        assert "skill-water_crops" in tool_names  # Skill 注册表中的浇水技能
        # core_tools 中的基本能力存在
        assert "say" in tool_names
        assert "go_to" in tool_names
        # L3 Skill 存在（skill: 前缀）
        assert "skill-pet_animals" in tool_names

    def test_old_tool_execution_unaffected(self):
        """旧工具走原有 LONG_ACTIONS 分支。"""
        from slow_loop.deliberative_loop import LONG_ACTIONS
        assert "water_crops" in LONG_ACTIONS

        # skill: 前缀不在 LONG_ACTIONS 中
        assert "skill-pet_animals" not in LONG_ACTIONS

    def test_dispatch_routes_correctly(self):
        """验证 skill: 前缀路由到 _execute_skill，其他保持不变。"""
        registry = _load_real_registry()
        mock_runner = MagicMock()

        async def mock_execute(entry, params):
            return {
                "success": True, "status": "ok",
                "observation": "测试完成",
                "data": {"petted": 1},
            }
        mock_runner.execute = mock_execute

        loop = _make_deliberative_loop(
            skill_registry=registry,
            skill_runner=mock_runner,
        )

        # skill: 路由 — 修复后应返回真实观测文本，而非 rejected
        result = loop._execute_skill("skill:pet_animals", {})
        assert "queued asynchronously" not in result
        assert "rejected" not in result
        assert "测试完成" in result

        # 非 skill: 路由不走 _execute_skill（verify by checking it doesn't affect name dispatch）
        assert not "water_crops".startswith("skill:")


# ── 场景 11：_format_skill_result 格式化 ─────────────────────────────────────

class TestScenario11_FormatSkillResult:
    """测试三种状态的格式化输出。"""

    def test_format_success(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True, "status": "ok",
            "observation": "已抚摸 3/3 只动物",
            "data": {"petted": 3, "skipped": 0, "total": 3},
        }
        text = DeliberativeLoop._format_skill_result("pet_animals", result)

        assert "[skill:pet_animals]" in text
        assert "已抚摸 3/3" in text
        assert 'data={"petted":3,"skipped":0,"total":3}' in text

    def test_format_structured_data_as_json(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True,
            "status": "ok",
            "observation": "找到 1 个候选",
            "data": {
                "items": [
                    {
                        "ref_id": "world_entity:crop:farm:1:0#o_24",
                        "tool_args": {"x": 1, "y": 0},
                    }
                ]
            },
        }
        text = DeliberativeLoop._format_skill_result("find_interactions", result)

        assert '"ref_id":"world_entity:crop:farm:1:0#o_24"' in text
        assert '"tool_args":{"x":1,"y":0}' in text

    def test_format_find_interactions_preserves_actionable_steps_before_truncation(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        items = []
        for index in range(80):
            items.append({
                "ref_id": f"world_entity:crop:farm:{index}:0#o_24",
                "action": "use_tool",
                "reason": "作物尚未浇水",
                "summary": "x" * 200,
                "execution_gate": {
                    "status": "ready",
                    "recommended_steps_policy": "directly_executable_if_preconditions_hold",
                },
                "preconditions": ["stand_adjacent_or_in_range"],
                "recommended_steps": [
                    {
                        "tool": "move_to",
                        "args": {
                            "mode": "adjacent",
                            "x": index,
                            "y": 0,
                            "adjacent_side": "left",
                        },
                    },
                    {
                        "tool": "use_tool",
                        "args": {"x": index, "y": 0},
                    },
                ],
            })
        result = {
            "success": True,
            "status": "ok",
            "observation": "当前视野内有很多交互候选",
            "data": {
                "items": items,
                "next_query": {
                    "tool": "find_interactions",
                    "args": {"radius": 8, "limit": 20, "offset": 20},
                },
            },
        }

        text = DeliberativeLoop._format_skill_result("find_interactions", result)

        assert text.index("actionable=") < text.index("data=")
        assert '"next_query":{"tool":"find_interactions","args":{"radius":8,"limit":20,"offset":20}}' in text
        assert text.index('"execution_gate":{"status":"ready"') < text.index('"recommended_steps"')
        assert text.index('"preconditions":["stand_adjacent_or_in_range"]') < text.index('"recommended_steps"')
        assert '"recommended_steps":[{"tool":"move_to","args":{"mode":"adjacent","x":0,"y":0,"adjacent_side":"left"}},{"tool":"use_tool","args":{"x":0,"y":0}}]' in text
        assert "...[truncated]" in text

    def test_format_find_interactions_keeps_bed_trigger_preconditions_actionable(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True,
            "status": "ok",
            "observation": "当前视野内有 1 个交互候选：move_to_trigger 1 个。",
            "data": {
                "items": [
                    {
                        "ref_id": "world_entity:bed:farmhouse:9:8#f_2048",
                        "action": "move_to_trigger",
                        "reason": "床需要走到可睡眠触发格来打开睡觉确认，不应作为普通 interact 目标处理",
                        "execution_gate": {
                            "status": "ready",
                            "recommended_steps_policy": "directly_executable_if_preconditions_hold",
                        },
                        "preconditions": [
                            "trigger_tile_known",
                            "sleep_confirmation_required",
                            "do_not_use_interact",
                        ],
                        "recommended_steps": [
                            {
                                "tool": "move_to",
                                "args": {"mode": "exact", "x": 10, "y": 9},
                            }
                        ],
                    }
                ],
            },
        }

        text = DeliberativeLoop._format_skill_result("find_interactions", result)

        assert text.index("actionable=") < text.index("data=")
        assert '"action":"move_to_trigger"' in text
        assert "不应作为普通 interact 目标处理" in text
        assert '"preconditions":["trigger_tile_known","sleep_confirmation_required","do_not_use_interact"]' in text
        assert '"recommended_steps":[{"tool":"move_to","args":{"mode":"exact","x":10,"y":9}}]' in text
        assert '"tool":"interact"' not in text.split("data=", 1)[0]

    def test_format_find_interactions_preserves_gate_and_warnings_before_steps(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True,
            "status": "ok",
            "observation": "找到 1 个候选",
            "data": {
                "items": [
                    {
                        "ref_id": "world_entity:resource_clump:farm:1:0#stump",
                        "action": "use_tool",
                        "reason": "资源点可用 axe 清理",
                        "tool_requirement": {
                            "kind": "tool_category",
                            "category": "axe",
                            "matching_inventory_items": [
                                {
                                    "slot": 33,
                                    "item_id": "Axe",
                                    "display_name": "斧头",
                                    "equip_args": {"slot": 33},
                                    "qualified_item_id": "(T)Axe",
                                }
                            ],
                        },
                        "execution_gate": {
                            "status": "requires_resolution",
                            "recommended_steps_policy": "conditional_do_not_execute_as_batch",
                            "warning_codes": ["ACTIVE_TOOL_MISMATCH_OR_UNKNOWN"],
                            "message": "先处理 execution_warnings，再考虑执行 recommended_steps。",
                        },
                        "execution_warnings": [
                            {
                                "code": "ACTIVE_TOOL_MISMATCH_OR_UNKNOWN",
                                "message": "当前手持工具未确认匹配目标所需工具。",
                                "required_tool_category": "axe",
                                "active_item": {"item_id": "CopperPickaxe"},
                                "matching_inventory_items": [
                                    {
                                        "slot": 33,
                                        "item_id": "Axe",
                                        "display_name": "斧头",
                                        "equip_args": {"slot": 33},
                                        "qualified_item_id": "(T)Axe",
                                    }
                                ],
                                "suggested_next": "先装备匹配工具，再执行 use_tool。",
                            }
                        ],
                        "recommended_steps": [
                            {
                                "tool": "move_to",
                                "args": {"mode": "adjacent", "x": 1, "y": 0, "adjacent_side": "left"},
                            },
                            {"tool": "use_tool", "args": {"x": 1, "y": 0}},
                        ],
                    }
                ]
            },
        }

        text = DeliberativeLoop._format_skill_result("find_interactions", result)

        assert text.index("actionable=") < text.index("data=")
        assert text.index('"execution_gate":{"status":"requires_resolution"') < text.index('"recommended_steps"')
        assert text.index('"tool_requirement":{"kind":"tool_category","category":"axe"') < text.index('"recommended_steps"')
        assert '"recommended_steps_policy":"conditional_do_not_execute_as_batch"' in text
        assert '"execution_warnings":[{"code":"ACTIVE_TOOL_MISMATCH_OR_UNKNOWN"' in text
        assert '"matching_inventory_items":[{"slot":33,"item_id":"Axe","display_name":"斧头","equip_args":{"slot":33}}]' in text
        assert '"suggested_next":"先装备匹配工具，再执行 use_tool。"' in text

    def test_format_search_facts_preserves_query_summary_before_truncation(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        items = []
        for index in range(80):
            items.append({
                "ref_id": f"inventory:item:player:{index}#t_tool",
                "type": "inventory_item",
                "label": "铜十字镐" if index == 0 else f"工具 {index}",
                "summary": "槽位 5，数量 1，工具等级 1" if index == 0 else "x" * 200,
                "fact": {"debug_blob": "y" * 300},
            })
        result = {
            "success": True,
            "status": "ok",
            "observation": "当前视野内匹配到 80 个背包物品，返回 80 个。",
            "data": {
                "scope": "current_view",
                "fact_type": "inventory_item",
                "items": items,
                "total": 80,
                "limit": 80,
                "offset": 40,
                "next_offset": 120,
                "has_more": True,
                "next_query": {
                    "tool": "search_facts",
                    "args": {"fact_type": "inventory_item", "sort": "slot", "limit": 80, "offset": 120, "radius": 8},
                },
                "schema": {
                    "fact_type": "inventory_item",
                    "label": "背包物品",
                    "default_sort": "slot",
                    "filter_fields": [
                        "base_sell_price",
                        "can_stack",
                        "display_name",
                        "internal_name",
                        "is_active",
                        "item_id",
                        "slot",
                        "upgrade_level",
                    ],
                    "sort_fields": ["slot", "upgrade_level"],
                    "operators": {"upgrade_level": ["eq", "exists", "in", "max", "min"]},
                },
            },
        }

        text = DeliberativeLoop._format_skill_result("search_facts", result)

        assert text.index("query_summary=") < text.index("data=")
        assert '"ref_id":"inventory:item:player:0#t_tool"' in text
        assert '"summary":"槽位 5，数量 1，工具等级 1"' in text
        assert '"offset":40' in text
        assert '"next_offset":120' in text
        assert '"items_in_summary":5' in text
        assert '"items_available":80' in text
        assert '"items_truncated":true' in text
        assert '"next_query":{"tool":"search_facts","args":{"fact_type":"inventory_item","sort":"slot","limit":80,"offset":120,"radius":8}}' in text
        assert '"filter_fields":["base_sell_price","can_stack","display_name","internal_name","is_active","item_id","slot","upgrade_level"]' in text
        assert "...[truncated]" in text

    def test_format_observe_preserves_group_summary_before_truncation(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        groups = []
        for index in range(60):
            groups.append({
                "fact_type": "tree" if index == 0 else f"type_{index}",
                "label": "树木" if index == 0 else f"类型 {index}",
                "count": 120 if index == 0 else index,
                "summary": {"has_moss": 30, "fertilized": 10} if index == 0 else {"blob": "x" * 200},
                "representatives": {
                    "items": [f"world_entity:tree:farm:{i}:0#wt_1" for i in range(20)],
                    "sort": "distance",
                    "limit": 10,
                    "has_more": True,
                    "next_query": {
                        "tool": "search_facts",
                        "args": {"fact_type": "tree", "sort": "distance", "limit": 10, "offset": 10},
                    },
                },
            })
        result = {
            "success": True,
            "status": "ok",
            "observation": "当前视野内有很多对象。",
            "data": {"scope": "current_view", "groups": groups},
        }

        text = DeliberativeLoop._format_skill_result("observe", result)

        assert text.index("query_summary=") < text.index("data=")
        assert '"fact_type":"tree"' in text
        assert '"summary":{"has_moss":30,"fertilized":10}' in text
        assert '"items":["world_entity:tree:farm:0:0#wt_1","world_entity:tree:farm:1:0#wt_1"' in text
        assert '"items_in_summary":5' in text
        assert '"items_available":20' in text
        assert '"items_truncated":true' in text
        assert '"group_count":60' in text
        assert '"groups_in_summary":5' in text
        assert '"groups_truncated":true' in text
        assert '"next_query":{"tool":"search_facts","args":{"fact_type":"tree","sort":"distance","limit":10,"offset":10}}' in text
        assert "...[truncated]" in text

    def test_format_observe_detail_preserves_next_query_before_truncation(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True,
            "status": "ok",
            "observation": "当前视野内有 80 个庄稼，按距离优先展示 30 个。",
            "data": {
                "scope": "current_view",
                "detail": "crops",
                "items": [
                    {
                        "ref_id": f"crop-{index}",
                        "type": "crop",
                        "label": "Parsnip",
                        "summary": "需要浇水",
                        "fact": {"debug_blob": "x" * 300},
                    }
                    for index in range(80)
                ],
                "total": 80,
                "limit": 30,
                "has_more": True,
                "next_query": {
                    "tool": "search_facts",
                    "args": {"fact_type": "crop", "sort": "distance", "limit": 30, "offset": 30},
                },
            },
        }

        text = DeliberativeLoop._format_skill_result("observe", result)

        assert text.index("query_summary=") < text.index("data=")
        assert '"items_in_summary":5' in text
        assert '"items_available":80' in text
        assert '"items_truncated":true' in text
        assert '"next_query":{"tool":"search_facts","args":{"fact_type":"crop","sort":"distance","limit":30,"offset":30}}' in text
        assert "...[truncated]" in text

    def test_format_inspect_preserves_compact_fact_before_truncation(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True,
            "status": "ok",
            "observation": "已 inspect 2 个 ref，其中 2 个成功解析：床, 出口到 Farm。",
            "data": {
                "items": [
                    {
                        "ref_id": "world_entity:bed:farmhouse:9:8#f_2048",
                        "resolved": True,
                        "status": "REF_RESOLVED",
                        "type": "bed",
                        "label": "床",
                        "summary": "Bed(Single) at (9,8)",
                        "fact": {
                            "display_name": "床",
                            "can_sleep": True,
                            "is_being_slept_in": True,
                            "bed_spot": {"x": 10, "y": 9},
                            "bounding_tiles": [{"x": index, "y": index} for index in range(80)],
                        },
                    },
                    {
                        "ref_id": "world_entity:warp:farmhouse:3:12#farm",
                        "resolved": True,
                        "status": "REF_RESOLVED",
                        "type": "warp",
                        "label": "出口到 Farm",
                        "summary": "Warp → Farm (56,16)",
                        "fact": {
                            "target_map": "Farm",
                            "target_x": 56,
                            "target_y": 16,
                            "debug_blob": "x" * 1000,
                        },
                    },
                ],
                "inspected": [{"raw": "x" * 1000} for _ in range(30)],
                "requested_refs": [
                    "world_entity:bed:farmhouse:9:8#f_2048",
                    "world_entity:warp:farmhouse:3:12#farm",
                ],
            },
        }

        text = DeliberativeLoop._format_skill_result("inspect", result)

        assert text.index("query_summary=") < text.index("data=")
        assert '"ref_id":"world_entity:bed:farmhouse:9:8#f_2048"' in text
        assert '"fact":{"display_name":"床","can_sleep":true,"is_being_slept_in":true,"bed_spot":{"x":10,"y":9}}' in text
        assert '"fact":{"target_map":"Farm","target_x":56,"target_y":16' in text
        assert "...[truncated]" in text

    def test_format_partial(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": True, "status": "partial",
            "observation": "已抚摸 1/3 只动物，跳过 2 只",
            "data": {"petted": 1, "skipped": 2, "total": 3},
        }
        text = DeliberativeLoop._format_skill_result("pet_animals", result)

        assert "[部分完成]" in text
        assert "跳过 2" in text

    def test_format_failed(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {
            "success": False, "status": "failed",
            "error": {"code": "TIMEOUT", "message": "pet_animals 超时 (120s)"},
        }
        text = DeliberativeLoop._format_skill_result("pet_animals", result)

        assert "[失败:" in text
        assert "超时" in text

    def test_format_non_dict(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        text = DeliberativeLoop._format_skill_result("pet_animals", "not a dict")
        assert "[skill:pet_animals]" in text
        assert "完成" in text

    def test_format_empty_data(self):
        from slow_loop.deliberative_loop import DeliberativeLoop

        result = {"success": True, "status": "ok", "observation": "好的"}
        text = DeliberativeLoop._format_skill_result("test", result)
        assert "好的" in text


# ── 场景 12：asyncio.run 在同步线程中工作 ────────────────────────────────────

class TestScenario12_AsyncInSyncThread:
    """验证 asyncio.run 在非 async 环境中正确执行技能。"""

    def test_asyncio_run_works_in_sync_context(self, tmp_path):
        """在普通同步函数中 asyncio.run(runner.execute(...)) 不报 RuntimeError。"""
        game_state_path = tmp_path / "game_state.json"
        game_state_path.write_text(json.dumps(MOCK_GAME_STATE), encoding="utf-8")

        runtime_result = _make_bridge_result(success=True, data=_runtime_fact())
        world_result = _make_bridge_result(success=True, data=_animal_world([]))
        bridge = MockBridge(responses={
            "query_runtime_snapshot": [runtime_result],
            "query_world_region": [world_result],
        })

        runner = SkillRunner(bridge, game_state_path)
        entry = _load_real_registry().get("pet_animals")

        # 在同步上下文中直接调用 asyncio.run
        result = asyncio.run(runner.execute(entry, {}))

        assert result["success"] is True
        assert result["data"]["petted"] == 0

    def test_execute_skill_method_sync(self):
        """DeliberativeLoop._execute_skill 在同步上下文中真实调用 SkillRunner 并返回观测文本。"""
        registry = _load_real_registry()

        async def mock_execute(entry, params):
            return {
                "success": True, "status": "ok",
                "observation": "同步测试完成",
                "data": {},
            }

        mock_runner = MagicMock()
        mock_runner.execute = mock_execute

        loop = _make_deliberative_loop(
            skill_registry=registry,
            skill_runner=mock_runner,
        )

        result = loop._execute_skill("skill:pet_animals", {})
        # 修复后：返回真实观测文本，而非 rejected
        assert "queued asynchronously" not in result
        assert "rejected" not in result
        assert "同步测试完成" in result


# ── 场景 13：observe core_tool dispatch（PR-1 hotfix 验证）─────────────────────

class TestScenario13_ObserveCoreToolDispatch:
    """验证 LLM 调 observe 时 tool_result 包含真实游戏状态，而非 "rejected"。

    背景（PR-1 hotfix）：_execute_skill 之前是存根，始终返回
    "rejected: skill tools are queued asynchronously"，导致感知能力失效。
    修复后应同步执行 SkillRunner.execute 并返回真实观测文本。
    """

    def test_observe_dispatch_returns_real_game_state(self):
        """LLM 调 observe → tool_result 包含真实位置/状态，不含 rejected。"""
        from slow_loop.deliberative_loop import DeliberativeLoop, L3_BACKED_CORE_TOOLS

        # 确认 observe 在 L3_BACKED_CORE_TOOLS 中
        assert "observe" in L3_BACKED_CORE_TOOLS

        registry = _load_real_registry()

        async def mock_observe_execute(entry, params):
            return {
                "status": "ok",
                "observation": "测试位置: Farm (10, 20), 精力: 200/270, 季节: Spring",
                "data": {
                    "location": "Farm",
                    "tile": {"x": 10, "y": 20},
                    "energy": 200,
                    "season": "Spring",
                },
            }

        mock_runner = MagicMock()
        mock_runner.execute = mock_observe_execute

        loop = _make_deliberative_loop(
            skill_registry=registry,
            skill_runner=mock_runner,
        )

        # 模拟 FC 循环路由 observe：通过 L3_BACKED_CORE_TOOLS 派发
        skill_id = L3_BACKED_CORE_TOOLS["observe"]
        result_text = loop._execute_skill(f"skill:{skill_id}", {})

        # 核心断言：不是 rejected，而是真实游戏状态
        assert "rejected" not in result_text
        assert "queued asynchronously" not in result_text
        assert "Farm" in result_text or "测试位置" in result_text

    def test_observe_dispatch_with_no_skill_runtime_returns_error_not_crash(self):
        """SkillRuntime 未加载时返回错误字符串，不崩溃。"""
        loop = _make_deliberative_loop(
            skill_registry=None,
            skill_runner=None,
        )

        result_text = loop._execute_skill("skill:observe", {})

        assert "SkillRuntime 未加载" in result_text
        assert "rejected" not in result_text

    def test_observe_dispatch_with_missing_skill_returns_error(self):
        """技能不在 registry 中时返回错误字符串，不崩溃。"""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # 技能不存在

        loop = _make_deliberative_loop(
            skill_registry=mock_registry,
            skill_runner=MagicMock(),
        )

        result_text = loop._execute_skill("skill:nonexistent", {})

        assert "技能不存在" in result_text
        assert "rejected" not in result_text
