"""Atomic L3 tree chopping MVP."""

from __future__ import annotations

from typing import Any


MAX_TARGET_WOOD = 50
DEFAULT_TARGET_WOOD = 5
DEFAULT_RADIUS = 16
MAX_HITS_PER_TREE = 12
MAX_LOOT_SWEEPS = 4
ESTIMATED_WOOD_PER_TREE = 10
STAND_SIDE_OFFSETS = {
    "left": (-1, 0),
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
}
STAND_SIDE_RANK = {"left": 0, "right": 1, "up": 2, "down": 3}


async def chop_trees(
    ctx,
    target_wood: int = DEFAULT_TARGET_WOOD,
    radius: int = DEFAULT_RADIUS,
    include_stumps: bool = False,
) -> dict:
    warnings: list[dict] = []
    target_wood, target_warning = _clamp_target_wood(target_wood)
    if target_warning:
        warnings.append(target_warning)
    radius, radius_warning = _clamp_radius(radius)
    if radius_warning:
        warnings.append(radius_warning)

    runtime_payload = await ctx.query_runtime_snapshot(
        include_player=True,
        include_world_time=True,
        include_menu_state=True,
    )
    runtime = _fact(runtime_payload)
    player_pos = _player_position(runtime)
    if player_pos is None:
        return _result(
            success=False,
            status="failed",
            observation="chop_trees failed: player position is unavailable.",
            stopped_reason="missing_player_position",
            target_wood=target_wood,
            error_code="MISSING_PLAYER_POSITION",
            warnings=warnings,
        )

    inventory_payload = await ctx.query_inventory_snapshot(
        container_kind="player",
        include_empty_slots=False,
        include_details=True,
    )
    inventory = _fact(inventory_payload)
    axe = _find_axe(inventory)
    wood_before = _wood_count(inventory)
    wood_after = wood_before
    if axe is None:
        return _result(
            success=False,
            status="blocked",
            observation="chop_trees blocked: no axe found in the player inventory.",
            stopped_reason="missing_axe",
            target_wood=target_wood,
            wood_before=wood_before,
            wood_after=wood_after,
            error_code="NO_AXE",
            warnings=warnings,
        )

    world = await _query_tree_region(ctx, player_pos, radius)
    world_fact = _fact(world)
    trees = _candidate_trees(world_fact, player_pos, include_stumps=include_stumps)
    if not trees:
        return _result(
            success=False,
            status="blocked",
            observation="chop_trees blocked: no nearby mature trees are available.",
            stopped_reason="no_trees",
            target_wood=target_wood,
            wood_before=wood_before,
            wood_after=wood_after,
            error_code="NO_TREES",
            warnings=warnings,
        )

    tile_lookup = _tile_lookup(world_fact)
    tree_plans: list[tuple[dict, str]] = []
    trees_skipped = 0
    for tree in trees:
        stand_side = _choose_stand_side(tree, player_pos, tile_lookup)
        if stand_side is None:
            trees_skipped += 1
            warnings.append(
                {
                    "code": "NO_STAND_TILE",
                    "message": "No passable adjacent tile was found for a candidate tree.",
                    "tree": _tree_summary(tree),
                }
            )
            continue
        tree_plans.append((tree, stand_side))

    if not tree_plans:
        return _result(
            success=False,
            status="blocked",
            observation="chop_trees blocked: nearby trees have no reachable standing tile.",
            stopped_reason="no_reachable_trees",
            target_wood=target_wood,
            wood_before=wood_before,
            wood_after=wood_after,
            trees_skipped=trees_skipped,
            error_code="NO_REACHABLE_TREES",
            warnings=warnings,
        )

    equip_result = await ctx.equip(slot=axe["slot"])
    if not equip_result.get("success"):
        return _result(
            success=False,
            status="failed",
            observation="chop_trees failed: equipping the axe failed.",
            stopped_reason="equip_failed",
            target_wood=target_wood,
            wood_before=wood_before,
            wood_after=wood_after,
            trees_skipped=trees_skipped,
            error_code="EQUIP_FAILED",
            warnings=warnings,
        )

    trees_chopped = 0
    swings = 0
    atom_failed = False
    stopped_reason = "no_more_reachable_trees"

    for tree, stand_side in tree_plans:
        ctx.check_cancelled()
        if wood_after - wood_before >= target_wood:
            stopped_reason = "wood_target_reached"
            break

        x, y = tree["x"], tree["y"]
        move_result = await ctx.move_adjacent(x, y, stand_side)
        if not move_result.get("success"):
            trees_skipped += 1
            warnings.append(
                {
                    "code": "MOVE_ADJACENT_FAILED",
                    "message": "move_adjacent failed for a candidate tree.",
                    "tree": _tree_summary(tree),
                    "side": stand_side,
                }
            )
            continue

        tree_gone = False
        for _ in range(MAX_HITS_PER_TREE):
            ctx.check_cancelled()
            if not await _tree_still_choppable(ctx, tree, include_stumps=include_stumps):
                tree_gone = True
                break

            use_result = await ctx.use_tool(x, y, charge_level=0)
            swings += 1
            if not use_result.get("success"):
                atom_failed = True
                warnings.append(
                    {
                        "code": "USE_TOOL_FAILED",
                        "message": "use_tool failed while chopping a candidate tree.",
                        "tree": _tree_summary(tree),
                    }
                )
                break

            inventory_payload = await ctx.query_inventory_snapshot(
                container_kind="player",
                include_empty_slots=False,
                include_details=True,
            )
            inventory = _fact(inventory_payload)
            wood_after = _wood_count(inventory)
            if wood_after - wood_before >= target_wood:
                stopped_reason = "wood_target_reached"
                tree_gone = True
                break

            if not await _tree_still_choppable(ctx, tree, include_stumps=include_stumps):
                tree_gone = True
                break

        if tree_gone:
            trees_chopped += 1
            loot_summary = await _collect_nearby_loose_items(ctx, tree)
            if loot_summary["remaining_items"] > 0:
                warnings.append(
                    {
                        "code": "LOOSE_ITEM_REMAINING",
                        "message": "Nearby loose items remained after loot collection attempts.",
                        "tree": _tree_summary(tree),
                        "remaining_items": loot_summary["remaining_items"],
                        "attempts": loot_summary["attempts"],
                    }
                )
        else:
            trees_skipped += 1

        inventory_payload = await ctx.query_inventory_snapshot(
            container_kind="player",
            include_empty_slots=False,
            include_details=True,
        )
        inventory = _fact(inventory_payload)
        wood_after = _wood_count(inventory)
        if wood_after - wood_before >= target_wood:
            stopped_reason = "wood_target_reached"
            break
        if trees_chopped * ESTIMATED_WOOD_PER_TREE >= target_wood:
            stopped_reason = "tree_budget_reached"
            warnings.append(
                {
                    "code": "TREE_BUDGET_REACHED",
                    "message": (
                        "Stopped after enough completed trees for the requested wood target; "
                        "inventory pickup may lag behind dropped wood."
                    ),
                    "estimated_wood_gained": trees_chopped * ESTIMATED_WOOD_PER_TREE,
                }
            )
            break

    wood_gained = max(0, wood_after - wood_before)
    if wood_gained >= target_wood:
        status = "ok"
        success = True
        error_code = None
        observation = (
            f"chop_trees completed: wood_gained={wood_gained} "
            "stopped_reason=wood_target_reached"
        )
    elif wood_gained > 0 or trees_chopped > 0:
        status = "partial"
        success = True
        error_code = None
        observation = (
            f"chop_trees partial: wood_gained={wood_gained}/{target_wood} "
            f"stopped_reason={stopped_reason}"
        )
    elif atom_failed:
        status = "failed"
        success = False
        error_code = "EXECUTION_FAILED"
        stopped_reason = "atom_failed"
        observation = "chop_trees failed: an execution atom failed before wood was gained."
    else:
        status = "blocked"
        success = False
        error_code = "NO_REACHABLE_TREES"
        stopped_reason = "no_reachable_trees"
        observation = "chop_trees blocked: no reachable tree could be completed."

    return _result(
        success=success,
        status=status,
        observation=observation,
        stopped_reason=stopped_reason,
        target_wood=target_wood,
        wood_before=wood_before,
        wood_after=wood_after,
        trees_chopped=trees_chopped,
        trees_skipped=trees_skipped,
        swings=swings,
        error_code=error_code,
        warnings=warnings,
    )


