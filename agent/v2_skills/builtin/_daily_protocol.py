"""Shared protocol helpers for daily L3 skills."""

from __future__ import annotations

from typing import Any


DEFAULT_DAILY_RADIUS = 50
STAND_SIDE_OFFSETS = {
    "left": (-1, 0),
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
}
STAND_SIDE_RANK = {"left": 0, "right": 1, "up": 2, "down": 3}


def fact(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("fact")
    return nested if isinstance(nested, dict) else payload


def player_position(runtime_fact: dict) -> dict | None:
    location = runtime_fact.get("player_location") if isinstance(runtime_fact, dict) else None
    if not isinstance(location, dict):
        return None
    if "x" in location and "y" in location:
        return location
    tile = location.get("tile") or location.get("position")
    return tile if isinstance(tile, dict) and "x" in tile and "y" in tile else None


def runtime_time(runtime_fact: dict) -> int:
    value = runtime_fact.get("time_of_day")
    if value is None and isinstance(runtime_fact.get("world_time"), dict):
        value = runtime_fact["world_time"].get("time")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def runtime_stamina(runtime_fact: dict) -> int:
    try:
        return int(runtime_fact.get("stamina") or 0)
    except (TypeError, ValueError):
        return 0


def stop_reason(runtime_fact: dict, *, stamina_below: int = 0, time_after: int = 0) -> str | None:
    if stamina_below > 0 and runtime_stamina(runtime_fact) <= stamina_below:
        return "stamina_low"
    current_time = runtime_time(runtime_fact)
    if time_after > 0 and current_time > 0 and current_time >= time_after:
        return "time_after"
    return None


async def query_current_region(
    ctx: Any,
    player_pos: dict,
    *,
    radius: int = DEFAULT_DAILY_RADIUS,
    world_entity_types: list[str] | None = None,
    include_tiles: bool = True,
    include_characters: bool = False,
) -> dict:
    map_token = player_pos.get("map_token") or player_pos.get("map_id") or ""
    center = {"x": int(player_pos["x"]), "y": int(player_pos["y"])}
    if map_token:
        center["map_id"] = str(map_token)
    return fact(
        await ctx.query_world_region(
            map_token=str(map_token) if map_token else None,
            center=center,
            radius=radius,
            world_entity_types=world_entity_types,
            include_tiles=include_tiles,
            include_characters=include_characters,
        )
    )


def tile_lookup(world_fact: dict) -> dict[tuple[int, int], dict]:
    tiles = world_fact.get("tiles") if isinstance(world_fact, dict) else []
    if not isinstance(tiles, list):
        return {}
    result: dict[tuple[int, int], dict] = {}
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        position = tile.get("position") if isinstance(tile.get("position"), dict) else {}
        if "x" in position and "y" in position:
            result[(int(position["x"]), int(position["y"]))] = tile
    return result


def position_of(item: dict) -> dict | None:
    position = item.get("position") if isinstance(item.get("position"), dict) else None
    if isinstance(position, dict) and "x" in position and "y" in position:
        return position
    if "x" in item and "y" in item:
        return item
    return None


def ref_value(item: dict) -> str:
    ref = item.get("ref")
    if isinstance(ref, dict):
        return str(ref.get("value") or "")
    return str(ref or "")


def entity_payload(entity: dict, kind: str) -> dict:
    payload = entity.get(kind)
    return payload if isinstance(payload, dict) else {}


def entities_of(world_fact: dict, kind: str) -> list[dict]:
    entities = world_fact.get("entities") if isinstance(world_fact, dict) else []
    if not isinstance(entities, list):
        return []
    return [
        entity
        for entity in entities
        if isinstance(entity, dict)
        and (entity.get("type") == kind or entity.get("fact_kind") == kind)
    ]


def distance(x: int, y: int, player_pos: dict) -> float:
    try:
        return abs(float(player_pos["x"]) - x) + abs(float(player_pos["y"]) - y)
    except (KeyError, TypeError, ValueError):
        return 0.0


def choose_stand_side(
    target: dict,
    player_pos: dict,
    tiles: dict[tuple[int, int], dict],
) -> str | None:
    sides = ordered_stand_sides(target, player_pos)
    if not sides:
        return None
    if not tiles:
        return sides[0]
    pos = position_of(target)
    if pos is None:
        return None
    x = int(pos["x"])
    y = int(pos["y"])
    for side in sides:
        dx, dy = STAND_SIDE_OFFSETS[side]
        tile = tiles.get((x + dx, y + dy))
        if tile and tile.get("passable") is True and tile.get("occupied") is not True:
            return side
    return None


def ordered_stand_sides(target: dict, player_pos: dict) -> list[str]:
    pos = position_of(target)
    if pos is None:
        return []
    x = int(pos["x"])
    y = int(pos["y"])
    ordered: list[tuple[float, int, str]] = []
    for side, (dx, dy) in STAND_SIDE_OFFSETS.items():
        ordered.append((distance(x + dx, y + dy, player_pos), STAND_SIDE_RANK[side], side))
    return [side for _, _, side in sorted(ordered)]


def sorted_by_distance(items: list[dict], player_pos: dict) -> list[dict]:
    def key(item: dict) -> tuple[float, int, int, str]:
        pos = position_of(item) or {}
        x = int(pos.get("x") or 0)
        y = int(pos.get("y") or 0)
        return (distance(x, y, player_pos), x, y, ref_value(item))

    return sorted(items, key=key)


def clamp_non_negative(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def result(
    *,
    success: bool,
    status: str,
    observation: str,
    data: dict,
    error_code: str | None = None,
) -> dict:
    payload = {"success": success, "status": status, "observation": observation, "data": data}
    if error_code:
        payload["error"] = {"code": error_code, "message": observation}
        payload["data"]["error_code"] = error_code
    return payload
