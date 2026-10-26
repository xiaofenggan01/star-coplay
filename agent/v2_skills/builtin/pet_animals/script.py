"""pet_animals — protocol-native L3 animal and pet greeting."""

from __future__ import annotations

from typing import Any

from v2_skills.builtin._daily_protocol import (
    DEFAULT_DAILY_RADIUS,
    choose_stand_side,
    clamp_non_negative,
    fact,
    ordered_stand_sides,
    player_position,
    position_of,
    query_current_region,
    result,
    sorted_by_distance,
    tile_lookup,
)


async def pet_animals(ctx: Any, max_count: int = 0, radius: int = DEFAULT_DAILY_RADIUS) -> dict:
    max_count = clamp_non_negative(max_count)
    radius = max(1, min(DEFAULT_DAILY_RADIUS, clamp_non_negative(radius) or DEFAULT_DAILY_RADIUS))

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
            observation="pet_animals failed: player position is unavailable.",
            data=_data(0, 0, 0, "missing_player_position"),
            error_code="MISSING_PLAYER_POSITION",
        )

    world = await query_current_region(
        ctx,
        player_pos,
        radius=radius,
        world_entity_types=None,
        include_tiles=True,
        include_characters=True,
    )
    tiles = tile_lookup(world)
    targets = sorted_by_distance(_unpetted_targets(world), player_pos)
    if max_count > 0:
        targets = targets[:max_count]
    total = len(targets)
    if total == 0:
        return result(
            success=True,
            status="ok",
            observation="pet_animals completed: 没有需要抚摸的动物或宠物。",
            data=_data(0, 0, 0, "all_done"),
        )

    petted = 0
    skipped = 0
    warnings: list[dict] = []

    for target in targets:
        ctx.check_cancelled()
        current_runtime = fact(
            await ctx.query_runtime_snapshot(
                include_player=True,
                include_world_time=False,
                include_weather=False,
                include_menu_state=False,
            )
        )
        current_pos = player_position(current_runtime) or player_pos
        pos = position_of(target)
        if pos is None:
            skipped += 1
            warnings.append({"code": "NO_STAND_TILE", "target": _summary(target)})
            continue
        side = choose_stand_side(target, current_pos, tiles)
        candidate_sides = [side] if side is not None else ordered_stand_sides(target, current_pos)
        if not candidate_sides:
            skipped += 1
            warnings.append({"code": "NO_STAND_TILE", "target": _summary(target)})
            continue
        x = int(pos["x"])
        y = int(pos["y"])
        move_result = None
        used_side = None
        for candidate_side in candidate_sides:
            attempt = await ctx.move_adjacent(x, y, candidate_side)
            if attempt.get("success"):
                move_result = attempt
                used_side = candidate_side
                break
        if move_result is None:
            skipped += 1
            warning_code = "MOVE_ADJACENT_FAILED"
            if side is None:
                warning_code = "NO_REACHABLE_STAND_TILE"
            warnings.append({
                "code": warning_code,
                "target": _summary(target),
                "attempted_sides": candidate_sides,
            })
            continue
        interact_result = await ctx.interact(x, y)
        if interact_result.get("success"):
            petted += 1
        else:
            skipped += 1
            warnings.append({
                "code": "INTERACT_FAILED",
                "target": _summary(target),
                "side": used_side,
            })

    if petted == total:
        status = "ok"
        success = True
    elif petted > 0:
        status = "partial"
        success = True
    else:
        status = "blocked"
        success = False

    data = _data(petted, skipped, total, "all_done" if success else "no_reachable_animals")
    data["warnings"] = warnings
    return result(
        success=success,
        status=status,
        observation=f"pet_animals {status}: petted={petted}/{total}",
        data=data,
        error_code=None if success else "NO_ANIMALS_PETTED",
    )


def _unpetted_targets(world: dict) -> list[dict]:
    characters = world.get("characters") if isinstance(world, dict) else []
    if not isinstance(characters, list):
        return []
    targets: list[dict] = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        farm_animal = character.get("farm_animal")
        if isinstance(farm_animal, dict):
            if farm_animal.get("was_petted") is True or farm_animal.get("was_auto_petted") is True:
                continue
            targets.append(character)
            continue
        pet = character.get("pet")
        if isinstance(pet, dict):
            if pet.get("petted_today") is True:
                continue
            targets.append(character)
    return targets


def _data(petted: int, skipped: int, total: int, stopped_reason: str) -> dict:
    return {
        "petted": petted,
        "skipped": skipped,
        "total": total,
        "stopped_reason": stopped_reason,
    }


def _summary(target: dict) -> dict:
    pos = position_of(target) or {}
    return {
        "x": pos.get("x"),
        "y": pos.get("y"),
        "name": target.get("display_name") or target.get("name") or target.get("summary") or "",
        "kind": target.get("fact_kind") or target.get("type") or "",
    }
