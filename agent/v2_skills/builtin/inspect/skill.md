---
name: inspect
description: "按前序 observe/query 返回的 RefId 查看具体对象详情。RefId 是不透明句柄，不应生成或猜测。"
category: perception
version: "1.0.0"
energy_estimate: "none"
time_estimate: "1-3s"
parameters:
  refs:
    type: array
    description: "RefId 字符串数组，来自前序 observe/query 结果"
    default: []
  ref_id:
    type: string
    description: "单个 RefId；refs 为空时使用"
    default: ""
  include_debug:
    type: boolean
    description: "是否包含 debug 信息"
    default: false
tags: [perception, inspect, ref]
---

# inspect

按前序 observe/query 返回的 RefId 查看具体对象详情。这个技能只负责把 opaque RefId 委托给 `query_inspect_refs` 解析，不会自行解析、生成或猜测 RefId。

适用场景：

- observe 返回某组代表 refs 后，查看其中一个或多个对象的具体 typed fact。
- 需要确认某棵树是否有苔藓、某株作物是否成熟、某台机器的产物或剩余时间。

参数：

- refs：RefId 字符串数组。
- ref_id：单个 RefId，`refs` 为空时使用。
- include_debug：是否包含 debug 信息，默认 false。
