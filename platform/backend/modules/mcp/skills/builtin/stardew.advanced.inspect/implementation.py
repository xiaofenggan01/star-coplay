from modules.mcp.advanced_skills import direct_capability


async def run(ctx, arguments):
    refs = list(arguments.get("refs") or [])
    if not refs and arguments.get("ref_id"):
        refs = [arguments["ref_id"]]
    if not refs:
        raise ValueError("at least one opaque ref is required")
    return await direct_capability(ctx, "query_inspect_refs", {
        "refs": refs,
        "include_debug": bool(arguments.get("include_debug", False)),
    })
