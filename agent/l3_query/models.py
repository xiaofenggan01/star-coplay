"""Shared models for L3 query/view organization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(slots=True)
class ObservationFrame:
    """A current-view fact frame assembled from proto-native L1 queries."""

    runtime: JsonDict | None = None
    world_region: JsonDict | None = None
    inventory: JsonDict | None = None
    ui: JsonDict | None = None
    warnings: list[JsonDict] = field(default_factory=list)
    errors: list[JsonDict] = field(default_factory=list)

    @property
    def has_proto_facts(self) -> bool:
        return any([self.runtime, self.world_region, self.inventory, self.ui])

    @property
    def is_complete_for_current_view(self) -> bool:
        critical = {"query_runtime_snapshot", "query_world_region"}
        return not any(error.get("capability") in critical for error in self.errors)

    @property
    def observer_position(self) -> JsonDict | None:
        if not self.runtime:
            return None
        location = self.runtime.get("player_location") or {}
        tile = location.get("tile") or location.get("position") or location
        if isinstance(tile, dict) and "x" in tile and "y" in tile:
            return tile
        return None

    @property
    def map_id(self) -> str:
        region = self.world_region or {}
        if isinstance(region.get("map_id"), str):
            return region["map_id"]
        if self.runtime:
            location = self.runtime.get("player_location") or {}
            for key in ("map_id", "location", "location_name", "map"):
                value = location.get(key)
                if isinstance(value, str):
                    return value
        return ""


@dataclass(slots=True)
class ViewFragment:
    """A contributor-produced structured view fragment."""

    kind: str
    fact_type: str
    label: str
    priority: int
    count: int
    summary: JsonDict = field(default_factory=dict)
    representatives: JsonDict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> JsonDict:
        return {
            "kind": self.kind,
            "fact_type": self.fact_type,
            "label": self.label,
            "priority": self.priority,
            "count": self.count,
            "summary": self.summary,
            "representatives": self.representatives,
            "description": self.description,
        }
