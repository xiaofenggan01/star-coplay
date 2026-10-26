from __future__ import annotations

import pytest

from l3_query.models import ObservationFrame
from l3_query.search import render_search
from slow_loop.core_tools import get_local_tool_names, get_local_tool_schemas
from slow_loop.deliberative_loop import L3_BACKED_CORE_TOOLS
from v2_skills.builtin.search_facts.script import search_facts
from v2_skills.registry import SKILLS_EXCLUDED_FROM_FC


def _entity(ref: str, type_: str, x: int, y: int, **payload):
    return {
        "ref": {"value": ref},
        "type": type_,
        "fact_kind": type_,
        "position": {"x": x, "y": y},
        type_: payload,
    }


def test_render_search_filters_with_schema_and_returns_opaque_refs():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("tree-near", "tree", 1, 0, display_name="Oak Tree", has_moss=True, growth_stage=5),
                _entity("tree-far", "tree", 5, 0, display_name="Pine Tree", has_moss=True, growth_stage=5),
                _entity("tree-no-moss", "tree", 2, 0, display_name="Maple Tree", has_moss=False, growth_stage=5),
            ],
            "characters": [],
        },
    )

    result = render_search(frame, fact_type="tree", filters={"has_moss": True}, sort="distance", limit=10)

    assert result["success"] is True
    assert result["data"]["schema"]["fact_type"] == "tree"
    assert [item["ref_id"] for item in result["data"]["items"]] == ["tree-near", "tree-far"]
    assert "has_moss" in result["data"]["schema"]["filter_fields"]


def test_render_search_supports_offset_pagination_after_sorting():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("tree-3", "tree", 3, 0, display_name="Tree 3"),
                _entity("tree-1", "tree", 1, 0, display_name="Tree 1"),
                _entity("tree-2", "tree", 2, 0, display_name="Tree 2"),
            ],
            "characters": [],
        },
    )

    first_page = render_search(frame, fact_type="tree", filters={"display_name": {"exists": True}}, sort="distance", limit=2, radius=6)
    second_page = render_search(frame, fact_type="tree", filters={"display_name": {"exists": True}}, sort="distance", limit=2, offset=first_page["data"]["next_offset"], radius=6)

    assert [item["ref_id"] for item in first_page["data"]["items"]] == ["tree-1", "tree-2"]
    assert first_page["data"]["radius"] == 6
    assert first_page["data"]["offset"] == 0
    assert first_page["data"]["next_offset"] == 2
    assert first_page["data"]["has_more"] is True
    assert first_page["data"]["next_query"] == {
        "tool": "search_facts",
        "args": {
            "fact_type": "tree",
            "filters": {"display_name": {"exists": True}},
            "sort": "distance",
            "limit": 2,
            "offset": 2,
            "radius": 6,
        },
    }
    assert "本页按 distance 展示第 1-2 个" in first_page["description"]
    assert [item["ref_id"] for item in second_page["data"]["items"]] == ["tree-3"]
    assert second_page["data"]["offset"] == 2
    assert second_page["data"]["next_offset"] is None
    assert second_page["data"]["has_more"] is False
    assert "next_query" not in second_page["data"]
    assert "本页按 distance 展示第 3-3 个" in second_page["description"]


def test_render_search_describes_out_of_range_offset_without_erasing_total():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("tree-1", "tree", 1, 0, display_name="Tree 1"),
            ],
            "characters": [],
        },
    )

    result = render_search(frame, fact_type="tree", sort="distance", limit=2, offset=99)

    assert result["success"] is True
    assert result["data"]["items"] == []
    assert result["data"]["total"] == 1
    assert result["data"]["offset"] == 99
    assert result["data"]["has_more"] is False
    assert "当前视野内匹配到 1 个树木" in result["description"]
    assert "offset=99 未返回结果" in result["description"]


def test_render_search_supports_explicit_eq_operator_promised_by_schema():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("crop-dry", "crop", 1, 0, display_name="Parsnip", watered=False),
                _entity("crop-wet", "crop", 2, 0, display_name="Bean", watered=True),
            ],
            "characters": [],
        },
    )

    result = render_search(frame, fact_type="crop", filters={"watered": {"eq": False}}, sort="distance")

    assert result["success"] is True
    assert result["data"]["schema"]["operators"]["watered"] == ["eq", "exists"]
    assert [item["ref_id"] for item in result["data"]["items"]] == ["crop-dry"]


