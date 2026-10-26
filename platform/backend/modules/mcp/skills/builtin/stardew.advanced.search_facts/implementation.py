from modules.mcp.advanced_skills import search_world_facts


async def run(ctx, arguments):
    return await search_world_facts(ctx, arguments)
