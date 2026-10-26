from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import modules.mcp.manifest as manifest_module
from modules.mcp.core.catalog import validate_arguments
from modules.mcp.core.errors import McpError
from modules.mcp.manifest import build_catalog, build_manifest, build_tool_catalog


REPO_ROOT = Path(__file__).resolve().parents[4]


def _surface():
    manifest = build_manifest(REPO_ROOT)
    return manifest, build_catalog(manifest)


def _minimal(schema):
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {
            name: _minimal(schema["properties"][name])
            for name in schema.get("required", [])
        }
    if kind == "array":
        return [_minimal(schema["items"])] if schema.get("minItems", 0) else []
    if kind == "string":
        return "x" * max(1, schema.get("minLength", 0))
    if kind == "integer":
        return schema.get("minimum", 0)
    if kind == "number":
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


def test_generated_matrix_covers_inventory_once_and_has_unique_public_names():
    manifest, catalog = _surface()
    covered = [source for entry in catalog for source in entry.source_manifest_ids]
    assert sorted(covered) == sorted(row.capability_id for row in manifest)
    assert len(covered) == len(set(covered))
    public = [entry for entry in catalog if entry.exposure_kind == "tool"]
    assert len({entry.canonical_name for entry in public}) == len(public)
    assert not any(entry.parity_status in {"unmapped", "planned"} for entry in public)
    assert not any(entry.mapping_type == "compatibility-source" for entry in catalog)


def test_public_surface_contains_all_proto_production_and_nine_v2_compounds():
    manifest, catalog = _surface()
    public = {entry.canonical_name: entry for entry in catalog if entry.exposure_kind == "tool"}
    proto = {
        row.capability_id.split(":", 1)[1]
        for row in manifest
        if row.capability_id.startswith("proto:") and row.registered
        and row.product_classification == "production"
    }
    compounds = {
        "water_crops", "harvest_crops", "ship_items", "buy_items", "plant_crops",
        "chop_trees", "mine_rocks", "execute_on_targets", "menu_interact",
    }
    renamed = {
        "navigate_to_target", "act_on_target", "move_to_target",
        "use_tool_at_target", "equip_matching_item", "observe_current_view",
        "sleep_for_night",
    }
    assert set(public) == proto | compounds | renamed
    assert {"query_inventory", "query_location", "query_map_exits"}.isdisjoint(public)


def test_every_public_entry_has_real_input_output_schema_and_truthful_profile_evidence():
    _, catalog = _surface()
    local_adapter_verified = {"go_to", "interact", "query_world_region", "say"}
    for entry in catalog:
        if entry.exposure_kind != "tool":
            continue
        assert entry.input_schema.get("type") == "object"
        assert entry.output_schema.get("type") == "object"
        assert entry.output_schema.get("required")
        assert entry.profile_support == {
            "hosted": "product-transport-verified",
            "local_direct": (
                "adapter-contract-verified"
                if entry.canonical_name in local_adapter_verified
                else "schema-mapper-verified"
            ),
            "local_gateway": "deferred",
        }


def test_every_public_schema_accepts_positive_output_and_rejects_negative_output():
    _, catalog = _surface()
    for entry in catalog:
        if entry.exposure_kind != "tool":
            continue
        good = {
            "command_id": "cmd",
            "capability": entry.output_schema["properties"]["capability"]["enum"][0],
            "success": True, "status": "COMPLETED",
            "data": _minimal(entry.output_schema["properties"]["data"]),
        }
        assert validate_arguments(entry.output_schema, good) == good
        with pytest.raises(McpError):
            validate_arguments(entry.output_schema, {"success": True})


def test_tool_catalog_is_built_directly_from_entries_and_has_stable_digest():
    manifest, entries = _surface()
    first = build_tool_catalog(manifest)
    second = build_tool_catalog(manifest)
    assert first.digest == second.digest
    assert set(first._tools) == {
        entry.canonical_name for entry in entries if entry.exposure_kind == "tool"
    }
    assert all(tool.output_schema for tool in first._tools.values())