def test_render_search_rejects_fields_not_declared_by_schema():
    frame = ObservationFrame(world_region={"map_id": "Farm", "entities": [], "characters": []})

    result = render_search(frame, fact_type="tree", filters={"made_up": True}, sort="distance")

    assert result["success"] is False
    assert result["data"]["error"]["code"] == "INVALID_QUERY"
    assert "made_up" in result["data"]["error"]["message"]
    assert "tree" in result["data"]["supported_fact_types"]


def test_render_search_rejects_invalid_filter_operator_values():
    frame = ObservationFrame(world_region={"map_id": "Farm", "entities": [], "characters": []})

    result = render_search(frame, fact_type="tree", filters={"growth_stage": {"min": "late"}}, sort="distance")

    assert result["success"] is False
    assert result["data"]["error"]["code"] == "INVALID_QUERY"
    assert "min" in result["data"]["error"]["message"]


def test_render_search_rejects_invalid_pagination_without_throwing():
    frame = ObservationFrame(world_region={"map_id": "Farm", "entities": [], "characters": []})

    result = render_search(frame, fact_type="tree", limit="bad")

    assert result["success"] is False
    assert result["data"]["error"]["code"] == "INVALID_PAGINATION"
    assert "limit 必须是整数" in result["data"]["error"]["message"]


def test_render_search_accepts_common_plural_aliases():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("crop-1", "crop", 1, 0, display_name="Parsnip", watered=False),
            ],
            "characters": [],
        },
    )

    result = render_search(frame, fact_type="crops", filters={"watered": False})

    assert result["success"] is True
    assert result["data"]["fact_type"] == "crop"
    assert result["data"]["items"][0]["ref_id"] == "crop-1"


def test_render_search_supports_character_fact_types_added_to_schema():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [],
            "characters": [
                {
                    "ref": {"value": "character:pet:farm:cat#pet_1"},
                    "type": "pet",
                    "fact_kind": "pet",
                    "display_name": "Miso",
                    "position": {"x": 2, "y": 0},
                    "pet": {"pet_type": "cat", "friendship": 500, "petted_today": False},
                },
                {
                    "ref": {"value": "character:horse:farm:horse#h_1"},
                    "type": "horse",
                    "fact_kind": "horse",
                    "display_name": "栗子",
                    "position": {"x": 4, "y": 0},
                    "horse": {"has_rider": False, "owner_id": "player"},
                },
            ],
        },
    )

    result = render_search(frame, fact_type="pet", filters={"petted_today": False}, sort="friendship")

    assert result["success"] is True
    assert result["data"]["items"][0]["ref_id"] == "character:pet:farm:cat#pet_1"
    assert "petted_today" in result["data"]["schema"]["filter_fields"]


def test_render_search_supports_resource_clump_and_loose_item_schema():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("clump-1", "resource_clump", 3, 0, clump_kind="large_stump", health=2, required_tool="axe"),
                _entity("item-1", "loose_item", 1, 0, display_name="Fiber", stack=1, can_be_grabbed=True, category=-81),
            ],
            "characters": [],
        },
    )

    clumps = render_search(frame, fact_type="resource_clump", filters={"required_tool": "axe"}, sort="health")
    loose_items = render_search(frame, fact_type="loose_item", filters={"can_be_grabbed": True}, sort="stack")

    assert clumps["success"] is True
    assert clumps["data"]["items"][0]["ref_id"] == "clump-1"
    assert loose_items["success"] is True
    assert loose_items["data"]["items"][0]["ref_id"] == "item-1"


def test_render_search_supports_indoor_bed_furniture_and_warp_schema():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "FarmHouse", "x": 9, "y": 9}},
        world_region={
            "map_id": "FarmHouse",
            "entities": [
                _entity(
                    "bed-1",
                    "bed",
                    9,
                    8,
                    display_name="床",
                    bed_type="Single",
                    can_sleep=True,
                    is_being_slept_in=False,
                    bed_spot={"x": 10, "y": 9},
                ),
                _entity(
                    "chair-1",
                    "furniture",
                    4,
                    4,
                    display_name="橡木椅子",
                    internal_name="Oak Chair",
                    furniture_type="chair",
                    can_sit=True,
                ),
                _entity(
                    "warp-farm",
                    "warp",
                    3,
                    12,
                    warp_key="FarmHouse_3_12",
                    target_map="Farm",
                    target_x=56,
                    target_y=16,
                ),
            ],
            "characters": [],
        },
    )

    beds = render_search(frame, fact_type="bed", filters={"can_sleep": {"eq": True}}, sort="distance")
    chairs = render_search(frame, fact_type="furniture", filters={"furniture_type": "chair"}, sort="distance")
    exits = render_search(frame, fact_type="exits", filters={"target_map": "Farm"}, sort="distance")

    assert beds["success"] is True
    assert beds["data"]["items"][0]["ref_id"] == "bed-1"
    assert "bed_spot.x" in beds["data"]["schema"]["filter_fields"]
    assert chairs["success"] is True
    assert chairs["data"]["items"][0]["ref_id"] == "chair-1"
    assert exits["success"] is True
    assert exits["data"]["fact_type"] == "exit"
    assert exits["data"]["items"][0]["ref_id"] == "warp-farm"
    assert exits["data"]["items"][0]["label"] == "出口到 Farm"


