"""Current-view interaction candidates backed by L3 facts."""

from __future__ import annotations

from typing import Any

from .frame import collect_observation_frame
from .models import JsonDict, ObservationFrame
from .query_core import enrich_entity, entity_payload, parse_pagination, ref_value, sort_facts


async def find_interactions(ctx: Any, *, radius: int = 8, limit: int = 20, offset: int = 0) -> JsonDict:
    """Find current-view interaction candidates from proto-native L1 facts."""
    frame = await collect_observation_frame(ctx, radius=radius, include_inventory=True, include_ui=True)
    return render_interactions(frame, radius=radius, limit=limit, offset=offset)


def render_interactions(frame: ObservationFrame, *, radius: int = 8, limit: int = 20, offset: int = 0) -> JsonDict:
    safe_limit, safe_offset, pagination_error = parse_pagination(limit, offset, default_limit=20)
    if pagination_error:
        return _invalid_pagination_result(pagination_error, frame)
    if not frame.is_complete_for_current_view:
        return {
            "success": False,
            "description": "当前视野交互候选不完整：核心 L1 查询失败，不能把无候选当成事实。",
            "data": {
                "scope": "current_view",
                "map_id": frame.map_id,
                "candidates": [],
                "items": [],
                "radius": radius,
                "limit": safe_limit,
                "offset": safe_offset,
                "next_offset": None,
                "has_more": False,
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
    candidates = _collect_candidates(frame)
    sorted_candidates = sort_facts(candidates, "distance")
    ordered = sorted_candidates[safe_offset:safe_offset + safe_limit]
    next_offset = safe_offset + safe_limit if safe_offset + safe_limit < len(sorted_candidates) else None
    description = _describe_candidates(ordered, total=len(candidates), offset=safe_offset)
    next_query = _next_query(radius=radius, limit=safe_limit, next_offset=next_offset)
    data: JsonDict = {
        "scope": "current_view",
        "map_id": frame.map_id,
        "candidates": ordered,
        "items": ordered,
        "sort": "distance",
        "radius": radius,
        "limit": safe_limit,
        "offset": safe_offset,
        "next_offset": next_offset,
        "has_more": next_offset is not None,
        "warnings": frame.warnings,
        "errors": frame.errors,
    }
    if next_query:
        data["next_query"] = next_query
    return {
        "description": description,
        "data": data,
        "has_proto_facts": frame.has_proto_facts,
    }


def _next_query(*, radius: int, limit: int, next_offset: int | None) -> JsonDict | None:
    if next_offset is None:
        return None
    return {
        "tool": "find_interactions",
        "args": {
            "radius": radius,
            "limit": limit,
            "offset": next_offset,
        },
    }


def _invalid_pagination_result(message: str, frame: ObservationFrame) -> JsonDict:
    return {
        "success": False,
        "description": f"交互候选分页参数无效：{message}",
        "data": {
            "scope": "current_view",
            "map_id": frame.map_id,
            "candidates": [],
            "items": [],
            "warnings": frame.warnings,
            "errors": frame.errors,
            "error": {"code": "INVALID_PAGINATION", "message": message},
        },
        "has_proto_facts": frame.has_proto_facts,
    }


def _collect_candidates(frame: ObservationFrame) -> list[JsonDict]:
    region = frame.world_region or {}
    entities = region.get("entities") if isinstance(region.get("entities"), list) else []
    characters = region.get("characters") if isinstance(region.get("characters"), list) else []
    active_item_known = isinstance(frame.inventory, dict)
    inventory_items = _inventory_items(frame.inventory)
    active_item = _active_inventory_item(frame.inventory)
    candidates: list[JsonDict] = []
    for entity in entities:
        enriched = enrich_entity(entity, frame.observer_position)
        candidates.extend(_entity_candidates(enriched, frame.observer_position, active_item, active_item_known, inventory_items))
    for character in characters:
        enriched = enrich_entity(character, frame.observer_position)
        candidates.extend(_character_candidates(enriched, frame.observer_position, active_item, active_item_known, inventory_items))
    ui = frame.ui or {}
    elements = ui.get("elements") if isinstance(ui.get("elements"), list) else []
    for element in elements:
        candidates.extend(_ui_candidates(element))
    return candidates


def _base_candidate(
    fact: JsonDict,
    action: str,
    reason: str,
    observer_position: JsonDict | None,
    *,
    target_tile_override: JsonDict | None = None,
    active_item: JsonDict | None = None,
    active_item_known: bool = False,
    inventory_items: list[JsonDict] | None = None,
) -> JsonDict:
    position = fact.get("position")
    target_tile = target_tile_override or (position if isinstance(position, dict) else None)
    execution_warnings = _execution_warnings(action, fact, active_item, active_item_known, inventory_items)
    candidate = {
        "ref_id": ref_value(fact),
        "fact_type": fact.get("type") or fact.get("fact_kind") or "",
        "action": action,
        "reason": reason,
        "target_tile": target_tile,
        "tool_args": _tool_args(action, target_tile),
        "tool_requirement": _tool_requirement(action, fact, inventory_items),
        "preconditions": _preconditions(action, fact, target_tile, active_item, active_item_known),
        "execution_warnings": execution_warnings,
        "execution_gate": _execution_gate(execution_warnings),
        "recommended_steps": _recommended_steps(action, fact, target_tile, observer_position),
        "distance": fact.get("distance"),
        "summary": fact.get("summary") or "",
    }
    return candidate


def _tool_args(action: str, target_tile: JsonDict | None) -> JsonDict:
    """Return directly usable tool arguments when the candidate maps cleanly.

    These args are advisory and only use objective current-view coordinates.
    They do not encode task intent or parse refs.
    """
    if action not in {"interact", "use_tool", "move_to", "move_to_trigger"} or not target_tile:
        return {}
    args: JsonDict = {}
    for key in ("x", "y"):
        value = target_tile.get(key)
        if value is not None:
            args[key] = value
    if action in {"move_to", "move_to_trigger"} and args:
        args["mode"] = "exact"
    return args if "x" in args and "y" in args else {}


def _preconditions(
    action: str,
    fact: JsonDict,
    target_tile: JsonDict | None,
    active_item: JsonDict | None,
    active_item_known: bool,
) -> list[str]:
    conditions: list[str] = []
    if action in {"interact", "use_tool"}:
        conditions.append("stand_adjacent_or_in_range")
    if action == "interact":
        if not active_item_known:
            conditions.append("active_item_known_safe_for_interact_or_equip_neutral_tool")
        elif _active_item_can_intercept_interact(active_item):
            conditions.append("active_item_safe_for_interact_or_equip_neutral_tool")
    if action == "use_tool":
        tool = _required_tool_category(action, fact)
        if tool:
            conditions.append(f"equip_tool_category:{tool}")
        else:
            conditions.append("equip_appropriate_tool")
    if action == "menu_click":
        conditions.append("menu_open")
    if action == "move_to_trigger":
        conditions.extend(["trigger_tile_known", "sleep_confirmation_required", "do_not_use_interact"])
    if not target_tile and action in {"interact", "use_tool", "move_to", "move_to_trigger"}:
        conditions.append("target_tile_known")
    return conditions


def _execution_warnings(
    action: str,
    fact: JsonDict,
    active_item: JsonDict | None,
    active_item_known: bool,
    inventory_items: list[JsonDict] | None,
) -> list[JsonDict]:
    if action == "interact" and not active_item_known:
        return [
            {
                "code": "ACTIVE_ITEM_UNKNOWN_FOR_INTERACT",
                "message": "当前手持物状态未知；interact 可能被食物、可放置物、礼物等 active item 抢占语义。",
                "suggested_next": "先查询 inventory/active item 或装备不会抢占交互的工具，再执行 interact。",
            }
        ]
    if action == "interact" and _active_item_can_intercept_interact(active_item):
        return [
            {
                "code": "ACTIVE_ITEM_MAY_INTERCEPT_INTERACT",
                "message": (
                    "当前手持物可能抢占 interact 语义；执行前应确认它不会触发吃掉、赠送、放置等动作，"
                    "或先装备不会抢占交互的工具。"
                ),
                "active_item": _active_item_summary(active_item),
                "suggested_next": "先查看/切换当前手持物，再执行 interact；不要把该候选当成无条件可执行步骤。",
            }
        ]
    if action == "use_tool":
        required_tool = _required_tool_category(action, fact)
        if required_tool and not _active_tool_matches_category(active_item, required_tool):
            matching_items = _matching_inventory_tools(inventory_items, required_tool)
            return [
                {
                    "code": "ACTIVE_TOOL_MISMATCH_OR_UNKNOWN",
                    "message": "当前手持工具未确认匹配目标所需工具；直接 use_tool 可能无效或产生错误动作。",
                    "required_tool_category": required_tool,
                    "active_item": _active_item_summary(active_item),
                    "matching_inventory_items": matching_items,
                    "suggested_next": "先装备匹配工具，再执行 use_tool；不要把该候选当成无条件可执行步骤。",
                }
            ]
        if not required_tool:
            return [
                {
                    "code": "APPROPRIATE_TOOL_UNCONFIRMED",
                    "message": "该目标需要使用工具，但 L3 当前无法从 fact 稳定判断具体工具类别。",
                    "active_item": _active_item_summary(active_item),
                    "suggested_next": "先 inspect 目标或确认当前工具适合该目标，再执行 use_tool。",
                }
            ]
    return []


def _execution_gate(execution_warnings: list[JsonDict]) -> JsonDict:
    if not execution_warnings:
        return {
            "status": "ready",
            "recommended_steps_policy": "directly_executable_if_preconditions_hold",
        }
    return {
        "status": "requires_resolution",
        "recommended_steps_policy": "conditional_do_not_execute_as_batch",
        "warning_codes": [
            str(warning.get("code"))
            for warning in execution_warnings
            if warning.get("code")
        ],
        "message": "先处理 execution_warnings，再考虑执行 recommended_steps。",
    }


def _recommended_steps(
    action: str,
    fact: JsonDict,
    target_tile: JsonDict | None,
    observer_position: JsonDict | None,
) -> list[JsonDict]:
    steps: list[JsonDict] = []
    if action in {"interact", "use_tool"} and target_tile:
        move_args = _adjacent_move_args(target_tile, observer_position)
        steps.append({
            "tool": "move_to",
            "args": move_args,
            "purpose": "stand_adjacent_to_target",
        })
    if action == "move_to_trigger" and target_tile:
        args = _tool_args(action, target_tile)
        if args:
            steps.append({
                "tool": "move_to",
                "args": args,
                "purpose": "enter_trigger_tile",
            })
    if action == "use_tool":
        payload = entity_payload(fact)
        tool_name = payload.get("required_tool_item_name") or payload.get("required_item_name")
        if tool_name:
            steps.append({"tool": "equip", "args": {"item_name": str(tool_name)}})
    args = _tool_args(action, target_tile)
    if args and action != "move_to_trigger":
        steps.append({"tool": action, "args": args})
    return steps


def _adjacent_move_args(
    target_tile: JsonDict,
    observer_position: JsonDict | None,
) -> JsonDict:
    args: JsonDict = {
        "mode": "adjacent",
        "x": target_tile.get("x"),
        "y": target_tile.get("y"),
    }
    side = _nearest_stand_side(target_tile, observer_position)
    if side:
        args["adjacent_side"] = side
    return args


def _tool_requirement(
    action: str,
    fact: JsonDict,
    inventory_items: list[JsonDict] | None = None,
) -> JsonDict:
    if action != "use_tool":
        return {}
    payload = entity_payload(fact)
    tool_name = payload.get("required_tool_item_name") or payload.get("required_item_name")
    if tool_name:
        return {
            "kind": "exact_item_name",
            "item_name": str(tool_name),
            "equip_tool_args": {"item_name": str(tool_name)},
        }
    tool_category = _required_tool_category(action, fact)
    if tool_category:
        requirement = {
            "kind": "tool_category",
            "category": str(tool_category),
            "note": "这是工具类别，不是 equip(item_name=...) 的精确物品名；需要从背包/工具栏确认具体工具名或使用更高层批量能力。",
        }
        matching_items = _matching_inventory_tools(inventory_items, str(tool_category))
        if matching_items:
            requirement["matching_inventory_items"] = matching_items
        return requirement
    return {"kind": "unknown", "note": "需要先确认当前装备是否适合该目标。"}


def _required_tool_category(action: str, fact: JsonDict) -> str | None:
    if action != "use_tool":
        return None
    payload = entity_payload(fact)
    tool = payload.get("required_tool")
    if tool:
        return str(tool)
    fact_type = fact.get("type") or fact.get("fact_kind")
    if fact_type == "crop":
        if payload.get("ready_for_harvest") and payload.get("harvest_method") == "scythe":
            return "scythe"
        if payload.get("watered") is False:
            return "watering_can"
    return None


def _active_inventory_item(inventory: JsonDict | None) -> JsonDict | None:
    for item in _inventory_items(inventory):
        if item.get("is_active") is True:
            return item
    return None


def _inventory_items(inventory: JsonDict | None) -> list[JsonDict]:
    if not isinstance(inventory, dict):
        return []
    items = inventory.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _active_item_can_intercept_interact(active_item: JsonDict | None) -> bool:
    if not isinstance(active_item, dict):
        return False
    category = active_item.get("category")
    # Stardew tools use category -99. Tools are normally safe as active items for X/interact.
    if category == -99:
        return False
    item_id = active_item.get("item_id") or active_item.get("qualified_item_id")
    return bool(item_id or active_item.get("display_name") or active_item.get("internal_name"))


def _active_tool_matches_category(active_item: JsonDict | None, required_tool: str) -> bool:
    if not isinstance(active_item, dict):
        return False
    if active_item.get("category") != -99:
        return False
    required_category = _canonical_tool_category(required_tool)
    active_category = _active_tool_category(active_item)
    return bool(required_category and active_category and required_category == active_category)


def _matching_inventory_tools(
    inventory_items: list[JsonDict] | None,
    required_tool: str,
) -> list[JsonDict]:
    if not inventory_items:
        return []
    matches: list[JsonDict] = []
    for item in inventory_items:
        if _active_tool_matches_category(item, required_tool):
            matches.append(_tool_option_summary(item))
    return matches


def _tool_option_summary(item: JsonDict) -> JsonDict:
    summary = _active_item_summary(item)
    equip_args: JsonDict = {}
    slot = item.get("slot")
    if slot is not None:
        equip_args["slot"] = slot
    elif item.get("internal_name"):
        equip_args["item_name"] = item["internal_name"]
    elif item.get("display_name"):
        equip_args["item_name"] = item["display_name"]
    if equip_args:
        summary["equip_args"] = equip_args
    return summary


def _active_tool_category(active_item: JsonDict) -> str | None:
    tokens = [
        active_item.get("item_id"),
        active_item.get("qualified_item_id"),
        active_item.get("internal_name"),
        active_item.get("display_name"),
    ]
    normalized = [_normalize_tool_token(str(token)) for token in tokens if token]
    joined = " ".join(normalized)
    if "wateringcan" in joined:
        return "watering_can"
    if "pickaxe" in joined:
        return "pickaxe"
    if "scythe" in joined:
        return "scythe"
    if "hoe" in joined:
        return "hoe"
    if "fishingrod" in joined or "bamboopole" in joined:
        return "fishing_rod"
    if any(token.endswith("axe") for token in normalized):
        return "axe"
    return None


def _canonical_tool_category(value: str) -> str:
    token = _normalize_tool_token(value)
    aliases = {
        "wateringcan": "watering_can",
        "water": "watering_can",
        "pickaxe": "pickaxe",
        "axe": "axe",
        "hoe": "hoe",
        "scythe": "scythe",
        "fishingrod": "fishing_rod",
        "rod": "fishing_rod",
    }
    return aliases.get(token, token)


def _normalize_tool_token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _active_item_summary(active_item: JsonDict | None) -> JsonDict:
    if not isinstance(active_item, dict):
        return {}
    summary: JsonDict = {}
    for key in (
        "ref",
        "slot",
        "qualified_item_id",
        "item_id",
        "internal_name",
        "display_name",
        "category",
        "category_name",
    ):
        if key in active_item:
            summary[key] = active_item[key]
    return summary


def _bed_trigger_tile(fact: JsonDict) -> JsonDict | None:
    payload = entity_payload(fact)
    bed_spot = payload.get("bed_spot")
    if isinstance(bed_spot, dict) and "x" in bed_spot and "y" in bed_spot:
        position = fact.get("position")
        tile = dict(bed_spot)
        if isinstance(position, dict) and position.get("map_id") and not tile.get("map_id"):
            tile["map_id"] = position["map_id"]
        return tile
    return None


def _nearest_stand_side(
    target_tile: JsonDict,
    observer_position: JsonDict | None,
) -> str:
    if not observer_position:
        return "down"
    try:
        dx = float(observer_position["x"]) - float(target_tile["x"])
        dy = float(observer_position["y"]) - float(target_tile["y"])
    except (KeyError, TypeError, ValueError):
        return "down"
    if abs(dx) >= abs(dy):
        return "left" if dx < 0 else "right"
    return "up" if dy < 0 else "down"


def _entity_candidates(
    entity: JsonDict,
    observer_position: JsonDict | None,
    active_item: JsonDict | None,
    active_item_known: bool,
    inventory_items: list[JsonDict] | None,
) -> list[JsonDict]:
    payload = entity_payload(entity)
    fact_type = entity.get("type")
    if fact_type == "crop":
        if payload.get("dead"):
            return []
        if payload.get("ready_for_harvest"):
            method = payload.get("harvest_method") or "grab"
            action = "use_tool" if method == "scythe" else "interact"
            return [_base_candidate(entity, action, "作物已经成熟，可以收获", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
        if payload.get("watered") is False:
            return [_base_candidate(entity, "use_tool", "作物尚未浇水", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type == "machine" and payload.get("ready_for_harvest"):
        return [_base_candidate(entity, "interact", "机器产物已经完成，可以收取", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type == "container":
        return [_base_candidate(entity, "interact", "容器可打开查看", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type == "bed" and payload.get("can_sleep", True):
        return [
            _base_candidate(
                entity,
                "move_to_trigger",
                "床需要走到可睡眠触发格来打开睡觉确认，不应作为普通 interact 目标处理",
                observer_position,
                target_tile_override=_bed_trigger_tile(entity),
                active_item=active_item,
                active_item_known=active_item_known,
                inventory_items=inventory_items,
            )
        ]
    if fact_type == "door":
        return [_base_candidate(entity, "interact", "门可交互", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type == "warp":
        return [_base_candidate(entity, "move_to", "可通过此处前往其他区域", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type in {"tree", "fruit_tree"}:
        if payload.get("fruit_count", 0) > 0:
            return [_base_candidate(entity, "interact", "果树有果实可采摘", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
        if payload.get("has_moss"):
            return [_base_candidate(entity, "use_tool", "树上有苔藓，可进一步处理", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type in {"resource_node", "resource_clump"}:
        tool = payload.get("required_tool")
        reason = f"资源点可用 {tool} 清理" if tool else "资源点可清理"
        return [_base_candidate(entity, "use_tool", reason, observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type == "loose_item" and payload.get("can_be_grabbed", True):
        return [_base_candidate(entity, "interact", "地面物品可拾取", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    return []


def _character_candidates(
    character: JsonDict,
    observer_position: JsonDict | None,
    active_item: JsonDict | None,
    active_item_known: bool,
    inventory_items: list[JsonDict] | None,
) -> list[JsonDict]:
    payload = entity_payload(character)
    fact_type = character.get("type")
    if fact_type == "npc":
        if payload.get("can_socialize", True):
            return [_base_candidate(character, "interact", "NPC 可社交或对话", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
        return []
    if fact_type == "farm_animal":
        if payload.get("was_petted") is False:
            return [_base_candidate(character, "interact", "动物今天尚未抚摸", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    if fact_type == "pet":
        if payload.get("petted_today") is False:
            return [_base_candidate(character, "interact", "宠物今天尚未抚摸", observer_position, active_item=active_item, active_item_known=active_item_known, inventory_items=inventory_items)]
    return []


def _ui_candidates(element: JsonDict) -> list[JsonDict]:
    if not element.get("visible", True) or not element.get("enabled", True):
        return []
    return [{
        "ref_id": ref_value(element),
        "fact_type": element.get("type") or "ui_element",
        "action": "menu_click",
        "reason": "当前 UI 元素可点击",
        "target_tile": None,
        "tool_args": _ui_tool_args(element),
        "tool_requirement": {},
        "preconditions": ["menu_open"],
        "execution_warnings": [],
        "execution_gate": _execution_gate([]),
        "recommended_steps": _ui_recommended_steps(element),
        "distance": None,
        "summary": element.get("label") or "",
    }]


def _ui_tool_args(element: JsonDict) -> JsonDict:
    component_id = element.get("component_id") or element.get("id")
    component_source = element.get("component_source") or element.get("source")
    if component_id is None or not component_source:
        return {}
    return {"component_id": component_id, "component_source": component_source}


def _ui_recommended_steps(element: JsonDict) -> list[JsonDict]:
    args = _ui_tool_args(element)
    if not args:
        return []
    return [{"tool": "menu_click", "args": args}]


def _describe_candidates(candidates: list[JsonDict], *, total: int, offset: int = 0) -> str:
    if not candidates:
        if total:
            return f"当前视野内有 {total} 个交互候选，但 offset={offset} 未返回候选；请使用较小 offset 或重新查询第一页。"
        return "当前视野内没有明显的交互候选。"
    by_action: dict[str, int] = {}
    by_gate: dict[str, int] = {}
    for candidate in candidates:
        action = str(candidate.get("action") or "unknown")
        by_action[action] = by_action.get(action, 0) + 1
        gate = candidate.get("execution_gate")
        gate_status = "unknown"
        if isinstance(gate, dict):
            gate_status = str(gate.get("status") or "unknown")
        by_gate[gate_status] = by_gate.get(gate_status, 0) + 1
    action_summary = "，".join(f"{action} {count} 个" for action, count in sorted(by_action.items()))
    gate_summary = "，".join(f"{status} {count} 个" for status, count in sorted(by_gate.items()))
    summary = f"{action_summary}；执行门控：{gate_summary}"
    if len(candidates) < total:
        page_start = offset + 1
        page_end = offset + len(candidates)
        return f"当前视野内有 {total} 个交互候选，本页按距离展示第 {page_start}-{page_end} 个：{summary}。"
    return f"当前视野内有 {total} 个交互候选：{summary}。"
