---
name: harvest_crops
description: '收割当前地图所有成熟作物。当玩家说“帮我收菜”“收割一下”“收菜”时，直接调用此 skill，不要先调用 observe。'
category: farming
version: "1.0.0"
energy_estimate: "low"
time_estimate: "15-60s"
routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: current_map
  ambiguity_policy: execute_when_clear
parameters:
  stamina_below:
    type: integer
    description: "体力低于此值时停止，0表示不检查（收割不耗体力）"
    default: 0
  time_after:
    type: integer
    description: "游戏时间超过此值时停止（如1400=14:00），0表示不检查"
    default: 0
tags: [farming, harvest, daily]
---

收割当前地图所有成熟作物。当玩家说"帮我收菜"、"收割一下"、"收菜"时，直接调用此技能，不要先调用 observe。技能内部先查询 crop facts 做成熟作物预检，然后调用 Mod 侧已有 `harvest_crops` 组合能力执行整张当前地图的收割、导航和可选补种，最后复查剩余成熟作物数量。

参数：stamina_below=0(收割不耗体力，默认不检查), time_after=0(默认不检查)
前提：当前在有作物的地图
示例："帮我收一下" → skill:harvest_crops()

### 行为

1. 查询当前地图 crop facts，筛出 `ready_for_harvest=true` 的作物。
2. 若没有成熟作物，返回 ok。
3. 调用 Mod 侧 `harvest_crops` 组合能力；Mod 负责扫描当前地图、按路径导航到成熟作物、收割并可选补种。
4. 完成后复查 crop facts，返回 `harvested/skipped/total/remaining_harvestable/stopped_reason`，不在 skill 内说话。

### 前置条件

- 角色在有作物的地图（通常是 Farm）

### 返回值

```json
{
  "success": true,
  "status": "ok",
  "observation": "收割了 15/15 棵作物",
  "data": {
    "harvested": 15,
    "skipped": 0,
    "total": 15,
    "stopped_reason": "all_done"
  }
}
```
