# StarCoPlay 确定性评估集

![eval](https://github.com/xiaofenggan01/star-coplay/actions/workflows/eval.yml/badge.svg)

离线、可重复、不依赖真实游戏和真实模型的评估基线。它回答一个问题：
**给定同一个存档状态和同一个目标，系统的行为是否仍然符合“正常玩家”的预期？**

评估集不是单元测试的替代品：单元测试验证函数，评估集验证*玩法语义*——砍完树有没有捡、体力不够会不会硬砍、深夜会不会继续干活。

## 运行

```bash
# 全部场景（推荐用 agent 的 venv，需要 Python 3.10+）
agent/.venv/bin/python eval/run_eval.py

# 只看某一类
agent/.venv/bin/python eval/run_eval.py --tag chop
agent/.venv/bin/python eval/run_eval.py --id chop-001 --verbose

# 生成机器可读报告（给 CI / 后续对比用）
agent/.venv/bin/python eval/run_eval.py --json eval/report.json

# 列出场景
agent/.venv/bin/python eval/run_eval.py --list

# 通过 make
make eval
```

退出码：全部 `passed` 或 `gap` 时返回 0；出现 `failed` / `error` 返回 1。
CI 已接入（[.github/workflows/eval.yml](../.github/workflows/eval.yml)）：push 到 main
和 PR 都会跑评估集，回归会让 job 失败，报告作为 artifact 上传。

## 结构

```
eval/
├── run_eval.py              # CLI
├── harness/
│   ├── bootstrap.py         # 让 eval 复用 agent/ 下的生产 skill 代码
│   ├── sim_world.py         # 确定性星露谷仿真（树/掉落/体力/时间/地块）
│   ├── sim_bridge.py        # 把 SimWorld 接到生产 SkillContext 上
│   ├── runner.py            # 跑一个场景：建世界 → 调真实 skill → 校验
│   ├── assertions.py        # expect 断言求值
│   └── report.py            # 汇总与渲染
├── scenarios/*.json         # 场景库
└── tests/                   # 评估集自身的测试
```

关键设计：**评估的是真实 skill 代码，不是重写的替身**。`runner` 构造生产用的
`SkillContext`，只把 bridge 换成 `SimBridge`，因此 `chop_trees` 的每一次查询和挥砍
都走真实实现。

## 两种运行模式

| 模式 | 场景字段 | 跑什么 | 评估什么 |
|---|---|---|---|
| `skill`（默认） | `skill` + `params` | 直接调用一个 L3 skill | 单个动作的语义与守卫 |
| `goal` | `mode: "goal"` + `goal` + `policy` | `GoalRouter` 把目标拆成 skill 调用 | 目标分解、前置检查、后置校验 |

`goal` 模式接的是 [`agent/slow_loop/goal_router.py`](../agent/slow_loop/goal_router.py)
——一个确定性的保底规划器，属于生产代码而非测试替身。它负责“正常玩家会不会做这件事”
（目标已满足？太晚了？体力够吗？下雨吗），skill 负责“怎么做”。
后续 LLM / HTN 规划器只要实现同一条 `Policy` 接口，就能与它做 A/B 对比。

## 场景 schema

```json
{
  "id": "chop-001-basic",
  "title": "人类可读的意图",
  "status": "runnable",            // runnable | planned
  "mode": "skill",                 // skill | goal
  "tags": ["chop", "drops"],
  "skill": "chop_trees",           // mode=skill 时必填
  "params": {"target_wood": 5},
  // mode=goal 时改为：
  // "goal": {"type": "acquire", "item": "wood", "qty": 10},
  // "policy": "routing",
  "world": {
    "player": {"x": 10, "y": 10, "stamina": 270, "axe_level": 0},
    "inventory": [{"item": "Axe", "qty": 1, "slot": 1}],
    "trees": [{"x": 12, "y": 10, "growth_stage": 5, "stump": false,
               "drops": [{"item": "Wood", "qty": 5}, {"item": "Sap", "qty": 1}]}],
    "loose_items": [],
    "blocked": [[11, 11]],
    "start_minutes": 900
  },
  "expect": { "...": "见下" }
}
```

- `status: planned` 的场景不执行，只作为语义规格与 backlog 列出。
- 树的生命周期由仿真决定：按斧头等级（10/8/6/4/2 刀）扣血 → 倒伏成树桩 → 生成掉落 → 走位捡取。
- `blocked` 用来构造不可达、无站位等地形。

## 断言

`expect` 支持以下块：

| 键 | 含义 |
|---|---|
| `success` / `status` | skill 返回的 `success` / `status` |
| `data.<field>` | skill 返回的 `data` 字段 |
| `world.<metric>` | 世界终态，如 `stumps`、`loose_items_left`、`inventory.Wood`、`mature_trees_left` |
| `budget.<metric>` | `commands_total`、`use_tool_calls`、`energy_spent`、`game_minutes` 等 |
| `commands.must_include` / `must_exclude` | 必须/禁止出现的 capability |
| `commands.max_total` / `max_per` | 命令数上限，防退化成狂暴循环 |
| `warnings_required` / `warnings_max` | skill 的 warnings |
| `known_gap: true` | 这是“当前实现还不满足”的期望；失败时记为 gap 而非 failed |

比较器：标量（相等）、`{"min": n}`、`{"max": n}`、`{"equals": v}`、`{"contains": [...]}`、`{"not_contains": [...]}`。

## 状态语义

| 状态 | 含义 |
|---|---|
| `passed` | 全部断言通过 |
| `gap` | 断言未过，但场景标了 `known_gap`——这是已知缺陷/待实现语义 |
| `failed` | 断言未过，且不是已知 gap——**回归** |
| `error` | skill 抛异常或超时 |
| `skipped` | `planned` 场景 |

## 当前基线（2026-10-26）

```
total=31  runnable=24  passed=20  gaps=4  failed=0  skipped=7
```

- 14 个 `skill` 场景：10 通过、4 个 gap；
- 10 个 `goal` 场景：全部通过，覆盖 Acquire / Water / Sleep 的目标分解与前置检查；
- 7 个 `planned` 场景：语义规格。

四个已知 gap，都是真实缺陷，不是测试写错：

| 场景 | 问题 |
|---|---|
| [chop-010-low-stamina](scenarios/chop-010-low-stamina.json) | skill 没有体力前置判断，12 点体力照样砍到 0 |
| [chop-011-wood-already-enough](scenarios/chop-011-wood-already-enough.json) | 背包已有 50 木头仍然去砍 |
| [chop-013-late-night](scenarios/chop-013-late-night.json) | 凌晨 1 点没有时间约束 |
| [chop-014-estimate-overshoot](scenarios/chop-014-estimate-overshoot.json) | `ESTIMATED_WOOD_PER_TREE=10` 导致实际 5 木/棵时提前停 |

`chop-002` 还暴露了效率问题：每挥一刀都查一次背包 + 世界，20 刀产生近百条命令。
断言里用 `budget.commands_total` 兜底，但后续应改成“事件驱动 + 批量查询”。

## 新增场景

1. 复制一个现有 JSON，改 `id`；
2. 构造 `world`，先手动推演一遍期望结果；
3. 写 `expect`，优先断言**世界终态**（`world.*`），再断言数据字段；
4. 如果这条期望当前实现做不到，先标 `known_gap: true`，等实现后再去掉。

```bash
agent/.venv/bin/python eval/run_eval.py --id <new-id> --verbose
```

## 局限

- `chop_trees` / `water_crops` / `go_bed` 可在仿真里真实运行；`harvest_crops` /
  `ship_items` 仍委托 mod compound handler，仿真尚未覆盖。
- `goal` 模式用的是确定性 `GoalRouter`，不是 LLM 规划；它定义“正确行为”的基线，
  而不是验证模型能力。
- 没有模拟 LLM 决策：评估的是 L3 skill 的语义，不是“玩家一句话 → 计划”。
- 仿真简化：单地图、无天气/季节/作物生长、掉落散射简化为固定环。
- 时间粒度 10 分钟/动作，用于预算断言，不是精确游戏时钟。

## 下一步

1. **LLM 规划器对比**：把真实 agent 作为 `policy` 接入，和 `GoalRouter` 跑同一批目标，
   比较成功率、动作数、澄清次数。
2. **trace 回放**：把真实运行记录成 trace，离线重放，替代手工构造期望。
3. **覆盖扩展**：`harvest_crops` / `ship_items`、背包满、季节与体力恢复。
