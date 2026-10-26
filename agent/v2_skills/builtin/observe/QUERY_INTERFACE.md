# observe Skill — Legacy L1 Query 接口参考文档

> ⚠️ 迁移说明（2026-06-10）
>
> 本文是旧 observe 实现的历史接口参考，只用于理解 `agent/v2_skills/builtin/observe/`
> 中仍保留的 legacy fallback 代码。它不再是 L3 查询能力的新方案入口。
>
> 新的 L3 查询主路径应从以下位置阅读：
>
> - `/Users/river/Dev/star/star-coplay-l3-observe-query/agent/l3_query/`
> - `/Users/river/Dev/star/stardew-agent/workspace/l3-observe-query/docs/l3-query-technical-design.md`
>
> 新方案依赖 proto-native Query 五件套：
>
> - `query_runtime_snapshot`
> - `query_world_region`
> - `query_inventory_snapshot`
> - `query_ui_snapshot`
> - `query_inspect_refs`
>
> 本文中的 `find_targets`、`query_inventory`、`query_containers`、
> `query_container_items` 等名称仅表示旧 observe fallback 的兼容依赖。
> 后续开发不应把这些接口作为新 L3 Query / Observe / Inspect / Search 的设计依据。

> 来源：调研 `/workspace/group/star-coplay/v2/mod/StarCoPlay/V2/QueryHandler.cs`（2151 行）、`StateReader.cs`、`Models/GameStateSnapshot.cs`
> 调研日期：2026-05-02
> 适用：star-coplay v2.4.x observe Skill 历史实现与当前 legacy fallback

---

## 概述

所有 L1 query 通过 V2 Bridge 以 `query` capability + `type` 参数调用（注意：参数 key 为 `type` 或 `tool`，不是 capability 名本身）。例外：`game_state` 由 Agent 从 Blackboard 同步读取，无 IPC。

capability 字符串 = `"query"`，参数中 `type=<type_name>`。

`find_targets` 是特殊的 capability，通过 `type=find_targets`（或直接 capability=`find_targets`）+ `target_type=<xxx>` 调用。

---

## 1. game_state（Blackboard 同步读取）

**capability**: 无 IPC，从 Agent Blackboard 读取（`agent._state` 或 `bridge.game_state`）

**params**: 无

**result.data schema**:
```json
{
  "status": "running",
  "game_day_started": true,
  "world": {
    "time": 1200,
    "time_display": "12:00",
    "day": 12,
    "season": "spring",
    "year": 1,
    "day_of_week": "Wednesday",
    "weather": {
      "current": "sunny",
      "is_raining": false,
      "is_snowing": false,
      "is_lightning": false,
      "tomorrow_forecast": null
    },
    "is_festival_day": false,
    "festival_name": null
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
      "water_capacity": 55
    },
    "current_tool_index": 2,
    "skills": {
      "farming": 3,
      "mining": 1,
      "foraging": 2,
      "fishing": 0,
      "combat": 0
    },
    "status_effects": {
      "is_exhausted": false,
      "is_swimming": false,
      "is_riding_horse": false,
      "can_move": true
    }
  },
  "inventory": {
    "slots_used": 12,
    "slots_total": 36,
    "items": [
      {"slot": 0, "name": "防风草种子", "item_id": "(O)24", "stack": 30, "quality": 0, "type": "object", "quality_name": null}
    ]
  },
  "farm_summary": {
    "crops": {"total_planted": 23, "unwatered_count": 12, "harvestable_count": 8, "empty_tilled_tiles": 5},
    "animals": {"total": 3, "unpetted": 3, "product_ready": 1},
    "machines": {"ready_to_collect": 1, "processing": 2, "empty": 0}
  },
  "surroundings": {
    "radius": 8,
    "objects": [{"tile": {"x": 12, "y": 14}, "name": "Stone", "type": "object"}],
    "crops": [{"tile": {"x": 10, "y": 15}, "crop_name": "防风草", "watered": false, "harvestable": true, "days_until_harvest": 0}],
    "npcs": [{"name": "Lewis", "tile": {"x": 27, "y": 18}, "hearts": 3, "distance": 5.2}],
    "terrain": [{"tile": {"x": 8, "y": 12}, "type": "tree", "tree_type": "Oak", "state": null, "has_crop": null}]
  }
}
```

**错误情况**: 无（Blackboard 始终存在，旧数据或 None 视为降级处理）

---

## 2. query_inventory（inventory）

**capability**: `"query"` with `type="inventory"`

**params**: 无

