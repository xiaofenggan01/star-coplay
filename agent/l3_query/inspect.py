"""Inspect helpers backed by proto-native query_inspect_refs."""

from __future__ import annotations

from typing import Any

from .frame import _protocol_payload, _query_optional
from .models import JsonDict
from .query_core import entity_payload, get_path


async def inspect_refs(ctx: Any, refs: list[str], *, include_debug: bool = False) -> JsonDict:
    """Inspect opaque refs by delegating resolution to query_inspect_refs."""
    result, error = await _query_optional(
        ctx,
        "query_inspect_refs",
        {
            "refs": refs,
            "include_debug": include_debug,
        },
    )
    if error:
        return {
            "success": False,
            "status": "failed",
            "description": "inspect 失败：底层 query_inspect_refs 没有返回可靠结果。",
            "data": {
                "items": [],
                "inspected": [],
                "requested_refs": refs,
                "warnings": [],
                "errors": [error],
            },
            "error": error,
        }
    payload = _protocol_payload(result, "query_inspect_refs")
    if not isinstance(payload, dict):
        payload = {}
    inspected = payload.get("inspected")
    inspected_list = inspected if isinstance(inspected, list) else []
    details = [_normalized_detail(item) for item in inspected_list]
    if refs and not inspected_list:
        empty_error = {
            "capability": "query_inspect_refs",
            "code": "INSPECT_EMPTY",
            "message": "query_inspect_refs returned no inspected refs",
            "retryable": True,
            "suggested_next": "重新 observe/search 获取当前视野中的新 ref_id；不要复用旧 ref 或自行构造 ref。",
        }
        return {
            "success": False,
            "status": "failed",
            "description": "inspect 没有返回任何对象；这些 ref 可能已经过期或不在当前可解析范围内。",
            "data": {
                "items": [],
                "inspected": [],
                "requested_refs": refs,
                "warnings": payload.get("warnings", []),
                "errors": [empty_error],
            },
            "error": empty_error,
        }
    return {
        "success": True,
        "status": "ok",
        "description": _describe_inspected(details),
        "data": {
            "items": details,
            "inspected": inspected_list,
            "requested_refs": refs,
            "warnings": payload.get("warnings", []),
            "errors": [],
        },
    }


def _describe_inspected(inspected: list[JsonDict]) -> str:
    if not inspected:
        return "没有返回可 inspect 的对象。"
    resolved = [
        item for item in inspected
        if item.get("resolved")
    ]
    labels = [str(item.get("label") or item.get("type") or item.get("ref_id")) for item in resolved[:3]]
    suffix = f"：{', '.join(labels)}" if labels else ""
    return f"已 inspect {len(inspected)} 个 ref，其中 {len(resolved)} 个成功解析{suffix}。"


def _normalized_detail(item: JsonDict) -> JsonDict:
    resolution = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
    world_entity = item.get("world_entity") if isinstance(item.get("world_entity"), dict) else {}
    status = str(resolution.get("status") or "")
    resolved = status in {"REF_RESOLVED", "REF_RESOLVED_DEGRADED", "resolved"} or status.endswith("RESOLVED")
    ref_id = item.get("ref") or get_path(resolution, "ref.value") or ""
    payload = entity_payload(item) or entity_payload(world_entity)
    merged = {**payload, **world_entity, **item}
    detail: JsonDict = {
        "ref_id": ref_id,
        "resolved": resolved,
        "status": status,
        "type": item.get("type") or world_entity.get("type") or "",
        "fact_kind": item.get("fact_kind") or "",
        "label": _label(merged),
        "summary": item.get("summary") or world_entity.get("summary") or _summary(item, payload),
        "position": item.get("position") or world_entity.get("position"),
    }
    if payload:
        detail["fact"] = payload
    if not resolved:
        detail["debug_reason"] = item.get("debug_reason") or resolution.get("debug_reason")
        detail["suggested_next"] = "重新 observe/search 获取当前视野中的新 ref_id；不要复用旧 ref 或自行构造 ref。"
    return detail


def _label(fact: JsonDict) -> str:
    target_map = fact.get("target_map")
    if isinstance(target_map, str) and target_map:
        return f"出口到 {target_map}"
    for key in ("display_name", "name", "internal_name", "qualified_item_id", "item_id", "type"):
        value = fact.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _summary(item: JsonDict, payload: JsonDict) -> str:
    fact_type = item.get("type") or item.get("fact_kind")
    if fact_type == "container":
        count = payload.get("items_count")
        capacity = payload.get("capacity")
        if count is not None and capacity is not None:
            return f"容器 {count}/{capacity} items"
    if fact_type == "tree":
        moss = payload.get("has_moss")
        stage = payload.get("growth_stage")
        return f"树木 stage={stage}, has_moss={moss}"
    if fact_type == "bed":
        return f"床 can_sleep={payload.get('can_sleep')}, bed_spot={payload.get('bed_spot')}"
    if fact_type == "warp":
        return f"出口到 {payload.get('target_map')} ({payload.get('target_x')},{payload.get('target_y')})"
    if fact_type == "furniture":
        name = payload.get("display_name") or payload.get("internal_name") or "家具"
        return f"{name} type={payload.get('furniture_type')}"
    return ""
