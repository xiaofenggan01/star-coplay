# L3 Developer Guide — Skill 编写最佳实践

> **状态：活跃** | 最后更新：2026-04-21 | 适用版本：v2.3+

**MUST：编写或修改 v2_skills/ 下的 Skill 前，先阅读本文档。**

## 1. Skill 编排三种模式

| 模式 | 适用场景 | 示例 |
|------|---------|------|
| **Pattern 1：查询-批量执行** | 单工具批量操作 | `harvest_crops`、`water_crops`、`pet_animals` |
| **Pattern 2：多目标串行交互** | 每个目标需独立决策 | `go_shopping`（导航→进店→购买） |
| **Pattern 3：多阶段多工具** | 同任务需切换工具 | `clear_farm`（镰刀→斧头→镐→镰刀） |

### Pattern 1 标准流程
```python
targets = await ctx.find_targets("crop", filter={"harvestable": True}, sort="nearest")
if not targets:
    return success("没有目标")
refs = [t["target_ref"] for t in targets]
result = await ctx.execute_on_targets(targets=refs, action="do", intent="harvest")
```

### Pattern 3 标准流程
```python
# 查询 → 分类 → 分阶段执行
all_items = await ctx.find_targets(...)
group_a = [t for t in all_items if is_type_a(t)]
group_b = [t for t in all_items if is_type_b(t)]

for label, items, tool in [("A", group_a, "ToolA"), ("B", group_b, "ToolB")]:
    if items:
        result = await ctx.execute_on_targets(targets=[...], action="use_tool", tool=tool)
        # 检查 stop_conditions
        if result stopped early: return partial result
    await ctx.check_cancelled()  # 允许 L4 中断
```

## 2. 工具名规范

**MUST：用 `tool_category`，不用 `tool` 名称。**

| 正确 | 错误 |
|------|------|
| `tool_category="pickaxe"` | `tool="Pickaxe"` ❌（铜级叫 `Copper Pickaxe`） |
| `tool_category="axe"` | `tool="斧头"` ❌ |
| `tool_category="scythe"` | `tool="镰刀"` ❌ |
| `tool_category="watering_can"` | `tool="喷壶"` ❌ |

**原因**：SDV 1.6 中 `Name` 和 `DisplayName` 都随升级变化（`Pickaxe` → `Copper Pickaxe` → `Steel Pickaxe`）。
`tool_category` 内部用 `is Pickaxe` C# 类型检查，适配所有升级等级。

## 3. find_targets 过滤技巧

### Mod 侧过滤（推荐，减少数据传输）
```python
# 可收割作物
targets = await ctx.find_targets("crop", filter={"harvestable": True})
# 枯萎作物（v2.4.0+ 支持 dead 过滤）
targets = await ctx.find_targets("crop", filter={"dead": True})
# Litter 对象
targets = await ctx.find_targets("object", filter={"object_type": "Litter"})
```

### Agent 侧过滤（Mod 不支持的组合条件）
```python
# 枯萎作物（v2.3.x，Mod 无 dead 过滤参数时）
all_crops = await ctx.find_targets("crop", sort="nearest")
dead_crops = [t for t in all_crops if t.get("is_dead")]
```

### 按 name 分类（Litter 场景）
```python
for t in all_litter:
    name = (t.get("name") or "").lower()
    if "weed" in name: weeds.append(t)
    elif "twig" in name: twigs.append(t)
    elif "stone" in name: stones.append(t)
```

## 4. 返回值契约

所有 Skill 必须返回统一结构：

```python
return {
    "success": bool,         # 是否有效果
    "status": str,           # "ok" | "partial" | "failed"
    "observation": str,      # 中文自然语言描述（给 L4 决策层看）
    "data": {                # 结构化数据
        "completed": int,
        "skipped": int,
        "stopped_reason": str,  # "all_done" | "stamina_low" | "time_after" | "cancelled"
        # ... Skill 特定字段
    },
}
```

### observation 编写原则
- 用中文，面向 L4 决策层可读
- 包含关键数据（"收割了 5/8 棵作物，跳过 3 棵不可达"）
- 异常原因要说明（"体力不足停止"、"时间过晚停止"）

## 5. stop_conditions 使用

```python
result = await ctx.execute_on_targets(
    targets=refs,
    action="use_tool",
    tool="Pickaxe",
    stamina_below=30,   # 体力 < 30 时停止
    time_after=1800,    # 游戏时间 > 18:00 时停止
)
```

**MUST**：检查 `stopped_reason` 并在返回值中体现（用户需要知道为什么没做完）。

## 6. check_cancelled 使用

多阶段 Skill 在阶段之间调用 `check_cancelled()`，允许 L4 决策层中断：

```python
# 阶段 1 完成
await ctx.check_cancelled()  # 如果 L4 发了取消指令，这里会抛异常
# 阶段 2 开始
```

**MUST**：只在阶段之间调用，不在阶段内部调用（阶段内的中断由 Mod 侧 stop_conditions 管理）。
