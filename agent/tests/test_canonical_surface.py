from __future__ import annotations

import json
from pathlib import Path
import pytest

from protocol.canonical_surface import (
    V2_PRODUCTION_CAPABILITIES,
    canonical_specs,
    map_canonical_command,
    map_v2_compatibility_alias,
    normalize_v2_alias_output,
    _validate_json,
)
from protocol.tool_mapper import ProtocolMappingError


POSITIVE_ARGUMENTS = {
    "say": {"message": "hello"},
    "emote": {"emote_type": "happy"},
    "face": {"direction": "up"},
    "move_to": {"mode": "exact", "x": 1, "y": 2},
    "go_to": {"map_id": "Farm", "x": 1, "y": 2, "arrival_mode": "exact"},
    "go_to_bed": {},
    "interact": {"x": 1, "y": 2},
    "use_tool": {"x": 1, "y": 2},
    "equip": {"slot": 1},
    "query_runtime_snapshot": {},
    "query_world_region": {},
    "query_inventory_snapshot": {},
    "query_ui_snapshot": {},
    "query_inspect_refs": {"refs": ["world_entity:tree:farm:1:2"]},
    "open_menu": {},
    "query_menu": {},
    "menu_click": {"component_id": 1, "component_source": "page"},
    "menu_close": {},
    "water_crops": {"max_count": 3},
    "harvest_crops": {"max_count": 3, "replant_seed": "Parsnip Seeds"},
    "ship_items": {"mode": "execute", "items": ["Parsnip"]},
    "buy_items": {"shop": "SeedShop", "items": [{"name": "Parsnip Seeds", "quantity": 2}]},
    "plant_crops": {"seed": "Parsnip Seeds"},
    "chop_trees": {"include_stumps": True},
    "mine_rocks": {"max_count": 2},
    "execute_on_targets": {
        "targets": ["Farm:tile_1_2"], "action": "use_tool", "tool": "Axe",
    },
    "menu_interact": {
        "menu_type": "Shop", "operations": [{"action": "buy", "item": "Parsnip Seeds"}],
    },
    "navigate_to_target": {"target": "[npc:Lewis]"},
    "act_on_target": {"intent": "pet", "target": "Farm:animal_Bessie"},
    "move_to_target": {"target": "Farm:animal_Bessie", "arrive_distance": 1},
    "use_tool_at_target": {},
    "equip_matching_item": {"item": "Axe"},
    "observe_current_view": {},
    "sleep_for_night": {},
}

V2_HANDLER_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures/v2-compatibility-handler-results.json").read_text()
)


def test_every_canonical_public_spec_has_positive_mapper_and_preserves_identity():
    specs = canonical_specs()
    assert set(POSITIVE_ARGUMENTS) <= set(specs)
    assert set(specs) - set(POSITIVE_ARGUMENTS) == {
        "query_inventory", "query_location", "query_map_exits",
    }
    for name, arguments in POSITIVE_ARGUMENTS.items():
        command_id = f"caller-{name}"
        mapped = map_canonical_command(name, arguments, command_id=command_id)
        assert mapped.command_id == command_id
        assert mapped.payload.get("command_id", mapped.payload.get("action_id")) == command_id
        assert mapped.capability == specs[name].wire_capability
        assert mapped.timeout_ms == specs[name].timeout_ms
        assert mapped.execution_model == specs[name].execution_model
        assert mapped.cancellable == specs[name].cancellable


@pytest.mark.parametrize("name", sorted(POSITIVE_ARGUMENTS))
def test_every_mapper_rejects_schema_invalid_unknown_argument(name):
    with pytest.raises(ProtocolMappingError, match="schema"):
        map_canonical_command(name, {**POSITIVE_ARGUMENTS[name], "authority_context": "attack"}, command_id="cmd")


@pytest.mark.parametrize("name", sorted(POSITIVE_ARGUMENTS))
def test_every_nonempty_input_schema_rejects_a_capability_field_type_mutation(name):
    arguments = POSITIVE_ARGUMENTS[name]
    if not arguments:
        return
    mutated = dict(arguments)
    field = next(iter(mutated))
    value = mutated[field]
    mutated[field] = [] if not isinstance(value, list) else "not-an-array"
    with pytest.raises(ProtocolMappingError):
        map_canonical_command(name, mutated, command_id="mutated")


def test_v2_production_set_is_exactly_the_nine_frozen_compound_capabilities():
    assert V2_PRODUCTION_CAPABILITIES == {
        "water_crops", "harvest_crops", "ship_items", "buy_items", "plant_crops",
        "chop_trees", "mine_rocks", "execute_on_targets", "menu_interact",
    }
    for name in V2_PRODUCTION_CAPABILITIES:
        assert map_canonical_command(name, POSITIVE_ARGUMENTS[name], command_id="stable").wire_format == "v2-json"


