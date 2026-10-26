from __future__ import annotations

import pytest

from v2_skills.builtin.observe.script import observe


class ProtoObserveCtx:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def get_game_state(self) -> dict:
        return {}

    def get_alias_map(self) -> dict:
        return {}

    async def query(self, capability: str, params: dict | None = None) -> dict:
        params = params or {}
        self.calls.append((capability, params))
        if capability == "query_runtime_snapshot":
            return {
                "protocol_data": {
                    "fact": {
                        "time_of_day": 1000,
                        "season": "spring",
                        "day_of_month": 5,
                        "player_location": {"map_id": "Farm", "x": 0, "y": 0},
                    }
                }
            }
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
                            },
                            {
                                "ref": {"value": "world_entity:tree:farm:2:0#wt_1"},
                                "type": "tree",
                                "fact_kind": "tree",
                                "position": {"x": 2, "y": 0},
                                "tree": {
                                    "display_name": "Oak Tree",
                                    "has_moss": True,
                                    "is_stump": False,
                                },
                            },
                        ],
                        "characters": [],
                    }
                }
            }
        if capability == "query_inventory_snapshot":
            return {"protocol_data": {"fact": {"container_kind": "player", "items": []}}}
        if capability == "query_ui_snapshot":
            return {"protocol_data": {"fact": {"menu_open": False, "elements": []}}}
        if capability == "query_inspect_refs":
            return {
                "protocol_data": {
                    "inspected": [
                        {
                            "ref": params["refs"][0],
                            "type": "crop",
                            "fact_kind": "crop",
                            "summary": "Parsnip",
                            "resolution": {"status": "REF_RESOLVED"},
                            "world_entity": {
                                "type": "crop",
                                "crop": {
                                    "display_name": "Parsnip",
                                    "ready_for_harvest": False,
                                    "watered": False,
                                    "dead": False,
                                },
                            },
                        }
                    ],
                    "warnings": [],
                }
            }
        raise KeyError(capability)


@pytest.mark.asyncio
async def test_observe_uses_proto_native_l3_view_when_available():
    ctx = ProtoObserveCtx()

    result = await observe(ctx, detail="")

    assert result["success"] is True
    assert result["status"] == "ok"
    assert "data" in result
    assert result["data"]["scope"] == "current_view"
    assert "当前视野内有 1 个庄稼" in result["observation"]
    assert "find_targets" not in [capability for capability, _ in ctx.calls]
    groups = {group["fact_type"]: group for group in result["data"]["groups"]}
    assert groups["crop"]["representatives"]["items"] == ["world_entity:crop:farm:1:0#o_24"]
    assert groups["tree"]["summary"]["has_moss"] == 1


@pytest.mark.asyncio
async def test_observe_group_representatives_offer_search_facts_continuation_when_truncated():
    class ManyTreesCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "map_id": "Farm",
                            "entities": [
                                {
                                    "ref": {"value": f"tree-{index}"},
                                    "type": "tree",
                                    "fact_kind": "tree",
                                    "position": {"x": index, "y": 0},
                                    "tree": {
                                        "display_name": "Oak Tree",
                                        "has_moss": False,
                                        "is_stump": False,
                                    },
                                }
                                for index in range(12)
                            ],
                            "characters": [],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(ManyTreesCtx(), detail="")

    assert result["success"] is True
    groups = {group["fact_type"]: group for group in result["data"]["groups"]}
    representatives = groups["tree"]["representatives"]
    assert representatives["has_more"] is True
    assert representatives["items"] == [f"tree-{index}" for index in range(10)]
    assert representatives["next_query"] == {
        "tool": "search_facts",
        "args": {
            "fact_type": "tree",
            "sort": "distance",
            "limit": 10,
            "offset": 10,
        },
    }


@pytest.mark.asyncio
async def test_observe_groups_schema_backed_world_fact_types_beyond_initial_handwritten_list():
    class BroadWorldCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "map_id": "Farm",
                            "entities": [
                                {
                                    "ref": {"value": "world_entity:loose_item:farm:3:0#o_24"},
                                    "type": "loose_item",
                                    "fact_kind": "loose_item",
                                    "position": {"x": 3, "y": 0},
                                    "loose_item": {
                                        "display_name": "Fiber",
                                        "can_be_grabbed": True,
                                        "category": -81,
                                    },
                                },
                                {
                                    "ref": {"value": "world_entity:resource_clump:farm:4:0#c_1"},
                                    "type": "resource_clump",
                                    "fact_kind": "resource_clump",
                                    "position": {"x": 4, "y": 0},
                                    "resource_clump": {
                                        "clump_kind": "large_stump",
                                        "required_tool": "axe",
                                        "health": 2,
                                    },
                                },
                            ],
                            "characters": [
                                {
                                    "ref": {"value": "character:farm_animal:farm:cow#1"},
                                    "type": "farm_animal",
                                    "fact_kind": "farm_animal",
                                    "position": {"x": 2, "y": 0},
                                    "farm_animal": {
                                        "display_name": "Moo",
                                        "animal_type": "Cow",
                                        "produce_ready": True,
                                        "was_petted": False,
                                    },
                                }
                            ],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(BroadWorldCtx(), detail="")

    assert result["success"] is True
    groups = {group["fact_type"]: group for group in result["data"]["groups"]}
    assert groups["loose_item"]["summary"]["can_grab"] == 1
    assert groups["resource_clump"]["summary"]["by_required_tool"] == {"axe": 1}
    assert groups["farm_animal"]["summary"]["needs_pet"] == 1
    assert groups["loose_item"]["representatives"]["items"] == ["world_entity:loose_item:farm:3:0#o_24"]