def test_only_legal_explicit_exceptions_remain_and_they_are_governed():
    _, catalog = _surface()
    legal = {"declared-not-registered", "dev-only", "internal-control"}
    governed = [entry for entry in catalog if entry.exception_decision is not None]
    assert governed
    assert {entry.mapping_type for entry in governed} <= legal
    now = datetime.now(timezone.utc)
    for entry in governed:
        assert entry.decision_id and entry.decision_owner and entry.decision_reason
        assert datetime.fromisoformat(entry.expires_at.replace("Z", "+00:00")) > now


def test_ship_items_truthfully_has_synchronous_non_cancellable_lifecycle():
    manifest, catalog = _surface()
    source = next(row for row in manifest if row.capability_id == "v2:ship_items")
    entry = next(row for row in catalog if row.canonical_name == "ship_items")
    assert source.execution_model == "request_result"
    assert source.cancellable is False
    assert entry.lifecycle_support == ("dispatch", "result")


def test_sleep_for_night_is_distinct_from_proto_go_to_bed_and_uses_real_v2_contract():
    _, catalog = _surface()
    sleep = next(row for row in catalog if row.canonical_name == "sleep_for_night")
    proto = next(row for row in catalog if row.canonical_name == "go_to_bed")
    assert sleep.source_manifest_ids == ("v2:go_bed",)
    assert sleep.mapping_type == "renamed-compatibility-adapter"
    assert proto.source_manifest_ids == ("proto:go_to_bed",)
    assert sleep.input_schema == {
        "type": "object", "properties": {}, "required": [], "additionalProperties": False,
    }
    data = sleep.output_schema["properties"]["data"]
    assert set(data["required"]) == {
        "method", "home_location", "sleep_triggered", "stopped_reason",
    }
    assert sleep.lifecycle_support == ("dispatch", "result", "status", "cancel")


def test_each_public_output_data_schema_rejects_critical_field_type_mutation():
    _, catalog = _surface()
    wrong = {
        "string": {}, "integer": "bad", "number": "bad", "boolean": "bad",
        "array": {}, "object": [],
    }
    for entry in catalog:
        if entry.exposure_kind != "tool":
            continue
        properties = entry.output_schema["properties"]["data"].get("properties", {})
        assert properties, entry.canonical_name
        field, field_schema = next(iter(properties.items()))
        payload = {
            "command_id": "cmd",
            "capability": entry.output_schema["properties"]["capability"]["enum"][0],
            "success": True, "status": "COMPLETED",
            "data": {field: wrong[field_schema["type"]]},
        }
        with pytest.raises(McpError):
            validate_arguments(entry.output_schema, payload)


def test_unknown_registered_production_capability_is_a_hard_drift_failure(monkeypatch):
    manifest = build_manifest(REPO_ROOT)
    prototype = next(row for row in manifest if row.capability_id == "proto:say")
    mutated = [*manifest, replace(prototype, capability_id="proto:new_public_action")]
    with pytest.raises(ValueError, match="no canonical schema"):
        build_catalog(mutated)


def test_unknown_v2_registration_requires_product_classification(tmp_path):
    # Classification is derived from source registration facts; a newly
    # registered V2 source must not silently become a compatibility exception.
    import shutil
    root = tmp_path / "repo"
    for relative in ("protocol/proto", "mod/StarCoPlay/ProtocolCore/Handlers", "mod/StarCoPlay/Legacy/AdapterRuntime"):
        shutil.copytree(REPO_ROOT / relative, root / relative)
    target = root / "mod/StarCoPlay/ModEntry.cs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((REPO_ROOT / "mod/StarCoPlay/ModEntry.cs").read_text().replace(
        "_adapterV2.Register(\"test_input\", HandleTestInputCommand);",
        "_adapterV2.Register(\"new_runtime_capability\", HandleTestInputCommand);\n"
        "_adapterV2.Register(\"test_input\", HandleTestInputCommand);",
    ))
    with pytest.raises(ValueError, match="requires product classification"):
        build_catalog(build_manifest(root))
