from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from bridge.bridge_result import BridgeResult
from agent.generated.protocol import capability_pb2, common_pb2, query_facts_pb2, query_pb2, ref_pb2
from agent.protocol.result_parser import command_result_to_bridge_result
from v2_skills.builtin.chop_trees.script import chop_trees
from v2_skills.context import SkillContext
from v2_skills.registry import SkillRegistry


def _bridge_result(capability: str, protocol_data: dict | None = None) -> BridgeResult:
    return BridgeResult(
        success=True,
        status="ok",
        data={
            "protocol_capability": capability,
            "protocol_data_field": capability,
            "protocol_data": protocol_data or {},
        },
        observation="ok",
        side_effects=[],
        error_code=None,
        message=None,
        action_id="act_test",
        seq=1,
    )


def _runtime() -> dict:
    return {
        "fact": {
            "player_location": {
                "map_token": "Farm",
                "x": 10,
                "y": 10,
            },
            "stamina": 270,
            "time_of_day": 900,
        }
    }


def _inventory(wood: int = 0, include_axe: bool = True) -> dict:
    items = []
    if include_axe:
        items.append(
            {
                "slot": 1,
                "item_id": "Axe",
                "qualified_item_id": "(T)Axe",
                "internal_name": "Axe",
                "display_name": "Axe",
                "stack": 1,
                "category": -99,
            }
        )
    if wood:
        items.append(
            {
                "slot": 2,
                "item_id": "388",
                "qualified_item_id": "(O)388",
                "internal_name": "Wood",
                "display_name": "Wood",
                "stack": wood,
                "category": -16,
            }
        )
    return {"fact": {"container_kind": "player", "items": items, "slots_used": len(items)}}


def _tree_world(
    include_tree: bool = True,
    *,
    include_tiles: bool = False,
    reachable: bool = True,
) -> dict:
    entities = []
    if include_tree:
        entities.append(
            {
                "ref": {"value": "world_entity:tree:farm:12:10#wt_1"},
                "type": "tree",
                "fact_kind": "tree",
                "position": {"map_id": "Farm", "x": 12, "y": 10},
                "summary": "Oak Tree (stage 5)",
                "tree": {
                    "tree_type": "1",
                    "growth_stage": 5,
                    "is_stump": False,
                    "health": 10.0,
                    "display_name": "Oak Tree",
                },
            }
        )
    tiles = []
    if include_tiles:
        for x, y in ((11, 10), (13, 10), (12, 9), (12, 11)):
            tile = {
                "position": {"map_id": "Farm", "x": x, "y": y},
                "passable": reachable,
                "occupied": False,
            }
            tiles.append(tile)
    return {"fact": {"map_token": "Farm", "map_id": "Farm", "tiles": tiles, "entities": entities}}


