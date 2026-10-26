# Adapter 层工具清单

> 精简自 `StardewAgent-Adapter层设计规格 v1.0`
> 完整 JSON Schema 和返回示例见原始规格文档

---

## Read Tools（7 个，只读查询）

| # | Tool | 描述 | 关键返回 |
|---|------|------|---------|
| 1 | `get_world_state` | 世界状态 | 时间、日期、季节、天气、是否节日 |
| 2 | `get_player_state` | 玩家状态 | 位置、体力、生命、金币、装备、技能、状态效果 |
| 3 | `get_inventory` | 背包物品 | 槽位列表（名称、ID、数量、品质），支持按类型过滤 |
| 4 | `get_location_objects` | 地图对象 | 物品/地形/作物/NPC，支持矩形区域过滤 |
| 5 | `get_farm_summary` | 农场概览 | 作物统计、机器状态、动物状态 |
| 6 | `get_npc_info` | NPC 信息 | 位置、好感度、今日日程、生日、送礼记录 |
| 7 | `get_available_actions` | 可用动作检测 | 能否移动/菜单是否打开/过场动画/对话中 |

## Exec Tools（13 个，执行动作）

| # | Tool | 描述 | 关键参数 | 稳定性 |
|---|------|------|---------|--------|
| 8 | `navigate_to` | 移动到目标位置 | target_tile(x,y) 或 target_location | ⭐⭐⭐ |
| 9 | `use_tool_on_tile` | 对瓦片使用工具 | tile(x,y) | ⭐⭐⭐⭐ |
| 10 | `equip_item` | 切换装备 | slot_index 或 item_name | ⭐⭐⭐⭐⭐ |
| 11 | `interact_with_tile` | 与瓦片交互 | tile(x,y) | ⭐⭐⭐⭐ |
| 12 | `plant_seeds` | 种植种子 | seed_name, tiles[] | ⭐⭐⭐⭐ |
| 13 | `water_crops` | 批量浇水 | tiles[]（可选）, max_count | ⭐⭐⭐⭐ |
| 14 | `harvest_crops` | 批量收获 | tiles[]（可选） | ⭐⭐⭐⭐ |
| 15 | `talk_to_npc` | 与 NPC 对话 | npc_name | ⭐⭐⭐⭐ |
| 16 | `give_gift` | 送礼 | npc_name, item_name | ⭐⭐⭐⭐ |
| 17 | `go_to_bed` | 回家睡觉 | 无 | ⭐⭐⭐⭐ |
| 18 | `buy_from_shop` | 商店购买 | shop, item_name, quantity | ⭐⭐⭐ |
| 19 | `place_item` | 放置物品 | item_name, tile(x,y) | ⭐⭐⭐⭐ |
| 20 | `refill_watering_can` | 浇水壶装水 | 无 | ⭐⭐⭐⭐ |

## Capability（1 个）

| # | Tool | 描述 |
|---|------|------|
| 21 | `get_capabilities` | 能力声明（支持/不支持的功能列表） |

## Event 类型（Mod → Agent）

### 生命周期事件

| 事件 | 触发时机 | 内容 |
|------|---------|------|
| `day_started` | 新一天开始 | 日期、天气、未浇水数、可收获数、NPC 生日 |
| `day_ending` | 一天结束 | 今日摘要、出货箱价值 |
| `time_changed` | 游戏时间推进 | 当前时间、剩余时间提醒 |
| `season_changed` | 季节更替 | 过季作物、新季可种作物 |

### 动作反馈事件

| 事件 | 触发条件 | 内容 |
|------|---------|------|
| `cmd_result` | 命令执行完成 | cmd_id + success + 状态变更 |
| `energy_low` | 体力 < 20% | 当前体力、建议（吃东西/回家） |
| `inventory_full` | 背包满 | 建议（卖/存箱子） |
| `tool_empty` | 浇水壶空 | 最近水源位置 |

### 环境感知事件

| 事件 | 触发条件 | 内容 |
|------|---------|------|
| `inventory_changed` | 背包变动 | added/removed 列表 + high_value 标注 |
| `npc_nearby` | NPC 进入范围 | NPC 名称、距离 |

## MVP 实施分层

| Phase | 目标 | 涉及工具 |
|-------|------|---------|
| **0** | 验证游戏接入 | get_world/player_state, get_location_objects, navigate_to, use_tool_on_tile |
| **1** | 每日浇水闭环 | day_started, water_crops, refill_watering_can, go_to_bed |
| **2** | 接入 LLM | 全部 Read + 核心 Exec + Event 注入 |
| **3** | 社交与商业 | talk_to_npc, give_gift, buy_from_shop |

## 统一返回格式（Exec Tools）

```json
{
  "success": true/false,
  "message": "人类可读描述",
  "error_code": "ERROR_CODE（仅失败时）",
  "suggestion": "建议操作（仅失败时）",
  "state_changes": [{"type": "...", ...}],
  "energy_cost": 0
}
```

## 不支持的操作（安全边界）

直接修改金币/体力/生命 | 强制作物成熟 | 瞬间传送 | 修改 NPC 好感度 | 控制天气/时间 | 矿洞自动战斗 | 钓鱼小游戏 | 建筑放置（Robin 菜单）
