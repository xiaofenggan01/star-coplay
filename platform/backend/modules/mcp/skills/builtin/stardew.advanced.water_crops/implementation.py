from modules.mcp.advanced_skills import direct_capability


async def run(ctx, arguments):
    return await direct_capability(ctx, "water_crops", arguments)
