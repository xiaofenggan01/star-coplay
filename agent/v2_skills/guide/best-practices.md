# L3 Skill 最佳实践

> 从 Sprint 11-12 实测中提炼的规则，每条都有对应的踩坑经历。

## 1. 取消检查（check_cancelled）

**规则**：长循环的每轮开头、多步编排的步骤之间，必须调用 `ctx.check_cancelled()`。

**为什么**：Skill 被 LLM 慢环管理，用户可能改变主意、游戏时间不够、有紧急事件。不检查取消 = 无法响应中断。

**不需要检查的场景**：
- `execute_on_targets` 内部有 stop_conditions，不需要额外 cancel check
- 单条 `go_to` 或 `do` 调用（它们有自己的 timeout）

```python
# 正确
for target in targets:
    await ctx.check_cancelled()  # ← 循环开头
    await ctx.go_to(target)
    await ctx.do(intent="harvest", target=target)

# 正确
result1 = await ctx.execute_on_targets(...)  # 不需要 check
await ctx.check_cancelled()                   # 阶段之间
result2 = await ctx.menu_interact(...)
```

---

## 2. 空结果是正常情况

**规则**：`find_targets` 返回空列表时，返回 `status: "ok"` + 友好 observation，不是失败。

**为什么**：农场可能没作物、没动物、今天已经浇过了。这不是 Skill 的错误，是当前游戏状态。

```python
targets = await ctx.find_targets("crop", filter={"unwatered": True})
if not targets:
    return {
        "success": True,
        "status": "ok",
        "observation": "所有作物已经浇过水了",  # 友好提示，不是报错
        "data": {"watered": 0, "total": 0}
    }
```

---

## 3. 预期错误 vs 非预期错误

**规则**：预期错误 catch + skip，非预期错误上抛。

| 错误码 | 类型 | 处理方式 |
|--------|------|---------|
| TARGET_UNREACHABLE | 预期 | skip，继续下一个 |
| OUT_OF_RANGE | 预期 | skip |
| TARGET_NOT_FOUND | 预期 | skip |
| EXECUTION_FAILED | 非预期 | raise，让 SkillRunner 处理 |
| TIMEOUT | 非预期 | raise |

```python
try:
    await ctx.go_to(ref)
    await ctx.do(intent="pet", target=ref)
except BridgeError as e:
    if e.code in ("TARGET_UNREACHABLE", "OUT_OF_RANGE", "TARGET_NOT_FOUND"):
        skipped += 1
        continue  # 预期内，跳过
    raise  # 系统错误，上抛
```

---

## 4. say() 只用于角色扮演

**规则**：`ctx.say()` 是游戏内聊天消息，只用于展示 Agent 个性。不用于 debug 输出。

**正确用法**：
```python
await ctx.say(f"今天的庄稼都浇好了~")     # 角色扮演
await ctx.say(f"去看看小鸡们吧！")         # 角色扮演
```

**debug 信息怎么带出来**：通过返回值的 `data` 字段。SkillRunner 会格式化后传给 LLM。

---

## 5. 参数可覆盖

**规则**：关键策略参数（stamina 阈值、时间限制、数量上限）通过 skill.md parameters 声明，让 L4 决策层可以根据情况调整。

```yaml
# skill.md
parameters:
  stamina_below:
    type: integer
    description: "体力低于此值时停止"
    default: 20
  time_after:
    type: integer
    description: "游戏时间超过此值时停止（如 1400 = 14:00）"
    default: 1400
```

```python
# script.py
async def water_crops(ctx, stamina_below=20, time_after=1400):
    ...
```

LLM 调用时可以覆盖：`skill:water_crops {"stamina_below": 10, "time_after": 1800}`

---

## 6. target_ref 是一等公民

**规则**：在 L3 层传递目标时，统一使用 `target_ref` 字符串，不要自己拼坐标。

```python
# 正确
ref = animal["target_ref"]  # "Coop82b38ef3:animal_甜豆"
await ctx.go_to(ref)
await ctx.do(intent="pet", target=ref)

# 错误
x, y = animal["x"], animal["y"]
await ctx.move_to(x, y)  # 动物会移动，坐标是快照
```

**为什么**：target_ref 包含位置+类型+标识，Mod 侧可以实时解析当前位置（动物/NPC 会移动）。直接用坐标是静态快照，到的时候目标可能已经走了。

---

## 7. 追踪时序

**规则**：go_to 追踪模式到达 adjacent 后，**立即执行动作**，不要插入额外等待。

**为什么**：追踪目标（动物/NPC）在持续移动。go_to 返回时角色在目标旁边，但下一帧目标可能就移走了。立即 do 可以最大化成功率。

```python
await ctx.go_to(animal_ref, arrive_distance=1)
# 不要在这里 sleep 或做其他查询
await ctx.do(intent="pet", target=animal_ref)  # 立即执行
```

---

## 8. 批量 vs 串行的选择

| 场景 | 推荐模式 | 原因 |
|------|---------|------|
| 浇水/收割/砍树 | `execute_on_targets` | 一条命令，Mod 侧最短路径 |
| 抚摸动物 + 说话 | 串行 go_to + do | 需要在每只之间插入 say |
| 收割 + 出货 | 多步编排 | 两个不同操作阶段 |
| 只是走到某处 | 单条 go_to | 不需要批量 |

**经验法则**：如果所有目标的操作完全相同且不需要中间决策，用 `execute_on_targets`。否则串行循环。

---

## 9. 不要 Skill 间互相调用

**规则**：Skill 是独立的原子能力单元，不 import 其他 Skill 的函数。

**为什么**：
- Skill 间调用会引入隐式依赖
- 嵌套取消（外层 cancel 时内层 Skill 怎么办）复杂度高
- 测试时无法隔离

**正确做法**：如果 daily_routine 需要"浇水"，直接用 ctx 方法编排 L1/L2 原语，而不是调用 water_crops 函数。

---

## 10. Skill 内部不依赖游戏时间推进

**规则**：不要 `await asyncio.sleep()` 等待游戏时间过去。

**为什么**：Python 的 sleep 和游戏时间不同步。SDV 的时间在暂停时冻结，在过场动画时跳跃。用 `stop_conditions.time_after` 让 Mod 侧在游戏 tick 中判断时间。
