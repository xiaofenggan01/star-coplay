from __future__ import annotations

from pathlib import Path

import pytest

from agent.protocol.tool_mapper import map_tool_call
from l3_query.interactions import render_interactions
from l3_query.models import ObservationFrame
from slow_loop.core_tools import get_local_tool_names, get_local_tool_schemas
from slow_loop.deliberative_loop import L3_BACKED_CORE_TOOLS
from v2_skills.builtin.find_interactions.script import find_interactions
from v2_skills.registry import SKILLS_EXCLUDED_FROM_FC


def _entity(ref: str, type_: str, x: int, y: int, **payload):
    return {
        "ref": {"value": ref},
        "type": type_,
        "fact_kind": type_,
        "position": {"x": x, "y": y},
        type_: payload,
    }


def test_render_interactions_returns_action_candidates_with_opaque_refs():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:crop:farm:1:0#o_24",
                    "crop",
                    1,
                    0,
                    ready_for_harvest=True,
                    harvest_method="grab",
                    dead=False,
                ),
                _entity(
                    "world_entity:machine:farm:3:0#bc_12",
                    "machine",
                    3,
                    0,
                    ready_for_harvest=True,
                ),
            ],
            "characters": [
                {
                    "ref": {"value": "character:npc:robin#robin"},
                    "type": "npc",
                    "fact_kind": "npc",
                    "position": {"x": 2, "y": 0},
                    "npc": {"can_socialize": True},
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidates = result["data"]["candidates"]

    assert result["has_proto_facts"] is True
    assert "3 个交互候选" in result["description"]
    assert "执行门控：requires_resolution 3 个" in result["description"]
    assert [candidate["ref_id"] for candidate in candidates] == [
        "world_entity:crop:farm:1:0#o_24",
        "character:npc:robin#robin",
        "world_entity:machine:farm:3:0#bc_12",
    ]
    assert candidates[0]["action"] == "interact"
    assert candidates[0]["tool_args"] == {"x": 1, "y": 0}
    assert candidates[0]["recommended_steps"] == [
        {
            "tool": "move_to",
            "args": {"mode": "adjacent", "x": 1, "y": 0, "adjacent_side": "left"},
            "purpose": "stand_adjacent_to_target",
        },
        {"tool": "interact", "args": {"x": 1, "y": 0}},
    ]
    assert candidates[1]["reason"] == "NPC 可社交或对话"


def test_render_interactions_description_counts_ready_candidates_when_inventory_known_safe():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:warp:farm:1:0#busstop",
                    "warp",
                    1,
                    0,
                    target_map="BusStop",
                ),
            ],
            "characters": [],
        },
        inventory={"container_kind": "player", "items": []},
    )

    result = render_interactions(frame)

    assert "1 个交互候选" in result["description"]
    assert "执行门控：ready 1 个" in result["description"]


def test_render_interactions_recommended_steps_are_protocol_mappable():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:crop:farm:1:0#o_24",
                    "crop",
                    1,
                    0,
                    ready_for_harvest=True,
                    harvest_method="grab",
                    dead=False,
                ),
            ],
            "characters": [],
        },
    )

    result = render_interactions(frame)
    steps = result["data"]["candidates"][0]["recommended_steps"]

    for step in steps:
        mapped = map_tool_call(step["tool"], step["args"])
        assert mapped[0].tool_name == step["tool"]


