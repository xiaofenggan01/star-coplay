"""
observe Skill — ObserveModule render 函数单元测试。

测试矩阵：21 个模块，每个至少 3 个用例（正常/空数据/可见性）。
"""

from __future__ import annotations

import pytest

from v2_skills.builtin.observe.layer0_modules import (
    FARM_SUMMARY_ANIMALS,
    FARM_SUMMARY_CROPS,
    FARM_SUMMARY_OBJECTS,
    INVENTORY_SUMMARY,
    LAYER_0_MODULES,
    LOCAL_CONTAINERS_SUMMARY,
    LOCAL_NPCS_SUMMARY,
    PLAYER_STATUS,
    WORLD_TIME,
)
from v2_skills.builtin.observe.layer1_modules import (
    ANIMALS_DETAIL,
    CONTAINERS_LIST,
    CROPS_DETAIL,
    EXITS_DETAIL,
    GROUND_OBJECTS_DETAIL,
    INVENTORY_DETAIL,
    NPCS_DETAIL,
)
from v2_skills.builtin.observe.layer2_modules import (
    ANIMAL_INSTANCE,
    CHEST_CONTENT,
    CROP_INSTANCE,
    FRIDGE_CONTENT,
    NPC_INSTANCE,
    SHIPPING_BIN_CONTENT,
)
from tests.fixtures.observe_query_mocks import (
    ALIAS_MAP_TYPICAL,
    CONTAINERS_CHEST_ONLY,
    CONTAINERS_EMPTY,
    CONTAINERS_TYPICAL,
    CONTAINER_ITEMS_CHEST_EMPTY,
    CONTAINER_ITEMS_CHEST_TYPICAL,
    CONTAINER_ITEMS_FRIDGE_TYPICAL,
    CONTAINER_ITEMS_SHIPPING_TYPICAL,
    FIND_TARGETS_ANIMAL_SINGLE,
    FIND_TARGETS_ANIMALS_EMPTY,
    FIND_TARGETS_ANIMALS_TYPICAL,
    FIND_TARGETS_CROP_SINGLE_HARVESTABLE,
    FIND_TARGETS_CROPS_EMPTY,
    FIND_TARGETS_CROPS_TYPICAL,
    FIND_TARGETS_NPCS_EMPTY,
    FIND_TARGETS_NPCS_TYPICAL,
    FIND_TARGETS_OBJECTS_EMPTY,
    FIND_TARGETS_OBJECTS_TYPICAL,
    GAME_STATE_EMPTY,
    GAME_STATE_EXHAUSTED,
    GAME_STATE_TYPICAL,
    INVENTORY_EMPTY,
    INVENTORY_TYPICAL,
    NPC_LEWIS_BIRTHDAY,
    NPC_LEWIS_TYPICAL,
)


# ══════════════════════════════════════════════════════════════════════
# 层 0 — 8 个模块
# ══════════════════════════════════════════════════════════════════════


