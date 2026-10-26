# Star CoPlay — Tool Reference

> Mod 层已实现的全部 Bridge 命令参考。
> Schema 配置：`mod/StarCoPlay/Bridge/tools.json`（Agent 层调用的机器可读定义）
> 更新时间：2026-03-28

---

## 核心概念

- **Bridge 命令**：Agent 通过 `commands.jsonl` 写入，Mod 读取并执行
- **交互原则**：`interact_with_tile` 调用游戏原生 `GameLocation.checkAction`（等同按 X 键），由游戏内置逻辑统一处理所有交互
- **异步命令**：标记 `async: true` 的命令通过状态机逐帧推进，完成后写入 `events.jsonl`
- **同步命令**：立即执行并返回结果

---

## 一、移动与传送（movement）

### navigate_to

移动到目标位置。同地图用 A* 寻路，提供 location 参数则跨地图传送。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x` | int | 是 | 目标瓦片 X |
| `y` | int | 是 | 目标瓦片 Y |
| `location` | string | 否 | 目标地图名（如 Farm, Town, Mine），提供则传送 |

**异步**：是（寻路需多帧完成）
**错误码**：`MISSING_PARAMS` / `WARP_FAILED` / `NAV_FAILED`

---

## 二、工具与交互（action）

### use_tool_on_tile

对指定瓦片使用当前装备的工具。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x` | int | 是 | 目标瓦片 X |
| `y` | int | 是 | 目标瓦片 Y |

**前置**：需先通过 `equip_item` 装备正确工具
**错误码**：`MISSING_PARAMS` / `TOOL_FAILED`

### equip_item

从快捷栏中按名称装备物品。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `item_name` | string | 是 | 物品名称（如 Watering Can, Hoe, Pickaxe） |

**错误码**：`MISSING_PARAMS` / `ITEM_NOT_FOUND`

### interact_with_tile

与瓦片上的对象交互 — 等同玩家按 X 键。调用 `GameLocation.checkAction`，由游戏内置逻辑统一处理所有可交互对象。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x` | int | 是 | 目标瓦片 X |
| `y` | int | 是 | 目标瓦片 Y |

**支持**：可收集品拾取、箱子、机器、NPC、家具、商店等
**自动处理**：交互前自动面朝目标瓦片
**错误码**：`MISSING_PARAMS` / `INTERACT_FAILED`

---

## 三、聊天与表情（communication）

### send_chat

在游戏聊天框发送消息，自动加上当前 Farmer 名称前缀。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 消息内容 |

**显示格式**：`{Farmer名}: {消息内容}`（浅绿色）
**错误码**：`MISSING_PARAMS`

### show_hud

在游戏左下角显示 HUD 通知消息。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `message` | string | 是 | — | 通知内容 |
| `type` | int | 否 | 0 | 0=普通 1=错误(红色) 2=成就 3=新配方 4=新任务 |

**错误码**：`MISSING_PARAMS`

### do_emote

让玩家角色播放表情气泡动画。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `emote` | string | 是 | 表情名称或数字索引 |

**可用表情**：

| 名称 | 别名 | 索引 | 效果 |
|------|------|------|------|
| happy | — | 32 | 开心 |
| sad | — | 28 | 难过 |
| heart | love | 20 | 爱心 |
| exclamation | alert | 16 | 惊叹号 |
| angry | — | 12 | 生气 |
| question | thinking | 8 | 问号 |
| sleep | zzz | 24 | 睡觉 |
| sick | — | 36 | 生病 |
| laugh | haha | 40 | 大笑 |
| note | music | 56 | 音符 |
| blush | — | 60 | 脸红 |

**错误码**：`MISSING_PARAMS` / `INVALID_EMOTE`

---

## 四、背包与物品（inventory）

### drop_item

从背包丢弃物品到地面。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `item_name` | string | 是 | — | 物品名称 |
| `count` | int | 否 | -1 | 丢弃数量，-1 = 全部 |

**错误码**：`MISSING_PARAMS` / `ITEM_NOT_FOUND`

### ship_item

将物品放入出货箱售卖。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `item_name` | string | 是 | — | 物品名称 |
| `count` | int | 否 | -1 | 售卖数量，-1 = 全部 |

**错误码**：`MISSING_PARAMS` / `ITEM_NOT_FOUND`

---

## 五、NPC 社交（social）

### talk_to_npc

与 NPC 对话。支持按内部名或显示名查找。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `npc_name` | string | 是 | NPC 名称（如 Abigail, Robin, Pierre） |

**约束**：需在 NPC 2 格距离内
**错误码**：`MISSING_PARAMS` / `INVALID_STATE` / `NPC_NOT_FOUND` / `OUT_OF_RANGE`

### give_gift

将背包中的物品送给 NPC。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `npc_name` | string | 是 | NPC 名称 |
| `item_name` | string | 是 | 要送的物品名称 |

**约束**：需在 NPC 2 格距离内
**错误码**：`MISSING_PARAMS` / `INVALID_STATE` / `NPC_NOT_FOUND` / `ITEM_NOT_FOUND` / `OUT_OF_RANGE` / `ACTION_FAILED`

---

## 六、批量复合操作（batch）

所有批量操作为异步状态机，逐帧推进直到完成。

### water_crops

批量浇水农场所有未浇水作物。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `max_count` | int | 否 | 999 | 最大浇水数量 |

**路径规划**：贪心最近邻（曼哈顿距离），避免来回跑
**自动处理**：体力 <10 停止 / 水壶没水停止 / 导航失败跳过 / 动画超时强制重置
**错误码**：`NO_FARM` / `NO_WATERING_CAN` / `TOOL_LOST` / `CANCELLED`

### harvest_crops

批量收获农场所有成熟作物。

**自动处理**：背包满停止 / 导航失败跳过
**错误码**：`CANCELLED`

### go_to_bed

传送回 FarmHouse 并触发睡觉，结束当天。

**流程**：直接传送到床边 → 定位玩家到床上 → checkForAction 触发睡觉
**错误码**：`WARP_FAILED` / `WARP_TIMEOUT` / `NOT_IN_FARMHOUSE` / `CANCELLED`

---

## 七、感知与查询（perception）

### query

查询游戏状态信息。同步返回数据，不执行任何游戏动作。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool` | string | 是 | 查询类型 |