async def _query_tree_region(ctx, player_pos: dict, radius: int) -> dict:
    map_token = player_pos.get("map_token") or player_pos.get("map_id") or ""
    center = {
        "x": int(player_pos["x"]),
        "y": int(player_pos["y"]),
    }
    if map_token:
        center["map_id"] = str(map_token)
    return await ctx.query_world_region(
        map_token=str(map_token) if map_token else None,
        center=center,
        radius=radius,
        world_entity_types=["tree"],
        include_tiles=True,
        include_characters=False,
    )


async def _tree_still_choppable(ctx, tree: dict, *, include_stumps: bool) -> bool:
    map_token = tree.get("map_token") or tree.get("map_id") or ""
    center = {"x": tree["x"], "y": tree["y"]}
    if map_token:
        center["map_id"] = str(map_token)
    world = await ctx.query_world_region(
        map_token=str(map_token) if map_token else None,
        center=center,
        radius=1,
        world_entity_types=["tree"],
        include_tiles=False,
        include_characters=False,
    )
    candidates = _candidate_trees(
        _fact(world),
        {"x": tree["x"], "y": tree["y"]},
        include_stumps=include_stumps,
    )
    return any(
        candidate["x"] == tree["x"] and candidate["y"] == tree["y"]
        for candidate in candidates
    )


