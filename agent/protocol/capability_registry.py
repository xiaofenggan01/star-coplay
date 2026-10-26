"""Central registry for Agent-visible Protocol 2.4.5 capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from agent.generated.protocol import capability_params_pb2 as params_pb2
from agent.generated.protocol import common_pb2
from agent.generated.protocol import query_pb2

from .constants import (
    DEFAULT_TIMEOUT_MS,
    INTERNAL_ONLY_CAPABILITIES,
    LONG_TIMEOUT_MS,
    QUERY_TIMEOUT_MS,
)


JsonSchema = dict[str, Any]


@dataclass(frozen=True)
class CapabilitySpec:
    tool_name: str
    capability: str
    params_cls: type
    oneof_field: str
    description: str
    parameters_schema: JsonSchema
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    exposed_to_llm: bool = True
    default_llm_exposed: bool = True
    internal_only: bool = False


DIRECTION_ENUM: Mapping[str, int] = MappingProxyType(
    {
        "up": common_pb2.DIRECTION_UP,
        "down": common_pb2.DIRECTION_DOWN,
        "left": common_pb2.DIRECTION_LEFT,
        "right": common_pb2.DIRECTION_RIGHT,
    }
)

EMOTE_ENUM: Mapping[str, int] = MappingProxyType(
    {
        "happy": params_pb2.EMOTE_HAPPY,
        "sad": params_pb2.EMOTE_SAD,
        "angry": params_pb2.EMOTE_ANGRY,
        "heart": params_pb2.EMOTE_HEART,
        "exclamation": params_pb2.EMOTE_EXCLAMATION,
        "question": params_pb2.EMOTE_QUESTION,
        "laugh": params_pb2.EMOTE_LAUGH,
    }
)

MOVE_TO_MODE_ENUM: Mapping[str, int] = MappingProxyType(
    {
        "exact": params_pb2.MOVE_TO_EXACT,
        "edge_warp": params_pb2.MOVE_TO_EDGE_WARP,
        "adjacent": params_pb2.MOVE_TO_ADJACENT,
    }
)

ARRIVAL_MODE_ENUM: Mapping[str, int] = MappingProxyType(
    {
        "exact": params_pb2.ARRIVAL_EXACT,
        "adjacent": params_pb2.ARRIVAL_ADJACENT,
    }
)

MENU_SOURCE_ENUM: Mapping[str, int] = MappingProxyType(
    {
        "page": common_pb2.MENU_COMPONENT_SOURCE_PAGE,
        "tab": common_pb2.MENU_COMPONENT_SOURCE_TAB,
    }
)

CHARGE_LEVEL_MIN = 0
CHARGE_LEVEL_MAX = 4
EQUIP_SLOT_MIN = 1
EQUIP_SLOT_MAX = 12


def _object_schema(
    properties: JsonSchema | None = None,
    required: list[str] | None = None,
) -> JsonSchema:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def _tile_position_schema(description: str) -> JsonSchema:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "map_id": {"type": "string", "minLength": 1},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    }


def _tile_rect_schema(description: str) -> JsonSchema:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "map_id": {"type": "string", "minLength": 1},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1},
        },
        "required": ["x", "y", "width", "height"],
        "additionalProperties": False,
    }


CAPABILITY_SPECS: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        tool_name="say",
        capability="say",
        params_cls=params_pb2.SayParams,
        oneof_field="say",
        description="Send a short in-game chat message.",
        parameters_schema=_object_schema(
            {
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Message content to say in game.",
                },
                "emote": {
                    "type": "string",
                    "enum": list(EMOTE_ENUM),
                    "description": "Optional emote bubble to show after saying the message.",
                },
            },
            ["message"],
        ),
    ),
    CapabilitySpec(
        tool_name="emote",
        capability="emote",
        params_cls=params_pb2.EmoteParams,
        oneof_field="emote",
        description="Show an emote bubble above the player.",
        parameters_schema=_object_schema(
            {"emote_type": {"type": "string", "enum": list(EMOTE_ENUM)}},
            ["emote_type"],
        ),
    ),
    CapabilitySpec(
        tool_name="face",
        capability="face",
        params_cls=params_pb2.FaceParams,
        oneof_field="face",
        description="Face a direction without moving.",
        parameters_schema=_object_schema(
            {"direction": {"type": "string", "enum": list(DIRECTION_ENUM)}},
            ["direction"],
        ),
    ),
    CapabilitySpec(
        tool_name="move_to",
        capability="move_to",
        params_cls=params_pb2.MoveToParams,
        oneof_field="move_to",
        description="Move to a tile on the current map.",
        parameters_schema=_object_schema(
            {
                "mode": {"type": "string", "enum": list(MOVE_TO_MODE_ENUM)},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "direction": {"type": "string", "enum": list(DIRECTION_ENUM)},
                "face_direction": {"type": "string", "enum": list(DIRECTION_ENUM)},
                "adjacent_side": {"type": "string", "enum": list(DIRECTION_ENUM)},
                "timeout_frames": {"type": "integer", "minimum": 1},
            },
            ["mode", "x", "y"],
        ),
        timeout_ms=LONG_TIMEOUT_MS,
    ),
    CapabilitySpec(
        tool_name="go_to",
        capability="go_to",
        params_cls=params_pb2.GoToParams,
        oneof_field="go_to",
        description="Move to a structured map/tile target.",
        parameters_schema=_object_schema(
            {
                "map_id": {"type": "string", "minLength": 1},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "arrival_mode": {"type": "string", "enum": list(ARRIVAL_MODE_ENUM)},
                "stand_side": {"type": "string", "enum": list(DIRECTION_ENUM)},
                "face_direction": {"type": "string", "enum": list(DIRECTION_ENUM)},
                "timeout_frames": {"type": "integer", "minimum": 1},
            },
            ["map_id", "x", "y", "arrival_mode"],
        ),
        timeout_ms=LONG_TIMEOUT_MS,
    ),
    CapabilitySpec(
        tool_name="go_to_bed",
        capability="go_to_bed",
        params_cls=params_pb2.GoToBedParams,
        oneof_field="go_to_bed",
        description="Go to bed and advance to the next day.",
        parameters_schema=_object_schema({"timeout_frames": {"type": "integer", "minimum": 1}}),
        timeout_ms=LONG_TIMEOUT_MS,
    ),
    CapabilitySpec(
        tool_name="interact",
        capability="interact",
        params_cls=params_pb2.InteractParams,
        oneof_field="interact",
        description="Interact with a target tile using the X key.",
        parameters_schema=_object_schema(
            {"x": {"type": "integer"}, "y": {"type": "integer"}},
            ["x", "y"],
        ),
    ),
    CapabilitySpec(
        tool_name="use_tool",
        capability="use_tool",
        params_cls=params_pb2.UseToolParams,
        oneof_field="use_tool",
        description="Use the currently equipped tool on a target tile.",
        parameters_schema=_object_schema(
            {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "charge_level": {
                    "type": "integer",
                    "minimum": CHARGE_LEVEL_MIN,
                    "maximum": CHARGE_LEVEL_MAX,
                },
            },
            ["x", "y"],
        ),
    ),
    CapabilitySpec(
        tool_name="equip",
        capability="equip",
        params_cls=params_pb2.EquipParams,
        oneof_field="equip",
        description="Equip an item by toolbar slot or exact item name.",
        parameters_schema=_object_schema(
            {
                "slot": {"type": "integer", "minimum": EQUIP_SLOT_MIN, "maximum": EQUIP_SLOT_MAX},
                "item_name": {"type": "string", "minLength": 1},
            }
        ),
    ),
    CapabilitySpec(
        tool_name="query_inventory",
        capability="query_inventory",
        params_cls=params_pb2.QueryInventoryParams,
        oneof_field="query_inventory",
        description="Query the player's inventory.",
        parameters_schema=_object_schema({"include_details": {"type": "boolean"}}),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_location",
        capability="query_location",
        params_cls=params_pb2.QueryLocationParams,
        oneof_field="query_location",
        description="Query the player's current location.",
        parameters_schema=_object_schema(),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_map_exits",
        capability="query_map_exits",
        params_cls=params_pb2.QueryMapExitsParams,
        oneof_field="query_map_exits",
        description="Query exits from the current map.",
        parameters_schema=_object_schema(),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_runtime_snapshot",
        capability="query_runtime_snapshot",
        params_cls=query_pb2.QueryRuntimeSnapshotParams,
        oneof_field="query_runtime_snapshot",
        description="Query runtime facts such as player, world time, weather, and menu state.",
        parameters_schema=_object_schema(
            {
                "include_player": {"type": "boolean"},
                "include_world_time": {"type": "boolean"},
                "include_weather": {"type": "boolean"},
                "include_menu_state": {"type": "boolean"},
            }
        ),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_world_region",
        capability="query_world_region",
        params_cls=query_pb2.QueryWorldRegionParams,
        oneof_field="query_world_region",
        description="Query world entity and tile facts in a map region.",
        parameters_schema=_object_schema(
            {
                "map_token": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Optional map token. Empty means the player's current map.",
                },
                "region": _tile_rect_schema(
                    "Optional tile rectangle. map_id can be omitted when map_token is provided."
                ),
                "center": _tile_position_schema(
                    "Optional center tile. Use with radius; map_id can be omitted when map_token is provided."
                ),
                "radius": {"type": "integer", "minimum": 1},
                "world_entity_types": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "Optional entity type filter such as crop, tree, container, machine.",
                },
                "include_tiles": {"type": "boolean"},
                "include_characters": {"type": "boolean"},
                "include_debug": {"type": "boolean"},
            }
        ),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_inventory_snapshot",
        capability="query_inventory_snapshot",
        params_cls=query_pb2.QueryInventorySnapshotParams,
        oneof_field="query_inventory_snapshot",
        description="Query inventory facts for the player or a referenced container.",
        parameters_schema=_object_schema(
            {
                "container_kind": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Container kind. First version supports player; later refs may use chest/fridge.",
                },
                "container_ref": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Optional RefId string returned by a prior query.",
                },
                "include_empty_slots": {"type": "boolean"},
                "include_details": {"type": "boolean"},
                "include_debug": {"type": "boolean"},
            }
        ),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_ui_snapshot",
        capability="query_ui_snapshot",
        params_cls=query_pb2.QueryUiSnapshotParams,
        oneof_field="query_ui_snapshot",
        description="Query current UI/menu facts.",
        parameters_schema=_object_schema(
            {
                "include_elements": {"type": "boolean"},
                "include_debug": {"type": "boolean"},
            }
        ),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="query_inspect_refs",
        capability="query_inspect_refs",
        params_cls=query_pb2.QueryInspectRefsParams,
        oneof_field="query_inspect_refs",
        description="Inspect one or more RefId strings returned by prior query facts.",
        parameters_schema=_object_schema(
            {
                "refs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "description": "RefId strings from previous query results.",
                },
                "include_debug": {"type": "boolean"},
            },
            ["refs"],
        ),
        timeout_ms=QUERY_TIMEOUT_MS,
        default_llm_exposed=False,
    ),
    CapabilitySpec(
        tool_name="open_menu",
        capability="open_menu",
        params_cls=params_pb2.OpenMenuParams,
        oneof_field="open_menu",
        description="Open the game menu.",
        parameters_schema=_object_schema(),
    ),
    CapabilitySpec(
        tool_name="query_menu",
        capability="query_menu",
        params_cls=params_pb2.QueryMenuParams,
        oneof_field="query_menu",
        description="Query the current menu snapshot.",
        parameters_schema=_object_schema(),
        timeout_ms=QUERY_TIMEOUT_MS,
    ),
    CapabilitySpec(
        tool_name="menu_click",
        capability="menu_click",
        params_cls=params_pb2.MenuClickParams,
        oneof_field="menu_click",
        description="Navigate to a menu component and confirm it.",
        parameters_schema=_object_schema(
            {
                "component_id": {"type": "integer"},
                "component_source": {"type": "string", "enum": list(MENU_SOURCE_ENUM)},
            },
            ["component_id", "component_source"],
        ),
    ),
    CapabilitySpec(
        tool_name="menu_close",
        capability="menu_close",
        params_cls=params_pb2.MenuCloseParams,
        oneof_field="menu_close",
        description="Close the current menu.",
        parameters_schema=_object_schema(),
    ),
    CapabilitySpec(
        tool_name="tp",
        capability="tp",
        params_cls=params_pb2.TpParams,
        oneof_field="tp",
        description="Internal diagnostic teleport. Never exposed to LLM.",
        parameters_schema=_object_schema(),
        exposed_to_llm=False,
        default_llm_exposed=False,
        internal_only=True,
    ),
)

SPECS_BY_TOOL: Mapping[str, CapabilitySpec] = MappingProxyType(
    {spec.tool_name: spec for spec in CAPABILITY_SPECS}
)

SPECS_BY_CAPABILITY: Mapping[str, CapabilitySpec] = MappingProxyType(
    {spec.capability: spec for spec in CAPABILITY_SPECS}
)

LLM_EXPOSED_CAPABILITIES = frozenset(
    spec.capability
    for spec in CAPABILITY_SPECS
    if (
        spec.exposed_to_llm
        and spec.default_llm_exposed
        and not spec.internal_only
        and spec.capability not in INTERNAL_ONLY_CAPABILITIES
    )
)


def get_spec(tool_name: str) -> CapabilitySpec:
    return SPECS_BY_TOOL[tool_name]


def iter_llm_exposed_specs() -> tuple[CapabilitySpec, ...]:
    return tuple(
        spec
        for spec in CAPABILITY_SPECS
        if (
            spec.exposed_to_llm
            and spec.default_llm_exposed
            and not spec.internal_only
            and spec.capability not in INTERNAL_ONLY_CAPABILITIES
        )
    )
