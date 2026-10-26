"""L3 current-view fact search backed by schema-checked query core."""

from __future__ import annotations

from typing import Any

from .frame import collect_observation_frame
from .models import JsonDict, ObservationFrame
from .query_core import apply_filters, enrich_entity, entity_payload, parse_pagination, ref_value, sort_facts
from .schema import (
    get_schema,
    normalize_fact_type,
    schema_operator_map,
    supported_fact_types,
    validate_filters,
    validate_sort,
)


async def search_current_view(
    ctx: Any,
    *,
    fact_type: str,
    filters: JsonDict | None = None,
    sort: str = "distance",
    limit: int = 20,
    offset: int = 0,
    radius: int = 8,
) -> JsonDict:
    frame = await collect_observation_frame(ctx, radius=radius)
    return render_search(frame, fact_type=fact_type, filters=filters, sort=sort, limit=limit, offset=offset, radius=radius)


def render_search(
    frame: ObservationFrame,
    *,
    fact_type: str,
    filters: JsonDict | None = None,
    sort: str | None = None,
    limit: int = 20,
    offset: int = 0,
    radius: int = 8,
) -> JsonDict:
    safe_limit, safe_offset, pagination_error = parse_pagination(limit, offset, default_limit=20)
    if pagination_error:
        return _invalid_pagination_result(pagination_error, frame)
    if not frame.is_complete_for_current_view:
        return {
            "success": False,
            "description": "当前视野查询不完整：核心 L1 查询失败，不能返回空匹配作为事实。",
            "data": {
                "scope": "current_view",
                "items": [],
                "total": 0,
                "radius": radius,
                "limit": safe_limit,
                "offset": safe_offset,
                "next_offset": None,
                "warnings": frame.warnings,
                "errors": frame.errors,
                "completeness": {
                    "runtime": bool(frame.runtime),
                    "world_region": bool(frame.world_region),
                    "current_view_complete": False,
                },
            },
            "has_proto_facts": frame.has_proto_facts,
        }
    normalized_type = normalize_fact_type(fact_type)
    schema = get_schema(normalized_type)
    if schema is None:
        return _invalid_result(
            f"未知 fact_type：{fact_type!r}",
            frame,
            suggested_fact_type=_suggest_fact_type(fact_type),
        )

    effective_sort = sort or schema.default_sort
    errors = validate_filters(schema, filters) + validate_sort(schema, effective_sort)
    if errors:
        return _invalid_result("；".join(errors), frame, schema=schema)

    facts = _facts_for_type(frame, schema.fact_type)
    filtered = apply_filters(facts, filters)
    ordered = sort_facts(filtered, effective_sort.removeprefix("-"))
    if effective_sort.startswith("-"):
        ordered.reverse()
    visible = ordered[safe_offset:safe_offset + safe_limit]
    items = [_search_item(fact) for fact in visible]
    next_offset = safe_offset + safe_limit if safe_offset + safe_limit < len(filtered) else None
    next_query = _next_query(
        fact_type=schema.fact_type,
        filters=filters or {},
        sort=effective_sort,
        radius=radius,
        limit=safe_limit,
        next_offset=next_offset,
    )
    data: JsonDict = {
        "scope": "current_view",
        "fact_type": schema.fact_type,
        "label": schema.label,
        "filters": filters or {},
        "sort": effective_sort,
        "items": items,
        "total": len(filtered),
        "radius": radius,
        "limit": safe_limit,
        "offset": safe_offset,
        "next_offset": next_offset,
        "has_more": next_offset is not None,
        "schema": _schema_payload(schema),
        "warnings": frame.warnings,
    }
    if next_query:
        data["next_query"] = next_query
    return {
        "success": True,
        "description": _describe_search(
            schema.label,
            total=len(filtered),
            shown=len(items),
            filters=filters,
            offset=safe_offset,
            sort=effective_sort,
        ),
        "data": data,
        "has_proto_facts": frame.has_proto_facts,
    }


def _next_query(
    *,
    fact_type: str,
    filters: JsonDict,
    sort: str,
    radius: int,
    limit: int,
    next_offset: int | None,
) -> JsonDict | None:
    if next_offset is None:
        return None
    args: JsonDict = {
        "fact_type": fact_type,
        "sort": sort,
        "limit": limit,
        "offset": next_offset,
        "radius": radius,
    }
    if filters:
        args["filters"] = filters
    return {"tool": "search_facts", "args": args}


def _facts_for_type(frame: ObservationFrame, fact_type: str) -> list[JsonDict]:
    if fact_type == "inventory_item":
        inventory = frame.inventory or {}
        items = inventory.get("items") if isinstance(inventory.get("items"), list) else []
        return [dict(item, type="inventory_item", fact_kind="inventory_item") for item in items]
    if fact_type == "ui_element":
        ui = frame.ui or {}
        elements = ui.get("elements") if isinstance(ui.get("elements"), list) else []
        return [dict(element, type="ui_element", fact_kind="ui_element") for element in elements]

    region = frame.world_region or {}
    if fact_type in {"npc", "farm_animal", "pet", "horse", "monster"}:
        characters = region.get("characters") if isinstance(region.get("characters"), list) else []
        return [
            enrich_entity(character, frame.observer_position)
            for character in characters
            if character.get("type") == fact_type
        ]

    entities = region.get("entities") if isinstance(region.get("entities"), list) else []
    if fact_type == "exit":
        return [
            enrich_entity(entity, frame.observer_position)
            for entity in entities
            if entity.get("type") in {"door", "warp"}
        ]
    return [
        enrich_entity(entity, frame.observer_position)
        for entity in entities
        if entity.get("type") == fact_type
    ]


