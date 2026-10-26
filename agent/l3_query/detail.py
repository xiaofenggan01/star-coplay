"""Category detail views backed by proto-native L1 facts."""

from __future__ import annotations

from typing import Any, Iterable

from .frame import collect_observation_frame
from .models import JsonDict, ObservationFrame
from .query_core import enrich_entity, entity_payload, ref_value, sort_facts


DETAIL_ALIASES: dict[str, str] = {
    "crops": "crops",
    "crop": "crops",
    "trees": "trees",
    "tree": "trees",
    "fruit_trees": "fruit_trees",
    "fruit_tree": "fruit_trees",
    "machines": "machines",
    "machine": "machines",
    "containers": "containers",
    "container": "containers",
    "objects": "objects",
    "object": "objects",
    "exits": "exits",
    "exit": "exits",
    "npcs": "npcs",
    "npc": "npcs",
    "animals": "animals",
    "animal": "animals",
    "inventory": "inventory",
}

DETAIL_LABELS: dict[str, str] = {
    "crops": "庄稼",
    "trees": "树木",
    "fruit_trees": "果树",
    "machines": "机器",
    "containers": "容器",
    "objects": "地图对象",
    "exits": "出口",
    "npcs": "NPC",
    "animals": "动物",
    "inventory": "背包物品",
}

ENTITY_TYPES: dict[str, tuple[str, ...]] = {
    "crops": ("crop",),
    "trees": ("tree",),
    "fruit_trees": ("fruit_tree",),
    "machines": ("machine",),
    "containers": ("container",),
    "objects": ("loose_item", "resource_node", "resource_clump", "bed", "furniture"),
    "exits": ("door", "warp"),
}

CHARACTER_TYPES: dict[str, tuple[str, ...]] = {
    "npcs": ("npc",),
    "animals": ("farm_animal", "pet", "horse"),
}

DETAIL_SEARCH_FACT_TYPES: dict[str, str] = {
    "crops": "crop",
    "trees": "tree",
    "fruit_trees": "fruit_tree",
    "machines": "machine",
    "containers": "container",
    "exits": "exit",
    "npcs": "npc",
    "inventory": "inventory_item",
}


async def observe_detail(ctx: Any, detail: str, *, radius: int = 8, limit: int = 30) -> JsonDict:
    """Return a category detail view from proto-native current-view facts."""
    category = normalize_detail(detail)
    frame = await collect_observation_frame(ctx, radius=radius)
    return render_observation_detail(frame, category, limit=limit)


def normalize_detail(detail: str) -> str:
    return DETAIL_ALIASES.get((detail or "").strip(), "")


def render_observation_detail(frame: ObservationFrame, category: str, *, limit: int = 30) -> JsonDict:
    """Render a supported category detail from an existing ObservationFrame."""
    if not category:
        return {
            "handled": False,
            "has_proto_facts": frame.has_proto_facts,
            "description": "",
            "data": {},
        }

    if category == "inventory":
        items = _inventory_items(frame)
    elif category in ENTITY_TYPES:
        items = _entity_items(frame, ENTITY_TYPES[category])
    elif category in CHARACTER_TYPES:
        items = _character_items(frame, CHARACTER_TYPES[category])
    else:
        return {
            "handled": False,
            "has_proto_facts": frame.has_proto_facts,
            "description": "",
            "data": {},
        }

    sort = _detail_sort(category)
    ordered = sort_facts(items, sort)
    visible_items = ordered[:limit]
    label = DETAIL_LABELS[category]
    next_query = _detail_next_query(category, total=len(ordered), limit=limit)
    description = _describe_detail(
        label,
        visible_items,
        total=len(ordered),
        limit=limit,
        sort_label=_detail_sort_label(category),
        has_next_query=bool(next_query),
    )
    data: JsonDict = {
        "scope": "current_view",
        "detail": category,
        "label": label,
        "map_id": frame.map_id,
        "sort": sort,
        "items": visible_items,
        "total": len(ordered),
        "limit": limit,
        "has_more": len(ordered) > limit,
        "warnings": frame.warnings,
    }
    if next_query:
        data["next_query"] = next_query
    return {
        "handled": True,
        "has_proto_facts": frame.has_proto_facts,
        "description": description,
        "data": data,
    }


def _detail_next_query(category: str, *, total: int, limit: int) -> JsonDict | None:
    if total <= limit:
        return None
    fact_type = DETAIL_SEARCH_FACT_TYPES.get(category)
    if not fact_type:
        return None
    sort = _detail_sort(category)
    return {
        "tool": "search_facts",
        "args": {
            "fact_type": fact_type,
            "sort": sort,
            "limit": limit,
            "offset": limit,
        },
    }


def _detail_sort(category: str) -> str:
    return "slot" if category == "inventory" else "distance"


def _detail_sort_label(category: str) -> str:
    return "槽位" if category == "inventory" else "距离"


