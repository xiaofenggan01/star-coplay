"""Shared orchestration helpers for Hosted Stardew advanced Skills."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


CAPABILITY_VERSION = ">=2.4.5,<3.0.0"
SUMMARY_MAX_CHARS = 2000
DATA_MAX_CHARS = 5000


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _bounded_text(value: str, limit: int = SUMMARY_MAX_CHARS) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    suffix = "\n[Result truncated.]"
    return value[:limit - len(suffix)] + suffix, True


def _bounded_data(value: Any) -> tuple[dict[str, Any], bool]:
    data = _jsonable(value)
    if not isinstance(data, dict):
        data = {"value": data}
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= DATA_MAX_CHARS:
        return data, False
    preview, _ = _bounded_text(encoded, DATA_MAX_CHARS - 200)
    return {"preview": preview, "truncated": True}, True


def _data_json(value: Any) -> tuple[str, bool]:
    data, truncated = _bounded_data(value)
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ), truncated


async def invoke_capability(ctx: Any, capability_id: str, arguments: Mapping[str, Any]) -> Any:
    return await ctx.invoke(capability_id, CAPABILITY_VERSION, dict(arguments))


async def direct_capability(
    ctx: Any,
    capability_id: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    result = await invoke_capability(ctx, capability_id, arguments)
    output = result.output if isinstance(result.output, Mapping) else {}
    data_json, data_truncated = _data_json(output.get("data", {}))
    observation = output.get("observation")
    if not isinstance(observation, str) or not observation:
        observation = data_json
    summary, summary_truncated = _bounded_text(observation)
    source_status = str(output.get("status", "COMPLETED")).upper()
    status = "completed" if source_status == "COMPLETED" else (
        "partial" if source_status == "PARTIAL" else "failed"
    )
    return {
        "status": status,
        "summary": summary,
        "data_json": data_json,
        "truncated": data_truncated or summary_truncated,
    }


def _record_ref(record: Mapping[str, Any]) -> str | None:
    for key in ("ref_id", "refId", "ref"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, Mapping):
            nested = value.get("value")
            if isinstance(nested, str) and nested:
                return nested
    return None


def collect_ref_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            ref = _record_ref(item)
            if ref is not None and ref not in seen:
                seen.add(ref)
                record = _jsonable(item)
                if isinstance(record, dict):
                    records.append(record)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for nested in item:
                visit(nested)

    visit(value)
    return records


def _field(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _matches(record: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    for name, expected in filters.items():
        actual = _field(record, name)
        if isinstance(expected, Mapping):
            if "eq" in expected and actual != expected["eq"]:
                return False
            if "min" in expected and (actual is None or actual < expected["min"]):
                return False
            if "max" in expected and (actual is None or actual > expected["max"]):
                return False
        elif actual != expected:
            return False
    return True


def compact_records_result(
    records: Sequence[Mapping[str, Any]], *, offset: int, limit: int,
) -> dict[str, Any]:
    total = len(records)
    page = list(records[offset:offset + limit])
    data_json, truncated = _data_json({
        "items": page,
        "total": total,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "next_offset": offset + len(page),
    })
    summary, summary_truncated = _bounded_text(
        f"Found {total} matching facts; returned {len(page)} from offset {offset}."
    )
    return {
        "status": "completed",
        "summary": summary,
        "data_json": data_json,
        "truncated": truncated or summary_truncated,
    }


async def search_world_facts(ctx: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    fact_type = str(arguments["fact_type"])
    entity_types = ["door", "warp"] if fact_type in {"exit", "exits"} else [fact_type]
    result = await invoke_capability(ctx, "query_world_region", {
        "world_entity_types": entity_types,
        "include_characters": fact_type in {"npc", "farm_animal", "pet"},
        "include_tiles": False,
    })
    output = result.output if isinstance(result.output, Mapping) else {}
    records = collect_ref_records(output.get("data", {}))
    filters_json = arguments.get("filters_json")
    if isinstance(filters_json, str) and filters_json:
        try:
            filters = json.loads(filters_json)
        except json.JSONDecodeError as exc:
            raise ValueError("filters_json must contain valid JSON") from exc
        if not isinstance(filters, Mapping):
            raise ValueError("filters_json must encode an object")
        records = [record for record in records if _matches(record, filters)]
    sort = arguments.get("sort")
    if isinstance(sort, str) and sort:
        descending = sort.startswith("-")
        name = sort[1:] if descending else sort
        records.sort(key=lambda record: (_field(record, name) is None, _field(record, name)), reverse=descending)
    offset = max(0, int(arguments.get("offset", 0)))
    limit = max(1, min(int(arguments.get("limit", 20)), 20))
    return compact_records_result(records, offset=offset, limit=limit)


async def find_interaction_candidates(
    ctx: Any, arguments: Mapping[str, Any],
) -> dict[str, Any]:
    result = await invoke_capability(ctx, "query_world_region", {
        "include_characters": True,
        "include_tiles": False,
    })
    output = result.output if isinstance(result.output, Mapping) else {}
    records = collect_ref_records(output.get("data", {}))
    offset = max(0, int(arguments.get("offset", 0)))
    limit = max(1, min(int(arguments.get("limit", 20)), 20))
    return compact_records_result(records, offset=offset, limit=limit)


async def walk_route(ctx: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    waypoints = list(arguments["waypoints"])
    stop_on_failure = bool(arguments.get("stop_on_failure", True))
    completed = 0
    stopped_reason = "route_completed"
    for waypoint in waypoints:
        result = await invoke_capability(ctx, "go_to", {
            "map_id": waypoint["map_id"],
            "x": waypoint["x"],
            "y": waypoint["y"],
            "arrival_mode": "exact",
        })
        output = result.output if isinstance(result.output, Mapping) else {}
        if output.get("success") is True or str(output.get("status", "")).upper() == "COMPLETED":
            completed += 1
            continue
        stopped_reason = "waypoint_failed"
        if stop_on_failure:
            break
    status = "completed" if completed == len(waypoints) else (
        "partial" if completed else "failed"
    )
    return {
        "status": status,
        "summary": f"Visited {completed} of {len(waypoints)} requested destinations.",
        "data_json": json.dumps({
            "completed_waypoints": completed,
            "total_waypoints": len(waypoints),
            "stopped_reason": stopped_reason,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "truncated": False,
    }


async def pet_animals(ctx: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    runtime_result = await invoke_capability(ctx, "query_runtime_snapshot", {
        "include_player": True,
        "include_world_time": False,
        "include_weather": False,
        "include_menu_state": False,
    })
    runtime_output = (
        runtime_result.output if isinstance(runtime_result.output, Mapping) else {}
    )
    runtime_data = runtime_output.get("data", {})
    if not isinstance(runtime_data, Mapping):
        runtime_data = {}
    player_location = runtime_data.get("player_location", {})
    if not isinstance(player_location, Mapping):
        player_location = {}
    try:
        player_x = int(player_location["x"])
        player_y = int(player_location["y"])
    except (KeyError, TypeError, ValueError):
        data_json, truncated = _data_json({
            "petted": 0, "skipped": 0, "targets_found": 0,
            "stopped_reason": "missing_player_position",
        })
        return {
            "status": "failed",
            "summary": "Could not find the player's current map position.",
            "data_json": data_json,
            "truncated": truncated,
        }

    map_token = str(player_location.get("map_token") or "")
    center: dict[str, Any] = {"x": player_x, "y": player_y}
    if map_token:
        center["map_id"] = map_token
    observed = await invoke_capability(ctx, "query_world_region", {
        "map_token": map_token or None,
        "center": center,
        "radius": 50,
        "world_entity_types": ["farm_animal", "pet"],
        "include_characters": True,
        "include_tiles": False,
    })
    output = observed.output if isinstance(observed.output, Mapping) else {}
    records = collect_ref_records(output.get("data", {}))
    refs = [
        ref for record in records
        if _record_needs_pet(record)
        for ref in [_record_ref(record)] if ref is not None
    ]
    max_count = int(arguments.get("max_count", 0))
    if max_count > 0:
        refs = refs[:max_count]
    if not refs:
        return {
            "status": "completed",
            "summary": "No unpetted animals were found on the current map.",
            "data_json": '{"petted":0,"skipped":0,"targets_found":0}',
            "truncated": False,
        }
    payload: dict[str, Any] = {
        "targets": refs,
        "action": "do",
        "intent": "pet",
        "repeat_per_target": "once",
    }
    if max_count > 0:
        payload["max_count"] = max_count
    execution = await invoke_capability(ctx, "execute_on_targets", payload)
    execution_output = execution.output if isinstance(execution.output, Mapping) else {}
    execution_data = execution_output.get("data", {})
    if not isinstance(execution_data, Mapping):
        execution_data = {}
    try:
        petted = max(0, int(execution_data.get("completed", 0)))
    except (TypeError, ValueError):
        petted = 0
    try:
        skipped = max(0, int(execution_data.get("skipped", len(refs) - petted)))
    except (TypeError, ValueError):
        skipped = max(0, len(refs) - petted)
    petted = min(petted, len(refs))
    data_json, truncated = _data_json({
        "petted": petted,
        "skipped": skipped,
        "targets_found": len(refs),
    })
    status = "completed" if petted == len(refs) else (
        "partial" if petted > 0 else "failed"
    )
    summary = (
        f"Petted {petted} of {len(refs)} animals on the current map."
        if petted > 0
        else f"Could not pet any of the {len(refs)} animals found on the current map."
    )
    return {
        "status": status,
        "summary": summary,
        "data_json": data_json,
        "truncated": truncated,
    }


def _record_needs_pet(record: Mapping[str, Any]) -> bool:
    farm_animal = record.get("farm_animal")
    if isinstance(farm_animal, Mapping):
        return (
            farm_animal.get("was_petted") is not True
            and farm_animal.get("was_auto_petted") is not True
        )
    pet = record.get("pet")
    if isinstance(pet, Mapping):
        return pet.get("petted_today") is not True
    return (
        record.get("was_petted") is not True
        and record.get("was_auto_petted") is not True
        and record.get("petted_today") is not True
    )


def _record_terms(record: Mapping[str, Any]) -> str:
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for nested in value:
                visit(nested)
        elif isinstance(value, str):
            values.append(value.lower())

    visit(record)
    return " ".join(values)


async def clear_farm(ctx: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    observed = await invoke_capability(ctx, "query_world_region", {
        "world_entity_types": ["debris", "rock"],
        "include_tiles": False,
    })
    output = observed.output if isinstance(observed.output, Mapping) else {}
    records = collect_ref_records(output.get("data", {}))
    grouped: dict[str, list[str]] = {"weeds": [], "twigs": [], "stones": []}
    for record in records:
        ref = _record_ref(record)
        if ref is None:
            continue
        terms = _record_terms(record)
        if any(term in terms for term in ("weed", "fiber", "grass")):
            grouped["weeds"].append(ref)
        elif any(term in terms for term in ("twig", "branch", "wood debris")):
            grouped["twigs"].append(ref)
        elif any(term in terms for term in ("rock", "stone")):
            grouped["stones"].append(ref)

    requested = str(arguments.get("targets", "all"))
    selected = list(grouped) if requested == "all" else [requested]
    tools = {"weeds": "Scythe", "twigs": "Axe", "stones": "Pickaxe"}
    stop_conditions = {
        key: int(arguments[key]) for key in ("stamina_below", "time_after")
        if int(arguments.get(key, 0)) > 0
    }
    phases_completed = 0
    targets_attempted = 0
    failed = False
    for phase in selected:
        refs = grouped[phase]
        if not refs:
            continue
        payload: dict[str, Any] = {
            "targets": refs,
            "action": "use_tool",
            "tool": tools[phase],
            "repeat_per_target": "once" if phase == "weeds" else "until_gone",
        }
        if stop_conditions:
            payload["stop_conditions"] = stop_conditions
        result = await invoke_capability(ctx, "execute_on_targets", payload)
        phase_output = result.output if isinstance(result.output, Mapping) else {}
        targets_attempted += len(refs)
        if phase_output.get("success") is True or str(phase_output.get("status", "")).upper() == "COMPLETED":
            phases_completed += 1
        else:
            failed = True
            break
    status = "failed" if failed and phases_completed == 0 else (
        "partial" if failed else "completed"
    )
    return {
        "status": status,
        "summary": f"Completed {phases_completed} farm-clearing phases.",
        "data_json": json.dumps({
            "phases_completed": phases_completed,
            "phases_requested": len(selected),
            "targets_attempted": targets_attempted,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "truncated": False,
    }
