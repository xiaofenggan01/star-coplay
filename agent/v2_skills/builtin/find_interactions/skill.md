---
name: find_interactions
description: "查询当前视野中的交互候选项。只组织客观可交互候选，不承载任务意图。"
category: perception
version: "1.0.0"
energy_estimate: "none"
time_estimate: "1-3s"
parameters:
  radius:
    type: integer
    description: "当前视野查询半径，默认 8"
    default: 8
  limit:
    type: integer
    description: "最多返回多少个候选，默认 20"
    default: 20
  offset:
    type: integer
    description: "分页偏移，默认 0；若返回 has_more=true，用 data.next_query 继续查询下一页候选"
    default: 0
tags: [perception, interactions, query]
---

# find_interactions

查询当前视野中的交互候选项。它基于 L1 Fact 和 L3 Query Core 组织可交互对象，例如可收获作物、待浇水作物、可收取机器、容器、NPC、UI 元素等。

边界：

- 不接收任务 intent。
- 不生成或解析 RefId。
- 不执行动作，只返回候选。

调用方约定：

- 每个候选可能包含 `recommended_steps`。
- 先读 `execution_gate`，再考虑 `recommended_steps`。
- 只有当 `execution_gate.status="ready"` 且前置条件满足时，`recommended_steps[].tool` 与 `recommended_steps[].args` 才可作为后续工具调用片段。
- 若 `execution_gate.status="requires_resolution"`，必须先处理 `execution_warnings`，不要把 `recommended_steps` 批量照做。
- 不要把 `ref_id` 当作坐标解析，也不要自行构造 `move_to(target=...)`、`interact(target=...)` 这类旧参数。
- 若候选带有 `tool_requirement.kind="tool_category"`，它表达的是工具类别需求，不是 `equip(item_name=...)` 的精确物品名。
- 分页：返回 `has_more=true` 时，使用 `data.next_query` 继续查询下一页候选；不要自行推断或重排候选。