**result.data schema**:
```json
{
  "type": "inventory",
  "inventory": {
    "slots_used": 12,
    "slots_total": 36,
    "items": [
      {
        "slot": 0,
        "name": "防风草种子",
        "item_id": "(O)24",
        "stack": 30,
        "quality": 0
      },
      {
        "slot": 1,
        "name": "锄头",
        "item_id": "(T)Hoe",
        "stack": 1,
        "quality": 0
      }
    ]
  }
}
```

**字段说明**:
- `items[].slot`: 背包槽位索引（0-35）
- `items[].quality`: 0=普通, 1=银★, 2=金★, 4=铱★
- `items[].item_id`: SDV 1.6 格式，含前缀 `(O)=Object, (T)=Tool, (W)=Weapon`
- 空槽位跳过不返回

**错误情况**: 正常情况无错误，玩家未加载时返回 slots_used=0, items=[]

---

## 3. query_location_info（location_info）

**capability**: `"query"` with `type="location_info"`

**params**: 无

**result.data schema**:
```json
{
  "type": "location_info",
  "location_info": {
    "name": "Farm",
    "unique_name": "Farm",
    "width": 80,
    "height": 65,
    "warps": [
      {"source_x": 76, "source_y": 17, "target_location": "Town", "target_x": 0, "target_y": 43},
      {"source_x": 49, "source_y": 17, "target_location": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2", "target_x": 4, "target_y": 8}
    ],
    "buildings": []
  }
}
```

**字段说明**:
- `warps` 包含 warp 点和 doors 两类（StateReader 合并写入）
- `target_location` 使用 NameOrUniqueName（内部建筑含 GUID）
- `buildings` 字段从 StateReader.GetLocationInfo() 返回（可能为空 list）

**错误情况**: 当前地图为 null 时返回 null（由 ResultBuilder 包成空对象）

---

## 4. query_farm_summary（farm_summary）

**capability**: `"query"` with `type="farm_summary"`

**params**: 无

**result.data schema**:
```json
{
  "type": "farm_summary",
  "farm_summary": {
    "crop_count": 23,
    "unwatered_count": 12,
    "harvestable_count": 8,
    "empty_tilled_tiles": 5,
    "total_animals": 3,
    "unfed_animals": 3,
    "product_ready": 1,
    "machines_ready": 1,
    "machines_processing": 2,
    "machines_empty": 0
  }
}
```

**字段说明**:
- `product_ready`: 农场上动物有产出可收取的数量
- `machines_*`: 农场上的大型制作物（bigCraftable=true）统计
- 返回 null 时表示当前不在农场上下文（无 Farm 地图）

**错误情况**: farm=null 时 farm_summary 字段为 null

---

## 5. query_walkable_area（walkable_area）

**capability**: `"query"` with `type="walkable_area"`

**params**: 无

**result.data schema**:
```json
{
  "type": "walkable_area",
  "walkable_area": {
    "tiles": [
      {"x": 10, "y": 12},
      {"x": 10, "y": 13}
    ]
  }
}
```

**字段说明**:
- `tiles`: 当前地图所有可行走 tile 列表（用于寻路规划）
- observe Skill 通常不需要此 query（由 go_to 使用），但可用于地图探索提示

**错误情况**: 地图为 null 时返回空列表

---

## 6. query_map_objects（map_objects）

**capability**: `"query"` with `type="map_objects"`

**params**:
- `detail` (bool, optional): 默认 false，true 时返回更多字段
- `x1, y1, x2, y2` (int, optional): 矩形范围过滤，默认以玩家为中心 9×9

**result.data schema**:
```json
{
  "type": "map_objects",
  "region": {"x1": 6, "y1": 11, "x2": 14, "y2": 19},
  "map_objects": [
    {
      "tile_x": 12,
      "tile_y": 14,
      "name": "Stone",
      "type": "object",
      "item_id": "(O)80",
      "category": -2
    }
  ]
}
```

**注意**: observe Skill 优先使用 `find_targets` 而非 `map_objects`（find_targets 有更好的过滤能力）

---

## 7. query_time（time）

**capability**: `"query"` with `type="time"`

**params**: 无

**result.data schema**:
```json
{
  "type": "time",
  "world_time": {
    "game_time": 1200,
    "display": "12:00",
    "day": 12,
    "season": "spring",
    "year": 1,
    "day_of_week": "Wednesday"
  },
  "weather": {
    "current": "sunny",
    "is_raining": false,
    "is_lightning": false,
    "is_snowing": false,
    "is_festival_day": false
  },
  "flags": {
    "is_raining": false,
    "is_lightning": false,
    "is_snowing": false,
    "is_festival_day": false
  }
}
```

