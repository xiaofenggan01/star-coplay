from modules.mcp.advanced_skills import pet_animals


async def run(ctx, arguments):
    return await pet_animals(ctx, arguments)
