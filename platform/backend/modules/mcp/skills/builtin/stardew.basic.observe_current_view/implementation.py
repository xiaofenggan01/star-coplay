async def run(ctx, arguments):
    observations = {}
    capabilities = {
        "runtime": "query_runtime_snapshot",
        "inventory": "query_inventory_snapshot",
        "ui": "query_ui_snapshot",
        "world": "query_world_region",
    }
    for name, capability in capabilities.items():
        result = await ctx.invoke(capability, ">=2.4.5,<3.0.0", {})
        observations[name] = str(result.output.get("observation", ""))
    # Inspection requires concrete RefIds returned by an earlier query.  The
    # generic current-view Skill has no truthful refs to inspect, so expose an
    # empty observation instead of issuing a guaranteed-invalid `{}` call.
    observations["inspected_refs"] = ""
    return {"status": "completed", "observations": observations}
