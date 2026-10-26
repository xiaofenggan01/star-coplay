async def run(ctx, arguments):
    result = await ctx.invoke("query_world_region", ">=2.4.5,<3.0.0", arguments)
    return result.output if isinstance(result.output, dict) else {"result": result.output}

