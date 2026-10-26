---
name: walk_route
description: "串行走当前用户指定的一组地图坐标点。适用于玩家明确给出多个地点并要求依次走过去的场景，例如 去 Farm 64,15 然后去 Farm 70,20 再去 Town 31,67。L3 负责编排 waypoint 队列，逐点调用 go_to，不要把整串地点压成一次底层寻路。"
category: navigation
version: "0.1.0"
energy_estimate: "low"
time_estimate: "10-180s"
timeout: 240
routing:
  owns_discovery: false
  handles_precheck: true
  verifies_result: true
  execution_scope: cross_map
  ambiguity_policy: ask_or_fail
parameters:
  waypoints:
    type: array
    description: "按顺序执行的 waypoint 列表。每个元素必须包含 map_id、x、y。"
    items:
      type: object
  stop_on_failure:
    type: boolean
    description: "某个 waypoint 失败后是否立即停止。默认 true。"
    default: true
tags: [navigation, route, waypoint, l3]
---

# walk_route

Use this skill when the user clearly specifies multiple concrete destinations and wants the character to visit them in order. This is an L3 orchestration skill: it keeps the waypoint queue in Python, calls `go_to()` once per waypoint, records which points succeeded, and stops at the exact failed waypoint instead of handing a whole route to the lower layers.

## Behavior

1. Validate waypoint shape and order.
2. For each waypoint, call `ctx.move_to(x, y, location=map_id)`.
3. Record succeeded and failed waypoints.
4. Stop immediately on the first failure by default.
5. Return partial status when only part of the route completed.
