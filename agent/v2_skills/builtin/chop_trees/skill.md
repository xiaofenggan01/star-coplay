---
name: chop_trees
description: "L3 atomic MVP for chopping normal mature trees to collect wood. Use this when the player explicitly asks to chop trees, cut trees, get wood, gather wood, or says Chinese equivalents like 砍树, 伐木, 搞点木头, 收集木头. If the player does not mention a quantity, stop after the backpack has gained 5 Wood. Clamp requested wood to at most 50, then stop and wait for the player's next command."
category: farming
version: "0.1.0"
energy_estimate: "medium"
time_estimate: "30-180s"
timeout: 240
routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: current_map
  ambiguity_policy: execute_when_clear
parameters:
  target_wood:
    type: integer
    description: "How many Wood to gain before stopping. Default 5. Values above 50 are clamped to 50."
    default: 5
  radius:
    type: integer
    description: "Current-map query radius around the player. Default 16."
    default: 16
  include_stumps:
    type: boolean
    description: "Whether to chop existing stumps too. Default false."
    default: false
tags: [farming, trees, wood, atomic, l3]
---

# chop_trees

Use this skill for direct player requests to chop trees or collect wood. This MVP composes proto-native L1 atoms in Python: query runtime and inventory, query nearby tree and tile facts, equip an axe, move adjacent, use the axe, re-query to verify, and stop when Wood in the backpack has increased enough.

Default behavior is deliberately bounded: if the player only says "砍树" or "搞点木头" without a quantity, call `skill:chop_trees()` and stop after +5 Wood. If a quantity is requested, pass it as `target_wood`, but never exceed 50. After completion, do not start another chopping round unless the player gives a new command.

## Behavior

1. Query player runtime and backpack inventory.
2. Find an axe in the player inventory.
3. Query nearby normal mature trees and adjacent tile facts on the current map.
4. Skip trees that have no passable adjacent standing tile.
5. Equip the axe, then for each reachable tree, move adjacent, swing the axe, re-query that tile, and continue until the tree is gone or becomes a stump.
6. Interact only with nearby loose-item facts after a tree falls.
7. Stop as soon as backpack Wood has increased by the requested amount, or when there are no reachable trees.

## Return

```json
{
  "success": true,
  "status": "ok",
  "observation": "Chopped 1 tree and gained 5 Wood. Waiting for the next player command.",
  "data": {
    "wood_before": 0,
    "wood_after": 5,
    "wood_gained": 5,
    "target_wood": 5,
    "trees_chopped": 1,
    "trees_skipped": 0,
    "stopped_reason": "wood_target_reached",
    "warnings": []
  }
}
```