def _search_item(fact: JsonDict) -> JsonDict:
    payload = entity_payload(fact)
    merged = {**payload, **fact}
    return {
        "ref_id": ref_value(fact),
        "type": fact.get("type") or fact.get("fact_kind") or "",
        "label": _label(merged),
        "summary": fact.get("summary") or _summary(merged),
        "position": fact.get("position"),
        "distance": fact.get("distance"),
        "fact": payload or fact,
    }


def _label(fact: JsonDict) -> str:
    target_map = fact.get("target_map")
    if isinstance(target_map, str) and target_map:
        return f"出口到 {target_map}"
    for key in ("display_name", "name", "internal_name", "qualified_item_id", "item_id"):
        value = fact.get(key)
        if isinstance(value, str) and value:
            return value
    return str(fact.get("type") or fact.get("fact_kind") or "unknown")


def _summary(fact: JsonDict) -> str:
    fact_type = fact.get("type") or fact.get("fact_kind")
    if fact_type == "inventory_item":
        parts = []
        if fact.get("is_active") is True:
            parts.append("当前手持")
        slot = fact.get("slot")
        if slot is not None:
            parts.append(f"槽位 {slot}")
        stack = fact.get("stack")
        if stack is not None:
            parts.append(f"数量 {stack}")
        upgrade_level = fact.get("upgrade_level")
        if upgrade_level not in (None, 0):
            parts.append(f"工具等级 {upgrade_level}")
        category_name = fact.get("category_name")
        if category_name:
            parts.append(str(category_name))
        return "，".join(parts)
    if fact_type == "warp":
        target = fact.get("target_map")
        target_x = fact.get("target_x")
        target_y = fact.get("target_y")
        if target:
            suffix = f" ({target_x},{target_y})" if target_x is not None and target_y is not None else ""
            return f"通往 {target}{suffix}"
    return ""


def _invalid_result(
    message: str,
    frame: ObservationFrame,
    *,
    schema: object | None = None,
    suggested_fact_type: str | None = None,
) -> JsonDict:
    data: JsonDict = {
        "scope": "current_view",
        "items": [],
        "total": 0,
        "warnings": frame.warnings,
        "errors": frame.errors,
        "error": {"code": "INVALID_QUERY", "message": message},
        "supported_fact_types": supported_fact_types(),
    }
    if schema is not None:
        data["schema"] = _schema_payload(schema)
    if suggested_fact_type:
        data["suggested_fact_type"] = suggested_fact_type
    return {
        "success": False,
        "description": f"查询条件无效：{message}",
        "data": data,
        "has_proto_facts": frame.has_proto_facts,
    }


def _invalid_pagination_result(message: str, frame: ObservationFrame) -> JsonDict:
    return {
        "success": False,
        "description": f"分页参数无效：{message}",
        "data": {
            "scope": "current_view",
            "items": [],
            "total": 0,
            "warnings": frame.warnings,
            "errors": frame.errors,
            "error": {"code": "INVALID_PAGINATION", "message": message},
        },
        "has_proto_facts": frame.has_proto_facts,
    }


def _schema_payload(schema: object) -> JsonDict:
    return {
        "fact_type": schema.fact_type,
        "label": schema.label,
        "default_sort": schema.default_sort,
        "filter_fields": schema.filter_fields(),
        "sort_fields": schema.sort_fields(),
        "aggregate_fields": schema.aggregate_fields(),
        "operators": schema_operator_map(schema),
    }


def _suggest_fact_type(fact_type: str) -> str | None:
    normalized = normalize_fact_type(fact_type)
    if normalized in supported_fact_types():
        return normalized
    raw = (fact_type or "").strip()
    candidates = supported_fact_types()
    for candidate in candidates:
        if raw and (candidate.startswith(raw) or raw.startswith(candidate)):
            return candidate
    return None


def _describe_search(label: str, *, total: int, shown: int, filters: JsonDict | None, offset: int = 0, sort: str = "distance") -> str:
    filter_text = "，已应用 filter" if filters else ""
    if shown == 0 and total:
        return f"当前视野内匹配到 {total} 个{label}{filter_text}，但 offset={offset} 未返回结果；请使用较小 offset 或重新查询第一页。"
    if shown < total:
        page_start = offset + 1
        page_end = offset + shown
        return f"当前视野内匹配到 {total} 个{label}{filter_text}，本页按 {sort} 展示第 {page_start}-{page_end} 个。"
    return f"当前视野内匹配到 {total} 个{label}{filter_text}，返回 {shown} 个。"