def test_render_search_exits_unions_door_and_warp_facts():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Town", "x": 10, "y": 10}},
        world_region={
            "map_id": "Town",
            "entities": [
                _entity(
                    "door-shop",
                    "door",
                    5,
                    5,
                    door_key="Town_5_5",
                    target_map="SeedShop",
                    target_tile={"x": 3, "y": 12},
                    locked=False,
                    door_kind="door",
                ),
                _entity(
                    "warp-bus-stop",
                    "warp",
                    2,
                    10,
                    warp_key="Town_2_10",
                    target_map="BusStop",
                    target_x=80,
                    target_y=23,
                    npc_only=False,
                ),
            ],
            "characters": [],
        },
    )

    exits = render_search(frame, fact_type="exits", filters={"target_map": {"in": ["SeedShop", "BusStop"]}})
    doors = render_search(frame, fact_type="door", filters={"door_kind": "door"})

    assert exits["success"] is True
    assert exits["data"]["fact_type"] == "exit"
    assert [item["ref_id"] for item in exits["data"]["items"]] == ["door-shop", "warp-bus-stop"]
    assert [item["type"] for item in exits["data"]["items"]] == ["door", "warp"]
    assert "door_kind" in exits["data"]["schema"]["filter_fields"]
    assert doors["success"] is True
    assert doors["data"]["fact_type"] == "door"
    assert doors["data"]["items"][0]["ref_id"] == "door-shop"


def test_render_search_supports_ui_elements_from_ui_snapshot():
    frame = ObservationFrame(
        ui={
            "menu_open": True,
            "elements": [
                {
                    "ref": {"value": "ui:shop:item:0#o_24"},
                    "type": "shop_item",
                    "label": "Parsnip Seeds",
                    "visible": True,
                    "enabled": True,
                    "index": 0,
                    "price": 20,
                    "stock": -1,
                    "qualified_item_id": "(O)472",
                }
            ],
        }
    )

    result = render_search(frame, fact_type="ui_element", filters={"enabled": True}, sort="price")

    assert result["success"] is True
    assert result["data"]["items"][0]["ref_id"] == "ui:shop:item:0#o_24"
    assert "price" in result["data"]["schema"]["sort_fields"]


def test_render_search_handles_mixed_sort_value_types_without_crashing():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity("tree-int-stage", "tree", 1, 0, display_name="Oak Tree", growth_stage=5),
                _entity("tree-str-stage", "tree", 2, 0, display_name="Pine Tree", growth_stage="mature"),
            ],
            "characters": [],
        },
    )

    result = render_search(frame, fact_type="tree", sort="growth_stage")

    assert result["success"] is True
    assert {item["ref_id"] for item in result["data"]["items"]} == {"tree-int-stage", "tree-str-stage"}


def test_render_search_uses_fact_type_default_sort_for_non_spatial_facts():
    frame = ObservationFrame(
        inventory={
            "container_kind": "player",
            "items": [
                {"ref": {"value": "slot-2"}, "slot": 2, "display_name": "Stone", "stack": 1},
                {"ref": {"value": "slot-1"}, "slot": 1, "display_name": "Parsnip", "stack": 1},
            ],
        },
        ui={
            "elements": [
                {"ref": {"value": "ui-2"}, "type": "button", "label": "B", "visible": True, "enabled": True, "index": 2},
                {"ref": {"value": "ui-1"}, "type": "button", "label": "A", "visible": True, "enabled": True, "index": 1},
            ],
        },
    )

    inventory = render_search(frame, fact_type="inventory_item")
    ui = render_search(frame, fact_type="ui_element")

    assert inventory["success"] is True
    assert inventory["data"]["sort"] == "slot"
    assert [item["ref_id"] for item in inventory["data"]["items"]] == ["slot-1", "slot-2"]
    assert ui["success"] is True
    assert ui["data"]["sort"] == "index"
    assert [item["ref_id"] for item in ui["data"]["items"]] == ["ui-1", "ui-2"]


