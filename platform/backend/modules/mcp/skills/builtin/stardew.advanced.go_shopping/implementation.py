from modules.mcp.advanced_skills import direct_capability, invoke_capability
from modules.mcp.place_catalog import resolve_place


async def run(ctx, arguments):
    destination = resolve_place(str(arguments["shop"]))
    await invoke_capability(ctx, "go_to", {
        "map_id": destination.map_id,
        "x": destination.x,
        "y": destination.y,
        "arrival_mode": "adjacent",
        "stand_side": destination.stand_side,
    })
    items = [
        {"name": item["item"], "quantity": item["qty"]}
        for item in arguments["items"]
    ]
    return await direct_capability(ctx, "buy_items", {
        "shop": arguments["shop"],
        "items": items,
    })