**字段说明**:
- `game_time`: 整数，600=06:00, 1200=12:00, 2600=02:00（次日凌晨）
- `weather.current`: "sunny" | "rainy" | "stormy" | "snowy" | "windy"
- observe Skill 使用 game_state 而非单独调用 time query

---

## 8. query_can_interact（can_interact）

**capability**: `"query"` with `type="can_interact"`

**params**:
- `x, y` (int, optional): 目标 tile 坐标
- `location` (str, optional): 目标地图名

**result.data schema（无目标 fallback 模式）**:
```json
{
  "type": "can_interact",
  "mode": "fallback",
  "can_interact": true,
  "available_actions": {
    "can_move": true,
    "is_player_free": true,
    "is_menu_open": false,
    "is_in_cutscene": false,
    "is_in_dialogue": false
  }
}
```

**result.data schema（targeted 模式，传入 x/y）**:
```json
{
  "type": "can_interact",
  "mode": "targeted",
  "target": {"x": 12, "y": 14, "location": "Farm"},
  "can_interact": false,
  "reason": "out_of_range",
  "checks": {
    "current_location": "Farm",
    "player_position": {"x": 10, "y": 15},
    "distance": 3.16,
    "has_object": true,
    "has_npc": false,
    "has_crop": false,
    "has_furniture": false,
    "player_free": true
  }
}
```

**错误情况**: `reason` 可为 "location_mismatch" | "out_of_range" | "no_interactable_target"（逗号分隔组合）

---

## 9. query_containers（containers）

**capability**: `"query"` with `type="containers"`

**params**:
- `location` (str, optional): 地图名（NameOrUniqueName 或 Name），默认当前地图，"all" 扫描全图
- `include_items` (bool, optional): 默认 false，true 时每个容器附带物品列表
- `container_type` (str, optional): 过滤类型 "chest" | "fridge" | "shipping_bin"

**result.data schema**:
```json
{
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
      "can_take": true,
      "can_store": true
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
      "can_take": true,
      "can_store": true
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
      "can_take": false,
      "can_store": true
    }
  ],
  "summary": {
    "total": 3,
    "by_type": {"chest": 1, "fridge": 1, "shipping_bin": 1}
  }
}
```

**错误情况**:
- `error.code = "INVALID_PARAMS"`: 指定 location 不存在时

---

## 10. query_container_items（container_items）

**capability**: `"query"` with `type="container_items"`

**params**:
- `container_id` (str, required): 容器 ID，格式 "LocationName:type_x_y" 或 "shipping_bin"

**result.data schema**:
```json
{
  "type": "container_items",
  "container": {
    "id": "Farm:chest_12_15",
    "type": "chest",
    "location": "Farm",
    "tile_x": 12,
    "tile_y": 15,
    "display_name": "箱子"
  },
  "items": [
    {
      "name": "防风草",
      "item_id": "(O)24",
      "stack": 15,
      "quality": 0,
      "category": -75,
      "sell_price": 35
    },
    {
      "name": "土豆",
      "item_id": "(O)78",
      "stack": 8,
      "quality": 1,
      "category": -75,
      "sell_price": 80
    }
  ],
  "slots_used": 6,
  "slots_total": 36
}
```

**字段说明**:
- `items[].quality`: 0=普通, 1=银★, 2=金★, 4=铱★
- `items[].category`: SDV 物品分类 ID（-75=作物, -79=花卉, -4=鱼, -12=矿石等）
- `items[].sell_price`: 单个出售价格（含品质加成）

**错误情况**:
- `error.code = "INVALID_PARAMS"`: 缺少 container_id
- `error.code = "CONTAINER_NOT_FOUND"`: container_id 不存在

---

## 11. find_targets（多种 target_type）

**capability**: `"find_targets"` (或 `"query"` with `type="find_targets"`)

**params 公共部分**:
- `target_type` (str, required): 见下表
- `location` (str, optional): 指定地图，默认当前地图
- `sort` (str, optional): "nearest" 按玩家距离排序
- `max` (int, optional): 最大返回数量

### 11a. target_type="crop"

**params**:
- `unwatered` (bool): 只返回未浇水作物
- `harvestable` (bool): 只返回可收割作物
- `dead` (bool): true=只返回枯萎; false=只返回存活
- `crop_name` (str): 按名称模糊过滤

**result.data schema**:
```json
{
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
      "is_harvestable": true,
      "is_dead": false,
      "is_watered": true,
      "days_of_current_phase": 0
    }
  ]
}
```