def test_render_search_summarizes_active_inventory_item_for_llm_context():
    frame = ObservationFrame(
        inventory={
            "container_kind": "player",
            "items": [
                {
                    "ref": {"value": "slot-4"},
                    "slot": 4,
                    "qualified_item_id": "(T)WateringCan",
                    "item_id": "WateringCan",
                    "internal_name": "Watering Can",
                    "display_name": "喷壶",
                    "category": -99,
                    "is_active": True,
                    "stack": 1,
                },
            ],
        },
    )

    result = render_search(frame, fact_type="inventory_item", filters={"is_active": {"eq": True}})

    assert result["success"] is True
    item = result["data"]["items"][0]
    assert item["summary"] == "当前手持，槽位 4，数量 1"
    assert "item_id" in result["data"]["schema"]["filter_fields"]
    assert "internal_name" in result["data"]["schema"]["filter_fields"]


def test_render_search_exposes_proto_stable_fields_that_llms_need_for_decisions():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "crop-regrow",
                    "crop",
                    1,
                    0,
                    crop_id="Corn",
                    harvest_item_id="270",
                    display_name="Corn",
                    harvest_method="grab",
                    day_of_current_phase=2,
                    regrows_after_harvest=True,
                    watered=True,
                    ready_for_harvest=False,
                    dead=False,
                ),
                _entity(
                    "tree-fertilized",
                    "tree",
                    2,
                    0,
                    display_name="Oak Tree",
                    fertilized=True,
                    health=10,
                    has_moss=False,
                ),
                _entity(
                    "fruit-lightning",
                    "fruit_tree",
                    3,
                    0,
                    display_name="Apple Tree",
                    fruit_count=1,
                    fruit_quality=2,
                    struck_by_lightning=True,
                    green_house_tile_tree=False,
                ),
                _entity(
                    "machine-output",
                    "machine",
                    4,
                    0,
                    display_name="Preserves Jar",
                    internal_name="Preserves Jar",
                    machine_data_id="(BC)15",
                    machine_state="MACHINE_READY",
                    ready_for_harvest=True,
                    held_object={"qualified_item_id": "(O)344", "display_name": "Jelly"},
                ),
                _entity(
                    "container-blue",
                    "container",
                    5,
                    0,
                    container_kind="chest",
                    color="#3366CC",
                    special_chest_type="None",
                    items_count=3,
                    capacity=36,
                ),
            ],
            "characters": [
                {
                    "ref": {"value": "npc-lewis"},
                    "type": "npc",
                    "fact_kind": "npc",
                    "name": "Lewis",
                    "display_name": "Lewis",
                    "position": {"x": 1, "y": 1},
                    "npc": {
                        "npc_key": "Lewis",
                        "current_map_token": "town",
                        "can_socialize": True,
                        "friendship_points": 500,
                        "schedule_key": "spring",
                        "birthday_season": "spring",
                        "birthday_day": 7,
                        "has_dialogue": True,
                    },
                },
                {
                    "ref": {"value": "animal-cow"},
                    "type": "farm_animal",
                    "fact_kind": "farm_animal",
                    "name": "Moo",
                    "display_name": "Moo",
                    "position": {"x": 2, "y": 1},
                    "farm_animal": {
                        "animal_type": "Cow",
                        "home_building_type": "Barn",
                        "home_building_id": "barn-guid",
                        "produce_ready": True,
                        "was_petted": False,
                        "was_auto_petted": True,
                        "friendship": 700,
                        "happiness": 200,
                    },
                },
            ],
        },
        inventory={
            "items": [
                {
                    "ref": {"value": "tool-pickaxe"},
                    "type": "inventory_item",
                    "fact_kind": "inventory_item",
                    "slot": 5,
                    "display_name": "Copper Pickaxe",
                    "item_id": "CopperPickaxe",
                    "internal_name": "Copper Pickaxe",
                    "category": -99,
                    "upgrade_level": 1,
                    "base_sell_price": -1,
                    "can_stack": False,
                    "is_active": False,
                }
            ]
        },
        ui={
            "elements": [
                {
                    "ref": {"value": "ui-option-yes"},
                    "type": "dialogue_option",
                    "element_token": "yes",
                    "identity_guard": "response_yes",
                    "label": "Yes",
                    "visible": True,
                    "enabled": True,
                    "index": 0,
                }
            ]
        },
    )

    checks = [
        ("crop", {"regrows_after_harvest": True}, "crop-regrow", "harvest_method"),
        ("tree", {"fertilized": True}, "tree-fertilized", "health"),
        ("fruit_tree", {"struck_by_lightning": True}, "fruit-lightning", "fruit_quality"),
        ("machine", {"held_object.qualified_item_id": "(O)344"}, "machine-output", "held_object.display_name"),
        ("container", {"color": "#3366CC"}, "container-blue", "special_chest_type"),
        ("npc", {"birthday_day": {"eq": 7}}, "npc-lewis", "has_dialogue"),
        ("farm_animal", {"was_auto_petted": True}, "animal-cow", "home_building_id"),
        ("inventory_item", {"upgrade_level": {"min": 1}}, "tool-pickaxe", "base_sell_price"),
        ("ui_element", {"element_token": "yes"}, "ui-option-yes", "identity_guard"),
    ]

    for fact_type, filters, expected_ref, expected_field in checks:
        result = render_search(frame, fact_type=fact_type, filters=filters)
        assert result["success"] is True, fact_type
        assert result["data"]["items"][0]["ref_id"] == expected_ref
        assert expected_field in result["data"]["schema"]["filter_fields"]

    inventory = render_search(frame, fact_type="inventory_item", filters={"upgrade_level": {"min": 1}})
    assert "工具等级 1" in inventory["data"]["items"][0]["summary"]