class ChoppingBridge:
    def __init__(
        self,
        *,
        initial_wood: int = 0,
        final_wood: int = 5,
        include_axe: bool = True,
        include_tree: bool = True,
        include_tiles: bool = False,
        reachable: bool = True,
        move_success: bool = True,
        inventory_override: dict | list[dict] | None = None,
        tree_persists_after_use: bool = False,
        loose_items: list[dict] | None = None,
    ):
        self.commands: list[tuple[str, dict]] = []
        self._results: dict[str, BridgeResult] = {}
        self._seq = 0
        self._initial_wood = initial_wood
        self._final_wood = final_wood
        self._include_axe = include_axe
        self._include_tree = include_tree
        self._include_tiles = include_tiles
        self._reachable = reachable
        self._move_success = move_success
        self._inventory_override = inventory_override
        self._tree_persists_after_use = tree_persists_after_use
        self._inventory_reads = 0
        self._tree_present = include_tree
        self._use_tool_calls = 0
        self._loose_items = list(loose_items or [])

    def is_protocol_transport_enabled(self) -> bool:
        return True

    def write_command(self, capability: str, params: dict) -> str:
        self._seq += 1
        action_id = f"act_{self._seq}"
        self.commands.append((capability, params))
        self._results[action_id] = self._make_result(capability, params)
        return action_id

    def read_command_result(self, action_id: str) -> BridgeResult | None:
        return self._results.get(action_id)

    def cancel_command(self, action_id: str) -> None:
        self._results.pop(action_id, None)

    def _make_result(self, capability: str, params: dict) -> BridgeResult:
        if capability == "query_runtime_snapshot":
            return _bridge_result(capability, _runtime())
        if capability == "query_inventory_snapshot":
            if self._inventory_override is not None:
                if isinstance(self._inventory_override, list):
                    index = min(self._inventory_reads, len(self._inventory_override) - 1)
                    self._inventory_reads += 1
                    return _bridge_result(capability, self._inventory_override[index])
                self._inventory_reads += 1
                return _bridge_result(capability, self._inventory_override)
            wood = self._initial_wood if self._inventory_reads == 0 else self._final_wood
            self._inventory_reads += 1
            return _bridge_result(capability, _inventory(wood, include_axe=self._include_axe))
        if capability == "query_world_region":
            if params.get("world_entity_types") == ["loose_item"]:
                entities = []
                for item in self._loose_items:
                    entities.append(
                        {
                            "ref": {"value": f"world_entity:loose_item:farm:{item['x']}:{item['y']}#loot"},
                            "type": "loose_item",
                            "fact_kind": "loose_item",
                            "position": {"map_id": "Farm", "x": item["x"], "y": item["y"]},
                            "loose_item": {"can_be_grabbed": True},
                        }
                    )
                return _bridge_result(
                    capability,
                    {"fact": {"map_token": "Farm", "map_id": "Farm", "entities": entities}},
                )
            return _bridge_result(
                capability,
                _tree_world(
                    self._tree_present,
                    include_tiles=self._include_tiles and bool(params.get("include_tiles")),
                    reachable=self._reachable,
                ),
            )
        if capability == "use_tool":
            self._use_tool_calls += 1
            if not self._tree_persists_after_use:
                self._tree_present = False
            return _bridge_result(
                capability,
                {"effect": {"target_tile": {"x": params["x"], "y": params["y"]}}},
            )
        if capability == "interact":
            self._loose_items = [
                item for item in self._loose_items
                if item["x"] != params["x"] or item["y"] != params["y"]
            ]
            return _bridge_result(capability, {"picked": True})
        if capability == "move_to" and not self._move_success:
            return BridgeResult(
                success=False,
                status="failed",
                data={},
                observation="move failed",
                side_effects=[],
                error_code="NO_PATH",
                message="No path",
                action_id="act_test",
                seq=1,
            )
        return BridgeResult(
            success=True,
            status="ok",
            data={"ok": True},
            observation="ok",
            side_effects=[],
            error_code=None,
            message=None,
            action_id="act_test",
            seq=1,
        )


