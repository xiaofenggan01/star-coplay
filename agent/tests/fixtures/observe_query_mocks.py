"""
observe Skill 单元测试用 mock fixtures。

基于 QUERY_INTERFACE.md 调研的 L1 query 实际接口设计。
每个 fixture 模拟一个 query_* 的典型返回，涵盖：
  - 典型场景（有数据）
  - 边界场景（空数据/零作物/无动物）
  - 降级场景（查询失败/字段缺失）

字段名与 QUERY_INTERFACE.md 中的实际 L1 接口精确对齐。
"""

from __future__ import annotations

# ── 1. game_state（Blackboard 同步读取）──────────────────────────────

GAME_STATE_TYPICAL = {
    "status": "running",
    "game_day_started": True,
    "world": {
        "time": 1000,
        "time_display": "10:00",
        "day": 12,
        "season": "spring",
        "year": 1,
        "day_of_week": "Wednesday",
        "weather": {
            "current": "sunny",
            "is_raining": False,
            "is_snowing": False,
            "is_lightning": False,
            "tomorrow_forecast": None,
        },
        "is_festival_day": False,
        "festival_name": None,
    },
    "player": {
        "player_id": 12345678,
        "name": "农夫",
        "location": "Farm",
        "tile": {"x": 10, "y": 15},
        "facing_direction": "down",
        "energy": {"current": 180, "max": 270},
        "health": {"current": 100, "max": 100},
        "gold": 5000,
        "equipped_item": {
            "name": "喷壶（铜级）",
            "type": "WateringCan",
            "item_id": "(T)WateringCan",
            "water_level": 55,
            "water_capacity": 55,
        },
        "current_tool_index": 2,
        "skills": {
            "farming": 3,
            "mining": 1,
            "foraging": 2,
            "fishing": 0,
            "combat": 0,
        },
        "status_effects": {
            "is_exhausted": False,
            "is_swimming": False,
            "is_riding_horse": False,
            "can_move": True,
        },
    },
    "inventory": {
        "slots_used": 12,
        "slots_total": 36,
        "items": [
            {"slot": 0, "name": "防风草种子", "item_id": "(O)24", "stack": 30, "quality": 0, "type": "object", "quality_name": None},
            {"slot": 1, "name": "锄头", "item_id": "(T)Hoe", "stack": 1, "quality": 0, "type": "tool", "quality_name": None},
            {"slot": 2, "name": "喷壶（铜级）", "item_id": "(T)WateringCan", "stack": 1, "quality": 0, "type": "tool", "quality_name": None},
            {"slot": 3, "name": "矿石", "item_id": "(O)378", "stack": 20, "quality": 0, "type": "object", "quality_name": None},
            {"slot": 4, "name": "煤炭", "item_id": "(O)382", "stack": 15, "quality": 0, "type": "object", "quality_name": None},
            {"slot": 5, "name": "金锭", "item_id": "(O)336", "stack": 5, "quality": 0, "type": "object", "quality_name": None},
        ],
    },
    "farm_summary": {
        "crops": {
            "total_planted": 23,
            "unwatered_count": 12,
            "harvestable_count": 8,
            "empty_tilled_tiles": 5,
        },
        "animals": {"total": 3, "unpetted": 3, "product_ready": 1},
        "machines": {"ready_to_collect": 1, "processing": 2, "empty": 0},
    },
    "surroundings": {
        "radius": 8,
        "objects": [{"tile": {"x": 12, "y": 14}, "name": "Stone", "type": "object"}],
        "crops": [
            {"tile": {"x": 10, "y": 15}, "crop_name": "防风草", "watered": False, "harvestable": True, "days_until_harvest": 0}
        ],
        "npcs": [
            {"name": "Lewis", "tile": {"x": 27, "y": 18}, "hearts": 3, "distance": 5.2}
        ],
        "terrain": [],
    },
}

GAME_STATE_EXHAUSTED = {
    **GAME_STATE_TYPICAL,
    "player": {
        **GAME_STATE_TYPICAL["player"],
        "energy": {"current": 10, "max": 270},
        "status_effects": {
            **GAME_STATE_TYPICAL["player"]["status_effects"],
            "is_exhausted": True,
        },
    },
}

