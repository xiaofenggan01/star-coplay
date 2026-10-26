from collections.abc import Mapping

from modules.mcp.place_catalog import PlaceResolutionError, resolve_place
from modules.mcp.skills.runtime_models import SkillFailure


async def run(ctx, arguments):
    try:
        destination = resolve_place(str(arguments["place"]))
    except PlaceResolutionError as error:
        raise SkillFailure(error.code) from None
    if destination.uses_safe_point:
        arrival = "safe_point"
        go_to_arguments = {
            "map_id": destination.map_id,
            "x": -1,
            "y": -1,
            "arrival_mode": "exact",
        }
    else:
        arrival = "counter"
        go_to_arguments = {
            "map_id": destination.map_id,
            "x": destination.x,
            "y": destination.y,
            "arrival_mode": "adjacent",
            "stand_side": destination.stand_side,
        }
    result = await ctx.invoke("go_to", ">=2.4.5,<3.0.0", go_to_arguments)
    output = result.output if isinstance(result.output, Mapping) else {}
    return {
        "status": "completed",
        "place_id": destination.place_id,
        "map_id": destination.map_id,
        "arrival": arrival,
        "observation": str(output.get("observation", "")),
    }
