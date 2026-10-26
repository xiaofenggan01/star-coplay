---
name: pet_animals
description: '抚摸当前地图中未被抚摸的农场动物或猫狗宠物。当玩家说“摸摸动物”“照顾动物”“摸摸宠物”“摸摸猫/狗”时，直接调用此 skill，不要先调用 observe。'
category: farming
version: "1.0.0"
energy_estimate: "low"
time_estimate: "30-120s"
routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: current_map
  ambiguity_policy: execute_when_clear
parameters:
  max_count:
    type: integer
    description: "最多抚摸几只动物，0表示不限制"
    default: 0
tags: [farming, animals, daily]
---

# pet_animals

抚摸当前地图中未被抚摸的农场动物或猫狗宠物。当玩家说"去摸摸鸡"、"照顾一下动物"、"摸摸宠物"、"摸摸猫/狗"时，直接调用此技能，不要先调用 observe。技能内部会查询 character facts，按距离逐个移动并 interact。

参数：max_count=0(最多抚摸几只，0=不限)
前提：当前地图有农场动物或猫狗宠物
示例："去看看小鸡们" → skill:pet_animals()
     "摸摸宠物" → skill:pet_animals()
     "摸两只就行" → skill:pet_animals(max_count=2)

## 行为

1. 查询当前地图中的 character facts
2. 筛出 `farm_animal.was_petted=false` 或 `pet.petted_today=false` 的目标
2. 按距离排序，逐个导航并抚摸
3. 返回结构化结果；不在 skill 内说话

## 参数

| 名称 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| max_count | int | 0 | 最多抚摸几只，0=不限 |

## 返回值

```json
{
  "success": true,
  "status": "ok",
  "observation": "已抚摸 3/3 只动物",
  "data": {"petted": 3, "skipped": 0, "total": 3}
}
```
