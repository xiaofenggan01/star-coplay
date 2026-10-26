# L3 Skill 编写指南

> 版本：v2.4.0 | 更新：2026-04-21

## 什么是 L3 Skill？

L3 Skill 是 Agent 侧的 Python async 函数，通过编排 L1（原子操作）和 L2（复合操作）的 IPC 命令，完成一个完整的游戏任务。

```
L4 决策层（LLM 慢环）
  ↓ 调用 skill:water_crops
L3 技能层（Python async）  ← 你在这里写代码
  ↓ ctx.find_targets() / ctx.go_to() / ctx.execute_on_targets()
L2 操作层（Mod C# Compound Handler）
  ↓
L1 原子层（Mod C# 单帧操作）
  ↓
SDV 游戏引擎
```

## 快速开始（5 分钟）

### 1. 创建目录

```
v2/agent/v2_skills/builtin/my_skill/
├── skill.md      ← 元数据（YAML frontmatter）
└── script.py     ← 实现（async def）
```

### 2. 写 skill.md

```yaml
---
name: my_skill
description: "一句话描述这个技能做什么。"
category: farming
version: "1.0.0"
energy_estimate: "low"          # low / medium / high
time_estimate: "30-60s"
parameters:
  max_count:
    type: integer
    description: "最多处理几个目标"
    default: 0
tags: [farming, daily]
---

## 使用说明

详细描述技能的行为、前置条件、预期结果。
```

### 3. 写 script.py

```python
from v2_skills.context import SkillContext
from v2_skills.errors import BridgeError

async def my_skill(ctx: SkillContext, max_count: int = 0) -> dict:
    """技能实现。"""

    # 1. 查询目标
    targets = await ctx.find_targets("crop", filter={"unwatered": True})
    if not targets:
        return {"success": True, "status": "ok", "observation": "没有需要处理的目标"}

    # 2. 提取 target_ref
    refs = [t["target_ref"] for t in targets]
    if max_count > 0:
        refs = refs[:max_count]

    # 3. 执行
    result = await ctx.execute_on_targets(
        targets=refs, action="use_tool", tool="喷壶"
    )

    # 4. 返回
    completed = result.get("data", {}).get("completed", 0)
    return {
        "success": completed > 0,
        "status": "ok" if completed == len(refs) else "partial",
        "observation": f"浇了 {completed}/{len(refs)} 棵作物",
        "data": {"watered": completed, "total": len(refs)}
    }
```

### 4. 自动发现

SkillRegistry 在启动时自动扫描 `builtin/` 下所有目录。函数名必须与目录名一致。LLM 慢环通过 `skill:my_skill` 调用。

---

## SkillContext API 速查表

### 查询（瞬时返回）

| 方法 | 说明 | 返回 |
|------|------|------|
| `find_targets(type, filter={}, sort="nearest", max=50)` | 查找目标 | `list[dict]`（target_ref + 属性） |
| `query_farm_summary()` | 农场摘要 | `dict` |
| `query_time()` | 游戏时间 | `dict`（time, season, day） |
| `query_inventory()` | 背包物品 | `list[dict]` |

### L1 操作（单步）

| 方法 | 说明 | timeout |
|------|------|---------|
| `do(intent, target=None, **kwargs)` | 意图动作（pet/harvest/talk/dismiss/...） | 30s |
| `move_to(x, y, location=None)` | L1 同地图坐标移动 | 60s |
| `say(message)` | 游戏内聊天（角色扮演用） | 5s |
| `emote(type)` | 表情动作 | 5s |

### L2 操作（多步异步）

| 方法 | 说明 | timeout |
|------|------|---------|
| `go_to(target, arrive_distance=1)` | 语义导航（NPC/动物/建筑/地图） | 120s |
| `execute_on_targets(targets, action, ...)` | 批量执行 | 300s |
| `menu_interact(menu_type, operations, ...)` | 统一菜单交互 | 120s |

### 生命周期

| 方法 | 说明 |
|------|------|
| `check_cancelled()` | 检查是否被取消（长循环必须调用） |

---

## 工具名约定

SDV 中文版中，`item.Name` 返回**中文**。`use_tool` 的 tool 参数使用中文名：

| 工具 | tool 参数 |
|------|----------|
| 喷壶 | `"喷壶"` |
| 斧头 | `"斧头"` |
| 锄头 | `"锄头"` |
| 镐 | `"镐"` |
| 挤奶桶 | `"挤奶桶"` |
| 剪刀 | `"剪刀"` |

---

## 返回值规范

所有 Skill **必须**返回统一结构：

```python
{
    "success": bool,              # 是否达成目标
    "status": "ok" | "partial" | "failed",  # 三值状态
    "observation": "人类可读摘要",  # 给 LLM 看的，简洁有信息量
    "data": { ... }               # 给程序消费的结构化数据
}
```

- `observation` 作为 tool result 回传给慢环 LLM，影响后续决策
- `data` 包含具体数值（completed/skipped/total 等）
- `status` 判断：全部完成 → ok，部分完成 → partial，全部失败 → failed

---

## 详细指南

- [编排模式](patterns.md) — 4 种标准编排模式及示例
- [最佳实践](best-practices.md) — 从实测中提炼的规则