async def _collect_nearby_loose_items(ctx, tree: dict) -> dict:
    attempts = 0
    for _ in range(MAX_LOOT_SWEEPS):
        items = await _query_nearby_loose_items(ctx, tree)
        if not items:
            return {"attempts": attempts, "remaining_items": 0}
        attempts += 1
        for item in items:
            try:
                move_result = await ctx.move_adjacent(item["x"], item["y"], item["approach_side"])
                if not move_result.get("success"):
                    continue
                await ctx.interact(item["x"], item["y"])
            except Exception:
                continue
    remaining = await _query_nearby_loose_items(ctx, tree)
    return {"attempts": attempts, "remaining_items": len(remaining)}


async def _query_nearby_loose_items(ctx, tree: dict) -> list[dict]:
    map_token = tree.get("map_token") or tree.get("map_id") or ""
    center = {"x": tree["x"], "y": tree["y"]}
    if map_token:
        center["map_id"] = str(map_token)
    try:
        world = await ctx.query_world_region(
            map_token=str(map_token) if map_token else None,
            center=center,
            radius=2,
            world_entity_types=["loose_item"],
            include_tiles=False,
            include_characters=False,
        )
    except Exception:
        return []
    return _nearby_loose_items(_fact(world), tree)


def _nearby_loose_items(world_fact: dict, tree: dict) -> list[dict]:
    entities = world_fact.get("entities") if isinstance(world_fact, dict) else []
    if not isinstance(entities, list):
        return []

    items: list[dict] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if (entity.get("type") or entity.get("fact_kind")) != "loose_item":
            continue
        payload = entity.get("loose_item") if isinstance(entity.get("loose_item"), dict) else {}
        if payload.get("can_be_grabbed") is False:
            continue
        position = entity.get("position") if isinstance(entity.get("position"), dict) else {}
        if "x" not in position or "y" not in position:
            continue
        x = int(position["x"])
        y = int(position["y"])
        if abs(x - int(tree["x"])) <= 2 and abs(y - int(tree["y"])) <= 2:
            approach_side = _approach_side_for_item(tree, x, y)
            items.append(
                {
                    "x": x,
                    "y": y,
                    "distance": abs(x - int(tree["x"])) + abs(y - int(tree["y"])),
                    "approach_side": approach_side,
                }
            )
    return sorted(items, key=lambda item: (item["distance"], item["x"], item["y"]))


