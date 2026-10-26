"""ship_items — 把背包物品放入运输箱出售。

L3 薄封装：委托 L2 ship_items compound handler 完成出货。
L2 handler（ShipItemsHandler.cs）支持两个 mode：
  - plan:    扫描库存，返回预览（不产生副作用）
  - execute: 实际将物品移入运输箱（明日结算）

L3 层新增 dry_run 参数语义糖，映射到 mode=plan/execute。
L2 原生参数 items / exclude / min_quality 直接透传。

注意：L2 不支持 keep_quantity（按数量保留），如需该功能后续迭代。
"""


async def ship_items(
    ctx,
    items: list = None,
    exclude: list = None,
    min_quality: str = "",
    dry_run: bool = False,
):
    """把背包物品放入运输箱出售。

    Args:
        ctx:         SkillContext 实例。
        items:       要出售的物品列表（显示名或英文名）。空=全部可售。
        exclude:     明确排除不卖的物品列表。
        min_quality: 保留该品质及以上："silver"/"gold"/"iridium"/"" 。
        dry_run:     True=仅预览不执行，False=直接出货。

    Returns:
        Result dict: {success, status, observation, data}
        data 在 execute 模式包含 {shipped, total_value, failed, message}
        data 在 plan 模式包含 {items, total_value, item_count, kept, excluded, message}
    """
    if items is None:
        items = []
    if exclude is None:
        exclude = []

    mode = "plan" if dry_run else "execute"

    params: dict = {"mode": mode}
    if items:
        params["items"] = items
    if exclude:
        params["exclude"] = exclude
    if min_quality and min_quality in ("silver", "gold", "iridium"):
        params["min_quality"] = min_quality

    result = await ctx.send_command(
        action="ship_items",
        params=params,
        timeout=30,
    )

    success = result.get("success", False)
    data = result.get("data", {})
    message = data.get("message", "")

    if not success:
        return {
            "success": False,
            "status": "failed",
            "observation": f"出货失败：{result.get('error', '未知错误')}",
            "data": data,
        }

    if dry_run:
        total_value = data.get("total_value", 0)
        item_count = data.get("item_count", 0)
        return {
            "success": True,
            "status": "ok",
            "observation": f"预览：可出货 {item_count} 件物品，预计收入 {total_value}g",
            "data": data,
        }
    else:
        total_value = data.get("total_value", 0)
        shipped = data.get("shipped", [])
        shipped_count = sum(s.get("count", 0) for s in shipped)
        return {
            "success": True,
            "status": "ok",
            "observation": f"已出货 {shipped_count} 件物品，明日结算 {total_value}g",
            "data": data,
        }
