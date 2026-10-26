"""
Tests for execute_on_targets and menu_interact L2 capabilities.

Verifies:
1. Action classification (LONG_ACTIONS membership)
2. SkillContext parameter construction (via source parsing, avoids heavy import chain)
3. Sample result data structures
4. E2E test case definitions

Note: These tests extract LONG_ACTIONS directly from source to avoid
importing the full deliberative_loop module (which pulls in openai, py_trees, etc.).
Full integration tests should run in the Poetry environment on Mac.
"""

import re
import ast
from pathlib import Path

import pytest

# ── 直接解析源文件提取常量（避免 import 链依赖 openai/py_trees）─────────────
TESTS_DIR = Path(__file__).resolve().parent
AGENT_DIR = TESTS_DIR.parent

def _extract_frozenset_from_source(filepath: Path, var_name: str) -> frozenset:
    """从 Python 源文件中提取 frozenset 常量。"""
    source = filepath.read_text(encoding="utf-8")
    pattern = rf'^{var_name}\s*=\s*frozenset\((\{{[^}}]+\}})\)'
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        raise ValueError(f"Cannot find {var_name} in {filepath}")
    return frozenset(ast.literal_eval(match.group(1)))

LONG_ACTIONS = _extract_frozenset_from_source(
    AGENT_DIR / "slow_loop" / "action_policy.py", "LONG_ACTIONS"
)


# ── Helpers ──────────────────────────────────────────────────────────────────

CONTEXT_SOURCE = (AGENT_DIR / "v2_skills" / "context.py").read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
#  1. Action Classification
# ════════════════════════════════════════════════════════════════════════════

class TestActionClassification:
    """Verify new L2 capabilities are in LONG_ACTIONS."""

    def test_execute_on_targets_is_long_action(self):
        assert "execute_on_targets" in LONG_ACTIONS

    def test_go_to_is_long_action(self):
        assert "go_to" in LONG_ACTIONS

    def test_menu_interact_is_long_action(self):
        assert "menu_interact" in LONG_ACTIONS

    def test_original_long_actions_preserved(self):
        for action in ("move_to", "do", "use_tool", "buy_items", "water_crops"):
            assert action in LONG_ACTIONS


# ════════════════════════════════════════════════════════════════════════════
#  2. SkillContext — 方法存在性和签名验证（源码级）
# ════════════════════════════════════════════════════════════════════════════

class TestSkillContextMethods:
    """Verify SkillContext has execute_on_targets and menu_interact methods."""

    def test_execute_on_targets_method_exists(self):
        assert "async def execute_on_targets(" in CONTEXT_SOURCE

    def test_execute_on_targets_params(self):
        """关键参数定义正确。"""
        assert "targets: list[str]" in CONTEXT_SOURCE
        assert 'action: str = "do"' in CONTEXT_SOURCE
        assert "tool: str | None = None" in CONTEXT_SOURCE
        assert "tool_category: str | None = None" in CONTEXT_SOURCE
        assert "intent: str | None = None" in CONTEXT_SOURCE
        assert 'repeat_per_target: str = "once"' in CONTEXT_SOURCE
        assert "stamina_below: int = 0" in CONTEXT_SOURCE
        assert "time_after: int = 0" in CONTEXT_SOURCE

    def test_execute_on_targets_timeout(self):
        """超时设为 300 秒（批量任务）。"""
        assert 'timeout=timeout_for_action("execute_on_targets")' in CONTEXT_SOURCE

    def test_execute_on_targets_capability_name(self):
        assert '"execute_on_targets"' in CONTEXT_SOURCE

    def test_menu_interact_method_exists(self):
        assert "async def menu_interact(" in CONTEXT_SOURCE

    def test_menu_interact_params(self):
        """关键参数定义正确。"""
        assert "menu_type: str" in CONTEXT_SOURCE
        assert "operations: list[dict]" in CONTEXT_SOURCE
        assert "target: str | None = None" in CONTEXT_SOURCE
        assert "shop: str | None = None" in CONTEXT_SOURCE

    def test_menu_interact_timeout(self):
        """超时设为 120 秒。"""
        assert 'timeout=timeout_for_action("menu_interact")' in CONTEXT_SOURCE

    def test_menu_interact_capability_name(self):
        assert '"menu_interact"' in CONTEXT_SOURCE


