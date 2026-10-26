---
name: search_facts
description: "在当前视野的 L1 Facts 上执行 L3 filter/sort 查询。只支持 schema 声明过的字段，不解析 ref_id。"
category: perception
version: "1.0.0"
energy_estimate: "none"
time_estimate: "1-3s"
parameters:
  fact_type:
    type: string
    description: "要查询的 fact 类型，例如 crop/tree/machine/container/bed/furniture/exit/door/warp/npc/farm_animal/inventory_item。exit/exits 会联合查询 door 与 warp。"
  filters:
    type: object
    description: "简单 KV filter，例如 {\"has_moss\": true}、{\"has_moss\": {\"eq\": true}} 或 {\"growth_stage\": {\"min\": 3}}。"
  sort:
    type: string
    description: "排序字段；留空时使用该 fact_type 的默认排序。用 -field 表示降序。"
  limit:
    type: integer
    description: "最多返回数量，默认 20。"
  offset:
    type: integer
    description: "分页偏移，默认 0；若返回 has_more=true，用 data.next_offset 继续查询下一页。"
  radius:
    type: integer
    description: "当前视野查询半径，默认 8。"
tags: [perception, query, facts, l3]
---

# search_facts

在当前视野的 L1 Facts 上执行 L3 查询。它负责 filter、sort、limit 和 ref 返回，不负责行动规划。

`ref_id` 是不透明字符串，只能原样传给 inspect 或相关工具，不能自行生成或解析。

支持的 filter/sort 字段由 L3 schema 声明；不支持的字段会返回 `INVALID_QUERY`。等值匹配可以写成 `{"field": value}`，也可以显式写成 `{"field": {"eq": value}}`。

分页：返回 `has_more=true` 时，使用返回的 `data.next_offset` 再次调用同样的 `fact_type/filters/sort/limit`，并传入 `offset=data.next_offset`。

`exit`/`exits` 是当前视野出口的联合查询类型，包含 `door` 与 `warp`；如果只想查询一种底层事实，可以直接使用 `door` 或 `warp`。
