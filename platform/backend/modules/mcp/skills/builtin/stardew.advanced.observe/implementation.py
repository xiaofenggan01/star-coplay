from modules.mcp.advanced_skills import direct_capability


DETAIL_TYPES = {
    "crops": ["crop"],
    "animals": ["farm_animal", "pet"],
    "npcs": ["npc"],
    "containers": ["container"],
    "inventory": ["inventory_item"],
    "objects": ["object", "tree", "rock", "debris"],
    "exits": ["door", "warp"],
}


async def run(ctx, arguments):
    detail = arguments.get("detail")
    if not detail:
        return await direct_capability(ctx, "observe_current_view", {})
    return await direct_capability(ctx, "query_world_region", {
        "world_entity_types": DETAIL_TYPES[detail],
        "include_characters": detail in {"animals", "npcs"},
        "include_tiles": False,
    })
