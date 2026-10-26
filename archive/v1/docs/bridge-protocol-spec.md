# Bridge Protocol 规格文档

> 版本：v1.0 | 日期：2026-03-28
> 定位：Agent 侧开发者的**唯一对齐源**，可直接对照编码
> 关联：`Agent-Mod协作协议设计.md`（运行时行为）、`StardewAgent-Adapter层设计规格.md`（C# 实现细节）

---

## 核心概念速览

- 通信通过 4 个文件实现，每个文件只有一个写方，不会冲突
- `game_state.json`（Mod→Agent，覆写）：游戏状态快照，每秒更新
- `events.jsonl`（Mod→Agent，追加行）：事件流，Agent 通过 seq 追踪进度
- `commands.jsonl`（Agent→Mod，追加行）：命令队列，Agent 写入动作指令
- `agent_status.json`（Agent→Mod，覆写）：Agent 自身状态，供 Mod/HUD 展示
- 所有 JSON 使用 UTF-8 编码，jsonl 每行一个完整 JSON 对象

---

## 0. 目录结构

```
bridge/                          ← Mod 启动时创建
├── game_state.json              ← Mod 只写，Agent 只读
├── events.jsonl                 ← Mod 只写（追加），Agent 只读
├── commands.jsonl               ← Agent 只写（追加），Mod 只读
└── agent_status.json            ← Agent 只写，Mod 只读
```

**路径约定**：`{Mod安装目录}/bridge/`（与 Mod DLL 同级）

---

## 1. game_state.json

Mod 每秒覆写一次。Agent 轮询读取，获得游戏当前状态的完整快照。

### 1.1 完整 Schema

```json
{
  "status": "running",
  "game_day_started": true,

  "world": {
    "time": 1030,
    "time_display": "10:30 AM",
    "day": 15,
    "season": "spring",
    "year": 1,
    "day_of_week": "Monday",
    "weather": {
      "current": "sunny",
      "is_raining": false,
      "is_snowing": false,
      "is_lightning": false,
      "tomorrow_forecast": "rainy"
    },
    "is_festival_day": false,
    "festival_name": null
  },

  "player": {
    "name": "Player2",
    "location": "Farm",
    "tile": { "x": 64, "y": 15 },
    "facing_direction": "down",
    "energy": { "current": 200, "max": 270 },
    "health": { "current": 100, "max": 100 },
    "gold": 2500,
    "equipped_item": {
      "name": "Watering Can",
      "type": "tool",
      "item_id": "(T)WateringCan",
      "water_level": 30,
      "water_capacity": 40
    },
    "current_tool_index": 0,
    "skills": {
      "farming": 4,
      "mining": 2,
      "foraging": 3,
      "fishing": 1,
      "combat": 1
    },
    "status_effects": {
      "is_exhausted": false,
      "is_swimming": false,
      "is_riding_horse": false,
      "can_move": true
    }
  },

  "inventory": {
    "slots_used": 8,
    "slots_total": 36,
    "items": [
      { "slot": 0, "name": "Watering Can", "item_id": "(T)WateringCan", "type": "tool", "stack": 1, "quality": 0 },
      { "slot": 2, "name": "Parsnip Seeds", "item_id": "(O)472", "type": "seed", "stack": 15, "quality": 0 },
      { "slot": 5, "name": "Parsnip", "item_id": "(O)24", "type": "crop", "stack": 23, "quality": 1, "quality_name": "Silver" }
    ]
  },

  "farm_summary": {
    "crops": {
      "total_planted": 45,
      "unwatered_count": 8,
      "harvestable_count": 15,
      "empty_tilled_tiles": 12
    },
    "animals": {
      "total": 4,
      "unfed": 1,
      "product_ready": 3
    },
    "machines": {
      "ready_to_collect": 2,
      "processing": 1,
      "empty": 3
    }
  },

  "surroundings": {
    "radius": 8,
    "objects": [
      { "tile": { "x": 62, "y": 13 }, "name": "Chest", "type": "container" },
      { "tile": { "x": 65, "y": 15 }, "name": "Sprinkler", "type": "machine" }
    ],
    "crops": [
      { "tile": { "x": 63, "y": 14 }, "crop_name": "Parsnip", "watered": false, "harvestable": false, "days_until_harvest": 2 },
      { "tile": { "x": 64, "y": 14 }, "crop_name": "Cauliflower", "watered": true, "harvestable": true, "days_until_harvest": 0 }
    ],
    "npcs": [
      { "name": "Abigail", "tile": { "x": 70, "y": 18 }, "distance": 6 }
    ],
    "terrain": [
      { "tile": { "x": 66, "y": 12 }, "type": "tree", "tree_type": "Oak" },
      { "tile": { "x": 68, "y": 14 }, "type": "hoe_dirt", "state": "dry", "has_crop": false }
    ]
  }
}
```