GAME_STATE_EMPTY = {
    "status": "running",
    "game_day_started": True,
    "world": GAME_STATE_TYPICAL["world"],
    "player": {
        **GAME_STATE_TYPICAL["player"],
        "location": "Town",
        "equipped_item": None,
    },
    "inventory": {"slots_used": 0, "slots_total": 36, "items": []},
    "farm_summary": None,
    "surroundings": {
        "radius": 8,
        "objects": [],
        "crops": [],
        "npcs": [],
        "terrain": [],
    },
}

# ── 2. query_inventory ────────────────────────────────────────────────

INVENTORY_TYPICAL = {
    "type": "inventory",
    "inventory": {
        "slots_used": 6,
        "slots_total": 36,
        "items": [
            {"slot": 0, "name": "防风草种子", "item_id": "(O)24", "stack": 30, "quality": 0},
            {"slot": 1, "name": "锄头", "item_id": "(T)Hoe", "stack": 1, "quality": 0},
            {"slot": 2, "name": "喷壶（铜级）", "item_id": "(T)WateringCan", "stack": 1, "quality": 0},
            {"slot": 3, "name": "矿石", "item_id": "(O)378", "stack": 20, "quality": 0},
            {"slot": 4, "name": "防风草", "item_id": "(O)24", "stack": 15, "quality": 1},
            {"slot": 5, "name": "土豆", "item_id": "(O)78", "stack": 8, "quality": 2},
        ],
    },
}

INVENTORY_EMPTY = {
    "type": "inventory",
    "inventory": {
        "slots_used": 0,
        "slots_total": 36,
        "items": [],
    },
}

# ── 3. find_targets — crop ─────────────────────────────────────────────

FIND_TARGETS_CROPS_TYPICAL = {
    "type": "find_targets",
    "target_type": "crop",
    "location": "Farm",
    "total": 23,
    "targets": [
        {
            "target_ref": "Farm:crop_10_15",
            "location_name": "Farm",
            "x": 10,
            "y": 15,
            "crop_name": "防风草",
            "crop_id": "(O)24",
            "phase": 3,
            "max_phase": 3,
            "is_harvestable": True,
            "is_dead": False,
            "is_watered": True,
            "days_of_current_phase": 0,
        },
        {
            "target_ref": "Farm:crop_12_15",
            "location_name": "Farm",
            "x": 12,
            "y": 15,
            "crop_name": "土豆",
            "crop_id": "(O)78",
            "phase": 5,
            "max_phase": 6,
            "is_harvestable": False,
            "is_dead": False,
            "is_watered": False,
            "days_of_current_phase": 2,
        },
        {
            "target_ref": "Farm:crop_20_10",
            "location_name": "Farm",
            "x": 20,
            "y": 10,
            "crop_name": "草莓种子",
            "crop_id": "(O)745",
            "phase": 0,
            "max_phase": 4,
            "is_harvestable": False,
            "is_dead": False,
            "is_watered": True,
            "days_of_current_phase": 0,
        },
        {
            "target_ref": "Farm:crop_5_8",
            "location_name": "Farm",
            "x": 5,
            "y": 8,
            "crop_name": "芜菁",
            "crop_id": "(O)18",
            "phase": 4,
            "max_phase": 4,
            "is_harvestable": False,
            "is_dead": True,
            "is_watered": False,
            "days_of_current_phase": 5,
        },
    ],
}

FIND_TARGETS_CROPS_EMPTY = {
    "type": "find_targets",
    "target_type": "crop",
    "location": "Farm",
    "total": 0,
    "targets": [],
}

# ── 4. find_targets — animal ──────────────────────────────────────────

