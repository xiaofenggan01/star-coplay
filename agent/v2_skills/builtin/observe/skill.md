---
name: observe
description: "感知当前视野的入口，返回语义化文字描述和可继续查询的 ref_id。对象详情优先交给 inspect。"
category: perception
version: "1.0.0"
energy_estimate: "none"
time_estimate: "1-3s"
parameters:
  detail:
    type: string
    description: "空=概览；类别名（crops/animals/npcs/containers/inventory/objects/exits）=列表；opaque ref_id=转交 inspect；旧式具体 id 仅兼容"
    default: ""
tags: [perception, observe, info]
---

# observe

感知当前视野。当你需要决策但不知道现状、玩家询问周围有什么、规划任务前需要确认条件时，应先使用此技能。返回语义化文字，并在可用时返回结构化 data 与可继续查询的 opaque `ref_id`。

- observe() — 概览（位置/时间/体力/农场聚合/附近 NPC），300-450 字
- observe(detail="crops") — 某类详情（每项附 `ref_id`）
- inspect(ref_id="...") — 单实例详情的主路径
- observe(detail="<opaque ref_id>") — 兼容转交 inspect

参数：detail（空/类别名/opaque ref_id；旧式具体 id 仅兼容）
前提：无（任意时刻可用）
示例："看看周围" → skill:observe()
     "哪些作物没浇水" → skill:observe(detail="crops")
     "看箱子里有啥" → skill:observe(detail="containers") → inspect(ref_id=<容器 ref_id>)
     "路易斯今天在哪" → skill:observe(detail="npcs") → 必要时 inspect(ref_id=<NPC ref_id>)

## 三层结构

### 层 0：场景速览（observe()）

无参数调用，返回完整的场景速览：
- 当前地图名 + 时间 + 体力
- 背包摘要
- 农场状态（作物/动物/地面对象）- 有内容时显示
- 容器摘要 - 有容器时显示
- 附近 NPC - 有 NPC 时显示
- 末尾附 hint 提示可深入查看的类别

### 层 1：类别详情（observe(detail=枚举)）

| detail 值 | 含义 |
|-----------|------|
| `crops` | 当前视野作物列表（含 opaque `ref_id`） |
| `animals` | 当前视野动物状态（含 opaque `ref_id`） |
| `npcs` | 当前地图 NPC（含 opaque `ref_id`） |
| `containers` | 当前视野容器（含 opaque `ref_id`） |
| `inventory` | 背包物品（按类别分组） |
| `objects` | 地面可互动对象（石头/树/杂物） |
| `exits` | 当前地图出口 |

### 层 2：单实例详情

主路径是 `inspect(ref_id=...)`。`ref_id` 必须来自前序 observe/query 结果，不能自行生成或解析。

`observe(detail="<opaque ref_id>")` 会兼容转交 inspect。

旧式 `chest:<locshort>_<x>_<y>`、`crop:<locshort>_<x>_<y>`、`animal:<name>`、`npc:<Name>` 仍作为兼容路径保留，但不应作为新调用方式。

## 错误降级

任何 query_* 失败时返回 "（无法获取）" 占位，不阻塞整体输出。

## 返回格式

返回语义化文字；proto-native 路径还会返回结构化 data。LLM 应直接引用 data 或文字中的 opaque `ref_id`，原样传给 inspect。

## 边界情况

| 情况 | 处理 |
|------|------|
| 无作物/动物/NPC | 对应模块不显示（层 0），或返回 "当前没有..." |
| query 失败 | 降级为 "（无法获取）"，不报错 |
| 内容过多 | 截断并提示使用更具体的 detail |
| 未知 detail 前缀 | 若看起来是 opaque ref，则转交 inspect；否则返回错误提示 |
