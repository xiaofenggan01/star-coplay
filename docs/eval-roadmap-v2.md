# 技术方案：评估体系 v2（判别力、归因、agent 策略）

> 目标：让评估集能区分「规则」「单次规划」「多轮 agent」，并把 `chop_trees`
> 的已知语义缺陷修掉，使基线全绿。

## 0. 元信息

- 状态：执行中
- 范围：M1 / M2 / M3（轻量）/ M4 / M5
- 非目标：完整 `DeliberativeLoop` 接入、CI 内真实模型调用、mod 侧 C# handler 修复

## 1. 背景

v1 的 10 个 goal 场景把「正常玩家规则」（午夜、体力、下雨、睡觉时间）直接写进了
planner prompt，router 也实现了同一套规则，所以 routing 与 llm 必然打平。v1 只证明了
链路可用，不能衡量规划能力。

同时 `chop_trees` 有 4 个语义缺陷（无体力/时间前置、不检查目标已达成、按 10 木/棵估算
提前停），被评估集以 `known_gap` 标出。

## 2. 里程碑

### M1 判别性场景

**目标模型**

| 类型 | 形状 | 语义 |
|---|---|---|
| `acquire` | `{"type":"acquire","item":"wood","qty":10}` | 采集到指定数量 |
| `water_crops` | `{"type":"water_crops"}` | 浇完全部未浇水作物 |
| `sleep` | `{"type":"sleep"}` | 结束当天 |
| `composite` | `{"type":"composite","steps":[<goal>, ...]}` | 顺序完成多个子目标 |

**澄清**：`acquire` 缺 `qty`（或 ≤0）视为模糊目标，策略应返回
`needs_clarification`，而不是猜一个数字。

**优先级**：目标已满足优先于环境约束（体力低、下雨），因为玩家已经不需要做任何事。

**postcheck**：`composite` 逐个子目标校验；任一失败即 partial，并给出失败子目标。

**新增场景（12）**

- 复合：浇水后睡觉、砍 10 木后睡觉、睡醒再浇水
- 澄清：缺数量、数量为 0
- 冲突：已满足但体力低、已浇完但下雨、深夜但目标已完成
- 恢复：复合作业中第一步 partial 仍继续后续
- 最优：目标 5 木时只砍 1 棵；目标 10 木时砍 2 棵（成本意识）

### M2 统计与归因

- 按策略聚合：运行次数、通过率、tokens 均值/标准差、延迟均值/标准差、可选成本
- 失败归因：`invalid_plan` / `llm_misjudged` / `skill_failed` / `wrong_choice` / `blocked`
- 报告 schema 加 `schema_version`

### M3 轻量 tool-calling agent

单步多轮循环：模型每次只做一个决策（一个 skill / satisfied / refuse / clarify），
执行后把结果与最新世界喂回，最多 4 轮。相较单次规划，能体现“看到 partial 结果后
继续补一刀”的能力。

### M4 修复 `chop_trees`

1. 体力 < 20 → `blocked: stamina_low`（执行中体力不足也停）
2. 时间 ≥ 2400 → `blocked: late_night`
3. 背包木材已达标 → `ok: already_satisfied`，零挥砍
4. 去掉 `ESTIMATED_WOOD_PER_TREE` 的提前停止，改为实测木材数；顺带把“每挥一刀查背包”
   改为“每棵倒伏后查一次”，减少命令数

对应 `chop-010/011/013/014` 从 `known_gap` 转为 `passed`。

### M5 工程化

- CI：离线跑全量 routing + llm/agent 的 scripted 回放
- 文档：README（eval）、架构文档同步
- 真实模型：只在本地手动跑，不写入 CI

## 3. 验收标准

- [ ] `make eval` 全绿：0 failed、0 gap
- [ ] routing / llm / agent 三种策略都能在离线回放下通过全量场景
- [ ] 本地真实模型对比产出一张可读的表（通过率、延迟、tokens）
- [ ] CI 在无密钥环境下跑通全量 + 回放
- [ ] README / 架构文档与实现一致

## 4. 风险

- **场景过拟合**：规则写进 prompt 会让 LLM 轻松通过；判别性场景必须依赖组合与反馈，
  而不是枚举规则。
- **仿真保真度**：M4 修复只作用于 Python skill；DSH 走的 C# handler 不受影响，
  文档里要写清楚。
- **真实模型非确定性**：只在本地手动跑，结果记录在案，不进 CI。
