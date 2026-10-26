"""Observation frame collection from proto-native query capabilities."""

from __future__ import annotations

import logging
from typing import Any

from .models import JsonDict, ObservationFrame

log = logging.getLogger("l3_query.frame")


def _protocol_payload(result: Any, field: str) -> JsonDict | None:
    """Extract a proto-native query result dict from SkillContext.query output."""
    if not isinstance(result, dict):
        return None
    protocol_data = result.get("protocol_data")
    if isinstance(protocol_data, dict):
        return protocol_data
    if isinstance(result.get(field), dict):
        return result[field]
    if field == "query_runtime_snapshot" and isinstance(result.get("fact"), dict):
        return result
    return result


def _fact(payload: JsonDict | None) -> JsonDict | None:
    if not isinstance(payload, dict):
        return None
    fact = payload.get("fact")
    return fact if isinstance(fact, dict) else payload


def _warnings(payload: JsonDict | None) -> list[JsonDict]:
    if not isinstance(payload, dict):
        return []
    warnings = payload.get("warnings")
    return warnings if isinstance(warnings, list) else []


async def _query_optional(ctx: Any, capability: str, params: JsonDict) -> tuple[JsonDict | None, JsonDict | None]:
    try:
        result = await ctx.query(capability, params)
    except Exception as exc:
        log.debug("optional L3 query failed capability=%s params=%s: %s", capability, params, exc)
        return None, {
            "capability": capability,
            "code": exc.__class__.__name__,
            "message": str(exc),
            "retryable": True,
            "suggested_next": "稍后重试该观察；如果连续失败，检查 Mod/WS 连接和协议版本。",
        }
    if isinstance(result, dict):
        raw = result.get("raw_protocol_result")
        if isinstance(raw, dict) and raw.get("success") is False:
            error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
            return result, {
                "capability": capability,
                "code": error.get("code") or raw.get("status") or "QUERY_FAILED",
                "message": error.get("message") or raw.get("observation") or "query returned success=false",
                "retryable": True,
                "suggested_next": "重试观察；若仍失败，先不要把空结果当成事实。",
            }
    return result, None


async def collect_observation_frame(
    ctx: Any,
    *,
    radius: int = 8,
    include_inventory: bool = True,
    include_ui: bool = True,
) -> ObservationFrame:
    """Collect a current-view frame using proto-native L1 fact capabilities."""
    runtime_raw, runtime_error = await _query_optional(
        ctx,
        "query_runtime_snapshot",
        {
            "include_player": True,
            "include_world_time": True,
            "include_weather": True,
            "include_menu_state": True,
        },
    )
    runtime_payload = _protocol_payload(runtime_raw, "query_runtime_snapshot")
    runtime_fact = _fact(runtime_payload)

    world_raw, world_error = await _query_optional(
        ctx,
        "query_world_region",
        {
            "radius": radius,
            "include_tiles": True,
            "include_characters": True,
        },
    )
    world_payload = _protocol_payload(world_raw, "query_world_region")
    world_fact = _fact(world_payload)

    inventory_fact = None
    inventory_payload = None
    inventory_error = None
    if include_inventory:
        inventory_raw, inventory_error = await _query_optional(
            ctx,
            "query_inventory_snapshot",
            {
                "container_kind": "player",
                "include_empty_slots": False,
                "include_details": True,
            },
        )
        inventory_payload = _protocol_payload(inventory_raw, "query_inventory_snapshot")
        inventory_fact = _fact(inventory_payload)

    ui_fact = None
    ui_payload = None
    ui_error = None
    if include_ui:
        ui_raw, ui_error = await _query_optional(
            ctx,
            "query_ui_snapshot",
            {"include_elements": True},
        )
        ui_payload = _protocol_payload(ui_raw, "query_ui_snapshot")
        ui_fact = _fact(ui_payload)

    warnings = []
    for payload in (runtime_payload, world_payload, inventory_payload, ui_payload):
        warnings.extend(_warnings(payload))
    errors = [error for error in (runtime_error, world_error, inventory_error, ui_error) if error]

    return ObservationFrame(
        runtime=runtime_fact,
        world_region=world_fact,
        inventory=inventory_fact,
        ui=ui_fact,
        warnings=warnings,
        errors=errors,
    )