**注意**:
- `is_harvestable` 使用 `dirt.readyForHarvest()`（排除枯萎作物）
- `max_phase` = `crop.phaseDays.Count - 1`（注意不是天数，是阶段数）

### 11b. target_type="animal"

**params**:
- `ungreeted` (bool): 只返回未抚摸的动物
- `animal_type` (str): 按类型过滤（如 "Chicken", "Cow"）

**result.data schema**:
```json
{
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
      "was_petted": false,
      "friendship": 350,
      "target_ref": "Coop82b38ef3-ac7b-4256-bc15-14aa9b59abe2:animal_甜豆"
    }
  ]
}
```

**注意**:
- 始终从 Farm + 所有建筑内 AnimalHouse 扫描（不受当前地图限制）
- `location_name` 使用 NameOrUniqueName（含 GUID）
- `friendship` 为原始友好值（250/心）

### 11c. target_type="npc"

**params**:
- `can_talk_today` (bool): 只返回今日未对话的 NPC
- `birthday_today` (bool): 只返回今日生日 NPC
- `name` (str): 按名称模糊过滤

**result.data schema**:
```json
{
  "type": "find_targets",
  "target_type": "npc",
  "location": "Town",
  "total": 3,
  "targets": [
    {
      "target_ref": "Town:npc_Lewis",
      "location_name": "Town",
      "x": 27,
      "y": 18,
      "name": "Lewis",
      "display_name": "路易斯",
      "hearts": 3,
      "talked_today": false,
      "is_birthday": false,
      "is_datable": false
    }
  ]
}
```

**注意**:
- 只搜索当前地图的 characters（不跨地图，需用 location 参数指定）
- `hearts` = friendship.Points / 250

### 11d. target_type="object"

**params**:
- `forage` (bool): true=只返回可采集野生物; false=只返回非采集品
- `name` (str): 名称模糊过滤
- `object_type` (str): 按 Type 属性过滤（"Crafting", "interactive"等）
- `category` (int): 按 category ID 过滤
- `item_id` (str): 按精确 ItemId 过滤

**result.data schema**:
```json
{
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
      "is_ready": false,
      "minutes_until_ready": 0,
      "is_forage": false
    }
  ]
}
```

### 11e. target_type="rock"

**params**:
- `type` (str): "stone" | "boulder"

**result.data schema**:
```json
{
  "type": "find_targets",
  "target_type": "rock",
  "location": "Farm",
  "total": 12,
  "targets": [
    {
      "target_ref": "Farm:rock_5_7",
      "location_name": "Farm",
      "x": 5,
      "y": 7,
      "rock_type": "stone",
      "name": "Stone",
      "clump_index": -1,
      "width": 1,
      "height": 1,
      "health": 0
    }
  ]
}
```

### 11f. target_type="tree"

**params**:
- `mature` (bool): 只返回成熟树（growthStage >= 5）
- `stump` (bool): 只返回树桩
- `tree_type` (str): 按类型 ID 过滤（"1"=橡木, "2"=枫树, "3"=松树等）

**result.data schema**:
```json
{
  "type": "find_targets",
  "target_type": "tree",
  "location": "Farm",
  "total": 3,
  "targets": [
    {
      "target_ref": "Farm:tree_8_12",
      "location_name": "Farm",
      "x": 8,
      "y": 12,
      "tree_name": "Oak",
      "tree_type": "1",
      "growth_stage": 5,
      "is_mature": true,
      "is_stump": false,
      "has_seed": true,
      "tapped": false
    }
  ]
}
```

### 11g. target_type="debris"（杂物）

**params**:
- `type` (str): "grass" | "twig" | "weed"

**result.data schema**:
```json
{
  "type": "find_targets",
  "target_type": "debris",
  "location": "Farm",
  "total": 8,
  "targets": [
    {
      "target_ref": "Farm:debris_3_5",
      "location_name": "Farm",
      "x": 3,
      "y": 5,
      "debris_type": "weed",
      "name": "Weeds",
      "number_of_weeds": 1
    }
  ]
}
```

### 11h. target_type="tile"（可耕/可播种地块）

**params（至少一个必填）**:
- `untilled` (bool): 返回未翻地但可翻（有 Diggable 属性）的 tile
- `plantable` (bool): 返回已翻地但无作物的 tile
- `area_radius` (int): untilled 扫描半径（默认 7，即 15×15 范围）

**result.data schema**:
```json
{
  "type": "find_targets",
  "target_type": "tile",
  "location": "Farm",
  "total": 30,
  "targets": [
    {
      "target_ref": "Farm:tile_12_15",
      "location_name": "Farm",
      "x": 12,
      "y": 15,
      "tile_state": "plantable",
      "is_watered": false
    }
  ]
}
```