def test_render_interactions_supports_offset_pagination_with_next_query():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    f"crop-{index}",
                    "crop",
                    index,
                    0,
                    watered=False,
                    ready_for_harvest=False,
                    dead=False,
                )
                for index in (3, 1, 2)
            ],
            "characters": [],
        },
        inventory={"container_kind": "player", "items": []},
    )

    first_page = render_interactions(frame, radius=6, limit=2)
    second_page = render_interactions(
        frame,
        radius=6,
        limit=2,
        offset=first_page["data"]["next_offset"],
    )

    assert [item["ref_id"] for item in first_page["data"]["items"]] == ["crop-1", "crop-2"]
    assert first_page["data"]["sort"] == "distance"
    assert first_page["data"]["offset"] == 0
    assert first_page["data"]["next_offset"] == 2
    assert first_page["data"]["has_more"] is True
    assert first_page["data"]["next_query"] == {
        "tool": "find_interactions",
        "args": {"radius": 6, "limit": 2, "offset": 2},
    }
    assert "本页按距离展示第 1-2 个" in first_page["description"]
    assert [item["ref_id"] for item in second_page["data"]["items"]] == ["crop-3"]
    assert second_page["data"]["offset"] == 2
    assert second_page["data"]["next_offset"] is None
    assert second_page["data"]["has_more"] is False
    assert "next_query" not in second_page["data"]
    assert "本页按距离展示第 3-3 个" in second_page["description"]


def test_render_interactions_describes_out_of_range_offset_without_erasing_total():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "crop-1",
                    "crop",
                    1,
                    0,
                    watered=False,
                    ready_for_harvest=False,
                    dead=False,
                )
            ],
            "characters": [],
        },
        inventory={"container_kind": "player", "items": []},
    )

    result = render_interactions(frame, limit=2, offset=99)

    assert result["data"]["items"] == []
    assert result["data"]["offset"] == 99
    assert result["data"]["has_more"] is False
    assert "当前视野内有 1 个交互候选" in result["description"]
    assert "offset=99 未返回候选" in result["description"]


def test_render_interactions_rejects_invalid_pagination_without_throwing():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={"map_id": "Farm", "entities": [], "characters": []},
    )

    result = render_interactions(frame, limit=2, offset="later")

    assert result["success"] is False
    assert result["data"]["error"]["code"] == "INVALID_PAGINATION"
    assert "offset 必须是整数" in result["data"]["error"]["message"]


def test_render_interactions_represents_bed_as_trigger_not_interact():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "FarmHouse", "x": 9, "y": 9}},
        world_region={
            "map_id": "FarmHouse",
            "entities": [
                _entity(
                    "world_entity:bed:farmhouse:9:8#f_2048",
                    "bed",
                    9,
                    8,
                    can_sleep=True,
                    bed_spot={"x": 10, "y": 9},
                ),
            ],
            "characters": [],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["action"] == "move_to_trigger"
    assert candidate["target_tile"] == {"x": 10, "y": 9}
    assert candidate["tool_args"] == {"mode": "exact", "x": 10, "y": 9}
    assert "sleep_confirmation_required" in candidate["preconditions"]
    assert "do_not_use_interact" in candidate["preconditions"]
    assert candidate["execution_warnings"] == []
    assert candidate["execution_gate"]["status"] == "ready"
    assert candidate["recommended_steps"] == [
        {
            "tool": "move_to",
            "args": {"mode": "exact", "x": 10, "y": 9},
            "purpose": "enter_trigger_tile",
        }
    ]

    mapped = map_tool_call(
        candidate["recommended_steps"][0]["tool"],
        candidate["recommended_steps"][0]["args"],
    )
    assert mapped[0].tool_name == "move_to"


def test_render_interactions_warns_when_active_item_can_intercept_interact():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:container:farm:1:0#bc_130",
                    "container",
                    1,
                    0,
                    player_chest=True,
                ),
            ],
            "characters": [],
        },
        inventory={
            "container_kind": "player",
            "items": [
                {
                    "ref": {"value": "inventory:item:player:0#o_216"},
                    "slot": 1,
                    "qualified_item_id": "(O)216",
                    "item_id": "216",
                    "internal_name": "Bread",
                    "display_name": "面包",
                    "category": -7,
                    "category_name": "菜品",
                    "is_active": True,
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["action"] == "interact"
    assert "active_item_safe_for_interact_or_equip_neutral_tool" in candidate["preconditions"]
    assert candidate["execution_warnings"][0]["code"] == "ACTIVE_ITEM_MAY_INTERCEPT_INTERACT"
    assert candidate["execution_warnings"][0]["active_item"]["display_name"] == "面包"
    assert candidate["execution_gate"] == {
        "status": "requires_resolution",
        "recommended_steps_policy": "conditional_do_not_execute_as_batch",
        "warning_codes": ["ACTIVE_ITEM_MAY_INTERCEPT_INTERACT"],
        "message": "先处理 execution_warnings，再考虑执行 recommended_steps。",
    }
    assert "执行门控：requires_resolution 1 个" in result["description"]
    assert candidate["recommended_steps"][-1] == {"tool": "interact", "args": {"x": 1, "y": 0}}


def test_render_interactions_warns_when_active_item_is_unknown_for_interact():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:container:farm:1:0#bc_130",
                    "container",
                    1,
                    0,
                    player_chest=True,
                ),
            ],
            "characters": [],
        },
        inventory=None,
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["action"] == "interact"
    assert "active_item_known_safe_for_interact_or_equip_neutral_tool" in candidate["preconditions"]
    assert candidate["execution_warnings"][0]["code"] == "ACTIVE_ITEM_UNKNOWN_FOR_INTERACT"
    assert candidate["execution_gate"]["status"] == "requires_resolution"
    assert candidate["execution_gate"]["warning_codes"] == ["ACTIVE_ITEM_UNKNOWN_FOR_INTERACT"]


