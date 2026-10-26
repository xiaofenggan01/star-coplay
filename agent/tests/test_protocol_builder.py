from __future__ import annotations

import pytest

from agent.generated.protocol import capability_params_pb2, capability_pb2, transport_pb2
from agent.protocol.capability_registry import (
    CAPABILITY_SPECS,
    CHARGE_LEVEL_MAX,
    LLM_EXPOSED_CAPABILITIES,
    SPECS_BY_TOOL,
    iter_llm_exposed_specs,
)
from agent.protocol.command_builder import build_command_request
from agent.protocol.constants import PROTOCOL_VERSION
from agent.protocol.envelope_builder import build_command_envelope, serialize_envelope
from agent.protocol.schemas import get_protocol_tool_schemas, get_static_protocol_tool_schemas
from agent.protocol.tool_mapper import ProtocolMappingError, map_tool_call
from agent.slow_loop.core_tools import get_core_tool_schemas


VALID_ARGS = {
    "say": {"message": "Hi"},
    "emote": {"emote_type": "happy"},
    "face": {"direction": "down"},
    "move_to": {"mode": "exact", "x": 0, "y": 12},
    "go_to": {"map_id": "Farm", "x": 64, "y": 15, "arrival_mode": "exact"},
    "go_to_bed": {"timeout_frames": 1200},
    "interact": {"x": 10, "y": 11},
    "use_tool": {"x": 10, "y": 11, "charge_level": 0},
    "equip": {"slot": 1},
    "query_inventory": {"include_details": True},
    "query_location": {},
    "query_map_exits": {},
    "query_runtime_snapshot": {"include_player": True, "include_world_time": True},
    "query_world_region": {
        "map_token": "Farm",
        "center": {"x": 64, "y": 15},
        "radius": 8,
        "world_entity_types": ["crop", "tree"],
        "include_characters": True,
    },
    "query_inventory_snapshot": {"container_kind": "player", "include_details": True},
    "query_ui_snapshot": {"include_elements": True},
    "query_inspect_refs": {"refs": ["world_entity:tree:farm:41:12#wt_1"]},
    "open_menu": {},
    "query_menu": {},
    "menu_click": {"component_id": 1001, "component_source": "page"},
    "menu_close": {},
}


def test_generated_protocol_classes_import() -> None:
    assert transport_pb2.Envelope.DESCRIPTOR.full_name == "starcoplay.protocol.v245.Envelope"
    assert (
        capability_pb2.CommandRequest.DESCRIPTOR.full_name
        == "starcoplay.protocol.v245.CommandRequest"
    )


def test_registry_covers_protocol_oneof_fields() -> None:
    oneof_fields = {
        field.name
        for field in capability_pb2.CommandRequest.DESCRIPTOR.oneofs_by_name["params"].fields
    }
    for spec in CAPABILITY_SPECS:
        assert spec.oneof_field in oneof_fields
        assert spec.capability == spec.oneof_field
        assert spec.timeout_ms > 0

    assert "tp" not in LLM_EXPOSED_CAPABILITIES
    assert "query_location" not in LLM_EXPOSED_CAPABILITIES
    assert all(spec.tool_name != "tp" for spec in iter_llm_exposed_specs())
    assert all(spec.tool_name != "query_location" for spec in iter_llm_exposed_specs())


def test_schema_runtime_gate_filters_disabled_and_internal_tools() -> None:
    assert get_protocol_tool_schemas(None) == []

    schemas = get_protocol_tool_schemas(["say", "tp", "query_location"])
    names = {schema["function"]["name"] for schema in schemas}
    assert names == {"say"}


def test_static_schemas_do_not_leak_transport_or_unsupported_tools() -> None:
    schemas = get_static_protocol_tool_schemas()
    names = {schema["function"]["name"] for schema in schemas}
    assert "tp" not in names
    assert {"observe", "search_wiki", "read_wiki", "remember"}.isdisjoint(names)

    forbidden_keys = {
        "$ref",
        "protocol_version",
        "message_id",
        "request_id",
        "session_id",
        "timestamp_ms",
        "command_id",
        "timeout_ms",
        "source",
        "capability",
    }
    for schema in schemas:
        keys = _walk_keys(schema)
        assert forbidden_keys.isdisjoint(keys)

    go_to_schema = next(schema for schema in schemas if schema["function"]["name"] == "go_to")
    assert "target" not in go_to_schema["function"]["parameters"]["properties"]

    equip_schema = next(schema for schema in schemas if schema["function"]["name"] == "equip")
    assert "item_category" not in equip_schema["function"]["parameters"]["properties"]

    use_tool_schema = next(schema for schema in schemas if schema["function"]["name"] == "use_tool")
    assert (
        use_tool_schema["function"]["parameters"]["properties"]["charge_level"]["maximum"]
        == CHARGE_LEVEL_MAX
    )


def test_core_tools_default_to_protocol_registry_schemas() -> None:
    protocol_schemas = {
        schema["function"]["name"]: schema["function"]["parameters"]
        for schema in get_static_protocol_tool_schemas()
    }
    core_schemas = {
        schema["function"]["name"]: schema["function"]["parameters"]
        for schema in get_core_tool_schemas()
    }

    assert core_schemas == protocol_schemas
    assert "tp" not in core_schemas
    assert {"observe", "search_wiki", "read_wiki", "remember"}.isdisjoint(core_schemas)