def _approach_side_for_item(tree: dict, x: int, y: int) -> str:
    dx = x - int(tree["x"])
    dy = y - int(tree["y"])
    if abs(dx) >= abs(dy):
        return "left" if dx >= 0 else "right"
    return "up" if dy >= 0 else "down"


def _candidate_trees(world_fact: dict, player_pos: dict, *, include_stumps: bool) -> list[dict]:
    entities = world_fact.get("entities") if isinstance(world_fact, dict) else []
    if not isinstance(entities, list):
        return []

    candidates: list[dict] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if (entity.get("type") or entity.get("fact_kind")) != "tree":
            continue
        payload = entity.get("tree") if isinstance(entity.get("tree"), dict) else {}
        is_stump = bool(payload.get("is_stump"))
        if not include_stumps and is_stump:
            continue
        if int(payload.get("growth_stage") or 0) < 5 and not (include_stumps and is_stump):
            continue
        position = entity.get("position") if isinstance(entity.get("position"), dict) else {}
        if "x" not in position or "y" not in position:
            continue
        x = int(position["x"])
        y = int(position["y"])
        map_token = (
            position.get("map_id")
            or world_fact.get("map_token")
            or world_fact.get("map_id")
            or ""
        )
        candidate = {
            "x": x,
            "y": y,
            "map_token": map_token,
            "map_id": position.get("map_id") or world_fact.get("map_id") or "",
            "distance": _distance(x, y, player_pos),
        }
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: (item["distance"], item["x"], item["y"]))


def _tile_lookup(world_fact: dict) -> dict[tuple[int, int], dict]:
    tiles = world_fact.get("tiles") if isinstance(world_fact, dict) else []
    if not isinstance(tiles, list):
        return {}
    lookup: dict[tuple[int, int], dict] = {}
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        position = tile.get("position") if isinstance(tile.get("position"), dict) else {}
        if "x" not in position or "y" not in position:
            continue
        lookup[(int(position["x"]), int(position["y"]))] = tile
    return lookup


def _choose_stand_side(
    tree: dict,
    player_pos: dict,
    tile_lookup: dict[tuple[int, int], dict],
) -> str | None:
    ordered = _ordered_stand_sides(tree, player_pos)
    if not tile_lookup:
        return ordered[0]
    for side in ordered:
        dx, dy = STAND_SIDE_OFFSETS[side]
        tile = tile_lookup.get((int(tree["x"]) + dx, int(tree["y"]) + dy))
        if tile and tile.get("passable") is True and tile.get("occupied") is not True:
            return side
    return None


def _ordered_stand_sides(tree: dict, player_pos: dict) -> list[str]:
    options: list[tuple[float, int, str]] = []
    for side, (dx, dy) in STAND_SIDE_OFFSETS.items():
        x = int(tree["x"]) + dx
        y = int(tree["y"]) + dy
        options.append((_distance(x, y, player_pos), STAND_SIDE_RANK[side], side))
    return [side for _, _, side in sorted(options)]


def _find_axe(inventory_fact: dict) -> dict | None:
    for item in _items(inventory_fact):
        if not _is_axe(item):
            continue
        slot = item.get("slot")
        if isinstance(slot, int) and 1 <= slot <= 12:
            return {"slot": slot, "item": item}
    return None


def _is_axe(item: dict) -> bool:
    tokens = _item_tokens(item)
    if any("pickaxe" in token for token in tokens):
        return False
    if any(token.endswith("axe") for token in tokens):
        return True
    values = [str(item.get(key) or "") for key in ("display_name", "internal_name")]
    return any("\u65a7" in value and "\u9550" not in value for value in values)


def _wood_count(inventory_fact: dict) -> int:
    total = 0
    for item in _items(inventory_fact):
        if _is_wood(item):
            total += int(item.get("stack") or 0)
    return total