def test_render_interactions_does_not_warn_when_inventory_known_and_no_active_item():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:container:farm:1:0#bc_130",
                    "container",
                    1,
                    0,
                    player_chest=True,
                ),
            ],
            "characters": [],
        },
        inventory={"container_kind": "player", "items": []},
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert "active_item_known_safe_for_interact_or_equip_neutral_tool" not in candidate["preconditions"]
    assert candidate["execution_warnings"] == []
    assert candidate["execution_gate"]["status"] == "ready"


def test_render_interactions_does_not_warn_when_active_item_is_tool():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:container:farm:1:0#bc_130",
                    "container",
                    1,
                    0,
                    player_chest=True,
                ),
            ],
            "characters": [],
        },
        inventory={
            "container_kind": "player",
            "items": [
                {
                    "slot": 4,
                    "qualified_item_id": "(T)WateringCan",
                    "item_id": "WateringCan",
                    "internal_name": "Watering Can",
                    "display_name": "喷壶",
                    "category": -99,
                    "is_active": True,
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert "active_item_safe_for_interact_or_equip_neutral_tool" not in candidate["preconditions"]
    assert candidate["execution_warnings"] == []
    assert candidate["execution_gate"]["status"] == "ready"


def test_render_interactions_does_not_treat_required_tool_category_as_item_name():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:resource_node:farm:1:0#stone_1",
                    "resource_node",
                    1,
                    0,
                    required_tool="pickaxe",
                ),
            ],
            "characters": [],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["tool_requirement"]["kind"] == "tool_category"
    assert candidate["tool_requirement"]["category"] == "pickaxe"
    assert "equip_tool_category:pickaxe" in candidate["preconditions"]
    assert candidate["execution_warnings"][0]["code"] == "ACTIVE_TOOL_MISMATCH_OR_UNKNOWN"
    assert candidate["execution_warnings"][0]["required_tool_category"] == "pickaxe"
    assert candidate["execution_gate"]["status"] == "requires_resolution"
    assert candidate["execution_gate"]["recommended_steps_policy"] == "conditional_do_not_execute_as_batch"
    assert all(step["tool"] != "equip" for step in candidate["recommended_steps"])


def test_render_interactions_does_not_warn_when_active_tool_matches_use_tool_requirement():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:resource_node:farm:1:0#stone_1",
                    "resource_node",
                    1,
                    0,
                    required_tool="pickaxe",
                ),
            ],
            "characters": [],
        },
        inventory={
            "container_kind": "player",
            "items": [
                {
                    "slot": 5,
                    "qualified_item_id": "(T)CopperPickaxe",
                    "item_id": "CopperPickaxe",
                    "internal_name": "Copper Pickaxe",
                    "display_name": "铜十字镐",
                    "category": -99,
                    "is_active": True,
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["tool_requirement"]["category"] == "pickaxe"
    assert candidate["execution_warnings"] == []
    assert candidate["execution_gate"] == {
        "status": "ready",
        "recommended_steps_policy": "directly_executable_if_preconditions_hold",
    }


def test_render_interactions_warns_when_active_tool_does_not_match_use_tool_requirement():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:resource_clump:farm:1:0#stump",
                    "resource_clump",
                    1,
                    0,
                    required_tool="axe",
                ),
            ],
            "characters": [],
        },
        inventory={
            "container_kind": "player",
            "items": [
                {
                    "slot": 5,
                    "qualified_item_id": "(T)CopperPickaxe",
                    "item_id": "CopperPickaxe",
                    "internal_name": "Copper Pickaxe",
                    "display_name": "铜十字镐",
                    "category": -99,
                    "is_active": True,
                },
                {
                    "slot": 33,
                    "qualified_item_id": "(T)Axe",
                    "item_id": "Axe",
                    "internal_name": "Axe",
                    "display_name": "斧头",
                    "category": -99,
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["tool_requirement"]["category"] == "axe"
    assert candidate["execution_warnings"][0]["code"] == "ACTIVE_TOOL_MISMATCH_OR_UNKNOWN"
    assert candidate["execution_warnings"][0]["required_tool_category"] == "axe"
    assert candidate["execution_warnings"][0]["active_item"]["item_id"] == "CopperPickaxe"
    assert candidate["execution_warnings"][0]["matching_inventory_items"] == [
        {
            "slot": 33,
            "qualified_item_id": "(T)Axe",
            "item_id": "Axe",
            "internal_name": "Axe",
            "display_name": "斧头",
            "category": -99,
            "equip_args": {"slot": 33},
        }
    ]
    assert candidate["tool_requirement"]["matching_inventory_items"][0]["equip_args"] == {"slot": 33}
    assert candidate["execution_gate"]["warning_codes"] == ["ACTIVE_TOOL_MISMATCH_OR_UNKNOWN"]


def test_render_interactions_infers_watering_can_for_unwatered_crop():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "world_entity:crop:farm:1:0#o_24",
                    "crop",
                    1,
                    0,
                    ready_for_harvest=False,
                    watered=False,
                    dead=False,
                ),
            ],
            "characters": [],
        },
        inventory={
            "container_kind": "player",
            "items": [
                {
                    "slot": 4,
                    "qualified_item_id": "(T)WateringCan",
                    "item_id": "WateringCan",
                    "internal_name": "Watering Can",
                    "display_name": "喷壶",
                    "category": -99,
                    "is_active": True,
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["tool_requirement"]["category"] == "watering_can"
    assert "equip_tool_category:watering_can" in candidate["preconditions"]
    assert candidate["execution_warnings"] == []
    assert candidate["execution_gate"]["status"] == "ready"


class InteractionCtx:
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
                                    "ready_for_harvest": False,
                                    "watered": False,
                                    "dead": False,
                                },
                            }
                        ],
                        "characters": [],
                    }
                }
            }
        if capability == "query_ui_snapshot":
            return {"protocol_data": {"fact": {"menu_open": False, "elements": []}}}
        if capability == "query_inventory_snapshot":
            return {"protocol_data": {"fact": {"container_kind": "player", "items": []}}}
        raise KeyError(capability)