# ════════════════════════════════════════════════════════════════════════════
#  4. Sample Result Data (for future result parsing tests)
# ════════════════════════════════════════════════════════════════════════════

SAMPLE_EXECUTE_ON_TARGETS_RESULT = {
    "seq": 50,
    "action_id": "act_0050",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "completed": 2,
        "skipped": 0,
        "total": 2,
        "stopped_reason": "all_done",
    },
    "observation": "completed=2 skipped=0",
}

SAMPLE_MENU_INTERACT_SHOP_RESULT = {
    "seq": 60,
    "action_id": "act_0060",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "menu_type": "Shop",
        "operations_requested": 1,
        "operations_completed": 1,
        "operations_failed": 0,
        "results": [
            {"action": "buy", "item": "Parsnip Seeds", "qty": 10, "cost": 200, "success": True}
        ],
        "gold_remaining": 4800,
    },
}

SAMPLE_MENU_INTERACT_CONTAINER_RESULT = {
    "seq": 61,
    "action_id": "act_0061",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "menu_type": "Container",
        "operations_requested": 1,
        "operations_completed": 1,
        "operations_failed": 0,
        "results": [
            {"action": "put", "item": "Parsnip", "qty": 50, "remaining_in_inventory": 0, "success": True}
        ],
    },
}

SAMPLE_FIND_TARGETS_LITTER_RESULT = {
    "seq": 55,
    "action_id": "act_0055",
    "protocol_version": "2.0",
    "success": True,
    "status": "ok",
    "data": {
        "type": "find_targets",
        "target_type": "object",
        "location": "Farm",
        "total": 3,
        "targets": [
            {
                "target_ref": "Farm:object_12_8",
                "location_name": "Farm",
                "x": 12,
                "y": 8,
                "name": "Weeds",
                "display_name": "Weeds",
                "item_id": "(O)0",
                "object_type": "Litter",
                "category": -999,
                "is_ready": False,
                "minutes_until_ready": 0,
                "is_forage": False,
            },
            {
                "target_ref": "Farm:object_15_11",
                "location_name": "Farm",
                "x": 15,
                "y": 11,
                "name": "Twig",
                "display_name": "Twig",
                "item_id": "(O)294",
                "object_type": "Litter",
                "category": -999,
                "is_ready": False,
                "minutes_until_ready": 0,
                "is_forage": False,
            },
            {
                "target_ref": "Farm:object_20_5",
                "location_name": "Farm",
                "x": 20,
                "y": 5,
                "name": "Stone",
                "display_name": "Stone",
                "item_id": "(O)390",
                "object_type": "Litter",
                "category": -999,
                "is_ready": False,
                "minutes_until_ready": 0,
                "is_forage": False,
            },
        ],
    },
    "observation": "found 3 objects at Farm (category=-999)",
}

SAMPLE_MENU_INTERACT_NOT_IMPLEMENTED = {
    "seq": 62,
    "action_id": "act_0062",
    "protocol_version": "2.0",
    "success": False,
    "status": "failed",
    "error": {
        "code": "NOT_IMPLEMENTED",
        "message": "menu_type 'Geode' 尚未实现（计划 v2.5.0+）。当前支持: Shop, Container, Craft, Ship",
    },
}


# ════════════════════════════════════════════════════════════════════════════
#  5. Mod C# 源码结构验证
# ════════════════════════════════════════════════════════════════════════════

MOD_DIR = AGENT_DIR.parent / "mod" / "StarCoPlay"
LEGACY_DIR = MOD_DIR / "Legacy"
ADAPTER_DIR = LEGACY_DIR / "AdapterRuntime"
COMPOUND_DIR = LEGACY_DIR / "CompoundActions" / "Handlers" / "Compound"
TARGETING_DIR = LEGACY_DIR / "DeprecatedTargeting"
QUERY_CORE_DIR = MOD_DIR / "QueryCore"

