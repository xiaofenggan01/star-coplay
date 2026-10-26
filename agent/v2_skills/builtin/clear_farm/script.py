"""clear_farm — 分阶段清理农场杂物（杂草、树枝、石头、枯萎作物）。

本 Skill 示范：多步编排模式（Pattern 3）— 同一任务需要多种工具分阶段执行。

不同类型的杂物需要不同工具：
    - 杂草（weeds）→ Scythe（镰刀）
    - 树枝（twigs）→ Axe（斧头）
    - 石头（stones）→ Pickaxe（镐）
    - 枯萎作物（dead_crops）→ Scythe（镰刀）

注意：使用英文 Name（不随升级变化），不用中文 DisplayName（"镐"/"铜十字镐" 随升级变化）。
MatchItem 支持按 DisplayName/Name/ItemId 匹配，英文 Name 是稳定标识。

查询方式：
    - Litter: find_targets("object", object_type="Litter") 统一查询所有 Litter 对象
    - 枯萎作物: find_targets("crop") 查询所有作物，Agent 侧按 is_dead 过滤

阶段之间调用 check_cancelled()，允许 L4 决策层随时中断。
每个阶段内的生命周期（跳过不可达目标、体力检查、时间检查）由 Mod 侧 execute_on_targets 管理。

Edge Case 分工说明：
    - 某阶段无目标     → L3 检测（过滤后为空），跳过该阶段，继续下一阶段
    - 单个目标不可达   → Mod (L2) 自动 skip，计入 skipped，L3 读取汇总数据
    - 体力不足停止     → stop_conditions.stamina_below 传给 execute_on_targets
    - 时间过晚停止     → stop_conditions.time_after 传给 execute_on_targets
    - 缺少工具         → Mod 返回错误，该阶段 cleared=0，L3 继续下一阶段
"""

from v2_skills.context import SkillContext

_VALID_TARGETS = ("all", "weeds", "twigs", "stones", "dead_crops")