class ProtocolChoppingBridge(ChoppingBridge):
    def _make_result(self, capability: str, params: dict) -> BridgeResult:
        result = capability_pb2.CommandResult(
            command_id=f"cmd_{capability}",
            success=True,
            status=capability_pb2.COMMAND_STATUS_COMPLETED,
            observation=common_pb2.Observation(text="ok"),
            capability=capability,
        )
        if capability == "query_runtime_snapshot":
            result.query_runtime_snapshot.CopyFrom(
                query_pb2.QueryRuntimeSnapshotResult(
                    fact=query_facts_pb2.RuntimeSnapshotFact(
                        player_location=common_pb2.LocationSnapshot(
                            map_token="Farm",
                            x=10,
                            y=10,
                        ),
                        stamina=270,
                        time_of_day=900,
                    )
                )
            )
        elif capability == "query_inventory_snapshot":
            wood = self._initial_wood if self._inventory_reads == 0 else self._final_wood
            self._inventory_reads += 1
            items = []
            if self._include_axe:
                items.append(
                    query_facts_pb2.InventoryItemFact(
                        slot=1,
                        item_id="Axe",
                        qualified_item_id="(T)Axe",
                        internal_name="Axe",
                        display_name="Axe",
                        stack=1,
                        category=-99,
                    )
                )
            if wood:
                items.append(
                    query_facts_pb2.InventoryItemFact(
                        slot=2,
                        item_id="388",
                        qualified_item_id="(O)388",
                        internal_name="Wood",
                        display_name="Wood",
                        stack=wood,
                        category=-16,
                    )
                )
            result.query_inventory_snapshot.CopyFrom(
                query_pb2.QueryInventorySnapshotResult(
                    fact=query_facts_pb2.InventorySnapshotFact(
                        container_kind="player",
                        items=items,
                    )
                )
            )
        elif capability == "query_world_region":
            entities = []
            if self._tree_present:
                entities.append(
                    query_facts_pb2.WorldEntityFact(
                        ref=ref_pb2.RefId(value="world_entity:tree:farm:12:10#wt_1"),
                        type="tree",
                        fact_kind="tree",
                        position=common_pb2.TilePosition(map_id="Farm", x=12, y=10),
                        summary="Oak Tree (stage 5)",
                        tree=query_facts_pb2.TreeFact(
                            tree_type="1",
                            growth_stage=5,
                            is_stump=False,
                            health=10.0,
                            display_name="Oak Tree",
                        ),
                    )
                )
            result.query_world_region.CopyFrom(
                query_pb2.QueryWorldRegionResult(
                    fact=query_facts_pb2.WorldRegionFact(
                        map_token="Farm",
                        map_id="Farm",
                        entities=entities,
                    )
                )
            )
        elif capability == "use_tool":
            self._use_tool_calls += 1
            self._tree_present = False
            result.use_tool.effect.target_tile.x = params["x"]
            result.use_tool.effect.target_tile.y = params["y"]
        else:
            result.move_to.reached_destination = True

        return command_result_to_bridge_result(result, action_id=f"act_{self._seq}", seq=self._seq)


def _ctx(bridge: Any) -> SkillContext:
    return SkillContext(bridge, Path("unused_game_state.json"))


def test_chop_trees_default_stops_after_five_wood():
    bridge = ChoppingBridge(initial_wood=0, final_wood=5)

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["target_wood"] == 5
    assert result["data"]["wood_gained"] == 5
    assert result["data"]["trees_chopped"] == 1
    assert result["data"]["warnings"] == []
    assert result["data"]["awaiting_next_player_command"] is True
    assert ("equip", {"slot": 1}) in bridge.commands
    assert any(
        command == "move_to" and params["mode"] == "adjacent"
        for command, params in bridge.commands
    )
    assert any(
        command == "use_tool" and params["x"] == 12 and params["y"] == 10
        for command, params in bridge.commands
    )


def test_chop_trees_clamps_requested_wood_to_fifty():
    bridge = ChoppingBridge(initial_wood=0, final_wood=60)

    result = asyncio.run(chop_trees(_ctx(bridge), target_wood=999))

    assert result["data"]["target_wood"] == 50
    assert result["data"]["wood_gained"] == 60
    assert result["data"]["stopped_reason"] == "wood_target_reached"
    assert result["data"]["warnings"][0]["code"] == "TARGET_WOOD_CLAMPED"


def test_chop_trees_equips_axe_not_pickaxe():
    def inventory_with_pickaxe(wood: int) -> dict:
        inventory = _inventory(wood=wood)
        inventory["fact"]["items"].insert(
            0,
            {
                "slot": 1,
                "item_id": "Pickaxe",
                "qualified_item_id": "(T)Pickaxe",
                "internal_name": "Pickaxe",
                "display_name": "Pickaxe",
                "stack": 1,
                "category": -99,
            },
        )
        inventory["fact"]["items"][1]["slot"] = 2
        return inventory

    bridge = ChoppingBridge(
        inventory_override=[inventory_with_pickaxe(0), inventory_with_pickaxe(5)]
    )

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is True
    assert ("equip", {"slot": 2}) in bridge.commands
    assert ("equip", {"slot": 1}) not in bridge.commands


