async def run(ctx, arguments):
    world = await ctx.invoke("query_world_region", ">=2.4.5,<3.0.0", {})
    inventory = await ctx.invoke("query_inventory_snapshot", ">=2.4.5,<3.0.0", {})
    await ctx.invoke("equip", ">=2.4.5,<3.0.0", {"item_name": "Watering Can"})
    for target in arguments["targets"]:
        await ctx.invoke("go_to", ">=2.4.5,<3.0.0", {
            "map_id": target["map_id"], "x": target["x"], "y": target["y"],
            "arrival_mode": "adjacent", "stand_side": target["stand_side"],
        })
        await ctx.invoke("use_tool", ">=2.4.5,<3.0.0", {
            "x": target["x"], "y": target["y"],
        })
    return {
        "status": "completed",
        "targets_completed": len(arguments["targets"]),
        "world_observation": str(world.output.get("observation", "")),
        "inventory_observation": str(inventory.output.get("observation", "")),
    }