@pytest.mark.parametrize(
    "tool_name",
    [spec.tool_name for spec in iter_llm_exposed_specs()],
)
def test_mapper_and_command_builder_cover_all_visible_tools(tool_name: str) -> None:
    mapped_commands = map_tool_call(tool_name, VALID_ARGS[tool_name])
    assert len(mapped_commands) == 1
    mapped = mapped_commands[0]
    spec = SPECS_BY_TOOL[tool_name]
    assert mapped.capability == spec.capability
    assert isinstance(mapped.params, spec.params_cls)

    request = build_command_request(mapped, command_id=f"cmd_{tool_name}")
    assert request.command_id == f"cmd_{tool_name}"
    assert request.source == capability_pb2.COMMAND_SOURCE_LLM
    assert request.timeout_ms == spec.timeout_ms
    assert request.capability == spec.capability
    assert request.WhichOneof("params") == spec.oneof_field


def test_say_with_emote_returns_explicit_command_list() -> None:
    mapped = map_tool_call("say", {"message": "Hi", "emote": "happy"})
    assert [command.oneof_field for command in mapped] == ["say", "emote"]
    assert [command.capability for command in mapped] == ["say", "emote"]


def test_mode_specific_arguments_are_explicit() -> None:
    edge = map_tool_call("move_to", {
        "mode": "edge_warp",
        "x": 1,
        "y": 2,
        "direction": "up",
    })[0]
    assert edge.params.mode == capability_params_pb2.MOVE_TO_EDGE_WARP
    assert edge.params.direction

    adjacent = map_tool_call("go_to", {
        "map_id": "Farm",
        "x": 1,
        "y": 2,
        "arrival_mode": "adjacent",
        "stand_side": "down",
    })[0]
    assert adjacent.params.arrival_mode
    assert adjacent.params.stand_side


def test_required_presence_distinguishes_zero_from_missing() -> None:
    mapped = map_tool_call("interact", {"x": 0, "y": 0})[0]
    assert mapped.params.target_tile.x == 0
    assert mapped.params.target_tile.y == 0

    with pytest.raises(ProtocolMappingError, match="missing required argument: y"):
        map_tool_call("interact", {"x": 0})


def test_query_fact_mapper_builds_nested_protocol_params() -> None:
    world = map_tool_call(
        "query_world_region",
        {
            "map_token": "Farm",
            "region": {"x": 10, "y": 20, "width": 5, "height": 6},
            "center": {"x": 12, "y": 22},
            "radius": 4,
            "world_entity_types": ["crop"],
            "include_tiles": True,
            "include_debug": True,
        },
    )[0]
    assert world.params.region.map_id == "Farm"
    assert world.params.region.width == 5
    assert world.params.center.map_id == "Farm"
    assert list(world.params.world_entity_types) == ["crop"]
    assert world.params.include_tiles is True
    assert world.params.include_debug is True

    inspected = map_tool_call(
        "query_inspect_refs",
        {"refs": ["world_entity:tree:farm:41:12#wt_1"], "include_debug": True},
    )[0]
    assert inspected.params.refs[0].value == "world_entity:tree:farm:41:12#wt_1"
    assert inspected.params.include_debug is True


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("face", {"direction": "north"}),
        ("move_to", {"mode": "edge_warp", "x": 1, "y": 2}),
        ("move_to", {"mode": "adjacent", "x": 1, "y": 2}),
        ("move_to", {"mode": "exact", "x": 1, "y": 2, "adjacent_side": "down"}),
        ("go_to", {"map_id": "Farm", "x": 1, "y": 2, "arrival_mode": "adjacent"}),
        ("go_to", {"map_id": "Farm", "x": 1, "y": 2, "arrival_mode": "exact", "stand_side": "down"}),
        ("use_tool", {"x": 1, "y": 2, "charge_level": CHARGE_LEVEL_MAX + 1}),
        ("equip", {"slot": 1, "item_name": "Axe"}),
        ("equip", {"item_category": "axe"}),
        ("query_world_region", {"radius": 0}),
        ("query_inspect_refs", {"refs": []}),
        ("tp", {}),
    ],
)
def test_mapper_rejects_invalid_or_internal_inputs(tool_name: str, args: dict) -> None:
    with pytest.raises(ProtocolMappingError):
        map_tool_call(tool_name, args)


def test_command_builder_rejects_invalid_transport_fields() -> None:
    mapped = map_tool_call("query_location", {})[0]

    with pytest.raises(ProtocolMappingError, match="timeout_ms must be > 0"):
        build_command_request(mapped, timeout_ms=0)

    with pytest.raises(ProtocolMappingError, match="source must not"):
        build_command_request(mapped, source=capability_pb2.COMMAND_SOURCE_UNSPECIFIED)


def test_envelope_serialization_roundtrip() -> None:
    mapped = map_tool_call("query_location", {})[0]
    request = build_command_request(mapped, command_id="cmd_roundtrip")
    envelope = build_command_envelope(
        request,
        session_id="session_1",
        message_id="msg_1",
        timestamp_ms=123456789,
    )

    assert envelope.protocol_version == PROTOCOL_VERSION
    assert envelope.WhichOneof("body") == "command_request"
    assert envelope.command_request.WhichOneof("params") == "query_location"

    parsed = transport_pb2.Envelope()
    parsed.ParseFromString(serialize_envelope(envelope))
    assert parsed == envelope


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_walk_keys(nested))
    return keys