**可用查询**：

| tool 值 | 返回内容 |
|---------|---------|
| `get_world_state` | 时间、日期、季节、年份、天气、节日 |
| `get_player_state` | 位置、坐标、体力、生命、金币、技能、手持物品 |
| `get_inventory` | 已用/总槽位、物品列表（名称、ID、数量、品质） |
| `get_farm_summary` | 作物总数、未浇水数、可收获数 |
| `get_available_actions` | 可移动、空闲、菜单/过场/对话状态 |

**错误码**：`UNKNOWN_TOOL` / `QUERY_ERROR`

---

## 八、流程控制（control）

### cancel

取消当前正在执行的命令或批量操作。无参数。

---

## 九、全局错误码

| 错误码 | 说明 |
|--------|------|
| `UNKNOWN_ACTION` | 未知命令 |
| `BUSY` | 上一个命令尚未完成 |
| `DISPATCH_ERROR` | 命令分发异常 |

---

## 十、命令调用方式

### 1. Bridge 文件（Agent 层）

写入 `commands.jsonl`，格式：
```json
{"id": "unique-id", "action": "navigate_to", "params": {"x": 64, "y": 15}}
```

### 2. SMAPI 控制台（调试）

`agent_xxx` 命令（TestCommands.cs）

### 3. 游戏内聊天框（调试）

`/agent_xxx` 命令（ChatCommandHandler.cs，Harmony patch 拦截）

| 聊天命令 | 对应 Bridge 命令 |
|---------|-----------------|
| `/agent_status` | query (多个) |
| `/agent_goto X Y` | navigate_to |
| `/agent_goto_npc NAME` | navigate_to + talk_to_npc |
| `/agent_warp LOC [X Y]` | navigate_to (with location) |
| `/agent_interact X Y` | interact_with_tile |
| `/agent_equip NAME` | equip_item |
| `/agent_usetool X Y` | use_tool_on_tile |
| `/agent_drop NAME [N]` | drop_item |
| `/agent_ship NAME [N]` | ship_item |
| `/agent_emote NAME` | do_emote |
| `/agent_npcs` | query get_nearby_npcs |
| `/agent_objects [R]` | query scan_location |
| `/agent_farm` | query get_farm_summary |
| `/agent_cancel` | cancel |