### 1.2 字段定义

#### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | ✅ | 游戏生命周期状态 |
| `game_day_started` | boolean | ✅ | 当天是否已开始（存档加载完成后 true） |
| `world` | object | ✅ | 世界状态 |
| `player` | object | ✅ | 玩家状态 |
| `inventory` | object | ✅ | 背包物品 |
| `farm_summary` | object | ✅ | 农场聚合统计 |
| `surroundings` | object | ✅ | 玩家周围环境（半径 8 瓦片） |

#### status 枚举

| 值 | 含义 | Agent 行为 |
|----|------|-----------|
| `"running"` | 游戏正常运行 | 正常工作 |
| `"paused"` | 游戏暂停（菜单/过场） | 暂停决策，等待恢复 |
| `"loading"` | 存档加载中 | 等待变为 running |
| `"shutdown"` | 游戏退出 | Agent 自行退出 |

#### facing_direction 枚举

| 值 | 含义 |
|----|------|
| `"up"` | 面朝上（北） |
| `"right"` | 面朝右（东） |
| `"down"` | 面朝下（南） |
| `"left"` | 面朝左（西） |

#### quality 枚举

| 值 | 名称 | 价格倍数 |
|----|------|---------|
| `0` | Normal（普通） | ×1.0 |
| `1` | Silver（银星） | ×1.25 |
| `2` | Gold（金星） | ×1.5 |
| `4` | Iridium（铱星） | ×2.0 |

#### weather.current 枚举

| 值 | 含义 |
|----|------|
| `"sunny"` | 晴天 |
| `"rainy"` | 下雨 |
| `"stormy"` | 雷暴 |
| `"snowy"` | 下雪 |
| `"windy"` | 刮风（沙漠） |
| `"festival"` | 节日 |

---

## 2. events.jsonl

Mod 追加写入，每行一个事件 JSON 对象。Agent 通过 `seq` 字段追踪已读位置。

### 2.1 信封格式