class SearchCtx:
    async def query(self, capability: str, params: dict | None = None) -> dict:
        if capability == "query_runtime_snapshot":
            return {"protocol_data": {"fact": {"player_location": {"map_id": "Farm", "x": 0, "y": 0}}}}
        if capability == "query_world_region":
            return {
                "protocol_data": {
                    "fact": {
                        "map_id": "Farm",
                        "entities": [
                            {
                                "ref": {"value": "world_entity:crop:farm:1:0#o_24"},
                                "type": "crop",
                                "fact_kind": "crop",
                                "position": {"x": 1, "y": 0},
                                "crop": {
                                    "display_name": "Parsnip",
                                    "watered": False,
                                    "ready_for_harvest": False,
                                    "dead": False,
                                },
                            }
                        ],
                        "characters": [],
                    }
                }
            }
        if capability == "query_inventory_snapshot":
            return {"protocol_data": {"fact": {"container_kind": "player", "items": []}}}
        if capability == "query_ui_snapshot":
            return {"protocol_data": {"fact": {"menu_open": False, "elements": []}}}
        raise KeyError(capability)


@pytest.mark.asyncio
async def test_search_facts_skill_uses_schema_checked_l3_query():
    result = await search_facts(SearchCtx(), fact_type="crop", filters={"watered": False}, sort="distance")

    assert result["success"] is True
    assert result["data"]["items"][0]["ref_id"] == "world_entity:crop:farm:1:0#o_24"


@pytest.mark.asyncio
async def test_search_facts_skill_accepts_offset_for_pagination():
    class MultiCropCtx(SearchCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                return {
                    "protocol_data": {
                        "fact": {
                            "map_id": "Farm",
                            "entities": [
                                {
                                    "ref": {"value": f"crop-{index}"},
                                    "type": "crop",
                                    "fact_kind": "crop",
                                    "position": {"x": index, "y": 0},
                                    "crop": {
                                        "display_name": "Parsnip",
                                        "watered": False,
                                        "ready_for_harvest": False,
                                        "dead": False,
                                    },
                                }
                                for index in (1, 2, 3)
                            ],
                            "characters": [],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await search_facts(MultiCropCtx(), fact_type="crop", sort="distance", limit=1, offset=1)

    assert result["success"] is True
    assert result["data"]["items"][0]["ref_id"] == "crop-2"
    assert result["data"]["offset"] == 1
    assert result["data"]["next_offset"] == 2
    assert result["data"]["has_more"] is True


def test_search_facts_is_l3_backed_local_tool():
    assert "search_facts" in get_local_tool_names()
    schemas = get_local_tool_schemas()
    search_schema = next(schema for schema in schemas if schema["function"]["name"] == "search_facts")
    assert "fact_type" in search_schema["function"]["parameters"]["required"]
    assert "crop" in search_schema["function"]["parameters"]["properties"]["fact_type"]["enum"]
    assert "offset" in search_schema["function"]["parameters"]["properties"]
    assert L3_BACKED_CORE_TOOLS["search_facts"] == "search_facts"
    assert "search_facts" in SKILLS_EXCLUDED_FROM_FC


@pytest.mark.asyncio
async def test_search_facts_reports_failure_when_world_region_query_fails():
    class FailingSearchCtx(SearchCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                raise RuntimeError("world query timed out")
            return await super().query(capability, params)

    result = await search_facts(FailingSearchCtx(), fact_type="crop")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["data"]["errors"][0]["capability"] == "query_world_region"