@pytest.mark.asyncio
async def test_find_interactions_skill_uses_proto_current_view():
    result = await find_interactions(InteractionCtx(), radius=8, limit=10)

    assert result["success"] is True
    assert result["data"]["candidates"][0]["action"] == "use_tool"
    assert result["data"]["items"][0]["tool_args"] == {"x": 1, "y": 0}
    assert result["data"]["items"][0]["recommended_steps"][0]["tool"] == "move_to"
    assert result["data"]["items"][0]["recommended_steps"][0]["args"] == {
        "mode": "adjacent",
        "x": 1,
        "y": 0,
        "adjacent_side": "left",
    }
    assert result["data"]["items"][0]["recommended_steps"][-1] == {
        "tool": "use_tool",
        "args": {"x": 1, "y": 0},
    }
    assert result["data"]["candidates"][0]["reason"] == "作物尚未浇水"


@pytest.mark.asyncio
async def test_find_interactions_skill_accepts_offset_for_pagination():
    class MultiCandidateCtx(InteractionCtx):
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

    result = await find_interactions(MultiCandidateCtx(), radius=8, limit=1, offset=1)

    assert result["success"] is True
    assert result["data"]["items"][0]["ref_id"] == "crop-2"
    assert result["data"]["offset"] == 1
    assert result["data"]["next_offset"] == 2
    assert result["data"]["next_query"] == {
        "tool": "find_interactions",
        "args": {"radius": 8, "limit": 1, "offset": 2},
    }