```json
{"seq": 1, "ts": 1711526400.123, "type": "event_type", "data": {}}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `seq` | integer | 单调递增序号，每游戏日重置为 1 |
| `ts` | float | Unix 时间戳（秒，精确到毫秒） |
| `type` | string | 事件类型（见下表） |
| `data` | object | 事件数据（结构因 type 而异） |

### 2.2 事件类型完整枚举

#### day_started — 新一天开始

**触发时机**：游戏新一天加载完成

```json
{
  "seq": 1, "ts": 1711526400, "type": "day_started",
  "data": {
    "day": 15,
    "season": "spring",
    "year": 1,
    "day_of_week": "Monday",
    "weather": "sunny",
    "is_festival": false,
    "festival_name": null
  }
}
```

**Agent 建议响应**：触发每日规划（调 LLM 决定今天做什么）

---

#### day_ending — 一天即将结束

**触发时机**：玩家上床 / 凌晨 2:00 昏倒

```json
{
  "seq": 50, "ts": 1711530000, "type": "day_ending",
  "data": {
    "day": 15,
    "season": "spring",
    "earnings_today": 2500,
    "items_shipped": 12
  }
}
```

**Agent 建议响应**：保存当日记忆、更新长期规划

---

#### time_changed — 游戏时间推进

**触发时机**：游戏内每 10 分钟（即每 7 秒现实时间）

```json
{
  "seq": 5, "ts": 1711526470, "type": "time_changed",
  "data": {
    "time": 1100,
    "time_display": "11:00 AM"
  }
}
```

**Agent 建议响应**：检查是否到了该做某事的时间（如商店开门、NPC 日程变化）

---

#### inventory_changed — 背包物品变动

**触发时机**：任何背包增减（拾取/丢弃/购买/出售/使用/赠送）

```json
{
  "seq": 8, "ts": 1711526500, "type": "inventory_changed",
  "data": {
    "added": [
      { "name": "Diamond", "item_id": "(O)72", "count": 1, "quality": 0, "sell_price": 750 }
    ],
    "removed": [
      { "name": "Parsnip Seeds", "item_id": "(O)472", "count": 5, "quality": 0, "sell_price": 10 }
    ],
    "source": "pickup"
  }
}
```

**source 枚举**：

| 值 | 含义 |
|----|------|
| `"pickup"` | 拾取/收获 |
| `"purchase"` | 购买 |
| `"sell"` | 出售 |
| `"gift"` | 赠送 |
| `"use"` | 使用/消耗 |
| `"drop"` | 丢弃 |
| `"craft"` | 制作 |
| `"unknown"` | 未知来源 |

**Agent 建议响应**：
- 高价值物品获得（`sell_price >= 500`）→ 庆祝/评论
- 高价值物品被卖（source=sell, sell_price >= 500）→ 提醒/纠正记忆

---

#### player_warped — 玩家切换地图

**触发时机**：玩家从一个地图移动到另一个地图

```json
{
  "seq": 12, "ts": 1711526600, "type": "player_warped",
  "data": {
    "from_location": "Farm",
    "to_location": "Town",
    "tile": { "x": 27, "y": 56 }
  }
}
```

**Agent 建议响应**：更新感知范围、考虑新位置可做的事

---

#### npc_nearby — NPC 进入感知范围

**触发时机**：NPC 进入玩家 10 格范围内（去重：同一 NPC 每 60 秒最多触发一次）

```json
{
  "seq": 15, "ts": 1711526700, "type": "npc_nearby",
  "data": {
    "name": "Abigail",
    "location": "Town",
    "tile": { "x": 52, "y": 18 },
    "distance": 5,
    "hearts": 4,
    "talked_today": false,
    "gifted_today": false
  }
}
```

**Agent 建议响应**：考虑是否打招呼/送礼

---

#### cmd_result — 命令执行结果

**触发时机**：Mod 执行完 Agent 下发的命令后

```json
{
  "seq": 20, "ts": 1711526800, "type": "cmd_result",
  "data": {
    "cmd_id": "c001",
    "success": true,
    "message": "已到达目标位置 Farm (64, 15)",
    "error_code": null,
    "result": {
      "tiles_walked": 9,
      "energy_cost": 0
    }
  }
}
```

**失败示例**：

```json
{
  "seq": 21, "ts": 1711526900, "type": "cmd_result",
  "data": {
    "cmd_id": "c002",
    "success": false,
    "message": "无法到达目标位置：路径被障碍物阻挡",
    "error_code": "PATH_BLOCKED",
    "result": {
      "blocked_at": { "x": 63, "y": 13 },
      "suggestion": "目标瓦片被 Chest 阻挡，尝试相邻瓦片"
    }
  }
}
```

---

#### energy_low — 体力过低警告

**触发时机**：玩家体力低于最大值的 20%

```json
{
  "seq": 30, "ts": 1711527000, "type": "energy_low",
  "data": {
    "current": 40,
    "max": 270,
    "percentage": 14.8
  }
}
```

**Agent 建议响应**：停止消耗体力的活动，考虑吃食物或回家

---

#### player_action — 玩家（主玩家）操作

**触发时机**：主玩家执行了可观察的操作

```json
{
  "seq": 35, "ts": 1711527100, "type": "player_action",
  "data": {
    "action_type": "use_tool",
    "tool": "Pickaxe",
    "target_tile": { "x": 100, "y": 50 },
    "location": "Mine"
  }
}
```

**action_type 枚举**：`"use_tool"` | `"interact"` | `"warp"` | `"eat"` | `"fish_cast"` | `"gift_given"`

---

#### season_changed — 季节变更

**触发时机**：进入新季节第 1 天

```json
{
  "seq": 1, "ts": 1711600000, "type": "season_changed",
  "data": {
    "from": "spring",
    "to": "summer",
    "year": 1
  }
}
```

---

### 2.3 Agent 读取 events.jsonl 伪代码

```python
last_seq = 0