class TestModSourceStructure:
    """Verify Mod-side files exist and contain expected structures."""

    def test_menu_interact_handler_exists(self):
        path = COMPOUND_DIR / "MenuInteractHandler.cs"
        assert path.exists(), f"MenuInteractHandler.cs not found at {path}"

    def test_menu_interact_registered_in_adapter(self):
        adapter = (ADAPTER_DIR / "AdapterV2.cs").read_text(encoding="utf-8")
        assert 'CapabilityMenuInteract = "menu_interact"' in adapter

    def test_menu_interact_registered_in_modentry(self):
        modentry = (MOD_DIR / "ModEntry.cs").read_text(encoding="utf-8")
        assert "MenuInteractHandler" in modentry
        assert "CapabilityMenuInteract" in modentry

    def test_execute_on_targets_has_max_repeats(self):
        handler = (COMPOUND_DIR / "ExecuteOnTargetsHandler.cs").read_text(encoding="utf-8")
        assert "MaxRepeatsPerTarget" in handler

    def test_execute_on_targets_has_until_gone_guard(self):
        handler = (COMPOUND_DIR / "ExecuteOnTargetsHandler.cs").read_text(encoding="utf-8")
        assert "until_gone" in handler
        assert "animal_" in handler  # animal guard

    def test_goto_has_total_nav_failures(self):
        handler = (COMPOUND_DIR / "GoToHandler.cs").read_text(encoding="utf-8")
        assert "GoToPhase.Failed" in handler
        assert "NavigationTimeout" in handler
        assert "BuildFailure" in handler

    def test_execute_on_targets_resolves_object_case(self):
        """ExecuteOnTargetsHandler 的 TargetRef 解析包含 "object" case，
        使得 find_targets target_type=object 返回的 TargetRef 可被正确路由。"""
        handler = (COMPOUND_DIR / "ExecuteOnTargetsHandler.cs").read_text(encoding="utf-8")
        resolver = (TARGETING_DIR / "TargetRefResolver.cs").read_text(encoding="utf-8")
        assert "TargetRefResolver.Resolve" in handler
        assert '"object"' in resolver, "TargetRefResolver 应支持 object 类型 TargetRef"

    def test_handle_find_objects_no_litter_exclusion(self):
        """QueryCore 不应排除 Litter，clear_farm 场景应能观察到 Twig/Weeds/Stone。"""
        classifier = (QUERY_CORE_DIR / "Classification" / "EntityClassifier.cs").read_text(encoding="utf-8")
        resource_node = (
            QUERY_CORE_DIR / "Projectors" / "WorldEntity" / "ResourceNodeProjector.cs"
        ).read_text(encoding="utf-8")
        assert '"Twig"' in classifier
        assert '"Weeds"' in classifier
        assert '"Stone"' in classifier
        assert "IsWeeds" not in classifier
        assert "IsTwig" not in classifier
        assert '"Twig"' in resource_node
        assert '"Weeds"' in resource_node

    def test_world_region_radius_without_center_uses_player_tile(self):
        """query_world_region(radius=N) 未传 center 时应以玩家位置裁剪实体。"""
        region_filter = (QUERY_CORE_DIR / "Scanning" / "RegionFilter.cs").read_text(encoding="utf-8")
        assert "Game1.player" in region_filter
        assert "request.Radius > 0 ? request.Radius : 8" in region_filter
        assert "Math.Max(dx, dy) <= radius" in region_filter

    def test_inventory_snapshot_empty_kind_defaults_to_player(self):
        """query_inventory_snapshot 空参数应默认查询玩家背包。"""
        handler = (QUERY_CORE_DIR / "Handlers" / "QueryInventorySnapshotHandler.cs").read_text(encoding="utf-8")
        assert 'string.IsNullOrWhiteSpace(request.ContainerKind)' in handler
        assert '? "player"' in handler
        assert "ProjectPlayerInventory" in handler

    def test_map_token_lookup_is_case_insensitive_for_encoded_refs(self):
        """encoded map_token 来自 ref_id，解析时不能因 NameOrUniqueName 大小写漂移而失败。"""
        map_lookup = (QUERY_CORE_DIR / "RefId" / "Resolution" / "MapLookup.cs").read_text(encoding="utf-8")
        scanner = (QUERY_CORE_DIR / "Scanning" / "LocationScanner.cs").read_text(encoding="utf-8")
        assert "StringComparison.OrdinalIgnoreCase" in map_lookup
        assert "StringComparison.OrdinalIgnoreCase" in scanner
        assert "Game1.player?.currentLocation" in map_lookup
        assert "Game1.player?.currentLocation" in scanner


