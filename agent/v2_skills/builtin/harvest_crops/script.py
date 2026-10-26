"""harvest_crops — protocol-native L3 crop harvesting."""

from __future__ import annotations

from typing import Any

from v2_skills.builtin._daily_protocol import (
    DEFAULT_DAILY_RADIUS,
    clamp_non_negative,
    entity_payload,
    entities_of,
    fact,
    player_position,
    query_current_region,
    result,
    runtime_time,
)


async def harvest_crops(
    ctx: Any,
    stamina_below: int = 0,
    time_after: int = 0,
    radius: int = DEFAULT_DAILY_RADIUS,
    max_count: int = 0,
    replant_seed: str | None = None,
) -> dict:
    stamina_below = clamp_non_negative(stamina_below)
    time_after = clamp_non_negative(time_after)
    max_count = clamp_non_negative(max_count)
    radius = max(1, min(DEFAULT_DAILY_RADIUS, clamp_non_negative(radius) or DEFAULT_DAILY_RADIUS))
    _ = stamina_below, time_after

    runtime = fact(
        await ctx.query_runtime_snapshot(
            include_player=True,
            include_world_time=True,
            include_weather=False,
            include_menu_state=True,
        )
    )
    player_pos = player_position(runtime)
    if player_pos is None:
        return result(
            success=False,
            status="failed",
            observation="harvest_crops failed: player position is unavailable.",
            data=_data(0, 0, 0, "missing_player_position"),
            error_code="MISSING_PLAYER_POSITION",
        )

    world = await query_current_region(
        ctx,
        player_pos,
        radius=radius,
        world_entity_types=["crop"],
        include_tiles=False,
        include_characters=False,
    )
    crops = _harvestable_crops(world)
    if max_count > 0:
        crops = crops[:max_count]
    total = len(crops)
    if total == 0:
        return result(
            success=True,
            status="ok",
            observation="harvest_crops completed: no harvestable crops found.",
            data=_data(0, 0, 0, "all_done"),
        )

    command_max_count = max_count if max_count > 0 else max(total, 999)
    compound_result = await ctx.harvest_crops_compound(
        max_count=command_max_count,
        replant_seed=replant_seed,
    )
    compound_data = compound_result.get("data") if isinstance(compound_result, dict) else {}
    if not isinstance(compound_data, dict):
        compound_data = {}

    verify_runtime = fact(
        await ctx.query_runtime_snapshot(
            include_player=True,
            include_world_time=True,
            include_weather=False,
            include_menu_state=False,
        )
    )
    verify_pos = player_position(verify_runtime) or player_pos
    verify_world = await query_current_region(
        ctx,
        verify_pos,
        radius=radius,
        world_entity_types=["crop"],
        include_tiles=False,
        include_characters=False,
    )
    remaining = len(_harvestable_crops(verify_world))
    harvested = _int_data(compound_data, "harvested", max(0, total - remaining))
    replanted = _int_data(compound_data, "replanted", 0)
    skipped = _int_data(compound_data, "skipped", 0)
    stopped = str(
        compound_data.get("stopped_reason")
        or ("all_done" if remaining == 0 else "remaining_harvestable")
    )

    data = _data(harvested, skipped, total, stopped)
    data["replanted"] = replanted
    data["remaining_harvestable"] = remaining
    data["harvestable_before"] = total
    data["command_max_count"] = command_max_count
    data["compound"] = compound_data
    data["time_of_day"] = runtime_time(verify_runtime) or runtime_time(runtime)

    if remaining == 0 and compound_result.get("success"):
        return result(
            success=True,
            status="ok",
            observation=f"harvest_crops ok: harvested={harvested}/{total} replanted={replanted} remaining=0",
            data=data,
        )

    if not compound_result.get("success"):
        error = compound_result.get("error") if isinstance(compound_result, dict) else {}
        error_code = None
        if isinstance(error, dict):
            error_code = str(error.get("code") or "") or None
        return result(
            success=False,
            status="blocked",
            observation=(
                "harvest_crops blocked: Mod compound harvesting failed "
                f"remaining_harvestable={remaining}"
            ),
            data=data,
            error_code=error_code or "HARVEST_CROPS_COMPOUND_FAILED",
        )

    return result(
        success=True,
        status="partial",
        observation=(
            f"harvest_crops partial: harvested={harvested}/{total} "
            f"replanted={replanted} remaining_harvestable={remaining}"
        ),
        data=data,
    )


def _harvestable_crops(world: dict) -> list[dict]:
    crops: list[dict] = []
    for entity in entities_of(world, "crop"):
        crop = entity_payload(entity, "crop")
        if crop.get("dead") is True:
            continue
        if crop.get("ready_for_harvest") is True:
            crops.append(entity)
    return crops


def _data(harvested: int, skipped: int, total: int, stopped_reason: str) -> dict:
    return {
        "harvested": harvested,
        "skipped": skipped,
        "total": total,
        "stopped_reason": stopped_reason,
    }


def _int_data(data: dict, key: str, default: int) -> int:
    try:
        return int(data.get(key, default))
    except (TypeError, ValueError):
        return default