FIND_TARGETS_ANIMALS_TYPICAL = {
    "type": "find_targets",
    "target_type": "animal",
    "location": "Farm",
    "total": 3,
    "animals": [
        {
            "name": "甜豆",
            "display_name": "甜豆",
            "animal_type": "Chicken",
            "location_name": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2",
            "x": 4,
            "y": 6,
            "was_petted": False,
            "friendship": 350,
            "target_ref": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2:animal_甜豆",
        },
        {
            "name": "樱桃",
            "display_name": "樱桃",
            "animal_type": "Chicken",
            "location_name": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2",
            "x": 3,
            "y": 5,
            "was_petted": True,
            "friendship": 750,
            "target_ref": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2:animal_樱桃",
        },
        {
            "name": "奶牛",
            "display_name": "奶牛",
            "animal_type": "Cow",
            "location_name": "Barn9a2c1b3d-ef45-6789-abcd-ef1234567890",
            "x": 8,
            "y": 4,
            "was_petted": False,
            "friendship": 120,
            "target_ref": "Barn9a2c1b3d-ef45-6789-abcd-ef1234567890:animal_奶牛",
        },
    ],
}

FIND_TARGETS_ANIMALS_EMPTY = {
    "type": "find_targets",
    "target_type": "animal",
    "location": "Farm",
    "total": 0,
    "animals": [],
}

# ── 5. find_targets — npc ─────────────────────────────────────────────

FIND_TARGETS_NPCS_TYPICAL = {
    "type": "find_targets",
    "target_type": "npc",
    "location": "Town",
    "total": 2,
    "targets": [
        {
            "target_ref": "Town:npc_Lewis",
            "location_name": "Town",
            "x": 27,
            "y": 18,
            "name": "Lewis",
            "display_name": "路易斯",
            "hearts": 3,
            "talked_today": False,
            "is_birthday": False,
            "is_datable": False,
        },
        {
            "target_ref": "Town:npc_Marnie",
            "location_name": "AnimalShop",
            "x": 14,
            "y": 6,
            "name": "Marnie",
            "display_name": "玛妮",
            "hearts": 5,
            "talked_today": True,
            "is_birthday": False,
            "is_datable": False,
        },
    ],
}

FIND_TARGETS_NPCS_EMPTY = {
    "type": "find_targets",
    "target_type": "npc",
    "location": "Farm",
    "total": 0,
    "targets": [],
}

# ── 6. find_targets — object ──────────────────────────────────────────

FIND_TARGETS_OBJECTS_TYPICAL = {
    "type": "find_targets",
    "target_type": "object",
    "location": "Farm",
    "total": 5,
    "targets": [
        {
            "target_ref": "Farm:object_14_18",
            "location_name": "Farm",
            "x": 14,
            "y": 18,
            "name": "Chest",
            "display_name": "箱子",
            "item_id": "(BC)130",
            "object_type": "Crafting",
            "category": -9,
            "is_ready": False,
            "minutes_until_ready": 0,
            "is_forage": False,
        },
        {
            "target_ref": "Farm:object_5_7",
            "location_name": "Farm",
            "x": 5,
            "y": 7,
            "name": "Stone",
            "display_name": "石头",
            "item_id": "(O)80",
            "object_type": "Litter",
            "category": -2,
            "is_ready": False,
            "minutes_until_ready": 0,
            "is_forage": False,
        },
    ],
}

FIND_TARGETS_OBJECTS_EMPTY = {
    "type": "find_targets",
    "target_type": "object",
    "location": "Farm",
    "total": 0,
    "targets": [],
}

# ── 7. query_containers ───────────────────────────────────────────────

