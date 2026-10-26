from modules.mcp.advanced_skills import find_interaction_candidates


async def run(ctx, arguments):
    return await find_interaction_candidates(ctx, arguments)
