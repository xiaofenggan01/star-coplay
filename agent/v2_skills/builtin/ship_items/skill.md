---
name: ship_items
description: "把背包物品放入运输箱出售。当玩家说"把这些卖了"、"出货"、"今天的菜卖掉"、"清一下背包"，或你判断背包将满需要清理时，都应使用此技能。"
category: economy
version: "1.0.0"
energy_estimate: "low"
time_estimate: "5-10s"
parameters:
  items:
    type: array
    description: "要出售的物品列表（按显示名称或英文内部名）。空列表=出售所有可售物品（自动排除种子/木材/石头等基础材料）。"
    default: []
  exclude:
    type: array
    description: "明确排除、不出售的物品列表（按显示名称或英文内部名）。"
    default: []
  min_quality:
    type: string
    description: "保留该品质及以上的物品不出售，可选值：silver/gold/iridium。默认不保留。"
    default: ""
  dry_run:
    type: boolean
    description: "仅预览出货计划，不实际出货。默认 false（直接出货）。"
    default: false
tags: [economy, shipping, daily]
---

把背包物品放入农场运输箱售出，明日结算金币。直接操作内存（无需导航到运输箱），同步完成。

参数：
- items=[]（指定物品白名单，空=全部可售）
- exclude=[]（明确排除不卖的物品）
- min_quality=""（保留该品质以上的物品，如 "gold" 保留金星及以上）
- dry_run=false（仅预览不执行）

前提：在游戏中（农场存档已加载）
示例："把这些菜卖了" → skill:ship_items()
     "卖菜，但留住金星以上的" → skill:ship_items(min_quality="gold")
     "只卖萝卜和南瓜" → skill:ship_items(items=["Parsnip", "Pumpkin"])
     "先看看能卖多少钱" → skill:ship_items(dry_run=true)