def _is_wood(item: dict) -> bool:
    values = [
        str(item.get("item_id") or ""),
        str(item.get("qualified_item_id") or ""),
        str(item.get("internal_name") or ""),
        str(item.get("display_name") or ""),
    ]
    normalized = {_normalize_token(value) for value in values if value}
    if "wood" in normalized or "o388" in normalized or "388" in normalized:
        return True
    return any(value.strip() == "\u6728\u5934" or "\u6728\u6750" in value for value in values)


def _items(inventory_fact: dict) -> list[dict]:
    items = inventory_fact.get("items") if isinstance(inventory_fact, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _fact(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    fact = payload.get("fact")
    return fact if isinstance(fact, dict) else payload


def _player_position(runtime_fact: dict) -> dict | None:
    location = runtime_fact.get("player_location") if isinstance(runtime_fact, dict) else None
    if not isinstance(location, dict):
        return None
    if "x" in location and "y" in location:
        return location
    tile = location.get("tile") or location.get("position")
    return tile if isinstance(tile, dict) and "x" in tile and "y" in tile else None


def _distance(x: int, y: int, player_pos: dict) -> float:
    try:
        return abs(float(player_pos["x"]) - x) + abs(float(player_pos["y"]) - y)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _tree_summary(tree: dict) -> dict:
    return {
        "x": int(tree["x"]),
        "y": int(tree["y"]),
        "map_token": str(tree.get("map_token") or tree.get("map_id") or ""),
    }


def _clamp_target_wood(value: int) -> tuple[int, dict | None]:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TARGET_WOOD, {
            "code": "TARGET_WOOD_INVALID",
            "message": "target_wood was not an integer; defaulted to 5.",
            "requested": repr(value),
            "normalized": DEFAULT_TARGET_WOOD,
        }
    normalized = max(1, min(MAX_TARGET_WOOD, requested))
    if normalized != requested:
        return normalized, {
            "code": "TARGET_WOOD_CLAMPED",
            "message": "target_wood was clamped to the supported 1..50 range.",
            "requested": requested,
            "normalized": normalized,
        }
    return normalized, None


def _clamp_radius(value: int) -> tuple[int, dict | None]:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RADIUS, {
            "code": "RADIUS_INVALID",
            "message": "radius was not an integer; defaulted to 16.",
            "requested": repr(value),
            "normalized": DEFAULT_RADIUS,
        }
    normalized = max(1, min(32, requested))
    if normalized != requested:
        return normalized, {
            "code": "RADIUS_CLAMPED",
            "message": "radius was clamped to the supported 1..32 range.",
            "requested": requested,
            "normalized": normalized,
        }
    return normalized, None


def _normalize_token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _item_tokens(item: dict) -> list[str]:
    return [
        _normalize_token(str(item.get(key) or ""))
        for key in ("item_id", "qualified_item_id", "internal_name", "display_name")
        if item.get(key)
    ]


def _tree_summary(tree: dict) -> dict:
    return {
        "x": tree.get("x"),
        "y": tree.get("y"),
        "map_id": tree.get("map_id") or tree.get("map_token") or "",
    }


def _result(
    *,
    success: bool,
    status: str,
    observation: str,
    stopped_reason: str,
    target_wood: int,
    wood_before: int = 0,
    wood_after: int = 0,
    trees_chopped: int = 0,
    trees_skipped: int = 0,
    swings: int = 0,
    error_code: str | None = None,
    warnings: list[dict] | None = None,
) -> dict:
    wood_gained = max(0, wood_after - wood_before)
    data = {
        "wood_before": wood_before,
        "wood_after": wood_after,
        "wood_gained": wood_gained,
        "estimated_wood_gained": trees_chopped * ESTIMATED_WOOD_PER_TREE,
        "target_wood": target_wood,
        "trees_chopped": trees_chopped,
        "trees_skipped": trees_skipped,
        "swings": swings,
        "stopped_reason": stopped_reason,
        "warnings": warnings or [],
        "awaiting_next_player_command": True,
    }
    if error_code:
        data["error_code"] = error_code
    result = {
        "success": success,
        "status": status,
        "observation": observation,
        "data": data,
    }
    if error_code:
        result["error"] = {"code": error_code, "message": observation}
    return result
