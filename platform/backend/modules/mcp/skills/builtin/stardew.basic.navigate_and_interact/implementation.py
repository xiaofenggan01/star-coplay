async def run(ctx, arguments):
    region = await ctx.invoke("query_world_region", ">=2.4.5,<3.0.0", {})
    navigation = await ctx.invoke("go_to", ">=2.4.5,<3.0.0", arguments["destination"])
    interaction = await ctx.invoke("interact", ">=2.4.5,<3.0.0", arguments["interaction"])
    return {
        "status": "completed",
        "region_observation": str(region.output.get("observation", "")),
        "navigation_observation": str(navigation.output.get("observation", "")),
        "interaction_observation": str(interaction.output.get("observation", "")),
    }
