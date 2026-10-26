"""Canonical MCP-facing capability schemas and command mapping.

Protocol tools reuse :mod:`capability_registry` directly.  The nine V2
compound capabilities live here because they have no protobuf message yet;
this module is consequently the single Agent/MCP schema source for them.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
import re

from google.protobuf.json_format import MessageToDict
from google.protobuf.descriptor import FieldDescriptor

from agent.generated.protocol import capability_pb2
from .capability_registry import CAPABILITY_SPECS, CapabilitySpec
from .command_builder import build_command_request
from .tool_mapper import ProtocolMappingError, map_tool_call


JsonSchema = dict[str, Any]


def _object(properties: JsonSchema | None = None, required: list[str] | None = None) -> JsonSchema:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def _string_array(*, min_items: int = 0) -> JsonSchema:
    schema: JsonSchema = {"type": "array", "items": {"type": "string", "minLength": 1}}
    if min_items:
        schema["minItems"] = min_items
    return schema


COMMAND_RESULT_SCHEMA: JsonSchema = _object(
    {
        "command_id": {"type": "string", "minLength": 1},
        "capability": {"type": "string", "minLength": 1},
        "success": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": ["COMPLETED", "REJECTED", "FAILED", "TIMEOUT", "CANCELLED"],
        },
        "observation": {"type": "string"},
        "data": {"type": "object"},
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "retryable": {"type": "boolean"},
            },
            "required": ["code", "message"],
            "additionalProperties": True,
        },
    },
    ["command_id", "capability", "success", "status"],
)


def _output_schema(capability: str, data_schema: JsonSchema | None = None) -> JsonSchema:
    schema = deepcopy(COMMAND_RESULT_SCHEMA)
    schema["properties"]["capability"]["enum"] = [capability]
    schema["properties"]["data"] = deepcopy(data_schema or _object())
    return schema


def _protobuf_message_schema(descriptor, seen: frozenset[str] = frozenset()) -> JsonSchema:
    if descriptor.full_name in seen:
        return {"type": "object"}
    nested_seen = seen | {descriptor.full_name}
    properties: JsonSchema = {}
    for field in descriptor.fields:
        is_map = bool(
            field.type == FieldDescriptor.TYPE_MESSAGE
            and field.message_type.GetOptions().map_entry
        )
        if is_map:
            value_field = field.message_type.fields_by_name["value"]
            if value_field.type == FieldDescriptor.TYPE_MESSAGE:
                mapped_value = _protobuf_message_schema(value_field.message_type, nested_seen)
            elif value_field.type == FieldDescriptor.TYPE_BOOL:
                mapped_value = {"type": "boolean"}
            elif value_field.type in {FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE}:
                mapped_value = {"type": "number"}
            elif value_field.type in {
                FieldDescriptor.TYPE_INT32, FieldDescriptor.TYPE_INT64,
                FieldDescriptor.TYPE_UINT32, FieldDescriptor.TYPE_UINT64,
            }:
                mapped_value = {"type": "integer"}
            else:
                mapped_value = {"type": "string"}
            properties[field.name] = {
                "type": "object", "additionalProperties": mapped_value,
            }
            continue
        if field.type == FieldDescriptor.TYPE_MESSAGE:
            value: JsonSchema = _protobuf_message_schema(field.message_type, nested_seen)
        elif field.type == FieldDescriptor.TYPE_ENUM:
            value = {"type": "string", "enum": [item.name for item in field.enum_type.values]}
        elif field.type in {
            FieldDescriptor.TYPE_INT32, FieldDescriptor.TYPE_INT64,
            FieldDescriptor.TYPE_UINT32, FieldDescriptor.TYPE_UINT64,
            FieldDescriptor.TYPE_SINT32, FieldDescriptor.TYPE_SINT64,
            FieldDescriptor.TYPE_FIXED32, FieldDescriptor.TYPE_FIXED64,
            FieldDescriptor.TYPE_SFIXED32, FieldDescriptor.TYPE_SFIXED64,
        }:
            value = {"type": "integer"}
        elif field.type in {FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE}:
            value = {"type": "number"}
        elif field.type == FieldDescriptor.TYPE_BOOL:
            value = {"type": "boolean"}
        else:
            value = {"type": "string"}
        if field.is_repeated:
            value = {"type": "array", "items": value}
        properties[field.name] = value
    return _object(properties)


_PROTO_RESULT_FIELD = {
    "say": "say", "emote": "emote", "face": "face", "move_to": "move_to",
    "go_to": "go_to", "go_to_bed": "go_to_bed", "interact": "interact",
    "use_tool": "use_tool", "equip": "equip", "open_menu": "menu",
    "query_menu": "menu_snapshot", "menu_click": "menu", "menu_close": "menu",
    "query_inventory": "query_inventory", "query_location": "query_location",
    "query_map_exits": "query_map_exits", "query_runtime_snapshot": "query_runtime_snapshot",
    "query_world_region": "query_world_region",
    "query_inventory_snapshot": "query_inventory_snapshot",
    "query_ui_snapshot": "query_ui_snapshot", "query_inspect_refs": "query_inspect_refs",
}


def _proto_result_schema(capability: str) -> JsonSchema:
    field_name = _PROTO_RESULT_FIELD[capability]
    field = capability_pb2.CommandResult.DESCRIPTOR.fields_by_name[field_name]
    return _protobuf_message_schema(field.message_type)


def _count_result(*names: str) -> JsonSchema:
    properties = {name: {"type": "integer", "minimum": 0} for name in names}
    properties.update({
        "stopped_reason": {"type": "string"},
        "location": {"type": "string"},
    })
    return _object(properties)


_V2_RESULT_SCHEMAS: Mapping[str, JsonSchema] = MappingProxyType({
    "water_crops": _count_result("watered", "skipped", "refilled"),
    "harvest_crops": _count_result("harvested", "replanted", "skipped"),
    "plant_crops": _object({
        **_count_result("planted", "skipped")["properties"],
        "seed": {"type": "string"},
    }),
    "chop_trees": _count_result("chopped", "skipped", "total_strikes"),
    "mine_rocks": _count_result("mined", "skipped", "total_strikes"),
    "execute_on_targets": _object({
        **_count_result("completed", "skipped", "total")["properties"],
        "stamina_used": {"type": "integer", "minimum": 0},
    }),
    "ship_items": _object({
        "mode": {"type": "string", "enum": ["plan", "execute", "cancelled"]},
        "items": {"type": "array", "items": {"type": "object"}},
        "shipped": {"type": "array", "items": {"type": "object"}},
        "kept": {"type": "array", "items": {"type": "object"}},
        "excluded": {"type": "array", "items": {"type": "string"}},
        "failed": {"type": "array", "items": {"type": "object"}},
        "total_value": {"type": "integer", "minimum": 0},
        "item_count": {"type": "integer", "minimum": 0},
        "message": {"type": "string"},
    }),
    "buy_items": _object({
        "shop": {"type": "string"},
        "bought": {"type": "array", "items": {"type": "object"}},
        "bought_count": {"type": "integer", "minimum": 0},
        "total_cost": {"type": "integer", "minimum": 0},
        "gold_remaining": {"type": "integer", "minimum": 0},
        "failed": {"type": "array", "items": {"type": "object"}},
        "stopped_reason": {"type": "string"},
    }),
    "menu_interact": _object({
        "menu_type": {"type": "string"},
        "operations_requested": {"type": "integer", "minimum": 0},
        "operations_completed": {"type": "integer", "minimum": 0},
        "operations_failed": {"type": "integer", "minimum": 0},
        "results": {"type": "array", "items": {"type": "object"}},
    }),
    "go_to": _object({
        "target": {"type": "string"}, "final_location": {"type": "string"},
        "final_tile": {"type": "object"}, "facing": {"type": "string"},
    }),
    "do": _object({
        "intent": {"type": "string"},
        "target": _object({"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
        "position": _object({"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
        "bed_position": _object({"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
        "target_desc": {"type": "string"},
        **{name: {"type": "string"} for name in (
            "npc", "display_name", "menu_type", "object_type", "animal_name",
            "animal_type", "npc_name", "item_name", "pickup_type", "recipe_name",
            "item_created", "location", "container_id", "item",
        )},
        "distance": {"type": "number"},
        **{name: {"type": "integer"} for name in (
            "choice", "count", "friendship_before", "friendship_after", "transferred",
            "remaining_in_inventory", "remaining_in_container", "count_crafted", "count_requested",
        )},
        **{name: {"type": "boolean"} for name in (
            "still_open", "already_petted", "actually_petted",
        )},
        "items": {"type": "array", "items": _object({
            "name": {"type": "string"}, "stack": {"type": "integer"},
            "quality": {"type": "integer"},
        }, ["name", "stack", "quality"])},
    }),
    "move_to": _object({
        "target": {"type": "object"}, "final_position": {"type": "object"},
        "location": {"type": "string"},
    }),
    "use_tool": _object({"target": {"type": "object"}, "tool": {"type": "string"}}),
    "equip": _object({
        "item_name": {"type": "string"},
        "slot": {"type": "integer", "minimum": 1},
        "method": {"type": "string", "enum": ["keypress", "direct_api"]},
        "swapped": {"type": "boolean"},
        "from_slot": {"type": "integer", "minimum": 1},
        "to_slot": {"type": "integer", "minimum": 1},
        "swapped_out": {"type": "string"},
    }),
    "observe": _object({
        "self": _object({
            "position": _object({
                "tile_x": {"type": "integer"}, "tile_y": {"type": "integer"},
                "location": {"type": "string"},
            }),
            "facing": {"type": "string"},
            "health": {"type": "number"}, "energy": {"type": "number"},
            "inventory": {"type": "array", "items": {"type": "object"}},
            "equipped": {"type": "object"},
            "status_effects": {"type": "array", "items": {"type": "string"}},
            "money": {"type": "integer"},
        }),
        "world": _object({
            "time": _object({"game_time": {"type": "integer"}, "display": {"type": "string"}}),
            "weather": {"type": "string"}, "location_name": {"type": "string"},
            "season": {"type": "string"}, "year": {"type": "integer"},
            "day_of_month": {"type": "integer"}, "is_raining": {"type": "boolean"},
        }),
        "entities": {"type": "array", "items": {"type": "object"}},
        "timestamp": {"type": "integer"},
        "active_dialogue": {"type": "object"},
    }, ["self", "world", "entities", "timestamp"]),
    "go_bed": _object({
        "method": {"type": "string", "enum": ["warp"]},
        "home_location": {"type": "string", "minLength": 1},
        "sleep_triggered": {"type": "boolean"},
        "stopped_reason": {"type": "string", "minLength": 1},
    }, ["method", "home_location", "sleep_triggered", "stopped_reason"]),
})


@dataclass(frozen=True)
class CanonicalCapabilitySpec:
    name: str
    wire_capability: str
    source_surface: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    timeout_ms: int
    execution_model: str
    cancellable: bool


@dataclass(frozen=True)
class CanonicalMappedCommand:
    command_id: str
    capability: str
    timeout_ms: int
    execution_model: str
    cancellable: bool
    wire_format: str
    payload: Mapping[str, Any]


_PROTO_ASYNC = frozenset({
    "face", "move_to", "go_to", "go_to_bed", "interact", "use_tool", "equip",
    "open_menu", "menu_click", "menu_close",
})


_V2_INPUT_SCHEMAS: Mapping[str, JsonSchema] = MappingProxyType({
    "water_crops": _object({"max_count": {"type": "integer", "minimum": 1}}),
    "harvest_crops": _object({
        "max_count": {"type": "integer", "minimum": 1},
        "replant_seed": {"type": "string", "minLength": 1},
    }),
    "ship_items": _object({
        "mode": {"type": "string", "enum": ["plan", "execute"]},
        "items": _string_array(),
        "exclude": _string_array(),
        "min_quality": {"type": "string", "enum": ["silver", "gold", "iridium"]},
    }),
    "buy_items": _object({
        "shop": {"type": "string", "enum": [
            "SeedShop", "FishShop", "Saloon", "Blacksmith", "AnimalShop",
            "Carpenter", "Hospital", "JojaMart",
        ]},
        "items": {
            "type": "array", "minItems": 1,
            "items": _object({
                "name": {"type": "string", "minLength": 1},
                "quantity": {"type": "integer", "minimum": 1},
            }, ["name", "quantity"]),
        },
    }, ["shop", "items"]),
    "plant_crops": _object({
        "seed": {"type": "string", "minLength": 1},
        "max_count": {"type": "integer", "minimum": 1},
    }, ["seed"]),
    "chop_trees": _object({
        "max_count": {"type": "integer", "minimum": 1},
        "include_stumps": {"type": "boolean"},
    }),
    "mine_rocks": _object({"max_count": {"type": "integer", "minimum": 1}}),
    "execute_on_targets": _object({
        "targets": _string_array(min_items=1),
        "action": {"type": "string", "enum": ["use_tool", "do"]},
        "tool": {"type": "string", "minLength": 1},
        "tool_category": {"type": "string", "minLength": 1},
        "intent": {"type": "string", "minLength": 1},
        "intent_args": {"type": "object"},
        "repeat_per_target": {"type": "string", "pattern": "^(once|until_gone|count:[1-9][0-9]*)$"},
        "max_count": {"type": "integer", "minimum": 1},
        "stop_conditions": _object({
            "stamina_below": {"type": "integer", "minimum": 1},
            "time_after": {"type": "integer", "minimum": 1},
        }),
    }, ["targets", "action"]),
    "menu_interact": _object({
        "menu_type": {"type": "string", "enum": ["Shop", "Container", "Craft", "Ship"]},
        "operations": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        "target": {"type": "string", "minLength": 1},
        "shop": {"type": "string", "minLength": 1},
    }, ["menu_type", "operations"]),
})

_V2_RENAMED_SCHEMAS: Mapping[str, tuple[str, JsonSchema]] = MappingProxyType({
    "navigate_to_target": (
        "go_to",
        _object({
            "target": {"type": "string", "minLength": 1},
            "face_direction": {
                "type": "string", "enum": ["north", "south", "east", "west"],
            },
        }, ["target"]),
    ),
    "act_on_target": (
        "do",
        _object({
            "intent": {"type": "string", "minLength": 1},
            "target": {"type": "string", "minLength": 1},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "choice": {"type": "integer"},
            "count": {"type": "integer", "minimum": 1},
            "index": {"type": "integer", "minimum": 0},
            "animal_name": {"type": "string", "minLength": 1},
            "container_id": {"type": "string", "minLength": 1},
            "item": {"type": "string", "minLength": 1},
            "item_name": {"type": "string", "minLength": 1},
            "npc": {"type": "string", "minLength": 1},
            "npc_name": {"type": "string", "minLength": 1},
            "recipe": {"type": "string", "minLength": 1},
        }, ["intent"]),
    ),
    "move_to_target": (
        "move_to",
        _object({
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "location": {"type": "string", "minLength": 1},
            "target": {"type": "string", "minLength": 1},
            "arrive_distance": {"type": "number", "minimum": 0},
        }),
    ),
    "use_tool_at_target": (
        "use_tool",
        _object({"x": {"type": "integer"}, "y": {"type": "integer"}}),
    ),
    "equip_matching_item": (
        "equip",
        _object({
            "item": {"type": "string", "minLength": 1},
            "slot": {"type": "integer", "minimum": 0},
        }),
    ),
    "observe_current_view": ("observe", _object()),
    "sleep_for_night": ("go_bed", _object()),
})

_V2_TIMEOUTS = MappingProxyType({
    "ship_items": 30_000,
    "buy_items": 60_000,
    "execute_on_targets": 300_000,
    "menu_interact": 120_000,
})
_V2_ASYNC = frozenset({
    "water_crops", "harvest_crops", "buy_items", "plant_crops", "chop_trees",
    "mine_rocks", "execute_on_targets", "menu_interact",
})
_V2_RENAMED_ASYNC = frozenset({"go_to", "go_bed", "move_to", "use_tool", "do"})


def canonical_specs() -> Mapping[str, CanonicalCapabilitySpec]:
    specs: dict[str, CanonicalCapabilitySpec] = {}
    for proto in CAPABILITY_SPECS:
        if proto.internal_only:
            continue
        schema = deepcopy(proto.parameters_schema)
        # ``emote`` on say is an Agent convenience which expands to two Mod
        # commands.  It is not part of the canonical one-command MCP contract.
        if proto.tool_name == "say":
            schema["properties"].pop("emote", None)
        specs[proto.tool_name] = CanonicalCapabilitySpec(
            name=proto.tool_name,
            wire_capability=proto.capability,
            source_surface="protocol-2.4.5",
            description=proto.description,
            input_schema=schema,
            output_schema=_output_schema(proto.capability, _proto_result_schema(proto.capability)),
            timeout_ms=proto.timeout_ms,
            execution_model="async" if proto.capability in _PROTO_ASYNC else "request_result",
            cancellable=proto.capability in _PROTO_ASYNC,
        )
    for name, schema in _V2_INPUT_SCHEMAS.items():
        specs[name] = CanonicalCapabilitySpec(
            name=name,
            wire_capability=name,
            source_surface="v2-compat",
            description=f"Canonical MCP mapping for the registered V2 {name} compound capability.",
            input_schema=deepcopy(schema),
            output_schema=_output_schema(name, _V2_RESULT_SCHEMAS[name]),
            timeout_ms=_V2_TIMEOUTS.get(name, 120_000),
            execution_model="async" if name in _V2_ASYNC else "request_result",
            cancellable=name in _V2_ASYNC,
        )
    for name, (wire_name, schema) in _V2_RENAMED_SCHEMAS.items():
        specs[name] = CanonicalCapabilitySpec(
            name=name,
            wire_capability=wire_name,
            source_surface="v2-compat",
            description=(
                "Versioned semantic-target compatibility mapping; intentionally distinct "
                "from the similarly named Protocol 2.4.5 tile contract."
            ),
            input_schema=deepcopy(schema),
            output_schema=_output_schema(wire_name, _V2_RESULT_SCHEMAS[wire_name]),
            timeout_ms=120_000 if wire_name in {"go_to", "go_bed", "move_to"} else 15_000,
            execution_model="async" if wire_name in _V2_RENAMED_ASYNC else "request_result",
            cancellable=wire_name in _V2_RENAMED_ASYNC,
        )
    return MappingProxyType(specs)


def map_canonical_command(
    name: str, arguments: Mapping[str, Any], *, command_id: str
) -> CanonicalMappedCommand:
    """Map exactly one canonical call without changing its command identity."""
    if not command_id:
        raise ProtocolMappingError("command_id must be non-empty")
    spec = canonical_specs().get(name)
    if spec is None:
        raise ProtocolMappingError(f"unsupported canonical capability: {name}")
    _validate_json(spec.input_schema, arguments)
    if name == "execute_on_targets":
        action = arguments["action"]
        if action == "use_tool" and not (arguments.get("tool") or arguments.get("tool_category")):
            raise ProtocolMappingError("execute_on_targets use_tool requires tool or tool_category")
        if action == "do" and not arguments.get("intent"):
            raise ProtocolMappingError("execute_on_targets do requires intent")
    if name in {"move_to_target", "use_tool_at_target"}:
        has_x, has_y = "x" in arguments, "y" in arguments
        if has_x != has_y:
            raise ProtocolMappingError(f"{name} requires x and y together")
    if name == "move_to_target" and not (
        arguments.get("target") or ("x" in arguments and "y" in arguments)
    ):
        raise ProtocolMappingError("move_to_target requires target or x/y")
    if name == "equip_matching_item" and not (
        arguments.get("item") or "slot" in arguments
    ):
        raise ProtocolMappingError("equip_matching_item requires item or slot")
    if spec.source_surface == "v2-compat":
        wire_arguments = deepcopy(dict(arguments))
        if name == "equip_matching_item":
            # The public compatibility contract intentionally preserves the
            # former V2 selectors (``item`` and zero-based ``slot``), while
            # the registered execution atom consumes ``item_name`` and a
            # one-based toolbar slot.  Translate at the canonical boundary
            # instead of leaking either wire convention into MCP clients.
            if "item" in wire_arguments:
                wire_arguments["item_name"] = wire_arguments.pop("item")
            if "slot" in wire_arguments:
                wire_arguments["slot"] += 1
        return CanonicalMappedCommand(
            command_id=command_id,
            capability=spec.wire_capability,
            timeout_ms=spec.timeout_ms,
            execution_model=spec.execution_model,
            cancellable=spec.cancellable,
            wire_format="v2-json",
            payload={
                "seq": 0,
                "protocol_version": "2.0",
                "action_id": command_id,
                "capability": spec.wire_capability,
                "params": wire_arguments,
            },
        )
    mapped = map_tool_call(name, arguments)
    if len(mapped) != 1:
        raise ProtocolMappingError("canonical call must map to exactly one command")
    request = build_command_request(mapped[0], command_id=command_id)
    return CanonicalMappedCommand(
        command_id=request.command_id,
        capability=request.capability,
        timeout_ms=request.timeout_ms,
        execution_model=spec.execution_model,
        cancellable=spec.cancellable,
        wire_format="protocol-2.4.5",
        payload=MessageToDict(request, preserving_proto_field_name=True),
    )


V2_PRODUCTION_CAPABILITIES = frozenset(_V2_INPUT_SCHEMAS)


_V2_COMPATIBILITY_ALIAS = MappingProxyType({
    "say": "say",
    "emote": "emote",
    "face": "face",
    "interact": "interact",
    "open_menu": "open_menu",
    "menu_click": "menu_click",
    "menu_close": "menu_close",
})


def map_v2_compatibility_alias(
    canonical_name: str, arguments: Mapping[str, Any], *, command_id: str
) -> CanonicalMappedCommand:
    """Map a proven/controlled canonical subset onto its registered V2 source."""
    if canonical_name not in _V2_COMPATIBILITY_ALIAS:
        raise ProtocolMappingError(f"no V2 compatibility alias for {canonical_name}")
    spec = canonical_specs()[canonical_name]
    _validate_json(spec.input_schema, arguments)
    params = deepcopy(dict(arguments))
    if canonical_name == "emote":
        params = {"type": params.pop("emote_type")}
    elif canonical_name == "menu_click":
        params = {
            "component_id": params["component_id"],
            "source": params["component_source"],
        }
    return CanonicalMappedCommand(
        command_id=command_id,
        capability=_V2_COMPATIBILITY_ALIAS[canonical_name],
        timeout_ms=spec.timeout_ms,
        execution_model=spec.execution_model,
        cancellable=spec.cancellable,
        wire_format="v2-json",
        payload={
            "seq": 0,
            "protocol_version": "2.0",
            "action_id": command_id,
            "capability": _V2_COMPATIBILITY_ALIAS[canonical_name],
            "params": params,
        },
    )


def normalize_v2_alias_output(
    canonical_name: str,
    arguments: Mapping[str, Any],
    source_data: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Normalize V2 compatibility results to the canonical Proto result shape.

    Only facts supplied by canonical input or the source result are emitted;
    unavailable optional Proto diagnostics are omitted rather than invented.
    """
    _validate_json(_V2_ALIAS_SOURCE_DATA_SCHEMAS[canonical_name], source_data)
    if canonical_name == "say":
        return {"content_length": len(str(arguments["message"]))}
    if canonical_name == "emote":
        return {"emote_type": "EMOTE_" + str(arguments["emote_type"]).upper()}
    if canonical_name == "face":
        return {"final_direction": "DIRECTION_" + str(arguments["direction"]).upper()}
    if canonical_name == "interact":
        return {"effect": {"target_tile": {"x": arguments["x"], "y": arguments["y"]}}}
    if canonical_name == "open_menu":
        return {"action": "open"}
    if canonical_name == "menu_click":
        return {
            "action": "click",
            "component_id": arguments["component_id"],
            "click_count": 1,
        }
    if canonical_name == "menu_close":
        return {
            "action": "close",
            "already_closed": bool(source_data.get("already_closed", False)),
        }
    raise ProtocolMappingError(f"no V2 result normalizer for {canonical_name}")


