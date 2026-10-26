from modules.mcp.wiki import read_wiki


async def run(ctx, arguments):
    del ctx
    return read_wiki(
        arguments["page"],
        arguments.get("section"),
        arguments.get("max_chars", 6000),
    )
