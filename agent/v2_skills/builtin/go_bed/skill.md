---
name: go_bed
description: "回房间床上睡觉结束这一天。当玩家说“该睡觉了”、“回家休息”、“今天到此为止”，或你判断时间已晚（>22:00）/体力极低（<30）/任务都完成了时使用。禁止传送，必须通过现有寻路与交互能力回到床边并进入睡觉。"
category: daily
version: "1.1.0"
energy_estimate: "low"
time_estimate: "30-120s"
timeout: 180
routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: home_bed
  ambiguity_policy: execute_when_clear
parameters:
tags: [daily, sleep, end_of_day]
---

回房间床上睡觉结束这一天。流程必须是：扫描可睡床 -> 使用现有 `go_to`/寻路能力走到床的睡眠触发格 -> 与床交互打开睡觉确认 -> 确认睡觉并推进日终流程。

约束：
- 禁止使用 `tp`、legacy `go_bed`、直接改坐标或任何传送式兜底。
- 不要让 LLM 手动猜床坐标；床扫描、寻路、交互与确认由 Mod 的 protocol `go_to_bed` 组合能力负责。
- 如果日终升级/职业选择菜单阻塞，返回 partial，让后续菜单工具处理。

参数：无
前提：当前位置能通过正常地图路线回到房间床上。
示例："睡觉吧" -> skill:go_bed()
     "今天到此为止" -> skill:go_bed()
     "该休息了" -> skill:go_bed()
