"""View contributors for proto-native L1 facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import JsonDict, ObservationFrame, ViewFragment
from .query_core import bucket, count_where, enrich_entity, representatives
from .schema import FactQuerySchema, get_schema, supported_fact_types


@dataclass(slots=True)
class EntityContributor:
    fact_type: str
    priority: int

    def contribute(self, frame: ObservationFrame) -> ViewFragment | None:
        schema = get_schema(self.fact_type)
        if schema is None:
            return None
        facts = _facts(frame, self.fact_type)
        if not facts:
            return None
        enriched = [enrich_entity(fact, frame.observer_position) for fact in facts]
        summary = _schema_summary(schema, enriched)
        reps = _with_continue_query(representatives(enriched, sort="distance", limit=10), self.fact_type)
        description = _describe_group(schema.label, len(enriched), summary)
        return ViewFragment(
            kind="group",
            fact_type=self.fact_type,
            label=schema.label,
            priority=self.priority,
            count=len(enriched),
            summary=summary,
            representatives=reps,
            description=description,
        )


def _with_continue_query(reps: JsonDict, fact_type: str) -> JsonDict:
    if not reps.get("has_more"):
        return reps
    limit = reps.get("limit")
    if not isinstance(limit, int) or limit < 1:
        return reps
    reps = dict(reps)
    reps["next_query"] = {
        "tool": "search_facts",
        "args": {
            "fact_type": fact_type,
            "sort": reps.get("sort") or "distance",
            "limit": limit,
            "offset": limit,
        },
    }
    return reps


def _schema_summary(schema: FactQuerySchema, facts: list[JsonDict]) -> JsonDict:
    summary: JsonDict = {}
    for name, filters in schema.count_where_specs.items():
        summary[name] = count_where(facts, filters)
    for name, field in schema.bucket_fields.items():
        summary[name] = bucket(facts, field)
    return summary


def _facts(frame: ObservationFrame, fact_type: str) -> list[JsonDict]:
    if fact_type in {"npc", "farm_animal", "pet", "horse", "monster"}:
        return _characters(frame, fact_type)
    region = frame.world_region or {}
    entities = region.get("entities")
    if not isinstance(entities, list):
        return []
    return [entity for entity in entities if entity.get("type") == fact_type]


def _characters(frame: ObservationFrame, char_type: str | None = None) -> list[JsonDict]:
    region = frame.world_region or {}
    characters = region.get("characters")
    if not isinstance(characters, list):
        return []
    if char_type is None:
        return characters
    return [char for char in characters if char.get("type") == char_type]


def _describe_group(name: str, count: int, summary: JsonDict) -> str:
    parts = [f"当前视野内有 {count} 个{name}"]
    if "needs_watering" in summary:
        parts.append(f"{summary['needs_watering']} 个需要浇水")
    if "ready_to_harvest" in summary:
        parts.append(f"{summary['ready_to_harvest']} 个可收获")
    if "dead" in summary:
        parts.append(f"{summary['dead']} 个已枯萎")
    if "has_moss" in summary:
        parts.append(f"{summary['has_moss']} 棵带有苔藓")
    if "ready" in summary:
        parts.append(f"{summary['ready']} 个可收取")
    if "can_sleep" in summary:
        parts.append(f"{summary['can_sleep']} 张可触发睡觉")
    if "being_slept_in" in summary and summary["being_slept_in"]:
        parts.append(f"{summary['being_slept_in']} 张当前被占用")
    if "sittable" in summary:
        parts.append(f"{summary['sittable']} 个可坐")
    if "by_target_map" in summary:
        targets = ", ".join(
            f"{target}×{count}"
            for target, count in sorted(summary["by_target_map"].items())
        )
        if targets:
            parts.append(f"通向 {targets}")
    if len(parts) == 1:
        return parts[0] + "。"
    return parts[0] + "，其中" + "，".join(parts[1:]) + "。"


class CharacterContributor:
    priority = 50

    def contribute(self, frame: ObservationFrame) -> ViewFragment | None:
        facts = _characters(frame)
        if not facts:
            return None
        enriched = [enrich_entity(fact, frame.observer_position) for fact in facts]
        summary = {"by_type": bucket(enriched, "type")}
        reps = representatives(enriched, sort="distance", limit=10)
        return ViewFragment(
            kind="group",
            fact_type="character",
            label="角色",
            priority=self.priority,
            count=len(enriched),
            summary=summary,
            representatives=reps,
            description=f"当前视野内有 {len(enriched)} 个角色。",
        )


_NON_WORLD_FACT_TYPES = {"inventory_item", "ui_element", "exit"}

_DEFAULT_PRIORITIES = {
    "crop": 100,
    "tree": 90,
    "fruit_tree": 85,
    "machine": 80,
    "resource_node": 70,
    "resource_clump": 68,
    "loose_item": 65,
    "container": 60,
    "bed": 59,
    "furniture": 58,
    "door": 57,
    "warp": 56,
    "farm_animal": 55,
    "pet": 54,
    "horse": 53,
    "npc": 50,
    "monster": 45,
}


def _default_contributors() -> tuple[EntityContributor, ...]:
    return tuple(
        EntityContributor(fact_type=fact_type, priority=_DEFAULT_PRIORITIES.get(fact_type, 40))
        for fact_type in supported_fact_types()
        if fact_type not in _NON_WORLD_FACT_TYPES
    )


DEFAULT_CONTRIBUTORS: tuple[object, ...] = _default_contributors()


def collect_fragments(
    frame: ObservationFrame,
    contributors: Iterable[object] = DEFAULT_CONTRIBUTORS,
) -> list[ViewFragment]:
    fragments: list[ViewFragment] = []
    for contributor in contributors:
        contribute = getattr(contributor, "contribute", None)
        if not callable(contribute):
            continue
        fragment = contribute(frame)
        if fragment is not None and fragment.count > 0:
            fragments.append(fragment)
    return sorted(fragments, key=lambda frag: frag.priority, reverse=True)