async def clear_farm(
    ctx: SkillContext,
    targets: str = "all",
    stamina_below: int = 30,
    time_after: int = 1800,
) -> dict:
    """分阶段清理农场杂物（杂草 / 树枝 / 石头 / 枯萎作物）。

    查询：
        - Litter: find_targets("object", object_type="Litter") 获取杂草/树枝/石头
        - 枯萎: find_targets("crop") 获取所有作物，按 is_dead 过滤
    分类：按 name 包含 "Weeds"/"Twig"/"Stone" 区分 Litter 类型
    执行：分 4 阶段用不同工具 execute_on_targets

    Args:
        ctx: SkillContext 实例。
        targets: 清理范围，"all" | "weeds" | "twigs" | "stones" | "dead_crops"。
        stamina_below: 体力低于此值时停止，0 表示不检查。
        time_after: 游戏时间超过此值时停止（如 1800 = 18:00），0 表示不检查。
    """
    if targets not in _VALID_TARGETS:
        return {
            "success": False,
            "status": "failed",
            "observation": f"参数 targets 非法：'{targets}'，有效值为 {_VALID_TARGETS}",
            "data": _empty_data("invalid_params"),
        }

    weeds, twigs, stones, dead_crops = [], [], [], []

    # 查询 Litter（杂草/树枝/石头）
    if targets in ("all", "weeds", "twigs", "stones"):
        all_litter = await ctx.find_targets(
            "object", filter={"object_type": "Litter"}, sort="nearest"
        )
        for t in all_litter:
            name = (t.get("name") or "").lower()
            if "weed" in name:
                weeds.append(t)
            elif "twig" in name:
                twigs.append(t)
            elif "stone" in name or t.get("display_name", "").lower().find("stone") >= 0:
                stones.append(t)

    # 查询枯萎作物
    if targets in ("all", "dead_crops"):
        all_crops = await ctx.find_targets("crop", sort="nearest")
        dead_crops = [t for t in all_crops if t.get("is_dead")]

    weeds_cleared = twigs_cleared = stones_cleared = dead_cleared = total_skipped = 0
    stopped_reason = "all_done"

    # ── 阶段 1：杂草 → Scythe ──
    if targets in ("all", "weeds") and weeds:
        refs = [t["target_ref"] for t in weeds]
        result = await ctx.execute_on_targets(
            targets=refs, action="use_tool", tool_category="scythe",
            stamina_below=stamina_below, time_after=time_after,
        )
        d = result.get("data", {})
        weeds_cleared = d.get("completed", 0)
        total_skipped += d.get("skipped", 0)
        stopped_reason = d.get("stopped_reason", "all_done")
        if stopped_reason in ("stamina_low", "time_after", "cancelled"):
            return _build_result(weeds_cleared, twigs_cleared, stones_cleared, dead_cleared, total_skipped, stopped_reason)

    if targets == "all":
        await ctx.check_cancelled()

    # ── 阶段 2：树枝 → Axe ──
    if targets in ("all", "twigs") and twigs:
        refs = [t["target_ref"] for t in twigs]
        result = await ctx.execute_on_targets(
            targets=refs, action="use_tool", tool_category="axe",
            stamina_below=stamina_below, time_after=time_after,
        )
        d = result.get("data", {})
        twigs_cleared = d.get("completed", 0)
        total_skipped += d.get("skipped", 0)
        stopped_reason = d.get("stopped_reason", "all_done")
        if stopped_reason in ("stamina_low", "time_after", "cancelled"):
            return _build_result(weeds_cleared, twigs_cleared, stones_cleared, dead_cleared, total_skipped, stopped_reason)

    if targets == "all":
        await ctx.check_cancelled()

    # ── 阶段 3：石头 → Pickaxe ──
    if targets in ("all", "stones") and stones:
        refs = [t["target_ref"] for t in stones]
        result = await ctx.execute_on_targets(
            targets=refs, action="use_tool", tool_category="pickaxe",
            stamina_below=stamina_below, time_after=time_after,
        )
        d = result.get("data", {})
        stones_cleared = d.get("completed", 0)
        total_skipped += d.get("skipped", 0)
        stopped_reason = d.get("stopped_reason", "all_done")
        if stopped_reason in ("stamina_low", "time_after", "cancelled"):
            return _build_result(weeds_cleared, twigs_cleared, stones_cleared, dead_cleared, total_skipped, stopped_reason)

    if targets == "all":
        await ctx.check_cancelled()

    # ── 阶段 4：枯萎作物 → Scythe ──
    if targets in ("all", "dead_crops") and dead_crops:
        refs = [t["target_ref"] for t in dead_crops]
        result = await ctx.execute_on_targets(
            targets=refs, action="use_tool", tool_category="scythe",
            stamina_below=stamina_below, time_after=time_after,
        )
        d = result.get("data", {})
        dead_cleared = d.get("completed", 0)
        total_skipped += d.get("skipped", 0)
        stopped_reason = d.get("stopped_reason", "all_done")

    return _build_result(weeds_cleared, twigs_cleared, stones_cleared, dead_cleared, total_skipped, stopped_reason)


def _empty_data(reason: str) -> dict:
    return {
        "weeds_cleared": 0, "twigs_cleared": 0, "stones_cleared": 0,
        "dead_crops_cleared": 0,
        "total_cleared": 0, "total_skipped": 0, "stopped_reason": reason,
    }


def _build_result(weeds: int, twigs: int, stones: int, dead: int, skipped: int, reason: str) -> dict:
    total = weeds + twigs + stones + dead
    parts = []
    if weeds > 0:
        parts.append(f"杂草 {weeds}")
    if twigs > 0:
        parts.append(f"树枝 {twigs}")
    if stones > 0:
        parts.append(f"石头 {stones}")
    if dead > 0:
        parts.append(f"枯萎作物 {dead}")

    if total == 0 and skipped == 0:
        obs = "农场没有需要清理的杂物"
        status = "ok"
    elif total > 0:
        obs = f"清理了 {' / '.join(parts)}"
        if skipped > 0:
            obs += f"，跳过 {skipped} 个不可达"
        status = "ok" if skipped == 0 else "partial"
    else:
        obs = f"清理失败，{skipped} 个目标全部跳过"
        status = "failed"

    if reason not in ("all_done", "unknown"):
        label = {"stamina_low": "体力不足", "time_after": "时间过晚", "cancelled": "已取消"}.get(reason, reason)
        obs += f"（{label}）"

    return {
        "success": total > 0 or (total == 0 and skipped == 0),
        "status": status,
        "observation": obs,
        "data": {
            "weeds_cleared": weeds, "twigs_cleared": twigs, "stones_cleared": stones,
            "dead_crops_cleared": dead,
            "total_cleared": total, "total_skipped": skipped, "stopped_reason": reason,
        },
    }
