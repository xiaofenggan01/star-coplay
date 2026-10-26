"""Common L3 filtering, sorting, aggregation, and ref passthrough helpers."""

from __future__ import annotations

from collections import Counter
from math import sqrt
from typing import Any, Iterable

from .models import JsonDict


def parse_pagination(limit: Any, offset: Any = 0, *, default_limit: int = 20) -> tuple[int, int, str | None]:
    try:
        safe_limit = int(default_limit if limit in (None, "") else limit)
    except (TypeError, ValueError):
        return 0, 0, f"limit 必须是整数，收到 {limit!r}"
    try:
        safe_offset = int(0 if offset in (None, "") else offset)
    except (TypeError, ValueError):
        return 0, 0, f"offset 必须是整数，收到 {offset!r}"
    if safe_limit < 1:
        return 0, 0, f"limit 必须大于等于 1，收到 {safe_limit}"
    if safe_offset < 0:
        return 0, 0, f"offset 必须大于等于 0，收到 {safe_offset}"
    return safe_limit, safe_offset, None


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    """Read a dotted path from nested dicts."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def entity_payload(entity: JsonDict) -> JsonDict:
    """Return the typed oneof payload from a WorldEntityFact-like dict."""
    fact_type = entity.get("type") or entity.get("fact_kind")
    if isinstance(fact_type, str) and isinstance(entity.get(fact_type), dict):
        return entity[fact_type]
    for key in (
        "tree",
        "fruit_tree",
        "crop",
        "resource_node",
        "loose_item",
        "machine",
        "container",
        "bed",
        "furniture",
        "resource_clump",
        "door",
        "warp",
        "npc",
        "monster",
        "farm_animal",
        "pet",
        "horse",
    ):
        value = entity.get(key)
        if isinstance(value, dict):
            return value
    data = entity.get("data")
    return data if isinstance(data, dict) else {}


def ref_value(fact: JsonDict) -> str:
    """Return the opaque ref string without parsing it."""
    ref = fact.get("ref")
    if isinstance(ref, dict):
        value = ref.get("value")
        return value if isinstance(value, str) else ""
    return ref if isinstance(ref, str) else ""


def tile_distance(a: JsonDict | None, b: JsonDict | None) -> float | None:
    if not a or not b:
        return None
    try:
        return sqrt((float(a["x"]) - float(b["x"])) ** 2 + (float(a["y"]) - float(b["y"])) ** 2)
    except (KeyError, TypeError, ValueError):
        return None


def enrich_entity(entity: JsonDict, observer_position: JsonDict | None) -> JsonDict:
    """Return a shallow copy with L3 derived fields."""
    enriched = dict(entity)
    distance = tile_distance(observer_position, entity.get("position"))
    if distance is not None:
        enriched["distance"] = distance
    return enriched


def match_filter(fact: JsonDict, filters: JsonDict | None) -> bool:
    """Evaluate the early-MVP KV predicate subset."""
    if not filters:
        return True
    payload = entity_payload(fact)
    merged = {**payload, **fact}
    for field, expected in filters.items():
        actual = get_path(merged, field)
        if isinstance(expected, dict):
            if "eq" in expected and actual != expected["eq"]:
                return False
            if "in" in expected and actual not in expected["in"]:
                return False
            try:
                if "min" in expected and (actual is None or actual < expected["min"]):
                    return False
                if "max" in expected and (actual is None or actual > expected["max"]):
                    return False
            except TypeError:
                return False
            if "exists" in expected and (actual is not None) is not bool(expected["exists"]):
                return False
        elif actual != expected:
            return False
    return True


def apply_filters(facts: Iterable[JsonDict], filters: JsonDict | None) -> list[JsonDict]:
    return [fact for fact in facts if match_filter(fact, filters)]


def sort_facts(facts: Iterable[JsonDict], sort: str = "distance") -> list[JsonDict]:
    def key_fn(fact: JsonDict) -> tuple[bool, Any]:
        value = get_path(fact, sort)
        if value is None:
            value = get_path(entity_payload(fact), sort)
        return (value is None, value)

    fact_list = list(facts)
    try:
        return sorted(fact_list, key=key_fn)
    except TypeError:
        return sorted(fact_list, key=lambda fact: (key_fn(fact)[0], str(key_fn(fact)[1])))


def count_where(facts: Iterable[JsonDict], filters: JsonDict) -> int:
    return len(apply_filters(facts, filters))


def bucket(facts: Iterable[JsonDict], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for fact in facts:
        payload = entity_payload(fact)
        value = get_path({**payload, **fact}, field)
        key = str(value) if value not in (None, "") else "unknown"
        counts[key] += 1
    return dict(counts)


def representatives(
    facts: Iterable[JsonDict],
    *,
    sort: str = "distance",
    limit: int = 10,
) -> JsonDict:
    ordered = sort_facts(facts, sort)
    refs = [ref for ref in (ref_value(fact) for fact in ordered[:limit]) if ref]
    return {
        "items": refs,
        "sort": sort,
        "limit": limit,
        "has_more": len(ordered) > limit,
    }