def test_semantically_distinct_v2_sources_have_distinct_canonical_names_and_wire_mapping():
    navigate = map_canonical_command("navigate_to_target", {"target": "Lewis"}, command_id="nav")
    act = map_canonical_command("act_on_target", {"intent": "pet"}, command_id="act")
    assert (navigate.capability, navigate.payload["capability"]) == ("go_to", "go_to")
    assert (act.capability, act.payload["capability"]) == ("do", "do")
    with pytest.raises(ProtocolMappingError):
        map_canonical_command("go_to", {"target": "Lewis"}, command_id="wrong-contract")
    with pytest.raises(ProtocolMappingError):
        map_canonical_command("interact", {"intent": "pet"}, command_id="wrong-contract")


def test_execute_on_targets_enforces_action_dependent_contract():
    with pytest.raises(ProtocolMappingError, match="tool or tool_category"):
        map_canonical_command(
            "execute_on_targets", {"targets": ["Farm:tile_1_2"], "action": "use_tool"}, command_id="a"
        )


@pytest.mark.parametrize(
    "name,expected_params",
    [
        ("say", {"message": "hello"}),
        ("emote", {"type": "happy"}),
        ("face", {"direction": "up"}),
        ("interact", {"x": 1, "y": 2}),
        ("open_menu", {}),
        ("menu_click", {"component_id": 1, "source": "page"}),
        ("menu_close", {}),
    ],
)
def test_v2_compatibility_alias_uses_same_canonical_contract_and_identity(name, expected_params):
    canonical = map_canonical_command(
        name, POSITIVE_ARGUMENTS[name], command_id=f"alias-{name}"
    )
    mapped = map_v2_compatibility_alias(
        name, POSITIVE_ARGUMENTS[name], command_id=f"alias-{name}"
    )
    assert mapped.command_id == f"alias-{name}"
    assert mapped.payload["action_id"] == f"alias-{name}"
    assert mapped.payload["params"] == expected_params
    assert mapped.capability == canonical.capability
    assert mapped.timeout_ms == canonical.timeout_ms
    assert mapped.execution_model == canonical.execution_model
    assert mapped.cancellable == canonical.cancellable


@pytest.mark.parametrize("name", sorted(V2_HANDLER_FIXTURES))
def test_v2_alias_real_handler_fixture_normalizes_to_canonical_output_schema(name):
    fixture = V2_HANDLER_FIXTURES[name]
    spec = canonical_specs()[name]
    assert fixture["arguments"] == POSITIVE_ARGUMENTS[name]
    normalized = normalize_v2_alias_output(
        name, fixture["arguments"], fixture["source_data"]
    )
    envelope = {
        "command_id": "cmd", "capability": spec.wire_capability,
        "success": True, "status": "COMPLETED", "data": normalized,
    }
    _validate_json(spec.output_schema, envelope)


@pytest.mark.parametrize("name", sorted(V2_HANDLER_FIXTURES))
def test_v2_alias_rejects_result_data_outside_real_handler_contract(name):
    with pytest.raises(ProtocolMappingError):
        normalize_v2_alias_output(name, POSITIVE_ARGUMENTS[name], {"fabricated": True})


def test_renamed_v2_contracts_enforce_real_conditionals():
    with pytest.raises(ProtocolMappingError, match="x and y together"):
        map_canonical_command("use_tool_at_target", {"x": 1}, command_id="bad")
    with pytest.raises(ProtocolMappingError, match="target or x/y"):
        map_canonical_command("move_to_target", {}, command_id="bad")
    with pytest.raises(ProtocolMappingError, match="item or slot"):
        map_canonical_command("equip_matching_item", {}, command_id="bad")
    with pytest.raises(ProtocolMappingError, match="requires intent"):
        map_canonical_command(
            "execute_on_targets", {"targets": ["Farm:animal_Bessie"], "action": "do"}, command_id="b"
        )


def test_equip_matching_item_translates_public_v2_selectors_to_registered_handler_contract():
    by_item = map_canonical_command(
        "equip_matching_item", {"item": "Axe"}, command_id="equip-item"
    )
    assert by_item.payload["params"] == {"item_name": "Axe"}

    by_slot = map_canonical_command(
        "equip_matching_item", {"slot": 0}, command_id="equip-slot"
    )
    assert by_slot.payload["params"] == {"slot": 1}


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("say", {"message": ""}),
        ("emote", {"emote_type": "not-real"}),
        ("move_to", {"mode": "exact", "x": 1}),
        ("go_to", {"map_id": "Farm", "x": 1, "y": 2, "arrival_mode": "warp"}),
        ("query_inspect_refs", {"refs": []}),
        ("water_crops", {"max_count": 0}),
        ("buy_items", {"shop": "SeedShop", "items": [{"name": "Seeds", "quantity": 0}]}),
        ("execute_on_targets", {"targets": [], "action": "do", "intent": "pet"}),
        ("menu_interact", {"menu_type": "Shop", "operations": []}),
        ("navigate_to_target", {"target": ""}),
        ("act_on_target", {"intent": ""}),
    ],
)
def test_required_enum_min_nested_and_conditional_contract_examples(name, arguments):
    with pytest.raises(ProtocolMappingError):
        map_canonical_command(name, arguments, command_id="invalid")
