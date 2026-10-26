"""go_shopping — 前往指定商店购买物品。

本 Skill 示范：导航-菜单交互模式（Pattern 4）。

menu_interact 的 Shop 模式内部通过 BuyItemsHandler 自动处理导航到商店，
L3 层无需手动 go_to，只需声明"要在哪家店买什么"即可。

Edge Case 分工说明：
    - 购买清单为空           → L3 检测（早返回，不发 IPC 命令）
    - 商店未营业             → Mod (BuyItemsHandler) 检测，返回 SHOP_CLOSED，L3 透传失败
    - 金币不足               → Mod 侧处理，partial 购买后返回 stopped_reason="insufficient_gold"
    - 库存不足（商店没货）   → Mod 侧跳过该条目，计入 skipped
    - 背包已满               → Mod 侧处理，partial 购买后返回 stopped_reason="inventory_full"
    - 部分购买成功           → L3 读取 bought/total，返回 status: partial
"""

from v2_skills.context import SkillContext


async def go_shopping(
    ctx: SkillContext,
    shop: str = "SeedShop",
    items: list[dict] | None = None,
) -> dict:
    """前往指定商店购买物品。

    本 Skill 示范：导航-菜单交互模式（Pattern 4）。
    menu_interact(menu_type="Shop") 内部自动导航到商店，无需手动 go_to。

    流程：
    1. 校验购买清单，为空则早返回
    2. 将 items 转换为 menu_interact 的 operations 格式
    3. menu_interact(menu_type="Shop", shop=shop) — 导航+开菜单+购买，一条 IPC 命令
    4. 解析购买结果，返回标准化 dict

    Args:
        ctx: SkillContext 实例。
        shop: 商店 ID，如 "SeedShop"、"Blacksmith"、"Saloon"。
        items: 购买清单，每项 {"item": str, "qty": int}。
               item 使用 SDV 英文内部名（ShopMenu 通过 ItemId 匹配）。

    Returns:
        Result dict: {success, status, observation, data: {bought, skipped, total, stopped_reason}}
    """
    # 1. 购买清单为空 → 早返回，不发 IPC 命令
    if not items:
        return {
            "success": True,
            "status": "ok",
            "observation": "购买清单为空，无需前往商店",
            "data": {"bought": 0, "skipped": 0, "total": 0, "stopped_reason": "empty_list"},
        }

    total = len(items)

    # 2. 转换为 menu_interact operations 格式
    #    item 使用英文内部名；qty 对应 SDV ShopMenu 的购买数量
    operations = [
        {"action": "buy", "item": entry["item"], "qty": entry["qty"]}
        for entry in items
    ]

    # 3. menu_interact Shop 模式：BuyItemsHandler 内部自动导航+购买
    #    无需手动 go_to，shop 参数告知 Mod 目标商店
    result = await ctx.menu_interact(
        menu_type="Shop",
        operations=operations,
        shop=shop,
    )

    # 4. 解析结果
    data = result.get("data", {})
    bought = data.get("bought", data.get("completed", 0))
    skipped = data.get("skipped", 0)
    stopped_reason = data.get("stopped_reason", "unknown")

    # 商店未营业或导航失败时 menu_interact 返回 success=False
    if not result.get("success", False) and bought == 0:
        return {
            "success": False,
            "status": "failed",
            "observation": result.get("observation") or f"前往 {shop} 失败，可能未营业或无法到达",
            "data": {
                "bought": 0,
                "skipped": skipped,
                "total": total,
                "stopped_reason": stopped_reason,
            },
        }

    # 判断完成程度
    if bought == total:
        status = "ok"
    elif bought > 0:
        status = "partial"
    else:
        status = "failed"

    obs_parts = [f"在 {shop} 购买了 {bought}/{total} 种物品"]
    if skipped > 0:
        obs_parts.append(f"跳过 {skipped} 种（库存不足或无此物品）")
    if stopped_reason not in ("unknown", "all_done"):
        reason_label = {
            "insufficient_gold": "金币不足",
            "inventory_full": "背包已满",
            "shop_closed": "商店未营业",
            "cancelled": "已取消",
        }.get(stopped_reason, stopped_reason)
        obs_parts.append(f"停止原因：{reason_label}")

    return {
        "success": bought > 0,
        "status": status,
        "observation": "，".join(obs_parts),
        "data": {
            "bought": bought,
            "skipped": skipped,
            "total": total,
            "stopped_reason": stopped_reason,
        },
    }