def _entity_items(frame: ObservationFrame, fact_types: Iterable[str]) -> list[JsonDict]:
    wanted = set(fact_types)
    region = frame.world_region or {}
    entities = region.get("entities") if isinstance(region.get("entities"), list) else []
    items: list[JsonDict] = []
    for entity in entities:
        if entity.get("type") not in wanted:
            continue
        enriched = enrich_entity(entity, frame.observer_position)
        payload = entity_payload(enriched)
        items.append(_fact_item(enriched, payload))
    return items


def _character_items(frame: ObservationFrame, fact_types: Iterable[str]) -> list[JsonDict]:
    wanted = set(fact_types)
    region = frame.world_region or {}
    characters = region.get("characters") if isinstance(region.get("characters"), list) else []
    items: list[JsonDict] = []
    for character in characters:
        if character.get("type") not in wanted:
            continue
        enriched = enrich_entity(character, frame.observer_position)
        payload = entity_payload(enriched)
        items.append(_fact_item(enriched, payload))
    return items


def _inventory_items(frame: ObservationFrame) -> list[JsonDict]:
    inventory = frame.inventory or {}
    items = inventory.get("items") if isinstance(inventory.get("items"), list) else []
    details: list[JsonDict] = []
    for item in items:
        details.append({
            "ref_id": ref_value(item),
            "type": "inventory_item",
            "label": _label(item),
            "summary": _inventory_summary(item),
            "position": None,
            "distance": None,
            "slot": item.get("slot"),
            "stack": item.get("stack"),
            "quality": item.get("quality"),
            "fact": item,
        })
    return details


def _fact_item(fact: JsonDict, payload: JsonDict) -> JsonDict:
    return {
        "ref_id": ref_value(fact),
        "type": fact.get("type") or fact.get("fact_kind") or "",
        "label": _label({**payload, **fact}),
        "summary": fact.get("summary") or _payload_summary(fact, payload),
        "position": fact.get("position"),
        "distance": fact.get("distance"),
        "fact": payload,
    }


def _label(fact: JsonDict) -> str:
    for key in ("display_name", "name", "internal_name", "qualified_item_id", "item_id"):
        value = fact.get(key)
        if isinstance(value, str) and value:
            return value
    fact_type = fact.get("type") or fact.get("fact_kind")
    return str(fact_type or "unknown")


def _payload_summary(fact: JsonDict, payload: JsonDict) -> str:
    fact_type = fact.get("type")
    if fact_type == "crop":
        if payload.get("dead"):
            return "已枯萎"
        if payload.get("ready_for_harvest"):
            return "可收获"
        if payload.get("watered") is False:
            return "需要浇水"
        return "生长中"
    if fact_type == "tree":
        flags = []
        if payload.get("has_moss"):
            flags.append("有苔藓")
        if payload.get("is_stump"):
            flags.append("树桩")
        return "，".join(flags) if flags else "树木"
    if fact_type == "fruit_tree":
        fruit_count = payload.get("fruit_count")
        if isinstance(fruit_count, int) and fruit_count > 0:
            return f"有 {fruit_count} 个果实"
    if fact_type == "machine":
        if payload.get("ready_for_harvest"):
            return "可收取"
        state = payload.get("machine_state")
        if state:
            return str(state)
    if fact_type in {"door", "warp"}:
        target = payload.get("target_map")
        if target:
            return f"通往 {target}"
    return ""


def _inventory_summary(item: JsonDict) -> str:
    stack = item.get("stack")
    slot = item.get("slot")
    parts = []
    if item.get("is_active") is True:
        parts.append("当前手持")
    if slot is not None:
        parts.append(f"槽位 {slot}")
    if stack is not None:
        parts.append(f"数量 {stack}")
    upgrade_level = item.get("upgrade_level")
    if upgrade_level not in (None, 0):
        parts.append(f"工具等级 {upgrade_level}")
    category_name = item.get("category_name")
    if category_name:
        parts.append(str(category_name))
    return "，".join(parts)


def _describe_detail(
    label: str,
    items: list[JsonDict],
    *,
    total: int,
    limit: int,
    sort_label: str = "距离",
    has_next_query: bool = False,
) -> str:
    if total == 0:
        return f"当前视野内没有可展示的{label}。"
    lines = [f"当前视野内有 {total} 个{label}，按{sort_label}优先展示 {min(total, limit)} 个。"]
    for item in items[:10]:
        ref = item.get("ref_id") or "无 ref"
        item_label = item.get("label") or item.get("type") or "unknown"
        summary = item.get("summary") or ""
        distance = item.get("distance")
        distance_text = f"，距离 {distance:.1f}" if isinstance(distance, (int, float)) else ""
        summary_text = f"，{summary}" if summary else ""
        lines.append(f"- {item_label}{summary_text}{distance_text}：{ref}")
    if total > limit:
        suffix = "可使用 data.next_query 继续分页查询。" if has_next_query else "可通过更具体的查询或 inspect 继续查看。"
        lines.append(f"还有 {total - limit} 个未展示，{suffix}")
    return "\n".join(lines)