_V2_ALIAS_SOURCE_DATA_SCHEMAS: Mapping[str, JsonSchema] = MappingProxyType({
    "say": _object({"message": {"type": "string"}}, ["message"]),
    "emote": _object({"emote": {"type": "string"}}, ["emote"]),
    "face": _object({"intent": {"type": "string"}}, ["intent"]),
    "interact": _object({"intent": {"type": "string"}}, ["intent"]),
    "open_menu": _object({"intent": {"type": "string"}}, ["intent"]),
    "menu_click": _object({"intent": {"type": "string"}}, ["intent"]),
    "menu_close": _object({
        "intent": {"type": "string"}, "already_closed": {"type": "boolean"},
    }),
})


def _validate_json(schema: Mapping[str, Any], value: Any) -> None:
    expected = schema.get("type")
    valid = expected is None or (
        (expected == "object" and isinstance(value, Mapping))
        or (expected == "array" and isinstance(value, list))
        or (expected == "string" and isinstance(value, str))
        or (expected == "boolean" and isinstance(value, bool))
        or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
    )
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ProtocolMappingError("arguments do not match canonical schema")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ProtocolMappingError("arguments do not match canonical schema")
        if schema.get("pattern") and re.fullmatch(str(schema["pattern"]), value) is None:
            raise ProtocolMappingError("arguments do not match canonical schema")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ProtocolMappingError("arguments do not match canonical schema")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ProtocolMappingError("arguments do not match canonical schema")
        if schema.get("items"):
            for item in value:
                _validate_json(schema["items"], item)
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            raise ProtocolMappingError("arguments do not match canonical schema")
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            raise ProtocolMappingError("arguments do not match canonical schema")
        for key, nested in value.items():
            if key in properties:
                _validate_json(properties[key], nested)