@pytest.mark.asyncio
async def test_observe_groups_indoor_view_facts_needed_for_llm_room_understanding():
    class IndoorCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "map_id": "FarmHouse",
                            "entities": [
                                {
                                    "ref": {"value": "world_entity:bed:farmhouse:9:8#f_2048"},
                                    "type": "bed",
                                    "fact_kind": "bed",
                                    "position": {"x": 9, "y": 8},
                                    "summary": "Bed(Single) at (9,8)",
                                    "bed": {
                                        "display_name": "床",
                                        "bed_type": "Single",
                                        "can_sleep": True,
                                        "is_being_slept_in": False,
                                        "bed_spot": {"x": 10, "y": 9},
                                    },
                                },
                                {
                                    "ref": {"value": "world_entity:furniture:farmhouse:4:4#f_0"},
                                    "type": "furniture",
                                    "fact_kind": "furniture",
                                    "position": {"x": 4, "y": 4},
                                    "furniture": {
                                        "display_name": "橡木椅子",
                                        "internal_name": "Oak Chair",
                                        "furniture_type": "chair",
                                        "can_sit": True,
                                    },
                                },
                                {
                                    "ref": {"value": "world_entity:warp:farmhouse:3:12#farm"},
                                    "type": "warp",
                                    "fact_kind": "warp",
                                    "position": {"x": 3, "y": 12},
                                    "warp": {
                                        "warp_key": "FarmHouse_3_12",
                                        "target_map": "Farm",
                                        "target_x": 56,
                                        "target_y": 16,
                                    },
                                },
                            ],
                            "characters": [],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(IndoorCtx(), detail="")

    assert result["success"] is True
    assert "当前视野内有 1 个床，其中1 张可触发睡觉" in result["observation"]
    assert "当前视野内有 1 个家具，其中1 个可坐" in result["observation"]
    assert "当前视野内有 1 个出口，其中通向 Farm×1" in result["observation"]
    groups = {group["fact_type"]: group for group in result["data"]["groups"]}
    assert groups["bed"]["summary"]["can_sleep"] == 1
    assert groups["furniture"]["summary"]["sittable"] == 1
    assert groups["warp"]["summary"]["by_target_map"] == {"Farm": 1}
    assert groups["bed"]["representatives"]["items"] == ["world_entity:bed:farmhouse:9:8#f_2048"]


@pytest.mark.asyncio
async def test_observe_groups_door_without_duplicate_virtual_exit_group():
    class DoorCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "map_id": "Town",
                            "entities": [
                                {
                                    "ref": {"value": "world_entity:door:town:5:5#seedshop"},
                                    "type": "door",
                                    "fact_kind": "door",
                                    "position": {"x": 5, "y": 5},
                                    "summary": "Door → SeedShop",
                                    "door": {
                                        "door_key": "Town_5_5",
                                        "target_map": "SeedShop",
                                        "target_tile": {"x": 3, "y": 12},
                                        "locked": False,
                                        "door_kind": "door",
                                    },
                                }
                            ],
                            "characters": [],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(DoorCtx(), detail="")

    assert result["success"] is True
    groups = {group["fact_type"]: group for group in result["data"]["groups"]}
    assert "door" in groups
    assert "exit" not in groups
    assert groups["door"]["summary"]["by_target_map"] == {"SeedShop": 1}
    assert groups["door"]["representatives"]["items"] == ["world_entity:door:town:5:5#seedshop"]


@pytest.mark.asyncio
async def test_observe_reports_failure_when_world_region_query_fails():
    class FailingWorldCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                raise RuntimeError("world query timed out")
            return await super().query(capability, params)

    result = await observe(FailingWorldCtx(), detail="")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "不完整" in result["observation"]
    assert result["data"]["errors"][0]["capability"] == "query_world_region"


@pytest.mark.asyncio
async def test_observe_crops_detail_uses_proto_native_current_view():
    ctx = ProtoObserveCtx()

    result = await observe(ctx, detail="crops")

    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["detail"] == "crops"
    assert result["data"]["items"][0]["ref_id"] == "world_entity:crop:farm:1:0#o_24"
    assert result["data"]["items"][0]["summary"] == "需要浇水"
    assert "当前视野内有 1 个庄稼" in result["observation"]
    assert "find_targets" not in [capability for capability, _ in ctx.calls]