def poll_events():
    global last_seq
    with open("bridge/events.jsonl", "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event["seq"] > last_seq:
                handle_event(event)
                last_seq = event["seq"]
```

---

## 3. commands.jsonl

Agent 追加写入，Mod 轮询读取并执行。

### 3.1 信封格式

```json
{"cmd_id": "c001", "action": "action_name", "params": {}}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `cmd_id` | string | Agent 生成的唯一命令 ID（建议格式：`c{自增数字}`） |
| `action` | string | 动作名称（见下表） |
| `params` | object | 动作参数（结构因 action 而异） |

Mod 执行完毕后，在 `events.jsonl` 写入 `cmd_result` 事件（通过 `cmd_id` 关联）。

### 3.2 统一错误码

所有命令共享以下错误码体系。Agent 可用 switch/match 统一处理。

| error_code | 含义 | 通用建议 |
|------------|------|---------|
| `null` | 成功 | — |
| `PATH_BLOCKED` | 路径不可达 | 尝试相邻瓦片或换路线 |
| `NO_TOOL` | 未装备所需工具 | 先 equip_item |
| `NO_ENERGY` | 体力不足 | 吃食物或回家睡觉 |
| `TOO_FAR` | 距离太远 | 先 navigate_to 到目标附近 |
| `INVALID_TARGET` | 目标不存在或类型不匹配 | 重新查询状态确认目标 |
| `ALREADY_DONE` | 操作已完成（如已浇过水） | 跳过，继续下一个 |
| `INVENTORY_FULL` | 背包已满 | 丢弃/出售物品腾空间 |
| `NOT_ENOUGH_GOLD` | 金币不足 | 先赚钱 |
| `ITEM_NOT_FOUND` | 背包中无此物品 | 检查背包或先获取 |
| `MENU_OPEN` | 有模态 UI 打开 | 等待 UI 关闭 |
| `IN_CUTSCENE` | 正在过场动画 | 等待过场结束 |
| `NO_WATER` | 水壶没水 | 去水源处装水 |
| `GIFT_LIMIT` | 今日/本周送礼已满 | 等明天/下周 |
| `NPC_NOT_FOUND` | 找不到指定 NPC | 检查 NPC 名称或当前是否可见 |
| `SHOP_CLOSED` | 商店关门 | 等营业时间 |
| `TILE_OCCUPIED` | 瓦片已被占用 | 换其他位置 |
| `UNKNOWN_ERROR` | 未分类错误 | 查看 message 字段 |

### 3.3 命令完整枚举

---

#### L0 navigate_to — 寻路移动

```json
{
  "cmd_id": "c001",
  "action": "navigate_to",
  "params": {
    "x": 64,
    "y": 15,
    "location": "Farm"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x` | integer | ✅ | 目标瓦片 X |
| `y` | integer | ✅ | 目标瓦片 Y |
| `location` | string | ❌ | 目标地图名（跨地图移动时填写，省略则当前地图） |

**成功 result**：`{ "tiles_walked": 9, "energy_cost": 0 }`
**可能错误**：`PATH_BLOCKED` / `INVALID_TARGET` / `MENU_OPEN` / `IN_CUTSCENE`

---

#### L0 use_tool_on_tile — 在瓦片上使用工具

```json
{
  "cmd_id": "c002",
  "action": "use_tool_on_tile",
  "params": {
    "x": 63,
    "y": 14
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x` | integer | ✅ | 目标瓦片 X |
| `y` | integer | ✅ | 目标瓦片 Y |

**前置条件**：已装备工具、在目标瓦片相邻位置（≤1.5 格）、体力 ≥ 2
**成功 result**：`{ "tool": "Watering Can", "effect": "WATERED", "energy_cost": 2, "water_remaining": 29 }`
**可能错误**：`NO_TOOL` / `TOO_FAR` / `NO_ENERGY` / `NO_WATER` / `MENU_OPEN`

**effect 枚举**：`"WATERED"` | `"TILLED"` | `"CHOPPED"` | `"MINED"` | `"CLEARED"`

---

#### L0 equip_item — 切换装备

```json
{
  "cmd_id": "c003",
  "action": "equip_item",
  "params": {
    "item_name": "Watering Can"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `item_name` | string | 二选一 | 物品名称（自动查找槽位） |
| `slot_index` | integer | 二选一 | 背包槽位索引（0-35） |

**成功 result**：`{ "from": "Hoe", "to": "Watering Can", "slot": 0 }`
**可能错误**：`ITEM_NOT_FOUND`

---

#### L0 interact_with_tile — 与瓦片交互

```json
{
  "cmd_id": "c004",
  "action": "interact_with_tile",
  "params": {
    "x": 64,
    "y": 14
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x` | integer | ✅ | 目标瓦片 X |
| `y` | integer | ✅ | 目标瓦片 Y |

**用途**：开箱、收割、捡起物品、与 NPC 对话等通用交互
**成功 result**：因目标类型而异，例：`{ "type": "crop_harvested", "crop": "Potato", "quantity": 3, "quality": 1 }`
**可能错误**：`TOO_FAR` / `INVALID_TARGET` / `MENU_OPEN` / `INVENTORY_FULL`

---

#### L0 send_chat — 发送聊天消息

```json
{
  "cmd_id": "c005",
  "action": "send_chat",
  "params": {
    "message": "早上好！今天天气不错，我去浇水啦~"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | ✅ | 聊天消息内容 |

**成功 result**：`{ "displayed": true }`
**可能错误**：无（聊天始终可发送）

**注意**：这是 Agent 在游戏中"说话"的主要方式，会显示在聊天气泡中。

---

#### L0 do_emote — 播放表情动画

```json
{
  "cmd_id": "c005b",
  "action": "do_emote",
  "params": {
    "emote": "heart"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `emote` | string | ✅ | 表情名称或数字索引。可用名称：happy, sad, heart/love, exclamation/alert, angry, question/thinking, sleep/zzz, sick, laugh/haha, note/music, blush |

**成功 result**：`{ "emote": "heart", "index": 20 }`
**可能错误**：`INVALID_EMOTE`（未知表情名称）

**注意**：表情是角色头顶的图标气泡（如爱心、问号、怒气），持续约 2 秒。适合表达情绪反应。

---

#### L0 say_bubble — 头顶对话气泡

```json
{
  "cmd_id": "c005c",
  "action": "say_bubble",
  "params": {
    "text": "去浇水啦~"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | ✅ | 气泡文字，最多 20 字符（超出则命令失败） |

**成功 result**：`{ "text": "去浇水啦~" }`
**可能错误**：`TEXT_TOO_LONG`（超过 20 字符限制）、`MISSING_PARAMS`

**注意**：漫画式对话气泡，显示在角色头顶约 3 秒后自动消失。与 send_chat 的区别：send_chat 写入聊天框（持久可见），say_bubble 是头顶临时气泡（视觉表达）。适合短句反应如"好累啊"、"发现宝石！"。

---

#### L0 talk_to_npc — 与 NPC 对话

```json
{
  "cmd_id": "c006",
  "action": "talk_to_npc",
  "params": {
    "npc_name": "Abigail"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `npc_name` | string | ✅ | NPC 名称 |

**成功 result**：`{ "dialogue_text": "Hi! Beautiful day, isn't it?", "friendship_change": 20, "talked_today": true }`
**可能错误**：`NPC_NOT_FOUND` / `PATH_BLOCKED`（无法走到 NPC 身边）

---

#### L0 give_gift — 赠送礼物

```json
{
  "cmd_id": "c007",
  "action": "give_gift",
  "params": {
    "npc_name": "Abigail",
    "item_name": "Amethyst"
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `npc_name` | string | ✅ | NPC 名称 |
| `item_name` | string | ✅ | 背包中的物品名称 |

**成功 result**：`{ "gift_taste": "love", "friendship_change": 80, "gifted_today": true, "gifts_this_week": 2 }`
**gift_taste 枚举**：`"love"` | `"like"` | `"neutral"` | `"dislike"` | `"hate"`
**可能错误**：`NPC_NOT_FOUND` / `ITEM_NOT_FOUND` / `GIFT_LIMIT` / `PATH_BLOCKED`

---

#### L0 drop_item — 丢弃物品

```json
{
  "cmd_id": "c008",
  "action": "drop_item",
  "params": {
    "item_name": "Stone",
    "count": 50
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `item_name` | string | ✅ | 物品名称 |
| `count` | integer | ❌ | 丢弃数量（省略则全部） |

**成功 result**：`{ "dropped": "Stone", "count": 50 }`
**可能错误**：`ITEM_NOT_FOUND`

---

#### L0 ship_item — 出售物品（放入出货箱）

```json
{
  "cmd_id": "c009",
  "action": "ship_item",
  "params": {
    "item_name": "Parsnip",
    "count": 10
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `item_name` | string | ✅ | 物品名称 |
| `count` | integer | ❌ | 出售数量（省略则全部） |

**成功 result**：`{ "shipped": "Parsnip", "count": 10, "unit_price": 35, "total_value": 350 }`
**可能错误**：`ITEM_NOT_FOUND`

---

#### L0 plant_seeds — 种植种子

```json
{
  "cmd_id": "c010",
  "action": "plant_seeds",
  "params": {
    "seed_name": "Parsnip Seeds",
    "tiles": [
      { "x": 63, "y": 14 },
      { "x": 64, "y": 14 },
      { "x": 65, "y": 14 }
    ]
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `seed_name` | string | ✅ | 种子名称 |
| `tiles` | array | ✅ | 瓦片坐标列表 `[{x, y}]` |

**成功 result**：`{ "planted_count": 12, "failed_tiles": [{"tile": {"x":65,"y":16}, "reason": "tile_not_tilled"}], "seeds_remaining": 3 }`
**可能错误**：`ITEM_NOT_FOUND`（无种子）/ `INVALID_TARGET`

---

#### L0 buy_from_shop — 商店购买

```json
{
  "cmd_id": "c011",
  "action": "buy_from_shop",
  "params": {
    "shop": "pierre",
    "item_name": "Parsnip Seeds",
    "quantity": 20
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `shop` | string | ✅ | 商店标识 |
| `item_name` | string | ✅ | 商品名称 |
| `quantity` | integer | ✅ | 购买数量 |

**shop 枚举**：`"pierre"` | `"joja"` | `"robin"` | `"clint"` | `"marnie"` | `"willy"` | `"krobus"` | `"sandy"` | `"dwarf"`

**成功 result**：`{ "total_cost": 400, "gold_remaining": 2100 }`
**可能错误**：`SHOP_CLOSED` / `NOT_ENOUGH_GOLD` / `ITEM_NOT_FOUND` / `INVENTORY_FULL`

---

#### L0 place_item — 放置物品

```json
{
  "cmd_id": "c012",
  "action": "place_item",
  "params": {
    "item_name": "Sprinkler",
    "x": 65,
    "y": 15
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `item_name` | string | ✅ | 背包中的物品名称 |
| `x` | integer | ✅ | 目标瓦片 X |
| `y` | integer | ✅ | 目标瓦片 Y |

**成功 result**：`{ "placed": "Sprinkler", "tile": {"x": 65, "y": 15} }`
**可能错误**：`ITEM_NOT_FOUND` / `TILE_OCCUPIED` / `INVALID_TARGET` / `TOO_FAR`

---

#### L0 go_to_bed — 回家睡觉

```json
{
  "cmd_id": "c013",
  "action": "go_to_bed",
  "params": {}
}
```

无参数。Agent 发出此命令后，Mod 自动导航到床并结束当天。

**成功 result**：`{ "day_ended": true, "day": 15, "season": "spring" }`
**可能错误**：`PATH_BLOCKED`

---

#### L0 query — 主动查询

Agent 主动请求详细信息（超出 game_state 快照范围的数据）。

```json
{
  "cmd_id": "c014",
  "action": "query",
  "params": {
    "tool": "get_npc_info",
    "args": { "npc_name": "Abigail", "include_schedule": true }
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool` | string | ✅ | 查询工具名称（见下表） |
| `args` | object | ❌ | 工具参数（因 tool 而异） |

**可用 tool 枚举**：

| tool | 用途 | args |
|------|------|------|
| `get_world_state` | 完整世界状态 | 无 |
| `get_player_state` | 完整玩家状态 | `player_index?` |
| `get_inventory` | 完整背包 | `filter_type?` ("all"\|"tools"\|"seeds"\|"crops"\|"resources"\|"food"\|"gifts") |
| `get_location_objects` | 地图对象 | `location_name?`, `area?`, `include?` |
| `get_farm_summary` | 农场详情 | 无 |
| `get_npc_info` | NPC 详情 | `npc_name?`, `include_schedule?` |
| `get_available_actions` | 当前可用动作 | 无 |
| `get_tool_state` | 工具状态 | `tool_name` |
| `get_item_value` | 物品价值 | `item_name`, `quality?` |

**成功时**，cmd_result 的 `result` 字段包含对应工具的完整返回数据。

---

#### L1 water_crops — 批量浇水

```json
{
  "cmd_id": "c020",
  "action": "water_crops",
  "params": {
    "max_count": 30
  }
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tiles` | array | ❌ | 指定瓦片列表 `[{x,y}]`，省略则自动扫描全部未浇水耕地 |
| `max_count` | integer | ❌ | 最多浇灌格数（默认 999） |

**成功 result**：

```json
{
  "watered_count": 20,
  "skipped_already_watered": 5,
  "skipped_no_crop": 3,
  "stop_reason": null,
  "energy_cost": 40,
  "water_used": 20,
  "water_remaining": 10
}
```

**stop_reason 枚举**：`null`（正常完成）| `"NO_WATER"` | `"NO_ENERGY"` | `"MAX_COUNT"`
**可能错误**：`NO_TOOL`（无水壶）

---

#### L1 harvest_crops — 批量收获

```json
{
  "cmd_id": "c021",
  "action": "harvest_crops",
  "params": {}
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tiles` | array | ❌ | 指定瓦片列表，省略则自动收获所有成熟作物 |

**成功 result**：

```json
{
  "total_harvested": 28,
  "harvested": [
    { "crop": "Potato", "quantity": 3, "quality": 1 },
    { "crop": "Parsnip", "quantity": 1, "quality": 0 }
  ],
  "failed_tiles": [],
  "inventory_full_stopped": false
}
```

**可能错误**：`INVENTORY_FULL`

---

### 3.4 Agent 写入 commands.jsonl 伪代码

```python
cmd_counter = 0

def send_command(action: str, params: dict) -> str:
    global cmd_counter
    cmd_counter += 1
    cmd_id = f"c{cmd_counter:04d}"
    line = json.dumps({
        "cmd_id": cmd_id,
        "action": action,
        "params": params
    }, ensure_ascii=False)
    with open("bridge/commands.jsonl", "a") as f:
        f.write(line + "\n")
    return cmd_id  # 用于跟踪 cmd_result
```

---

## 4. agent_status.json

Agent 覆写，Mod 轮询读取（用于 HUD 显示、超时检测等）。

### 4.1 完整 Schema

```json
{
  "status": "idle",
  "current_task": null,
  "thinking_about": null,
  "last_updated": 1711526400.123,
  "error": null
}
```

### 4.2 字段定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | ✅ | Agent 当前状态 |
| `current_task` | string\|null | ✅ | 当前执行的任务描述（如 "浇灌农场"） |
| `thinking_about` | string\|null | ❌ | Agent 正在思考的内容（供 HUD 展示） |
| `last_updated` | float | ✅ | Unix 时间戳 |
| `error` | string\|null | ✅ | 错误信息（Agent 内部错误，非游戏错误） |

### 4.3 status 枚举

| 值 | 含义 | HUD 建议显示 |
|----|------|-------------|
| `"idle"` | 空闲，等待事件/心跳 | 💤 发呆中 |
| `"thinking"` | 正在调 LLM 决策 | 🤔 思考中… |
| `"acting"` | 正在执行动作序列 | 🏃 执行中 |
| `"chatting"` | 正在生成/发送聊天 | 💬 想说话 |
| `"error"` | Agent 出错 | ⚠️ 出错了 |
| `"shutdown"` | Agent 正在退出 | 👋 离开 |

---

## 5. 完整交互时序示例

以下是一个完整的 MVP 场景（玩家卖钻石 → Agent 反应）：

```
时间线 ────────────────────────────────────────────────────→

[T=0s]  游戏新一天开始
Mod → events.jsonl:
  {"seq":1, "ts":1711526400, "type":"day_started", "data":{"day":15, "season":"spring", "year":1, "day_of_week":"Monday", "weather":"sunny", "is_festival":false, "festival_name":null}}

Mod → game_state.json:
  { "status":"running", "game_day_started":true, "world":{...}, "player":{...}, ... }

[T=0.5s]  Agent 读到 day_started 事件
Agent → agent_status.json:
  { "status":"thinking", "current_task":"每日规划", "last_updated":1711526400.5, "error":null }

[T=2.5s]  LLM 返回规划结果：先浇水
Agent → commands.jsonl:
  {"cmd_id":"c0001", "action":"water_crops", "params":{"max_count":30}}
Agent → agent_status.json:
  { "status":"acting", "current_task":"浇灌农场", ... }

[T=15s]  浇水完成
Mod → events.jsonl:
  {"seq":5, "ts":1711526415, "type":"cmd_result", "data":{"cmd_id":"c0001", "success":true, "message":"成功浇灌了 20 格耕地", "error_code":null, "result":{"watered_count":20, "energy_cost":40, "water_remaining":10, "stop_reason":null}}}

[T=60s]  心跳 — Agent 进入闲聊模式
Agent → commands.jsonl:
  {"cmd_id":"c0002", "action":"send_chat", "params":{"message":"今天天气不错~浇水完成了，剩下的交给你啦"}}

[T=120s]  玩家在商店不小心卖了钻石
Mod → events.jsonl:
  {"seq":12, "ts":1711526520, "type":"inventory_changed", "data":{"added":[{"name":"Gold","item_id":"(O)384","count":750,"quality":0,"sell_price":0}], "removed":[{"name":"Diamond","item_id":"(O)72","count":1,"quality":0,"sell_price":750}], "source":"sell"}}

[T=120.2s]  Agent 读到事件：钻石被卖了！
Agent → agent_status.json:
  { "status":"thinking", "current_task":null, "thinking_about":"钻石被卖了…要不要提醒？", ... }

[T=122s]  LLM 返回反应
Agent → commands.jsonl:
  {"cmd_id":"c0003", "action":"send_chat", "params":{"message":"啊！你刚把钻石卖掉了！那个很珍贵的！下次卖之前先问问我好不好 😭"}}
Agent → agent_status.json:
  { "status":"idle", ... }
```

---

## 6. 设计约束备忘

| 约束 | 说明 |
|------|------|
| **原子写** | 覆写文件（game_state / agent_status）先写 `.tmp` 再 `rename` |
| **完整行** | 读 jsonl 时只处理以 `\n` 结尾的行，忽略不完整行 |
| **单写方** | 每个文件只有一个进程写入，不存在写冲突 |
| **seq 重置** | events.jsonl 的 seq 每游戏日重置为 1 |
| **日清理** | 每游戏日开始时 Mod 清空两个 jsonl 文件 |
| **状态过期** | 不特殊处理，失败重试兜底 |
| **字符编码** | 所有文件 UTF-8，JSON 中文不转义（`ensure_ascii=False`） |

---

## 7. 版本演进预留

以下为 MVP 后可能扩展的能力，当前 **不实现**，但协议格式已预留兼容空间：

- **L2 意图命令**：`do_farming` / `go_socialize` 等高层级目标
- **Skill 执行**：`execute_skill` 命令，调用 JSON DSL 定义的技能
- **多 Agent**：game_state 扩展为多玩家、commands 增加 agent_id 字段
- **surroundings 增强**：ASCII 地图表征或结构化 tile 网格

---

## 关联文档

| 文档 | 说明 |
|------|------|
| `Agent-Mod协作协议设计.md` | 运行时行为（轮询频率、生命周期、节奏同步、并发策略） |
| `StardewAgent-Adapter层设计规格.md` | C# 实现细节（SMAPI API 映射、稳定性评估） |
| `design-Adapter层扩展.md` | P0/P1 能力缺口分析与扩展方案 |
| `skill-runtime-architecture.md` | JSON DSL 技能系统（独立于本协议） |
