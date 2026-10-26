"""Map LLM tool arguments to Protocol 2.4.5 params messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from google.protobuf.message import Message

from agent.generated.protocol import capability_params_pb2 as params_pb2
from agent.generated.protocol import common_pb2
from agent.generated.protocol import query_pb2
from agent.generated.protocol import ref_pb2

from .capability_registry import (
    ARRIVAL_MODE_ENUM,
    CHARGE_LEVEL_MAX,
    CHARGE_LEVEL_MIN,
    DIRECTION_ENUM,
    EMOTE_ENUM,
    EQUIP_SLOT_MAX,
    EQUIP_SLOT_MIN,
    MENU_SOURCE_ENUM,
    MOVE_TO_MODE_ENUM,
    CapabilitySpec,
    get_spec,
)


class ProtocolMappingError(ValueError):
    """Raised when LLM-provided tool args cannot form a valid protocol command."""


@dataclass(frozen=True)
class MappedCommand:
    tool_name: str
    capability: str
    oneof_field: str
    params: Message
    timeout_ms: int


def map_tool_call(tool_name: str, args: Mapping[str, Any] | None) -> list[MappedCommand]:
    """Map one LLM tool call to one or more protocol params messages."""

    try:
        spec = get_spec(tool_name)
    except KeyError as exc:
        raise ProtocolMappingError(f"unsupported protocol tool: {tool_name}") from exc
    if spec.internal_only or not spec.exposed_to_llm:
        raise ProtocolMappingError(f"tool is not exposed to LLM: {tool_name}")
    normalized_args = _normalize_args(args)
    return _map_by_tool(spec, normalized_args)


def _map_by_tool(spec: CapabilitySpec, args: Mapping[str, Any]) -> list[MappedCommand]:
    tool = spec.tool_name
    if tool == "say":
        return _map_say(spec, args)
    if tool == "emote":
        return [
            _mapped(
                spec,
                params_pb2.EmoteParams(
                    emote_type=_required_enum(args, "emote_type", EMOTE_ENUM)
                ),
            )
        ]
    if tool == "face":
        return [
            _mapped(
                spec,
                params_pb2.FaceParams(
                    direction=_required_enum(args, "direction", DIRECTION_ENUM)
                ),
            )
        ]
    if tool == "move_to":
        return [_map_move_to(spec, args)]
    if tool == "go_to":
        return [_map_go_to(spec, args)]
    if tool == "go_to_bed":
        return [_map_go_to_bed(spec, args)]
    if tool == "interact":
        _reject_unknown(args, {"x", "y"})
        return [_mapped(spec, params_pb2.InteractParams(target_tile=_tile_point(args)))]
    if tool == "use_tool":
        return [_map_use_tool(spec, args)]
    if tool == "equip":
        return [_map_equip(spec, args)]
    if tool == "query_inventory":
        _reject_unknown(args, {"include_details"})
        return [
            _mapped(
                spec,
                params_pb2.QueryInventoryParams(
                    include_details=_optional_bool(args, "include_details", False)
                ),
            )
        ]
    if tool == "query_location":
        _reject_unknown(args, set())
        return [_mapped(spec, params_pb2.QueryLocationParams())]
    if tool == "query_map_exits":
        _reject_unknown(args, set())
        return [_mapped(spec, params_pb2.QueryMapExitsParams())]
    if tool == "query_runtime_snapshot":
        return [_map_query_runtime_snapshot(spec, args)]
    if tool == "query_world_region":
        return [_map_query_world_region(spec, args)]
    if tool == "query_inventory_snapshot":
        return [_map_query_inventory_snapshot(spec, args)]
    if tool == "query_ui_snapshot":
        return [_map_query_ui_snapshot(spec, args)]
    if tool == "query_inspect_refs":
        return [_map_query_inspect_refs(spec, args)]
    if tool == "open_menu":
        _reject_unknown(args, set())
        return [_mapped(spec, params_pb2.OpenMenuParams())]
    if tool == "query_menu":
        _reject_unknown(args, set())
        return [_mapped(spec, params_pb2.QueryMenuParams())]
    if tool == "menu_click":
        _reject_unknown(args, {"component_id", "component_source"})
        return [
            _mapped(
                spec,
                params_pb2.MenuClickParams(
                    component_id=_required_int(args, "component_id"),
                    source=_required_enum(args, "component_source", MENU_SOURCE_ENUM),
                ),
            )
        ]
    if tool == "menu_close":
        _reject_unknown(args, set())
        return [_mapped(spec, params_pb2.MenuCloseParams())]
    raise ProtocolMappingError(f"mapper missing for protocol tool: {tool}")


def _map_say(spec: CapabilitySpec, args: Mapping[str, Any]) -> list[MappedCommand]:
    _reject_unknown(args, {"message", "emote"})
    message = _required_str(args, "message")
    commands = [_mapped(spec, params_pb2.SayParams(content=message))]
    if "emote" in args and args["emote"] is not None:
        emote_spec = get_spec("emote")
        commands.append(
            _mapped(
                emote_spec,
                params_pb2.EmoteParams(
                    emote_type=_enum_value(args["emote"], "emote", EMOTE_ENUM)
                ),
            )
        )
    return commands


def _map_move_to(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(
        args,
        {"mode", "x", "y", "direction", "face_direction", "adjacent_side", "timeout_frames"},
    )
    mode = _required_enum(args, "mode", MOVE_TO_MODE_ENUM)
    params = params_pb2.MoveToParams(
        mode=mode,
        x=_required_int(args, "x"),
        y=_required_int(args, "y"),
    )
    if mode == params_pb2.MOVE_TO_EDGE_WARP:
        params.direction = _required_enum(args, "direction", DIRECTION_ENUM)
        _reject_present(args, {"face_direction", "adjacent_side"}, "move_to mode edge_warp")
    elif "direction" in args and args["direction"] is not None:
        raise ProtocolMappingError("direction is only valid for move_to mode edge_warp")
    if mode == params_pb2.MOVE_TO_ADJACENT:
        params.adjacent_side = _required_enum(args, "adjacent_side", DIRECTION_ENUM)
        _reject_present(args, {"face_direction"}, "move_to mode adjacent")
    elif "adjacent_side" in args and args["adjacent_side"] is not None:
        raise ProtocolMappingError("adjacent_side is only valid for move_to mode adjacent")
    if "face_direction" in args and args["face_direction"] is not None:
        if mode != params_pb2.MOVE_TO_EXACT:
            raise ProtocolMappingError("face_direction is only valid for move_to mode exact")
        params.face_direction = _enum_value(
            args["face_direction"], "face_direction", DIRECTION_ENUM
        )
    if "timeout_frames" in args and args["timeout_frames"] is not None:
        params.timeout_frames = _positive_int(args, "timeout_frames")
    return _mapped(spec, params)


def _map_go_to(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(
        args,
        {"map_id", "x", "y", "arrival_mode", "stand_side", "face_direction", "timeout_frames"},
    )
    params = params_pb2.GoToParams(
        map_id=_required_str(args, "map_id"),
        x=_required_int(args, "x"),
        y=_required_int(args, "y"),
        arrival_mode=_required_enum(args, "arrival_mode", ARRIVAL_MODE_ENUM),
    )
    if params.arrival_mode == params_pb2.ARRIVAL_ADJACENT:
        params.stand_side = _required_enum(args, "stand_side", DIRECTION_ENUM)
        _reject_present(args, {"face_direction"}, "go_to arrival_mode adjacent")
    elif "stand_side" in args and args["stand_side"] is not None:
        raise ProtocolMappingError("stand_side is only valid for go_to arrival_mode adjacent")
    if "face_direction" in args and args["face_direction"] is not None:
        if params.arrival_mode != params_pb2.ARRIVAL_EXACT:
            raise ProtocolMappingError("face_direction is only valid for go_to arrival_mode exact")
        params.face_direction = _enum_value(
            args["face_direction"], "face_direction", DIRECTION_ENUM
        )
    if "timeout_frames" in args and args["timeout_frames"] is not None:
        params.timeout_frames = _positive_int(args, "timeout_frames")
    return _mapped(spec, params)


def _map_go_to_bed(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(args, {"timeout_frames"})
    params = params_pb2.GoToBedParams()
    if "timeout_frames" in args and args["timeout_frames"] is not None:
        params.timeout_frames = _positive_int(args, "timeout_frames")
    return _mapped(spec, params)


def _map_use_tool(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(args, {"x", "y", "charge_level"})
    params = params_pb2.UseToolParams(target_tile=_tile_point(args))
    charge_level = _optional_int(args, "charge_level", CHARGE_LEVEL_MIN)
    if not CHARGE_LEVEL_MIN <= charge_level <= CHARGE_LEVEL_MAX:
        raise ProtocolMappingError(
            f"charge_level must be between {CHARGE_LEVEL_MIN} and {CHARGE_LEVEL_MAX}"
        )
    params.charge_level = charge_level
    return _mapped(spec, params)


def _map_equip(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(args, {"slot", "item_name"})
    has_slot = "slot" in args and args["slot"] is not None
    has_item_name = "item_name" in args and args["item_name"] is not None
    if has_slot == has_item_name:
        raise ProtocolMappingError("equip requires exactly one of slot or item_name")
    params = params_pb2.EquipParams()
    if has_slot:
        slot = _required_int(args, "slot")
        if not EQUIP_SLOT_MIN <= slot <= EQUIP_SLOT_MAX:
            raise ProtocolMappingError(
                f"slot must be between {EQUIP_SLOT_MIN} and {EQUIP_SLOT_MAX}"
            )
        params.slot = slot
    else:
        params.item_name = _required_str(args, "item_name")
    return _mapped(spec, params)


def _map_query_runtime_snapshot(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(
        args,
        {"include_player", "include_world_time", "include_weather", "include_menu_state"},
    )
    return _mapped(
        spec,
        query_pb2.QueryRuntimeSnapshotParams(
            include_player=_optional_bool(args, "include_player", False),
            include_world_time=_optional_bool(args, "include_world_time", False),
            include_weather=_optional_bool(args, "include_weather", False),
            include_menu_state=_optional_bool(args, "include_menu_state", False),
        ),
    )


def _map_query_world_region(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(
        args,
        {
            "map_token",
            "region",
            "center",
            "radius",
            "world_entity_types",
            "include_tiles",
            "include_characters",
            "include_debug",
        },
    )
    map_token = _optional_str(args, "map_token", "")
    params = query_pb2.QueryWorldRegionParams(
        map_token=map_token,
        radius=_optional_positive_int(args, "radius", 0),
        include_tiles=_optional_bool(args, "include_tiles", False),
        include_characters=_optional_bool(args, "include_characters", False),
        include_debug=_optional_bool(args, "include_debug", False),
    )
    if "region" in args and args["region"] is not None:
        params.region.CopyFrom(_tile_rect(args["region"], "region", default_map_id=map_token))
    if "center" in args and args["center"] is not None:
        params.center.CopyFrom(_tile_position(args["center"], "center", default_map_id=map_token))
    params.world_entity_types.extend(_optional_str_list(args, "world_entity_types"))
    return _mapped(spec, params)


def _map_query_inventory_snapshot(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(
        args,
        {"container_kind", "container_ref", "include_empty_slots", "include_details", "include_debug"},
    )
    params = query_pb2.QueryInventorySnapshotParams(
        container_kind=_optional_str(args, "container_kind", ""),
        include_empty_slots=_optional_bool(args, "include_empty_slots", False),
        include_details=_optional_bool(args, "include_details", False),
        include_debug=_optional_bool(args, "include_debug", False),
    )
    if "container_ref" in args and args["container_ref"] is not None:
        params.container_ref.CopyFrom(ref_pb2.RefId(value=_required_str(args, "container_ref")))
    return _mapped(spec, params)


def _map_query_ui_snapshot(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(args, {"include_elements", "include_debug"})
    return _mapped(
        spec,
        query_pb2.QueryUiSnapshotParams(
            include_elements=_optional_bool(args, "include_elements", False),
            include_debug=_optional_bool(args, "include_debug", False),
        ),
    )


def _map_query_inspect_refs(spec: CapabilitySpec, args: Mapping[str, Any]) -> MappedCommand:
    _reject_unknown(args, {"refs", "include_debug"})
    refs = _required_str_list(args, "refs")
    return _mapped(
        spec,
        query_pb2.QueryInspectRefsParams(
            refs=[ref_pb2.RefId(value=ref) for ref in refs],
            include_debug=_optional_bool(args, "include_debug", False),
        ),
    )


def _mapped(spec: CapabilitySpec, params: Message) -> MappedCommand:
    return MappedCommand(
        tool_name=spec.tool_name,
        capability=spec.capability,
        oneof_field=spec.oneof_field,
        params=params,
        timeout_ms=spec.timeout_ms,
    )


def _tile_point(args: Mapping[str, Any]) -> common_pb2.TilePoint:
    return common_pb2.TilePoint(x=_required_int(args, "x"), y=_required_int(args, "y"))


def _tile_position(value: Any, key: str, *, default_map_id: str = "") -> common_pb2.TilePosition:
    obj = _required_object_value(value, key)
    _reject_unknown(obj, {"map_id", "x", "y"})
    return common_pb2.TilePosition(
        map_id=_optional_str(obj, "map_id", default_map_id),
        x=_required_int(obj, "x"),
        y=_required_int(obj, "y"),
    )


def _tile_rect(value: Any, key: str, *, default_map_id: str = "") -> common_pb2.TileRect:
    obj = _required_object_value(value, key)
    _reject_unknown(obj, {"map_id", "x", "y", "width", "height"})
    return common_pb2.TileRect(
        map_id=_optional_str(obj, "map_id", default_map_id),
        x=_required_int(obj, "x"),
        y=_required_int(obj, "y"),
        width=_positive_int(obj, "width"),
        height=_positive_int(obj, "height"),
    )


def _normalize_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if args is None:
        return {}
    if not isinstance(args, Mapping):
        raise ProtocolMappingError("tool arguments must be an object")
    return args


def _reject_unknown(args: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(args) - allowed
    if unknown:
        raise ProtocolMappingError(f"unsupported argument(s): {sorted(unknown)}")


def _reject_present(args: Mapping[str, Any], keys: set[str], context: str) -> None:
    present = sorted(key for key in keys if key in args and args[key] is not None)
    if present:
        raise ProtocolMappingError(f"{context} does not support argument(s): {present}")


def _required_str(args: Mapping[str, Any], key: str) -> str:
    if key not in args:
        raise ProtocolMappingError(f"missing required argument: {key}")
    value = args[key]
    if not isinstance(value, str) or not value.strip():
        raise ProtocolMappingError(f"{key} must be a non-empty string")
    return value


def _optional_str(args: Mapping[str, Any], key: str, default: str) -> str:
    if key not in args or args[key] is None:
        return default
    return _required_str(args, key)


def _required_int(args: Mapping[str, Any], key: str) -> int:
    if key not in args:
        raise ProtocolMappingError(f"missing required argument: {key}")
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolMappingError(f"{key} must be an integer")
    return value


def _optional_int(args: Mapping[str, Any], key: str, default: int) -> int:
    if key not in args or args[key] is None:
        return default
    return _required_int(args, key)


def _positive_int(args: Mapping[str, Any], key: str) -> int:
    value = _required_int(args, key)
    if value <= 0:
        raise ProtocolMappingError(f"{key} must be > 0")
    return value


def _optional_positive_int(args: Mapping[str, Any], key: str, default: int) -> int:
    if key not in args or args[key] is None:
        return default
    return _positive_int(args, key)


def _optional_bool(args: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if not isinstance(value, bool):
        raise ProtocolMappingError(f"{key} must be a boolean")
    return value


def _required_object_value(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolMappingError(f"{key} must be an object")
    return value


def _required_str_list(args: Mapping[str, Any], key: str) -> list[str]:
    if key not in args:
        raise ProtocolMappingError(f"missing required argument: {key}")
    value = args[key]
    if not isinstance(value, list) or not value:
        raise ProtocolMappingError(f"{key} must be a non-empty string array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ProtocolMappingError(f"{key}[{index}] must be a non-empty string")
        result.append(item)
    return result


def _optional_str_list(args: Mapping[str, Any], key: str) -> list[str]:
    if key not in args or args[key] is None:
        return []
    return _required_str_list(args, key)


def _required_enum(args: Mapping[str, Any], key: str, enum_map: Mapping[str, int]) -> int:
    if key not in args:
        raise ProtocolMappingError(f"missing required argument: {key}")
    return _enum_value(args[key], key, enum_map)


def _enum_value(value: Any, key: str, enum_map: Mapping[str, int]) -> int:
    if not isinstance(value, str):
        raise ProtocolMappingError(f"{key} must be a string enum")
    normalized = value.strip().lower()
    if normalized not in enum_map:
        raise ProtocolMappingError(f"invalid {key}: {value}")
    return enum_map[normalized]