@pytest.mark.asyncio
async def test_observe_detail_offers_search_facts_continuation_when_truncated():
    class ManyCropsCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_world_region":
                self.calls.append((capability, params or {}))
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
                                for index in range(35)
                            ],
                            "characters": [],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(ManyCropsCtx(), detail="crops")

    assert result["success"] is True
    assert result["data"]["has_more"] is True
    assert len(result["data"]["items"]) == 30
    assert "data.next_query" in result["observation"]
    assert result["data"]["next_query"] == {
        "tool": "search_facts",
        "args": {
            "fact_type": "crop",
            "sort": "distance",
            "limit": 30,
            "offset": 30,
        },
    }


@pytest.mark.asyncio
async def test_observe_inventory_detail_uses_proto_native_snapshot():
    class InventoryCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_inventory_snapshot":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "container_kind": "player",
                            "slots_used": 1,
                            "slots_total": 36,
                            "items": [
                                {
                                    "ref": {"value": "inventory:item:player:0#o_24"},
                                    "container_kind": "player",
                                    "slot": 1,
                                    "display_name": "Parsnip",
                                    "stack": 3,
                                    "quality": 0,
                                }
                            ],
                        }
                    }
                }
            return await super().query(capability, params)

    ctx = InventoryCtx()

    result = await observe(ctx, detail="inventory")

    assert result["success"] is True
    assert result["data"]["detail"] == "inventory"
    assert result["data"]["items"][0]["ref_id"] == "inventory:item:player:0#o_24"
    assert result["data"]["items"][0]["summary"] == "槽位 1，数量 3"
    assert "query_inventory" not in [capability for capability, _ in ctx.calls]


@pytest.mark.asyncio
async def test_observe_inventory_detail_sorts_by_slot_and_continues_with_slot_offset():
    class ManyInventoryCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_inventory_snapshot":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "container_kind": "player",
                            "items": [
                                {
                                    "ref": {"value": f"slot-{slot}"},
                                    "slot": slot,
                                    "display_name": f"Item {slot}",
                                    "stack": 1,
                                }
                                for slot in [34, 0, *range(1, 34)]
                            ],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(ManyInventoryCtx(), detail="inventory")

    assert result["success"] is True
    assert result["data"]["sort"] == "slot"
    assert result["data"]["has_more"] is True
    assert [item["ref_id"] for item in result["data"]["items"][:3]] == ["slot-0", "slot-1", "slot-2"]
    assert "按槽位优先展示 30 个" in result["observation"]
    assert result["data"]["next_query"] == {
        "tool": "search_facts",
        "args": {
            "fact_type": "inventory_item",
            "sort": "slot",
            "limit": 30,
            "offset": 30,
        },
    }


@pytest.mark.asyncio
async def test_observe_inventory_detail_marks_active_item_for_llm_context():
    class ActiveInventoryCtx(ProtoObserveCtx):
        async def query(self, capability: str, params: dict | None = None) -> dict:
            if capability == "query_inventory_snapshot":
                self.calls.append((capability, params or {}))
                return {
                    "protocol_data": {
                        "fact": {
                            "container_kind": "player",
                            "items": [
                                {
                                    "ref": {"value": "inventory:item:player:3#t_wateringcan"},
                                    "slot": 4,
                                    "qualified_item_id": "(T)WateringCan",
                                    "item_id": "WateringCan",
                                    "internal_name": "Watering Can",
                                    "display_name": "喷壶",
                                    "category": -99,
                                    "is_active": True,
                                    "stack": 1,
                                }
                            ],
                        }
                    }
                }
            return await super().query(capability, params)

    result = await observe(ActiveInventoryCtx(), detail="inventory")

    assert result["success"] is True
    assert result["data"]["items"][0]["summary"] == "当前手持，槽位 4，数量 1"
    assert "喷壶，当前手持，槽位 4，数量 1" in result["observation"]


@pytest.mark.asyncio
async def test_observe_detail_accepts_opaque_ref_and_delegates_to_inspect():
    ctx = ProtoObserveCtx()
    ref = "world_entity:crop:farm:1:0#o_24"

    result = await observe(ctx, detail=ref)

    assert result["success"] is True
    assert result["data"]["inspected"][0]["ref"] == ref
    assert ctx.calls == [("query_inspect_refs", {"refs": [ref], "include_debug": False})]


@pytest.mark.asyncio
async def test_observe_detail_accepts_ref_wrapper_without_parsing_ref():
    ctx = ProtoObserveCtx()
    ref = "world_entity:crop:farm:1:0#o_24"

    result = await observe(ctx, detail=f"ref:{ref}")

    assert result["success"] is True
    assert result["data"]["inspected"][0]["ref"] == ref
    assert ctx.calls == [("query_inspect_refs", {"refs": [ref], "include_debug": False})]
