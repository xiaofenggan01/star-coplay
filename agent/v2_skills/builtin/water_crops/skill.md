---
name: water_crops
description: '浇灌当前地图所有未浇水的作物。当玩家说“帮我浇水”“浇一下地”“起来给作物浇水”时，直接调用此 skill，不要先调用 observe。'
category: farming
version: "1.0.0"
energy_estimate: "medium"
time_estimate: "30-180s"
routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: current_map
  ambiguity_policy: execute_when_clear
parameters:
  stamina_below:
    type: integer
    description: "体力低于此值时停止浇水（0 表示不检查）"
    default: 20
  time_after:
    type: integer
    description: "游戏时间超过此值时停止（如 1400 = 14:00，0 表示不检查）"
    default: 1400
tags: [farming, crops, daily, watering]
---

# water_crops

浇灌当前地图所有未浇水的作物。当玩家说"帮我浇水"、"浇一下地"、"起来给作物浇水"时，直接调用此技能，不要先调用 observe。技能内部先用 protocol query 做天气和缺水格预检，然后调用 Mod 侧已有 `water_crops` 组合能力执行浇水、移动和补水，最后复查缺水格数量。

参数：stamina_below=20(体力低于此值停止), time_after=1400(超过此时间停止)
前提：背包有喷壶，在农场地图
示例："帮我浇水" → skill:water_crops()
     "浇完水就歇着吧" → skill:water_crops(stamina_below=50)

## 行为

1. 查询当前地图中的 crop facts，筛出未浇水作物
2. 若无目标，立即返回成功（今天已全部浇过）
3. 调用 Mod 侧 `water_crops` 组合能力；Mod 负责装备喷壶、按路径浇水、没水时找水源补满并继续
4. 执行完成后只复查一次缺水格数量，确认是否为 0

## 参数

| 名称 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| stamina_below | int | 20 | 体力低于此值时停止，0=不检查 |
| time_after | int | 1400 | 游戏时间超过此值时停止（24 小时制 × 100），0=不检查 |

## 前置条件

- 玩家背包中有喷壶（否则 Mod 返回 ITEM_NOT_FOUND 错误）
- 当前在农场地图（Farm）或含作物的地图

## Edge Case 处理说明

| 边界情况 | 处理层 | 说明 |
|---------|--------|------|
| 所有作物已浇水 | L3 Skill | crop facts 无未浇水目标，立即返回 ok |
| 喷壶缺失 | Mod Compound | `water_crops` 返回失败，L3 转为 blocked |
| 喷壶没水 | Mod Compound | 自动寻找水源并补满后继续 |
| 体力不足 | Mod Compound | 组合能力按自身规则停止并返回 stopped_reason |
| 单棵作物不可达 | Mod Compound | 跳过并计入 skipped |
| 最短路径优化 | Mod Compound | 使用 Mod 侧已有路径/排序逻辑 |
| 最终确认 | L3 Skill | 完成后复查 crop facts，返回 `remaining_unwatered` |

## 返回值

```json
{
  "success": true,
  "status": "ok",
  "observation": "浇灌了 12/12 棵作物",
  "data": {
    "watered": 12,
    "skipped": 0,
    "refilled": 1,
    "total": 12,
    "remaining_unwatered": 0,
    "stopped_reason": "all_done"
  }
}
```

`status` 取值：
- `ok`：全部作物浇灌完成（或本来就没有未浇水作物）
- `partial`：浇了一部分（体力/时间触发 stop_condition，或有跳过）
- `failed`：一棵都没浇（喷壶缺失或执行异常）