CONTAINERS_TYPICAL = {
    "type": "containers",
    "location": "Farm",
    "containers": [
        {
            "id": "Farm:chest_12_15",
            "type": "chest",
            "location": "Farm",
            "tile_x": 12,
            "tile_y": 15,
            "display_name": "箱子",
            "item_count": 15,
            "capacity": 36,
            "can_take": True,
            "can_store": True,
        },
        {
            "id": "Farm:chest_14_18",
            "type": "chest",
            "location": "Farm",
            "tile_x": 14,
            "tile_y": 18,
            "display_name": "箱子",
            "item_count": 8,
            "capacity": 36,
            "can_take": True,
            "can_store": True,
        },
        {
            "id": "FarmHouse:fridge_0_0",
            "type": "fridge",
            "location": "FarmHouse",
            "tile_x": 0,
            "tile_y": 0,
            "display_name": "冰箱",
            "item_count": 20,
            "capacity": 36,
            "can_take": True,
            "can_store": True,
        },
        {
            "id": "shipping_bin",
            "type": "shipping_bin",
            "location": "Farm",
            "tile_x": 71,
            "tile_y": 14,
            "display_name": "运输箱",
            "item_count": 3,
            "capacity": 999,
            "can_take": False,
            "can_store": True,
        },
    ],
    "summary": {
        "total": 4,
        "by_type": {"chest": 2, "fridge": 1, "shipping_bin": 1},
    },
}

CONTAINERS_EMPTY = {
    "type": "containers",
    "location": "Farm",
    "containers": [],
    "summary": {"total": 0, "by_type": {}},
}

CONTAINERS_CHEST_ONLY = {
    "type": "containers",
    "location": "Farm",
    "containers": [
        {
            "id": "Farm:chest_12_15",
            "type": "chest",
            "location": "Farm",
            "tile_x": 12,
            "tile_y": 15,
            "display_name": "箱子",
            "item_count": 15,
            "capacity": 36,
            "can_take": True,
            "can_store": True,
        },
    ],
    "summary": {"total": 1, "by_type": {"chest": 1}},
}

# ── 8. query_container_items ──────────────────────────────────────────

CONTAINER_ITEMS_CHEST_TYPICAL = {
    "type": "container_items",
    "container": {
        "id": "Farm:chest_12_15",
        "type": "chest",
        "location": "Farm",
        "tile_x": 12,
        "tile_y": 15,
        "display_name": "箱子",
    },
    "items": [
        {"name": "防风草", "item_id": "(O)24", "stack": 15, "quality": 0, "category": -75, "sell_price": 35},
        {"name": "土豆", "item_id": "(O)78", "stack": 8, "quality": 1, "category": -75, "sell_price": 80},
        {"name": "矿石", "item_id": "(O)378", "stack": 30, "quality": 0, "category": -15, "sell_price": 10},
        {"name": "煤炭", "item_id": "(O)382", "stack": 20, "quality": 0, "category": -15, "sell_price": 15},
        {"name": "金锭", "item_id": "(O)336", "stack": 5, "quality": 0, "category": -15, "sell_price": 250},
        {"name": "木材", "item_id": "(O)388", "stack": 100, "quality": 0, "category": -16, "sell_price": 2},
    ],
    "slots_used": 6,
    "slots_total": 36,
}

CONTAINER_ITEMS_CHEST_EMPTY = {
    "type": "container_items",
    "container": {
        "id": "Farm:chest_12_15",
        "type": "chest",
        "location": "Farm",
        "tile_x": 12,
        "tile_y": 15,
        "display_name": "箱子",
    },
    "items": [],
    "slots_used": 0,
    "slots_total": 36,
}

CONTAINER_ITEMS_FRIDGE_TYPICAL = {
    "type": "container_items",
    "container": {
        "id": "FarmHouse:fridge_0_0",
        "type": "fridge",
        "location": "FarmHouse",
        "tile_x": 0,
        "tile_y": 0,
        "display_name": "冰箱",
    },
    "items": [
        {"name": "牛奶", "item_id": "(O)186", "stack": 5, "quality": 2, "category": -6, "sell_price": 200},
        {"name": "鸡蛋", "item_id": "(O)176", "stack": 12, "quality": 0, "category": -5, "sell_price": 50},
        {"name": "草莓酱", "item_id": "(O)344", "stack": 3, "quality": 2, "category": -26, "sell_price": 150},
    ],
    "slots_used": 3,
    "slots_total": 36,
}