# ════════════════════════════════════════════════════════════════════════════
#  6. E2E 测试用例定义（实机验证清单）
#     这些不是自动化测试，是需要在游戏中通过 WS mock 执行的测试场景。
#     用 pytest.mark.skip 标记，作为文档化的测试计划。
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="E2E: 需要游戏运行 + WS mock server")
class TestE2E_ExecuteOnTargets:
    """execute_on_targets 实机验证用例。"""

    def test_pet_all_animals_in_coop(self):
        """
        前置：角色在 Farm，鸡舍内有 2 只鸡
        命令：
          1. find_targets target_type=animal → 获取 targets
          2. go_to 鸡舍
          3. execute_on_targets targets=[...] action=do intent=pet
        验证：
          - completed=2, skipped=0
          - 两只鸡的 wasPetToday=true
        """

    def test_use_tool_watering_can_on_crops(self):
        """
        前置：Farm 上有未浇水的作物
        命令：
          1. find_targets target_type=crop unwatered=true
          2. execute_on_targets targets=[...] action=use_tool tool=喷壶
        验证：
          - completed > 0
          - 作物 is_watered=true
        """

    def test_until_gone_chop_tree(self):
        """
        前置：Farm 上有一棵树
        命令：
          1. find_targets target_type=tree max=1
          2. execute_on_targets targets=[...] action=use_tool tool=斧头 repeat_per_target=until_gone
        验证：
          - 树消失（terrainFeature 被移除）
          - repeat 次数 <= MaxRepeatsPerTarget(10)
        """

    def test_stop_condition_stamina(self):
        """
        前置：角色体力较低
        命令：execute_on_targets targets=[多个] action=use_tool tool=锄头 stamina_below=50
        验证：
          - stopped_reason=stamina_low
          - 在体力低于 50 时停止
        """

    def test_dist_threshold(self):
        """
        前置：目标在障碍物后方（adjacent 可走但路径长）
        命令：execute_on_targets targets=[...] action=do
        验证：
          - dist > 2 时 skip（不是 dist > 3）
        """


@pytest.mark.skip(reason="E2E: 需要游戏运行 + WS mock server")
class TestE2E_MenuInteract:
    """menu_interact 实机验证用例。"""

    def test_shop_buy_seeds(self):
        """
        命令：menu_interact menu_type=Shop target=Pierre
              operations=[{action:buy, item:Parsnip Seeds, qty:10}]
        验证：
          - operations_completed=1
          - 背包新增 Parsnip Seeds x10
          - gold_remaining 正确扣减
        """

    def test_container_put_and_take(self):
        """
        前置：背包有 Parsnip x50，Farm 上有 Chest
        命令 1：menu_interact menu_type=Container target=Farm:chest_X_Y
                operations=[{action:put, item:Parsnip, qty:50}]
        命令 2：menu_interact menu_type=Container target=Farm:chest_X_Y
                operations=[{action:take, item:Parsnip, qty:25}]
        验证：
          - put: transferred=50, remaining_in_inventory=0
          - take: transferred=25, remaining_in_container=25
        """

    def test_craft_chest(self):
        """
        前置：背包有 木材 x50
        命令：menu_interact menu_type=Craft
              operations=[{action:craft, item:Chest, qty:1}]
        验证：
          - qty_crafted=1
          - 背包新增 Chest
          - 木材减少 50
        """

    def test_ship_items(self):
        """
        前置：背包有 Parsnip x30
        命令：menu_interact menu_type=Ship
              operations=[{action:ship, item:Parsnip, qty:30}]
        验证：
          - qty_shipped=30
          - 背包 Parsnip 减少 30
        """

    def test_not_implemented_geode(self):
        """
        命令：menu_interact menu_type=Geode operations=[{action:crack}]
        验证：
          - error.code=NOT_IMPLEMENTED
          - message 包含 "v2.5.0"
          - message 包含 "当前支持: Shop, Container, Craft, Ship"
        """

    def test_unknown_menu_type(self):
        """
        命令：menu_interact menu_type=InvalidType operations=[{}]
        验证：
          - error.code=INVALID_PARAMS
          - message 包含支持的 MenuType 列表
        """
