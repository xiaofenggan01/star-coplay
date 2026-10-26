from modules.mcp.advanced_skills import walk_route


async def run(ctx, arguments):
    return await walk_route(ctx, arguments)