def test_render_interactions_returns_menu_click_steps_for_ui_candidates():
    frame = ObservationFrame(
        runtime={"player_location": {"map_id": "Farm", "x": 0, "y": 0}},
        world_region={"map_id": "Farm", "entities": [], "characters": []},
        ui={
            "menu_open": True,
            "elements": [
                {
                    "ref": {"value": "ui:inventory:tab#1"},
                    "type": "tab",
                    "label": "Inventory",
                    "visible": True,
                    "enabled": True,
                    "component_id": 7,
                    "component_source": "tab",
                }
            ],
        },
    )

    result = render_interactions(frame)
    candidate = result["data"]["candidates"][0]

    assert candidate["preconditions"] == ["menu_open"]
    assert candidate["tool_args"] == {"component_id": 7, "component_source": "tab"}
    assert candidate["tool_requirement"] == {}
    assert candidate["execution_warnings"] == []
    assert candidate["execution_gate"] == {
        "status": "ready",
        "recommended_steps_policy": "directly_executable_if_preconditions_hold",
    }
    assert candidate["recommended_steps"] == [
        {"tool": "menu_click", "args": {"component_id": 7, "component_source": "tab"}}
    ]


@pytest.mark.asyncio
async def test_find_interactions_reports_failure_when_world_region_query_fails():
    class FailingInteractionCtx(InteractionCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                raise RuntimeError("world query timed out")
            return await super().query(capability, params)

    result = await find_interactions(FailingInteractionCtx(), radius=8, limit=10)

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["data"]["errors"][0]["capability"] == "query_world_region"


def test_find_interactions_is_l3_backed_local_tool():
    assert "find_interactions" in get_local_tool_names()
    schemas = get_local_tool_schemas()
    find_schema = next(schema for schema in schemas if schema["function"]["name"] == "find_interactions")
    assert "offset" in find_schema["function"]["parameters"]["properties"]
    assert L3_BACKED_CORE_TOOLS["find_interactions"] == "find_interactions"
    assert "find_interactions" in SKILLS_EXCLUDED_FROM_FC


def test_find_interactions_llm_facing_description_mentions_execution_gate():
    schemas = {schema["function"]["name"]: schema for schema in get_local_tool_schemas()}
    description = schemas["find_interactions"]["function"]["description"]

    assert "execution_gate" in description
    assert "execution_warnings" in description
    assert "requires_resolution" in description
    assert "不要批量照做 recommended_steps" in description


def test_find_interactions_skill_doc_does_not_allow_blind_step_copy():
    skill_doc = (
        Path(__file__).resolve().parents[1]
        / "v2_skills"
        / "builtin"
        / "find_interactions"
        / "skill.md"
    ).read_text(encoding="utf-8")

    assert "先读 `execution_gate`" in skill_doc
    assert "不要把 `recommended_steps` 批量照做" in skill_doc
    assert "LLM 可以原样复制为下一轮 tool call" not in skill_doc
