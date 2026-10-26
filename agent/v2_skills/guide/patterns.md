# L3 编排模式

> 从 builtin/ 实际代码和实测经验中提炼的 4 种标准模式。

## 模式 1：查询-批量执行（最常用）

**适用场景**：对同类目标执行相同操作（浇水、收割、砍树、碎石）

```python
async def water_crops(ctx, stamina_below=20, time_after=1400):
    """本 Skill 示范：查询-批量执行模式"""

    # 查询
    targets = await ctx.find_targets("crop", filter={"unwatered": True})
    if not targets:
        return early_success("没有需要浇水的作物")

    refs = [t["target_ref"] for t in targets]

    # 批量执行（一条 IPC 命令，Mod 侧状态机驱动）
    result = await ctx.execute_on_targets(
        targets=refs,
        action="use_tool",
        tool="喷壶",
        stamina_below=stamina_below,
        time_after=time_after,
    )

    return build_result(result)
```

**核心要点**：
- `find_targets` 返回的列表已按距离排序（`sort="nearest"` 默认）
- `execute_on_targets` 是一次 IPC 调用，Mod 侧自动导航+执行+跳过
- stop_conditions 在 Mod 侧检查，不需要 Python 侧轮询
- 不需要 `check_cancelled()`——execute_on_targets 内部有超时/停止机制

---

## 模式 2：串行循环（每个目标需要个性化处理）

**适用场景**：每个目标之间需要不同的操作、聊天、或条件判断

```python
async def pet_animals(ctx, max_count=0):
    """本 Skill 示范：串行循环模式"""

    animals = await ctx.find_targets("animal", filter={"ungreeted": True})
    if not animals:
        return early_success("所有动物都已抚摸过了")

    petted, skipped = 0, 0
    for animal in animals:
        await ctx.check_cancelled()  # ← 每轮必须检查

        name = animal.get("display_name", "?")
        ref = animal["target_ref"]

        try:
            await ctx.go_to(ref, arrive_distance=1)
            await ctx.do(intent="pet", target=ref)
            petted += 1
        except BridgeError as e:
            if e.code in ("TARGET_UNREACHABLE", "OUT_OF_RANGE"):
                skipped += 1
                continue
            raise  # 系统错误上抛

        if max_count > 0 and petted >= max_count:
            break

    return {...}
```

**核心要点**：
- `check_cancelled()` 在每轮循环**开头**调用，让 Agent 可以随时中断
- 预期错误（TARGET_UNREACHABLE、OUT_OF_RANGE）catch 后 skip 继续
- 非预期错误（EXECUTION_FAILED 等）上抛给 SkillRunner
- go_to 用 L2 语义导航（自动处理跨地图、追踪移动目标）

---

## 模式 3：多步编排（串联多个不同操作）

**适用场景**：一个任务包含多个阶段（收割 → 出货/存箱）

```python
async def harvest_and_sell(ctx, keep_items=None):
    """本 Skill 示范：多步编排模式"""

    # 阶段 1：收割
    crops = await ctx.find_targets("crop", filter={"harvestable": True})
    if not crops:
        return early_success("没有可收割的作物")

    refs = [c["target_ref"] for c in crops]
    harvest_result = await ctx.execute_on_targets(
        targets=refs, action="do", intent="harvest"
    )

    await ctx.check_cancelled()  # ← 阶段之间检查

    # 阶段 2：查背包，决定出货还是保留
    inventory = await ctx.query_inventory()
    to_ship = []
    for item in inventory:
        if item["category"] in ("农作物", "蔬菜", "水果"):
            if keep_items and item["name"] in keep_items:
                continue
            to_ship.append({"action": "ship", "item": item["name"], "qty": item["count"]})

    if not to_ship:
        return {"success": True, "status": "ok", "observation": "收割完成，无需出货"}

    # 阶段 3：出货
    ship_result = await ctx.menu_interact(
        menu_type="Ship", operations=to_ship
    )

    return {...}
```

**核心要点**：
- 多个主要步骤之间调用 `check_cancelled()`
- 用 `query_inventory()` 获取中间状态辅助决策
- `menu_interact` 统一接口，不需要知道底层是 API 还是 UI
- 参数 `keep_items` 让 L4 决策层可以控制保留策略

---

## 模式 4：导航-菜单交互（商店/容器）

**适用场景**：需要走到某个地点并执行菜单操作

```python
async def go_shopping(ctx, shop="SeedShop", items=None):
    """本 Skill 示范：导航-菜单交互模式"""

    if not items:
        return early_success("没有购物清单")

    # menu_interact 的 Shop 模式内部会自动导航到商店
    operations = [{"action": "buy", "item": i["name"], "qty": i["qty"]} for i in items]

    result = await ctx.menu_interact(
        menu_type="Shop",
        operations=operations,
        shop=shop,
    )

    return {...}
```

**核心要点**：
- Shop 场景 `menu_interact` 内部处理导航（通过 BuyItemsHandler）
- Container 场景如果用 container_id，不需要导航（跨地图 API 直调）
- Container 场景如果用 tile 坐标，需要先 `go_to` 靠近

---

## 模式选择决策树

```
任务需要对 N 个同类目标执行相同操作？
├── 是 → 模式 1（查询-批量执行）
│         execute_on_targets 一条命令搞定
└── 否
    ├── 每个目标需要个性化处理？
    │   └── 是 → 模式 2（串行循环）
    │             go_to + do 逐个处理
    ├── 任务包含多个不同阶段？
    │   └── 是 → 模式 3（多步编排）
    │             阶段间 check_cancelled
    └── 需要商店/容器/合成操作？
        └── 是 → 模式 4（导航-菜单交互）
                  menu_interact 统一接口
```