CONTAINER_ITEMS_SHIPPING_TYPICAL = {
    "type": "container_items",
    "container": {
        "id": "shipping_bin",
        "type": "shipping_bin",
        "location": "Farm",
        "tile_x": 71,
        "tile_y": 14,
        "display_name": "运输箱",
    },
    "items": [
        {"name": "防风草", "item_id": "(O)24", "stack": 30, "quality": 0, "category": -75, "sell_price": 35},
        {"name": "土豆", "item_id": "(O)78", "stack": 15, "quality": 1, "category": -75, "sell_price": 80},
        {"name": "蓝莓", "item_id": "(O)258", "stack": 50, "quality": 0, "category": -79, "sell_price": 50},
    ],
    "slots_used": 3,
    "slots_total": 999,
}

# ── 9. query_npc（详细信息）──────────────────────────────────────────

NPC_LEWIS_TYPICAL = {
    "type": "npc",
    "name": "Lewis",
    "location": {
        "name": "Town",
        "tile_x": 27,
        "tile_y": 18,
    },
    "friendship_hearts": 3,
    "friendship_points": 750,
    "can_talk_today": True,
    "has_given_gift_today": False,
    "gifts_this_week": 0,
    "birthday_season": "spring",
    "birthday_day": 7,
    "is_birthday_today": False,
    "loved_gifts": ["金南瓜", "秋月鱼", "松露"],
    "schedule_hint": "Lewis 在 Town (27,18)",
    "is_dating": False,
    "is_married": False,
}

NPC_LEWIS_BIRTHDAY = {
    **NPC_LEWIS_TYPICAL,
    "is_birthday_today": True,
    "can_talk_today": True,
}

# ── 10. find_targets — crop（单株，层 2 用）──────────────────────────

FIND_TARGETS_CROP_SINGLE_HARVESTABLE = {
    "type": "find_targets",
    "target_type": "crop",
    "location": "Farm",
    "total": 1,
    "targets": [
        {
            "target_ref": "Farm:crop_10_15",
            "location_name": "Farm",
            "x": 10,
            "y": 15,
            "crop_name": "防风草",
            "crop_id": "(O)24",
            "phase": 3,
            "max_phase": 3,
            "is_harvestable": True,
            "is_dead": False,
            "is_watered": True,
            "days_of_current_phase": 0,
        }
    ],
}

FIND_TARGETS_CROP_SINGLE_EMPTY = {
    "type": "find_targets",
    "target_type": "crop",
    "location": "Farm",
    "total": 0,
    "targets": [],
}

# ── 11. find_targets — animal（单只，层 2 用）────────────────────────

FIND_TARGETS_ANIMAL_SINGLE = {
    "type": "find_targets",
    "target_type": "animal",
    "location": "Farm",
    "total": 1,
    "animals": [
        {
            "name": "甜豆",
            "display_name": "甜豆",
            "animal_type": "Chicken",
            "location_name": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2",
            "x": 4,
            "y": 6,
            "was_petted": False,
            "friendship": 350,
            "target_ref": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2:animal_甜豆",
        }
    ],
}

# ── alias_map 示例 ─────────────────────────────────────────────────────
# {NameOrUniqueName → locshort}

ALIAS_MAP_TYPICAL = {
    "Farm": "farm",
    "FarmHouse": "farmhouse",
    "Town": "town",
    "Beach": "beach",
    "Mine": "mine",
    "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2": "coop1",
    "Barn9a2c1b3d-ef45-6789-abcd-ef1234567890": "barn1",
}

# ── 错误/降级 mock ────────────────────────────────────────────────────

# 查询失败（用于测试降级逻辑，simulate Exception）
QUERY_ERROR_CONTAINER_NOT_FOUND = {
    "error": {
        "code": "CONTAINER_NOT_FOUND",
        "message": "找不到容器: Farm:chest_99_99",
    }
}

QUERY_ERROR_INVALID_PARAMS = {
    "error": {
        "code": "INVALID_PARAMS",
        "message": "缺少必填参数",
    }
}

QUERY_ERROR_TARGET_NOT_FOUND = {
    "error": {
        "code": "TARGET_NOT_FOUND",
        "message": "找不到 NPC: NonExistentNPC",
    }
}
