from modules.mcp.advanced_skills import direct_capability


async def run(ctx, arguments):
    del arguments
    return await direct_capability(ctx, "go_to_bed", {})