### 11i. target_type="water"（水源 tile）

**params**:
- `radius` (int): 扫描半径（默认 40，最大 200）
- `open_water` (bool): 只返回 isOpenWater（可钓鱼/可补水）

**result.data schema**:
```json
{
  "type": "find_targets",
  "target_type": "water",
  "location": "Farm",
  "total": 15,
  "targets": [
    {
      "target_ref": "Farm:tile_25_30",
      "location_name": "Farm",
      "x": 25,
      "y": 30,
      "type": "water",
      "water_type": "open_water",
      "has_adjacent_walkable": true,
      "reachable": true
    }
  ]
}
```

**注意**:
- `reachable` 仅前 5 个 has_adjacent_walkable=true 的 tile 做 PathFindController 检查，其余为 null
- water_type: "open_water"（可补水可钓鱼）| "water_tile"（宽泛水面，可能部分阻挡）

---

## 12. query_npc（npc）

**capability**: `"query"` with `type="npc"`

**params**:
- `name` (str, required): NPC 英文名（如 "Lewis"）

**result.data schema**:
```json
{
  "type": "npc",
  "name": "Lewis",
  "location": {
    "name": "Town",
    "tile_x": 27,
    "tile_y": 18
  },
  "friendship_hearts": 3,
  "friendship_points": 750,
  "can_talk_today": true,
  "has_given_gift_today": false,
  "gifts_this_week": 0,
  "birthday_season": "spring",
  "birthday_day": 7,
  "is_birthday_today": false,
  "loved_gifts": ["金南瓜", "秋月鱼", "松露"],
  "schedule_hint": "Lewis 在 Town (27,18)",
  "is_dating": false,
  "is_married": false
}
```

**字段说明**:
- `friendship_hearts`: 0-14（普通）或 0-8（已婚）
- `can_talk_today`: `!talkedToday`（取反）
- `loved_gifts`: 从 Game1.NPCGiftTastes[name] 解析，可能为空数组
- 跨所有 locations 搜索 NPC（含建筑室内）

**错误情况**:
- `error.code = "INVALID_PARAMS"`: 缺少 name
- `error.code = "TARGET_NOT_FOUND"`: NPC 不存在

---

## 13. query_entities_nearby（entities_nearby）

**capability**: `"query"` with `type="entities_nearby"`

**params**:
- `radius` (int, optional): 搜索半径（默认 10）
- `detail` (bool, optional): 默认 false

**result.data schema**:
```json
{
  "type": "entities_nearby",
  "location": "Farm",
  "radius": 10,
  "detail": false,
  "summary": {"npcs": 1, "objects": 5, "total": 6},
  "entities": [
    {
      "id": "Farm:npc_Lewis",
      "type": "npc",
      "name": "路易斯",
      "position": {"tile_x": 27, "tile_y": 18, "location": "Farm"},
      "interactable": true,
      "properties": {"hearts": 3, "talked_today": false}
    },
    {
      "id": "Farm:tile_12_15",
      "type": "crop",
      "name": "Crop (24)",
      "position": {"tile_x": 12, "tile_y": 15, "location": "Farm"},
      "interactable": true,
      "properties": {"tile_category": "terrain", "is_watered": false, "is_harvestable": true}
    }
  ]
}
```

**注意**: observe Skill 优先使用 `find_targets` 而非 `entities_nearby`（后者字段较粗糙）

---

## 错误码参考

| code | 含义 |
|------|------|
| `INVALID_PARAMS` | 参数缺失或格式错误 |
| `EXECUTION_FAILED` | 执行失败（地图为空、API 调用失败等） |
| `TARGET_NOT_FOUND` | 目标（NPC/容器）不存在 |
| `CONTAINER_NOT_FOUND` | 容器 ID 不存在（ContainerManager 专用） |

---

## observe Skill 使用的 capability 汇总

| 场景 | capability | type 参数 |
|------|-----------|----------|
| 玩家状态/世界时间 | Blackboard | — |
| 背包 | query | inventory |
| 容器列表 | query | containers |
| 容器物品 | query | container_items |
| 作物 | find_targets | target_type=crop |
| 动物 | find_targets | target_type=animal |
| NPC（当前地图） | find_targets | target_type=npc |
| NPC（详情） | query | npc |
| 地面对象 | find_targets | target_type=object |
| 石头/树/杂物 | find_targets | target_type=rock/tree/debris |
| 出口（暂无专用query） | query | location_info（取 warps） |
