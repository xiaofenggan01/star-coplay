from modules.mcp.wiki import search_wiki


async def run(ctx, arguments):
    del ctx
    return search_wiki(arguments["query"], arguments.get("limit", 8))
