from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / "platform/backend/modules/mcp/skills/builtin"


def _program(skill_name: str):
    path = SKILLS / f"stardew.advanced.{skill_name}" / "implementation.py"
    spec = importlib.util.spec_from_file_location(f"hosted_{skill_name}_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


class FakeContext:
    def __init__(self, outputs=None):
        self.calls = []
        self.outputs = list(outputs or [])

    async def invoke(self, capability_id, version, arguments):
        self.calls.append((capability_id, version, dict(arguments)))
        output = self.outputs.pop(0) if self.outputs else {
            "success": True,
            "status": "COMPLETED",
            "data": {},
        }
        return SimpleNamespace(output=output)


@pytest.mark.asyncio
async def test_water_crops_delegates_once_to_v2_compound():
    ctx = FakeContext([{
        "success": True,
        "status": "COMPLETED",
        "observation": "Watered 12 crops.",
        "data": {"watered": 12, "skipped": 0},
    }])

    result = await _program("water_crops")(ctx, {"max_count": 20})

    assert ctx.calls == [("water_crops", ">=2.4.5,<3.0.0", {"max_count": 20})]
    assert result["status"] == "completed"
    assert json.loads(result["data_json"])["watered"] == 12


@pytest.mark.asyncio
async def test_chop_trees_uses_registered_compound_contract():
    ctx = FakeContext()

    await _program("chop_trees")(
        ctx, {"max_count": 4, "include_stumps": True},
    )

    assert ctx.calls == [(
        "chop_trees", ">=2.4.5,<3.0.0",
        {"max_count": 4, "include_stumps": True},
    )]


@pytest.mark.asyncio
async def test_harvest_crops_preserves_optional_replant_request():
    ctx = FakeContext()

    await _program("harvest_crops")(
        ctx, {"max_count": 30, "replant_seed": "Parsnip Seeds"},
    )

    assert ctx.calls[0][0] == "harvest_crops"
    assert ctx.calls[0][2] == {
        "max_count": 30, "replant_seed": "Parsnip Seeds",
    }


@pytest.mark.asyncio
async def test_go_shopping_walks_to_counter_before_buying():
    ctx = FakeContext()

    await _program("go_shopping")(ctx, {
        "shop": "SeedShop",
        "items": [{"item": "Parsnip Seeds", "qty": 10}],
    })

    assert ctx.calls[0] == (
        "go_to", ">=2.4.5,<3.0.0", {
            "map_id": "SeedShop",
            "x": 4,
            "y": 19,
            "arrival_mode": "adjacent",
            "stand_side": "down",
        },
    )
    assert ctx.calls[1][0] == "buy_items"
    assert ctx.calls[1][2] == {
        "shop": "SeedShop",
        "items": [{"name": "Parsnip Seeds", "quantity": 10}],
    }


@pytest.mark.asyncio
async def test_go_shopping_does_not_buy_when_navigation_fails():
    class NavigationFailureContext(FakeContext):
        async def invoke(self, capability_id, version, arguments):
            self.calls.append((capability_id, version, dict(arguments)))
            raise RuntimeError("navigation failed")

    ctx = NavigationFailureContext()

    with pytest.raises(RuntimeError, match="navigation failed"):
        await _program("go_shopping")(ctx, {
            "shop": "SeedShop",
            "items": [{"item": "Parsnip Seeds", "qty": 10}],
        })

    assert [call[0] for call in ctx.calls] == ["go_to"]


@pytest.mark.asyncio
async def test_ship_items_keeps_dry_run_non_mutating():
    ctx = FakeContext()

    await _program("ship_items")(ctx, {
        "items": ["Parsnip"], "exclude": [], "dry_run": True,
    })

    assert ctx.calls[0][0] == "ship_items"
    assert ctx.calls[0][2] == {
        "items": ["Parsnip"], "exclude": [], "mode": "plan",
    }


@pytest.mark.asyncio
async def test_go_bed_uses_non_teleporting_proto_capability():
    ctx = FakeContext()

    await _program("go_bed")(ctx, {})

    assert ctx.calls == [("go_to_bed", ">=2.4.5,<3.0.0", {})]


@pytest.mark.asyncio
async def test_observe_uses_registered_compact_view_capability():
    ctx = FakeContext()

    result = await _program("observe")(ctx, {})

    assert ctx.calls == [("observe_current_view", ">=2.4.5,<3.0.0", {})]
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_inspect_preserves_opaque_refs_without_parsing_them():
    ctx = FakeContext()
    refs = ["world_entity:tree:farm:41:12#wt_1"]

    await _program("inspect")(ctx, {"refs": refs})

    assert ctx.calls[0] == (
        "query_inspect_refs", ">=2.4.5,<3.0.0",
        {"refs": refs, "include_debug": False},
    )


@pytest.mark.asyncio
async def test_search_facts_filters_and_pages_compact_world_records():
    ctx = FakeContext([{
        "success": True,
        "status": "COMPLETED",
        "data": {"entities": [
            {"ref_id": "crop:1", "kind": "crop", "ready": True},
            {"ref_id": "crop:2", "kind": "crop", "ready": False},
        ]},
    }])

    result = await _program("search_facts")(ctx, {
        "fact_type": "crop", "filters_json": '{"ready":true}', "limit": 1,
    })

    assert ctx.calls[0][0] == "query_world_region"
    assert ctx.calls[0][2]["world_entity_types"] == ["crop"]
    assert json.loads(result["data_json"])["items"] == [
        {"ref_id": "crop:1", "kind": "crop", "ready": True},
    ]


@pytest.mark.asyncio
async def test_search_facts_rejects_non_object_filter_json():
    ctx = FakeContext([{
        "success": True, "status": "COMPLETED", "data": {"entities": []},
    }])

    with pytest.raises(ValueError, match="must encode an object"):
        await _program("search_facts")(ctx, {
            "fact_type": "crop", "filters_json": "[]",
        })


@pytest.mark.asyncio
async def test_find_interactions_returns_only_bounded_ref_candidates():
    ctx = FakeContext([{
        "success": True, "status": "COMPLETED",
        "data": {"entities": [
            {"ref_id": "npc:Lewis", "kind": "npc"},
            {"ref_id": "crop:1", "kind": "crop"},
        ]},
    }])

    result = await _program("find_interactions")(ctx, {"limit": 1, "offset": 1})

    assert ctx.calls[0][0] == "query_world_region"
    data = json.loads(result["data_json"])
    assert data["items"] == [{"ref_id": "crop:1", "kind": "crop"}]
    assert data["has_more"] is False


@pytest.mark.asyncio
async def test_walk_route_executes_waypoints_serially_and_reports_counts_only():
    ctx = FakeContext()
    waypoints = [
        {"map_id": "Farm", "x": 10, "y": 20},
        {"map_id": "Town", "x": 30, "y": 40},
    ]

    result = await _program("walk_route")(ctx, {"waypoints": waypoints})

    assert [call[0] for call in ctx.calls] == ["go_to", "go_to"]
    assert json.loads(result["data_json"]) == {
        "completed_waypoints": 2,
        "total_waypoints": 2,
        "stopped_reason": "route_completed",
    }


@pytest.mark.asyncio
async def test_pet_animals_discovers_refs_then_uses_batch_action():
    ctx = FakeContext([
        {
            "success": True, "status": "COMPLETED",
            "data": {"player_location": {
                "map_token": "Farm@save-1", "x": 64, "y": 15,
            }},
        },
        {
            "success": True, "status": "COMPLETED",
            "data": {"characters": [
                {"ref_id": "animal:1", "position": {"x": 90, "y": 15},
                 "farm_animal": {"was_petted": False}},
                {"ref_id": "animal:2", "position": {"x": 91, "y": 15},
                 "farm_animal": {"was_petted": False}},
            ]},
        },
        {"success": True, "status": "COMPLETED",
         "data": {"completed": 2, "skipped": 0, "total": 2}},
    ])

    result = await _program("pet_animals")(ctx, {"max_count": 2})

    assert [call[0] for call in ctx.calls] == [
        "query_runtime_snapshot", "query_world_region", "execute_on_targets",
    ]
    assert ctx.calls[1][2] == {
        "map_token": "Farm@save-1",
        "center": {"map_id": "Farm@save-1", "x": 64, "y": 15},
        "radius": 50,
        "world_entity_types": ["farm_animal", "pet"],
        "include_characters": True,
        "include_tiles": False,
    }
    assert ctx.calls[2][2]["targets"] == ["animal:1", "animal:2"]
    assert ctx.calls[2][2]["intent"] == "pet"
    assert result["status"] == "completed"
    assert json.loads(result["data_json"])["petted"] == 2


@pytest.mark.asyncio
async def test_pet_animals_reports_zero_targets_without_claiming_successful_pet():
    ctx = FakeContext([
        {"success": True, "status": "COMPLETED", "data": {
            "player_location": {"map_token": "Farm", "x": 10, "y": 20},
        }},
        {"success": True, "status": "COMPLETED", "data": {"characters": [
            {"ref_id": "animal:done", "farm_animal": {"was_petted": True}},
            {"ref_id": "pet:done", "pet": {"petted_today": True}},
        ]}},
    ])

    result = await _program("pet_animals")(ctx, {})

    assert [call[0] for call in ctx.calls] == [
        "query_runtime_snapshot", "query_world_region",
    ]
    assert result["status"] == "completed"
    assert "No unpetted" in result["summary"]
    assert json.loads(result["data_json"]) == {
        "petted": 0, "skipped": 0, "targets_found": 0,
    }


@pytest.mark.asyncio
async def test_pet_animals_reports_partial_and_zero_execution_results_accurately():
    base_outputs = [
        {"success": True, "status": "COMPLETED", "data": {
            "player_location": {"map_token": "Farm", "x": 10, "y": 20},
        }},
        {"success": True, "status": "COMPLETED", "data": {"characters": [
            {"ref_id": "animal:1", "farm_animal": {"was_petted": False}},
            {"ref_id": "animal:2", "farm_animal": {"was_petted": False}},
        ]}},
    ]
    partial_ctx = FakeContext(base_outputs + [
        {"success": True, "status": "COMPLETED",
         "data": {"completed": 1, "skipped": 1, "total": 2}},
    ])
    failed_ctx = FakeContext(base_outputs + [
        {"success": True, "status": "COMPLETED",
         "data": {"completed": 0, "skipped": 2, "total": 2}},
    ])

    partial = await _program("pet_animals")(partial_ctx, {})
    failed = await _program("pet_animals")(failed_ctx, {})

    assert partial["status"] == "partial"
    assert json.loads(partial["data_json"])["petted"] == 1
    assert failed["status"] == "failed"
    assert json.loads(failed["data_json"])["petted"] == 0
    assert "Petted" not in failed["summary"]


@pytest.mark.asyncio
async def test_clear_farm_groups_refs_by_required_tool():
    ctx = FakeContext([
        {
            "success": True, "status": "COMPLETED",
            "data": {"entities": [
                {"ref_id": "debris:weed", "debris_type": "weed"},
                {"ref_id": "debris:twig", "debris_type": "twig"},
                {"ref_id": "rock:1", "kind": "rock"},
            ]},
        },
        {"success": True, "status": "COMPLETED", "data": {"completed": 1}},
        {"success": True, "status": "COMPLETED", "data": {"completed": 1}},
        {"success": True, "status": "COMPLETED", "data": {"completed": 1}},
    ])

    result = await _program("clear_farm")(ctx, {"targets": "all"})

    assert [call[0] for call in ctx.calls] == [
        "query_world_region", "execute_on_targets", "execute_on_targets",
        "execute_on_targets",
    ]
    assert [call[2]["tool"] for call in ctx.calls[1:]] == ["Scythe", "Axe", "Pickaxe"]
    assert json.loads(result["data_json"])["phases_completed"] == 3
