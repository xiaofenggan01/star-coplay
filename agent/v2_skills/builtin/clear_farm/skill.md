---
name: clear_farm
description: "清理农场杂物（杂草、树枝、石头）。当玩家说"清理一下农场"、"帮我除草"、"把石头清掉"、或你观察到农场杂物较多时使用。"
category: farming
version: "1.0.0"
energy_estimate: "high"
time_estimate: "60-300s"
parameters:
  targets:
    type: string
    description: "要清理的目标类型：all（全部）| weeds（杂草）| twigs（树枝）| stones（石头）"
    default: "all"
  stamina_below:
    type: integer
    description: "体力低于此值时停止（0 表示不检查）。清理杂物消耗体力，建议设置合理阈值。"
    default: 30
  time_after:
    type: integer
    description: "游戏时间超过此值时停止（如 1800 = 18:00，0 表示不检查）"
    default: 1800
tags: [farming, clearing, daily, debris, maintenance]
---

# clear_farm

清理农场中的杂物（杂草、树枝、石头）。当玩家说"清理一下农场"、"帮我除草"、"把石头清掉"、或你观察到农场杂物较多时，都应使用此技能。按类型分阶段执行：镰刀割草 → 斧头砍树枝 → 镐敲石头。

参数：targets=all(清理范围：all/weeds/twigs/stones), stamina_below=30, time_after=1800
前提：背包有对应工具（镰刀/斧头/镐），在农场地图
示例："帮我清理农场" → skill:clear_farm()
     "只除草就行" → skill:clear_farm(targets="weeds")

## 行为

**targets="all"（默认）**：

1. **阶段 1 — 杂草**：`find_targets("debris")` 筛选杂草 → `execute_on_targets(use_tool, 镰刀)`
2. **取消检查**
3. **阶段 2 — 树枝**：`find_targets("debris")` 筛选树枝 → `execute_on_targets(use_tool, 斧头)`
4. **取消检查**
5. **阶段 3 — 石头**：`find_targets("rock")` → `execute_on_targets(use_tool, 镐)`

**targets="weeds"/"twigs"/"stones"**：只执行对应阶段。

每个阶段的 `execute_on_targets` 传入 `stamina_below` 和 `time_after` 作为 stop_conditions，由 Mod 侧在每个目标处理后检查。

## 参数

| 名称 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| targets | string | "all" | 清理范围：all / weeds / twigs / stones |
| stamina_below | int | 30 | 体力低于此值时停止，0=不检查 |
| time_after | int | 1800 | 游戏时间超过此值时停止，0=不检查 |

## 前置条件

- 背包中有对应工具（镰刀 / 斧头 / 镐），缺少工具的阶段会被跳过（Mod 返回 ITEM_NOT_FOUND）
- 当前在农场地图（Farm）或含杂物的地图

## Edge Case 处理说明

| 边界情况 | 处理层 | 说明 |
|---------|--------|------|
| 某类杂物不存在 | L3 Skill | `find_targets` 返回空列表，该阶段跳过，继续下一阶段 |
| 单个目标不可达 | Mod (L2) | 自动跳过（skip），计入 skipped，继续下一个 |
| 体力不足 | Mod (L2) via stop_conditions | 任意阶段触发后终止，后续阶段不再执行 |
| 游戏时间过晚 | Mod (L2) via stop_conditions | 同上 |
| 缺少工具 | Mod (L2) | 返回错误，该阶段 cleared=0，其余阶段正常执行 |
| targets 参数非法 | L3 Skill | 返回 failed + 友好错误提示 |

## 返回值

```json
{
  "success": true,
  "status": "ok",
  "observation": "清理完成：杂草 5 株、树枝 3 根、石头 8 块，共 16 件，跳过 2 件",
  "data": {
    "weeds_cleared": 5,
    "twigs_cleared": 3,
    "stones_cleared": 8,
    "total_cleared": 16,
    "total_skipped": 2,
    "stopped_reason": "all_done"
  }
}
```

`status` 取值：
- `ok`：所有请求阶段均执行完毕（包括无目标的阶段）
- `partial`：至少清理了一件，但因 stop_condition 提前结束
- `failed`：一件都没清理（全部阶段无目标，或参数非法）
