from modules.mcp.advanced_skills import direct_capability


async def run(ctx, arguments):
    payload = {
        key: value for key, value in arguments.items()
        if key in {"items", "exclude", "min_quality"}
    }
    payload["mode"] = "plan" if arguments.get("dry_run") else "execute"
    return await direct_capability(ctx, "ship_items", payload)