def test_chop_trees_handles_protocol_message_to_dict_payloads():
    bridge = ProtocolChoppingBridge(initial_wood=0, final_wood=5)

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is True
    assert result["data"]["wood_gained"] == 5
    assert result["data"]["stopped_reason"] == "wood_target_reached"
    assert any(command == "query_world_region" for command, _params in bridge.commands)
    assert any(
        command == "use_tool" and params["x"] == 12 and params["y"] == 10
        for command, params in bridge.commands
    )


def test_chop_trees_stops_when_inventory_reaches_target_even_if_tree_fact_lags():
    bridge = ChoppingBridge(
        initial_wood=0,
        final_wood=20,
        tree_persists_after_use=True,
    )

    result = asyncio.run(chop_trees(_ctx(bridge), target_wood=20))

    assert result["success"] is True
    assert result["data"]["wood_gained"] == 20
    assert bridge._use_tool_calls == 1


def test_chop_trees_missing_axe_blocks_without_moving():
    bridge = ChoppingBridge(include_axe=False)

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error"]["code"] == "NO_AXE"
    assert result["data"]["stopped_reason"] == "missing_axe"
    assert not any(command == "equip" for command, _params in bridge.commands)
    assert not any(command == "move_to" for command, _params in bridge.commands)
    assert not any(command == "use_tool" for command, _params in bridge.commands)


def test_chop_trees_no_trees_blocks_without_equipping():
    bridge = ChoppingBridge(include_tree=False)

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error"]["code"] == "NO_TREES"
    assert result["data"]["stopped_reason"] == "no_trees"
    assert not any(command == "equip" for command, _params in bridge.commands)
    assert not any(command == "move_to" for command, _params in bridge.commands)
    assert not any(command == "use_tool" for command, _params in bridge.commands)


def test_chop_trees_unreachable_tree_is_skipped_before_equipping():
    bridge = ChoppingBridge(include_tiles=True, reachable=False)

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error"]["code"] == "NO_REACHABLE_TREES"
    assert result["data"]["trees_skipped"] == 1
    assert result["data"]["stopped_reason"] == "no_reachable_trees"
    assert result["data"]["warnings"][0]["code"] == "NO_STAND_TILE"
    assert not any(command == "equip" for command, _params in bridge.commands)
    assert not any(command == "move_to" for command, _params in bridge.commands)
    assert not any(command == "use_tool" for command, _params in bridge.commands)


def test_chop_trees_move_failure_skips_tree_without_using_tool():
    bridge = ChoppingBridge(move_success=False)

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert result["error"]["code"] == "NO_REACHABLE_TREES"
    assert result["data"]["trees_skipped"] == 1
    assert result["data"]["warnings"][0]["code"] == "MOVE_ADJACENT_FAILED"
    assert ("equip", {"slot": 1}) in bridge.commands
    assert any(command == "move_to" for command, _params in bridge.commands)
    assert not any(command == "use_tool" for command, _params in bridge.commands)


def test_registry_exposes_chop_trees_skill():
    registry = SkillRegistry(Path(__file__).parent.parent / "v2_skills")
    entry = registry.get("chop_trees")

    assert entry is not None
    assert entry.fc_schema["function"]["name"] == "skill-chop_trees"
    assert "砍树" in entry.fc_schema["function"]["description"]

def test_chop_trees_collects_loose_items_after_tree_falls():
    bridge = ChoppingBridge(
        initial_wood=0,
        final_wood=5,
        loose_items=[{"x": 13, "y": 10}, {"x": 12, "y": 11}],
    )

    result = asyncio.run(chop_trees(_ctx(bridge)))

    assert result["success"] is True
    interact_calls = [params for command, params in bridge.commands if command == "interact"]
    assert {"x": 13, "y": 10} in interact_calls
    assert {"x": 12, "y": 11} in interact_calls
    assert result["data"]["warnings"] == []
