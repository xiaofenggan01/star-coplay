"""Observe orchestration backed by L3 Query Core and View Contributors."""

from __future__ import annotations

from typing import Any

from .contributors import collect_fragments
from .frame import collect_observation_frame
from .models import JsonDict, ObservationFrame, ViewFragment


async def observe_current_view(ctx: Any, *, radius: int = 8) -> JsonDict:
    """Return current-view description/data from proto-native L1 facts."""
    frame = await collect_observation_frame(ctx, radius=radius)
    return render_observation_frame(frame)


def render_observation_frame(frame: ObservationFrame) -> JsonDict:
    if not frame.is_complete_for_current_view:
        return {
            "success": False,
            "status": "failed",
            "description": "当前视野观察不完整：核心 L1 查询失败，不能把空结果当成环境事实。",
            "data": _base_data(frame, []),
            "has_proto_facts": frame.has_proto_facts,
        }
    fragments = collect_fragments(frame)
    description = _description(frame, fragments)
    return {
        "success": True,
        "status": "ok",
        "description": description,
        "data": _base_data(frame, fragments),
        "has_proto_facts": frame.has_proto_facts,
    }


def _base_data(frame: ObservationFrame, fragments: list[ViewFragment]) -> JsonDict:
    return {
        "scope": "current_view",
        "map_id": frame.map_id,
        "observer": {
            "position": frame.observer_position,
        },
        "groups": [fragment.to_dict() for fragment in fragments],
        "warnings": frame.warnings,
        "errors": frame.errors,
        "completeness": {
            "runtime": bool(frame.runtime),
            "world_region": bool(frame.world_region),
            "inventory": bool(frame.inventory),
            "ui": bool(frame.ui),
            "current_view_complete": frame.is_complete_for_current_view,
        },
    }


def _description(frame: ObservationFrame, fragments: list[ViewFragment]) -> str:
    prefix_parts: list[str] = []
    if frame.map_id:
        prefix_parts.append(f"当前位置：{frame.map_id}")
    if frame.runtime:
        time_of_day = frame.runtime.get("time_of_day")
        season = frame.runtime.get("season")
        day = frame.runtime.get("day_of_month")
        if time_of_day or season or day:
            prefix_parts.append(f"时间：{season or ''} {day or ''} {time_of_day or ''}".strip())

    lines = []
    if prefix_parts:
        lines.append(" | ".join(prefix_parts))
    lines.extend(fragment.description for fragment in fragments if fragment.description)
    if not fragments:
        lines.append("当前视野内没有可聚合的 L1 Fact。")
    return "\n".join(lines)