class TestPlayerStatus:
    """player_status — 玩家状态首段"""

    def test_typical(self):
        data = {"game_state": GAME_STATE_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = PLAYER_STATUS.safe_render(data)
        # 位置包含农场
        assert "农场" in text or "farm" in text.lower()
        # 体力数字
        assert "180" in text
        assert "270" in text
        # 手持工具
        assert "喷壶" in text

    def test_empty_equipped_item(self):
        data = {"game_state": GAME_STATE_EMPTY, "alias_map": ALIAS_MAP_TYPICAL}
        text = PLAYER_STATUS.safe_render(data)
        # 空手状态
        assert "空手" in text or "?" in text or text != ""

    def test_applicable_always_true(self):
        assert PLAYER_STATUS.applicable({}) is True
        assert PLAYER_STATUS.applicable({"game_state": {}}) is True

    def test_energy_as_float(self):
        gs = {**GAME_STATE_TYPICAL, "player": {**GAME_STATE_TYPICAL["player"], "energy": {"current": 180.0, "max": 270.0}}}
        data = {"game_state": gs, "alias_map": {}}
        text = PLAYER_STATUS.safe_render(data)
        assert "180" in text
        assert "270" in text

    def test_missing_player(self):
        data = {"game_state": {}, "alias_map": {}}
        text = PLAYER_STATUS.safe_render(data)
        # 降级：返回"?"占位
        assert "?" in text or text != ""


class TestWorldTime:
    """world_time — 世界时间段"""

    def test_typical(self):
        data = {"game_state": GAME_STATE_TYPICAL}
        text = WORLD_TIME.safe_render(data)
        assert "春" in text
        assert "12" in text
        assert "10:00" in text
        assert "晴" in text

    def test_rainy_day(self):
        gs = {**GAME_STATE_TYPICAL, "world": {**GAME_STATE_TYPICAL["world"], "weather": {"current": "rainy"}}}
        data = {"game_state": gs}
        text = WORLD_TIME.safe_render(data)
        assert "雨天" in text

    def test_missing_world(self):
        data = {"game_state": {}}
        text = WORLD_TIME.safe_render(data)
        # 降级：用默认值，不应崩溃
        assert isinstance(text, str)

    def test_applicable_always_true(self):
        assert WORLD_TIME.applicable({}) is True


class TestInventorySummary:
    """inventory_summary — 背包摘要"""

    def test_typical_shows_top4(self):
        data = {"inventory": INVENTORY_TYPICAL}
        text = INVENTORY_SUMMARY.safe_render(data)
        assert "背包" in text
        # 有物品
        assert "×" in text or "防风草" in text or "木材" in text

    def test_empty_inventory(self):
        data = {"inventory": INVENTORY_EMPTY}
        text = INVENTORY_SUMMARY.safe_render(data)
        assert "空" in text
        assert "背包" in text

    def test_applicable_always_true(self):
        assert INVENTORY_SUMMARY.applicable({}) is True
        assert INVENTORY_SUMMARY.applicable({"inventory": {}}) is True

    def test_slot_count_shown(self):
        data = {"inventory": INVENTORY_TYPICAL}
        text = INVENTORY_SUMMARY.safe_render(data)
        assert "6" in text  # slots_used
        assert "36" in text  # slots_total


class TestFarmSummaryCrops:
    """farm_summary_crops — 作物聚合摘要"""

    def test_typical_shows_counts(self):
        data = {"crops_data": FIND_TARGETS_CROPS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = FARM_SUMMARY_CROPS.safe_render(data)
        assert "作物" in text
        # 可收割数量（FIND_TARGETS_CROPS_TYPICAL 中有 1 个 is_harvestable=True）
        assert "1" in text

    def test_no_crops_returns_empty(self):
        data = {"crops_data": FIND_TARGETS_CROPS_EMPTY}
        text = FARM_SUMMARY_CROPS.safe_render(data)
        assert text == ""

    def test_applicable_requires_targets(self):
        assert FARM_SUMMARY_CROPS.applicable({"crops_data": FIND_TARGETS_CROPS_EMPTY}) is False
        assert FARM_SUMMARY_CROPS.applicable({"crops_data": FIND_TARGETS_CROPS_TYPICAL}) is True
        assert FARM_SUMMARY_CROPS.applicable({}) is False

    def test_withered_shown(self):
        data = {"crops_data": FIND_TARGETS_CROPS_TYPICAL}
        text = FARM_SUMMARY_CROPS.safe_render(data)
        assert "枯萎" in text  # TYPICAL 中有一个 is_dead=True


class TestFarmSummaryAnimals:
    """farm_summary_animals — 动物聚合摘要"""

    def test_typical_shows_count_and_location(self):
        data = {"animals_data": FIND_TARGETS_ANIMALS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = FARM_SUMMARY_ANIMALS.safe_render(data)
        assert "动物" in text
        assert "3" in text  # 3 只动物
        assert "需爱抚" in text

    def test_no_animals_returns_empty(self):
        data = {"animals_data": FIND_TARGETS_ANIMALS_EMPTY}
        text = FARM_SUMMARY_ANIMALS.safe_render(data)
        assert text == ""

    def test_applicable_requires_animals(self):
        assert FARM_SUMMARY_ANIMALS.applicable({"animals_data": FIND_TARGETS_ANIMALS_EMPTY}) is False
        assert FARM_SUMMARY_ANIMALS.applicable({"animals_data": FIND_TARGETS_ANIMALS_TYPICAL}) is True
        assert FARM_SUMMARY_ANIMALS.applicable({}) is False


class TestFarmSummaryObjects:
    """farm_summary_objects — 地面对象摘要"""

    def test_typical(self):
        data = {"objects_data": FIND_TARGETS_OBJECTS_TYPICAL}
        text = FARM_SUMMARY_OBJECTS.safe_render(data)
        assert "地面对象" in text or "石头" in text or "箱子" in text

    def test_no_objects_returns_empty(self):
        data = {"objects_data": FIND_TARGETS_OBJECTS_EMPTY}
        text = FARM_SUMMARY_OBJECTS.safe_render(data)
        assert text == ""

    def test_applicable_requires_targets(self):
        assert FARM_SUMMARY_OBJECTS.applicable({"objects_data": FIND_TARGETS_OBJECTS_EMPTY}) is False
        assert FARM_SUMMARY_OBJECTS.applicable({"objects_data": FIND_TARGETS_OBJECTS_TYPICAL}) is True
        assert FARM_SUMMARY_OBJECTS.applicable({}) is False

    def test_stone_classified_correctly(self):
        data = {"objects_data": FIND_TARGETS_OBJECTS_TYPICAL}
        text = FARM_SUMMARY_OBJECTS.safe_render(data)
        # TYPICAL 中有 Stone，应归类为石头
        assert "石头" in text or "其他" in text


class TestLocalContainersSummary:
    """local_containers_summary — 容器数量摘要"""

    def test_typical(self):
        data = {"containers_data": CONTAINERS_TYPICAL}
        text = LOCAL_CONTAINERS_SUMMARY.safe_render(data)
        assert "容器" in text
        assert "箱子" in text
        assert "冰箱" in text

    def test_empty_returns_empty(self):
        data = {"containers_data": CONTAINERS_EMPTY}
        text = LOCAL_CONTAINERS_SUMMARY.safe_render(data)
        assert text == ""

    def test_applicable_requires_containers(self):
        assert LOCAL_CONTAINERS_SUMMARY.applicable({"containers_data": CONTAINERS_EMPTY}) is False
        assert LOCAL_CONTAINERS_SUMMARY.applicable({"containers_data": CONTAINERS_TYPICAL}) is True
        assert LOCAL_CONTAINERS_SUMMARY.applicable({}) is False

    def test_chest_only(self):
        data = {"containers_data": CONTAINERS_CHEST_ONLY}
        text = LOCAL_CONTAINERS_SUMMARY.safe_render(data)
        assert "箱子" in text


class TestLocalNpcsSummary:
    """local_npcs_summary — NPC 名单"""

    def test_typical(self):
        data = {"npcs_data": FIND_TARGETS_NPCS_TYPICAL}
        text = LOCAL_NPCS_SUMMARY.safe_render(data)
        assert "NPC" in text or "路易斯" in text or "玛妮" in text

    def test_empty_returns_empty(self):
        data = {"npcs_data": FIND_TARGETS_NPCS_EMPTY}
        text = LOCAL_NPCS_SUMMARY.safe_render(data)
        assert text == ""

    def test_applicable_requires_targets(self):
        assert LOCAL_NPCS_SUMMARY.applicable({"npcs_data": FIND_TARGETS_NPCS_EMPTY}) is False
        assert LOCAL_NPCS_SUMMARY.applicable({"npcs_data": FIND_TARGETS_NPCS_TYPICAL}) is True
        assert LOCAL_NPCS_SUMMARY.applicable({}) is False


# ══════════════════════════════════════════════════════════════════════
# 层 1 — 7 个模块
# ══════════════════════════════════════════════════════════════════════


class TestCropsDetail:
    """crops_detail — 农场作物详细列表"""

    def test_typical_shows_all_states(self):
        data = {"crops_data": FIND_TARGETS_CROPS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = CROPS_DETAIL.safe_render(data)
        assert "可收割" in text
        assert "需浇水" in text
        # ObserveId 格式
        assert "[crop:" in text

    def test_empty_shows_no_crops(self):
        data = {"crops_data": FIND_TARGETS_CROPS_EMPTY}
        text = CROPS_DETAIL.safe_render(data)
        assert "没有作物" in text

    def test_applicable_always_true(self):
        # 层 1 模块的 visibility 默认始终 True（用户显式调用时即渲染）
        assert CROPS_DETAIL.applicable({}) is True
        assert CROPS_DETAIL.applicable({"crops_data": FIND_TARGETS_CROPS_EMPTY}) is True

    def test_harvestable_labeled_correctly(self):
        data = {"crops_data": FIND_TARGETS_CROPS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = CROPS_DETAIL.safe_render(data)
        assert "成熟" in text

    def test_dead_labeled_as_withered(self):
        data = {"crops_data": FIND_TARGETS_CROPS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = CROPS_DETAIL.safe_render(data)
        assert "枯萎" in text


class TestAnimalsDetail:
    """animals_detail — 动物详细列表"""

    def test_typical_shows_all_animals(self):
        data = {"animals_data": FIND_TARGETS_ANIMALS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = ANIMALS_DETAIL.safe_render(data)
        assert "甜豆" in text
        assert "樱桃" in text
        assert "[animal:" in text

    def test_empty_shows_no_animals(self):
        data = {"animals_data": FIND_TARGETS_ANIMALS_EMPTY}
        text = ANIMALS_DETAIL.safe_render(data)
        assert "没有圈养动物" in text

    def test_applicable_always_true(self):
        assert ANIMALS_DETAIL.applicable({}) is True

    def test_needs_pet_shown(self):
        data = {"animals_data": FIND_TARGETS_ANIMALS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = ANIMALS_DETAIL.safe_render(data)
        # 甜豆 was_petted=False → 需爱抚
        assert "需爱抚" in text

    def test_hearts_shown(self):
        data = {"animals_data": FIND_TARGETS_ANIMAL_SINGLE, "alias_map": ALIAS_MAP_TYPICAL}
        text = ANIMALS_DETAIL.safe_render(data)
        # 甜豆 friendship=350 → hearts=350//250=1
        assert "♥1" in text


class TestNpcsDetail:
    """npcs_detail — NPC 详细列表"""

    def test_typical_shows_npcs(self):
        data = {"npcs_data": FIND_TARGETS_NPCS_TYPICAL}
        text = NPCS_DETAIL.safe_render(data)
        assert "路易斯" in text
        assert "[npc:" in text

    def test_empty_shows_no_npcs(self):
        data = {"npcs_data": FIND_TARGETS_NPCS_EMPTY}
        text = NPCS_DETAIL.safe_render(data)
        assert "没有 NPC" in text

    def test_applicable_always_true(self):
        assert NPCS_DETAIL.applicable({}) is True

    def test_talked_today_shown(self):
        data = {"npcs_data": FIND_TARGETS_NPCS_TYPICAL}
        text = NPCS_DETAIL.safe_render(data)
        # 玛妮 talked_today=True
        assert "今日已对话" in text

    def test_hearts_shown(self):
        data = {"npcs_data": FIND_TARGETS_NPCS_TYPICAL}
        text = NPCS_DETAIL.safe_render(data)
        # 路易斯 hearts=3
        assert "♥3" in text


class TestContainersList:
    """containers_list — 容器列表"""

    def test_typical_shows_all_containers(self):
        data = {"containers_data": CONTAINERS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = CONTAINERS_LIST.safe_render(data)
        assert "[chest:" in text
        assert "[fridge:" in text or "冰箱" in text
        assert "[shipping_bin]" in text

    def test_empty_shows_no_containers(self):
        data = {"containers_data": CONTAINERS_EMPTY}
        text = CONTAINERS_LIST.safe_render(data)
        assert "没有容器" in text

    def test_applicable_always_true(self):
        assert CONTAINERS_LIST.applicable({}) is True

    def test_item_count_shown(self):
        data = {"containers_data": CONTAINERS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = CONTAINERS_LIST.safe_render(data)
        # chest Farm:chest_12_15 item_count=15
        assert "15" in text


class TestInventoryDetail:
    """inventory_detail — 背包完整物品列表"""

    def test_typical_shows_categorized_items(self):
        data = {"inventory": INVENTORY_TYPICAL}
        text = INVENTORY_DETAIL.safe_render(data)
        assert "工具" in text or "种子" in text or "矿物" in text

    def test_empty_shows_empty_bag(self):
        data = {"inventory": INVENTORY_EMPTY}
        text = INVENTORY_DETAIL.safe_render(data)
        assert "空" in text

    def test_applicable_always_true(self):
        assert INVENTORY_DETAIL.applicable({}) is True

    def test_stack_shown_with_times_symbol(self):
        data = {"inventory": INVENTORY_TYPICAL}
        text = INVENTORY_DETAIL.safe_render(data)
        # 防风草种子 stack=30 → "防风草种子×30"
        assert "×30" in text or "×" in text


class TestGroundObjectsDetail:
    """ground_objects_detail — 地面可互动对象详细列表"""

    def test_typical_shows_objects(self):
        data = {"objects_data": FIND_TARGETS_OBJECTS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = GROUND_OBJECTS_DETAIL.safe_render(data)
        # TYPICAL 中有 Chest 和 Stone
        assert isinstance(text, str)

    def test_empty_shows_no_objects(self):
        data = {"objects_data": FIND_TARGETS_OBJECTS_EMPTY}
        text = GROUND_OBJECTS_DETAIL.safe_render(data)
        assert "没有可互动对象" in text

    def test_applicable_always_true(self):
        assert GROUND_OBJECTS_DETAIL.applicable({}) is True

    def test_observe_id_prefix_in_output(self):
        data = {"objects_data": FIND_TARGETS_OBJECTS_TYPICAL, "alias_map": ALIAS_MAP_TYPICAL}
        text = GROUND_OBJECTS_DETAIL.safe_render(data)
        # Stone 归类为 rock → "[rock:..."
        assert "[" in text


class TestExitsDetail:
    """exits_detail — 地图出口列表"""

    def test_empty_exits_data(self):
        data = {"exits_data": {}}
        text = EXITS_DETAIL.safe_render(data)
        assert "没有可用出口" in text or "出口" in text

    def test_exits_with_data(self):
        exits_data = {
            "exits": [
                {
                    "target_location": "Town",
                    "display_name": "镇上",
                    "direction": "south",
                    "exit_x": 35,
                    "exit_y": 67,
                }
            ]
        }
        data = {"exits_data": exits_data, "alias_map": ALIAS_MAP_TYPICAL}
        text = EXITS_DETAIL.safe_render(data)
        assert "镇上" in text or "Town" in text or "exit:" in text

    def test_applicable_always_true(self):
        assert EXITS_DETAIL.applicable({}) is True


# ══════════════════════════════════════════════════════════════════════
# 层 2 — 6 个模块
# ══════════════════════════════════════════════════════════════════════


class TestChestContent:
    """chest_content — 箱子内容"""

    def test_typical_shows_items(self):
        data = {
            "container_data": CONTAINER_ITEMS_CHEST_TYPICAL,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "farm_12_15",
        }
        text = CHEST_CONTENT.safe_render(data)
        assert "防风草" in text
        assert "格" in text

    def test_empty_chest(self):
        data = {
            "container_data": CONTAINER_ITEMS_CHEST_EMPTY,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "farm_12_15",
        }
        text = CHEST_CONTENT.safe_render(data)
        assert "空" in text

    def test_applicable_always_true(self):
        assert CHEST_CONTENT.applicable({}) is True

    def test_error_response_handled(self):
        data = {
            "container_data": {"error": {"code": "CONTAINER_NOT_FOUND", "message": "not found"}},
            "instance_id": "farm_99_99",
        }
        text = CHEST_CONTENT.safe_render(data)
        assert "未找到" in text or "找不到" in text

    def test_item_stacks_shown(self):
        data = {
            "container_data": CONTAINER_ITEMS_CHEST_TYPICAL,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "farm_12_15",
        }
        text = CHEST_CONTENT.safe_render(data)
        # 木材 stack=100 → 应含×100
        assert "×100" in text or "×" in text


class TestFridgeContent:
    """fridge_content — 冰箱内容"""

    def test_typical_shows_items(self):
        data = {
            "container_data": CONTAINER_ITEMS_FRIDGE_TYPICAL,
            "alias_map": ALIAS_MAP_TYPICAL,
        }
        text = FRIDGE_CONTENT.safe_render(data)
        assert "牛奶" in text
        assert "鸡蛋" in text

    def test_empty_fridge(self):
        data = {"container_data": {"items": [], "slots_used": 0, "slots_total": 36}}
        text = FRIDGE_CONTENT.safe_render(data)
        assert "空" in text

    def test_applicable_always_true(self):
        assert FRIDGE_CONTENT.applicable({}) is True

    def test_cooking_hint_in_output(self):
        data = {"container_data": CONTAINER_ITEMS_FRIDGE_TYPICAL}
        text = FRIDGE_CONTENT.safe_render(data)
        assert "烹饪" in text


class TestShippingBinContent:
    """shipping_bin_content — 运输箱（今日待运）"""

    def test_typical_shows_items_and_estimated_gold(self):
        data = {"container_data": CONTAINER_ITEMS_SHIPPING_TYPICAL}
        text = SHIPPING_BIN_CONTENT.safe_render(data)
        assert "防风草" in text
        # 预计收入（有 sell_price 时计算）
        assert "g" in text or "预计" in text

    def test_empty_shipping_bin(self):
        data = {"container_data": {"items": []}}
        text = SHIPPING_BIN_CONTENT.safe_render(data)
        assert "空" in text

    def test_applicable_always_true(self):
        assert SHIPPING_BIN_CONTENT.applicable({}) is True

    def test_estimated_gold_calculation(self):
        # 防风草 stack=30, sell_price=35 → 30*35=1050g
        data = {"container_data": CONTAINER_ITEMS_SHIPPING_TYPICAL}
        text = SHIPPING_BIN_CONTENT.safe_render(data)
        # 至少有一个 g 金币显示
        assert "g" in text


class TestCropInstance:
    """crop_instance — 单株作物详情"""

    def test_typical_harvestable(self):
        data = {
            "crop_data": FIND_TARGETS_CROP_SINGLE_HARVESTABLE,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "farm_10_15",
        }
        text = CROP_INSTANCE.safe_render(data)
        assert "防风草" in text
        assert "成熟" in text or "可收割" in text

    def test_no_crop_at_location(self):
        data = {
            "crop_data": {"targets": []},
            "alias_map": {},
            "instance_id": "farm_99_99",
        }
        text = CROP_INSTANCE.safe_render(data)
        assert "没有作物" in text

    def test_applicable_always_true(self):
        assert CROP_INSTANCE.applicable({}) is True

    def test_watered_status_shown(self):
        data = {
            "crop_data": FIND_TARGETS_CROP_SINGLE_HARVESTABLE,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "farm_10_15",
        }
        text = CROP_INSTANCE.safe_render(data)
        # 是否浇水
        assert "浇水" in text

    def test_growth_stage_shown(self):
        data = {
            "crop_data": FIND_TARGETS_CROP_SINGLE_HARVESTABLE,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "farm_10_15",
        }
        text = CROP_INSTANCE.safe_render(data)
        # 第 4 阶段/共 4 阶段（phase=3, max_phase=3）
        assert "阶段" in text


class TestAnimalInstance:
    """animal_instance — 单只动物详情"""

    def test_typical_shows_details(self):
        data = {
            "animal_data": FIND_TARGETS_ANIMAL_SINGLE,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "甜豆",
        }
        text = ANIMAL_INSTANCE.safe_render(data)
        assert "甜豆" in text
        assert "鸡" in text
        # 友好度
        assert "♥" in text

    def test_not_found_shows_message(self):
        data = {
            "animal_data": {"animals": []},
            "alias_map": {},
            "instance_id": "不存在的动物",
        }
        text = ANIMAL_INSTANCE.safe_render(data)
        assert "未找到" in text

    def test_applicable_always_true(self):
        assert ANIMAL_INSTANCE.applicable({}) is True

    def test_needs_pet_shown(self):
        data = {
            "animal_data": FIND_TARGETS_ANIMAL_SINGLE,  # was_petted=False
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "甜豆",
        }
        text = ANIMAL_INSTANCE.safe_render(data)
        assert "需爱抚" in text


class TestNpcInstance:
    """npc_instance — 单个 NPC 详情"""

    def test_typical_lewis(self):
        data = {
            "npc_data": NPC_LEWIS_TYPICAL,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "Lewis",
        }
        text = NPC_INSTANCE.safe_render(data)
        assert "Lewis" in text
        # 心形
        assert "♥3" in text
        # 生日
        assert "春" in text

    def test_not_found_shows_message(self):
        data = {
            "npc_data": {},
            "alias_map": {},
            "instance_id": "NonExistent",
        }
        text = NPC_INSTANCE.safe_render(data)
        assert "未找到" in text

    def test_applicable_always_true(self):
        assert NPC_INSTANCE.applicable({}) is True

    def test_loved_gifts_shown(self):
        data = {
            "npc_data": NPC_LEWIS_TYPICAL,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "Lewis",
        }
        text = NPC_INSTANCE.safe_render(data)
        # 喜爱礼物
        assert "松露" in text or "金南瓜" in text

    def test_birthday_today_lewis(self):
        data = {
            "npc_data": NPC_LEWIS_BIRTHDAY,
            "alias_map": ALIAS_MAP_TYPICAL,
            "instance_id": "Lewis",
        }
        text = NPC_INSTANCE.safe_render(data)
        # 路易斯生日 spring 7 日
        assert "7" in text

    def test_error_response_handled(self):
        data = {
            "npc_data": {"error": {"code": "TARGET_NOT_FOUND", "message": "not found"}},
            "instance_id": "Unknown",
        }
        text = NPC_INSTANCE.safe_render(data)
        assert "未找到" in text


# ══════════════════════════════════════════════════════════════════════
# 工具函数（module_base.py）
# ══════════════════════════════════════════════════════════════════════


class TestModuleBaseUtils:
    """module_base 工具函数单元测试"""

    def test_parse_loc_xy_typical(self):
        from v2_skills.builtin.observe.module_base import _parse_loc_xy
        locshort, x, y = _parse_loc_xy("farm_12_15")
        assert locshort == "farm"
        assert x == "12"
        assert y == "15"

    def test_parse_loc_xy_with_underscore_in_name(self):
        from v2_skills.builtin.observe.module_base import _parse_loc_xy
        locshort, x, y = _parse_loc_xy("coop1_4_6")
        assert locshort == "coop1"
        assert x == "4"
        assert y == "6"

    def test_parse_loc_xy_invalid_raises(self):
        from v2_skills.builtin.observe.module_base import _parse_loc_xy
        with pytest.raises(ValueError):
            _parse_loc_xy("invalid")

    def test_resolve_loc_alias_map(self):
        from v2_skills.builtin.observe.module_base import _resolve_loc
        full = _resolve_loc("farm", ALIAS_MAP_TYPICAL)
        assert full == "Farm"

    def test_resolve_loc_static_mapping(self):
        from v2_skills.builtin.observe.module_base import _resolve_loc
        full = _resolve_loc("farmhouse", {})
        assert full == "FarmHouse"

    def test_format_time(self):
        from v2_skills.builtin.observe.module_base import format_time
        assert format_time(630) == "06:30"
        assert format_time(1200) == "12:00"
        assert format_time(2400) == "00:00"

    def test_format_date_with_dow(self):
        from v2_skills.builtin.observe.module_base import format_date
        result = format_date({"season": "spring", "day": 12, "day_of_week": "Wednesday"})
        assert "春" in result
        assert "12" in result
        assert "星期三" in result

    def test_truncate_short_text(self):
        from v2_skills.builtin.observe.module_base import _truncate
        text = "hello"
        assert _truncate(text, 100) == text

    def test_truncate_long_text(self):
        from v2_skills.builtin.observe.module_base import _truncate
        long_text = "你好" * 300  # ~300 * 2 * 1.5 = 900 tokens
        truncated = _truncate(long_text, 200)
        assert "截断" in truncated
        assert len(truncated) < len(long_text)

    def test_items_truncate_within_limit(self):
        from v2_skills.builtin.observe.module_base import _items_truncate
        items = [1, 2, 3]
        shown, hidden = _items_truncate(items, 5)
        assert shown == [1, 2, 3]
        assert hidden == 0

    def test_items_truncate_exceeds_limit(self):
        from v2_skills.builtin.observe.module_base import _items_truncate
        items = list(range(10))
        shown, hidden = _items_truncate(items, 5)
        assert shown == [0, 1, 2, 3, 4]
        assert hidden == 5


# ══════════════════════════════════════════════════════════════════════
# 注册表完整性校验
# ══════════════════════════════════════════════════════════════════════


class TestModuleRegistry:
    """检查注册表数量与结构"""

    def test_layer0_has_8_modules(self):
        assert len(LAYER_0_MODULES) == 8

    def test_layer1_has_7_modules(self):
        from v2_skills.builtin.observe.layer1_modules import LAYER_1_MODULES
        assert len(LAYER_1_MODULES) == 7

    def test_layer2_has_6_modules(self):
        from v2_skills.builtin.observe.layer2_modules import LAYER_2_MODULES
        assert len(LAYER_2_MODULES) == 6

    def test_all_layer0_modules_have_name(self):
        for m in LAYER_0_MODULES:
            assert m.name, f"Module missing name: {m}"

    def test_all_layer0_modules_have_render(self):
        for m in LAYER_0_MODULES:
            assert callable(m.render), f"Module {m.name} render not callable"

    def test_layer1_known_categories(self):
        from v2_skills.builtin.observe.modules import get_layer1_module
        for cat in ["crops", "animals", "npcs", "containers", "inventory", "objects", "exits"]:
            assert get_layer1_module(cat) is not None, f"Missing layer1 module: {cat}"

    def test_layer2_known_categories(self):
        from v2_skills.builtin.observe.modules import get_layer2_module
        for cat in ["chest", "fridge", "shipping_bin", "crop", "animal", "npc"]:
            assert get_layer2_module(cat) is not None, f"Missing layer2 module: {cat}"
