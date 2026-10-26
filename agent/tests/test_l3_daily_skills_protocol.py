from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from bridge.bridge_result import BridgeResult
from v2_skills.builtin.harvest_crops.script import harvest_crops
from v2_skills.builtin.go_bed.script import go_bed
from v2_skills.builtin.pet_animals.script import pet_animals
from v2_skills.builtin.water_crops.script import water_crops
from v2_skills.context import SkillContext


def _bridge_result(capability: str, protocol_data: dict | None = None, success: bool = True) -> BridgeResult:
    return BridgeResult(
        success=success,
        status="ok" if success else "failed",
        data={
            "protocol_capability": capability,
            "protocol_data_field": capability,
            "protocol_data": protocol_data or {},
        },
        observation="ok" if success else "failed",
        side_effects=[],
        error_code=None if success else "FAILED",
        message=None if success else "failed",
        action_id="act_test",
        seq=1,
    )


def _runtime() -> dict:
    return {
        "fact": {
            "player_location": {"map_token": "Farm", "map_id": "Farm", "x": 10, "y": 10},
            "stamina": 270,
            "time_of_day": 900,
            "is_currently_raining": False,
        }
    }


def _crop(ref: str, x: int, y: int, *, watered: bool, ready: bool) -> dict:
    return {
        "ref": {"value": ref},
        "type": "crop",
        "fact_kind": "crop",
        "position": {"map_id": "Farm", "x": x, "y": y},
        "summary": "Parsnip",
        "crop": {
            "display_name": "Parsnip",
            "watered": watered,
            "ready_for_harvest": ready,
            "dead": False,
        },
    }


def _character(
    name: str,
    x: int,
    y: int,
    *,
    petted: bool,
    is_pet: bool = False,
) -> dict:
    payload_key = "pet" if is_pet else "farm_animal"
    payload = {"petted_today": petted} if is_pet else {"was_petted": petted}
    return {
        "ref": {"value": f"character:{name}"},
        "type": "pet" if is_pet else "farm_animal",
        "fact_kind": "pet" if is_pet else "farm_animal",
        "name": name,
        "display_name": name,
        "position": {"map_id": "Farm", "x": x, "y": y},
        payload_key: payload,
    }


def _world(*, crops: list[dict] | None = None, characters: list[dict] | None = None) -> dict:
    tiles = []
    for x in range(8, 15):
        for y in range(8, 15):
            tiles.append({"position": {"map_id": "Farm", "x": x, "y": y}, "passable": True})
    return {
        "fact": {
            "map_token": "Farm",
            "map_id": "Farm",
            "tiles": tiles,
            "entities": crops or [],
            "characters": characters or [],
        }
    }


class DailyBridge:
    def __init__(
        self,
        *,
        world: dict,
        worlds: list[dict] | None = None,
        water_result: dict | None = None,
        harvest_result: dict | None = None,
        equip_success: bool = True,
    ) -> None:
        self.commands: list[tuple[str, dict]] = []
        self._results: dict[str, BridgeResult] = {}
        self._seq = 0
        self._worlds = list(worlds or [world])
        self._world = world
        self._water_result = water_result or {"watered": 0, "skipped": 0, "refilled": 0}
        self._harvest_result = harvest_result or {"harvested": 0, "replanted": 0, "skipped": 0}
        self._equip_success = equip_success

    def is_protocol_transport_enabled(self) -> bool:
        return True

    def write_command(self, capability: str, params: dict) -> str:
        self._seq += 1
        action_id = f"act_{self._seq}"
        self.commands.append((capability, params))
        self._results[action_id] = self._make_result(capability)
        return action_id

    def write_legacy_command(self, capability: str, params: dict) -> str:
        self._seq += 1
        action_id = f"act_{self._seq}"
        self.commands.append((capability, params))
        self._results[action_id] = self._make_result(capability, legacy=True)
        return action_id

    def read_command_result(self, action_id: str) -> BridgeResult | None:
        return self._results.get(action_id)

    def cancel_command(self, action_id: str) -> None:
        self._results.pop(action_id, None)

    def _make_result(self, capability: str, *, legacy: bool = False) -> BridgeResult:
        if capability == "query_runtime_snapshot":
            return _bridge_result(capability, _runtime())
        if capability == "query_world_region":
            world = self._worlds.pop(0) if self._worlds else self._world
            return _bridge_result(capability, world)
        if capability == "equip":
            return _bridge_result(capability, {"ok": True}, success=self._equip_success)
        if capability == "water_crops" and legacy:
            return BridgeResult(
                success=True,
                status="ok",
                data=self._water_result,
                observation="water_crops ok",
                side_effects=[],
                error_code=None,
                message=None,
                action_id="act_test",
                seq=1,
            )
        if capability == "harvest_crops" and legacy:
            return BridgeResult(
                success=True,
                status="ok",
                data=self._harvest_result,
                observation="harvest_crops ok",
                side_effects=[],
                error_code=None,
                message=None,
                action_id="act_test",
                seq=1,
            )
        if capability == "go_to_bed":
            return _bridge_result(
                capability,
                {
                    "used_tp": False,
                    "day_advanced": True,
                    "sleep_prompt_seen": True,
                    "sleep_confirmed": True,
                    "decision_required": False,
                    "phase": "completed",
                    "bed_position": {"map_id": "FarmHouse", "x": 9, "y": 8},
                },
            )
        return _bridge_result(capability, {"ok": True})


def _ctx(bridge: Any) -> SkillContext:
    return SkillContext(bridge, Path("unused_game_state.json"))


def test_water_crops_delegates_execution_to_mod_compound_and_verifies_once():
    world_before = _world(
        crops=[
            _crop("crop-dry", 11, 10, watered=False, ready=False),
            _crop("crop-wet", 12, 10, watered=True, ready=False),
        ]
    )
    world_after = _world(
        crops=[
            _crop("crop-dry", 11, 10, watered=True, ready=False),
            _crop("crop-wet", 12, 10, watered=True, ready=False),
        ]
    )
    bridge = DailyBridge(
        world=world_before,
        worlds=[world_before, world_after],
        water_result={"watered": 1, "skipped": 0, "refilled": 1, "stopped_reason": "all_done"},
    )

    result = asyncio.run(water_crops(_ctx(bridge)))

    commands = [command for command, _params in bridge.commands]
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["watered"] == 1
    assert result["data"]["refilled"] == 1
    assert result["data"]["remaining_unwatered"] == 0
    assert commands.count("query_world_region") == 2
    assert "water_crops" in commands
    assert "equip" not in commands
    assert "move_to" not in commands
    assert "use_tool" not in commands
    assert "find_targets" not in commands
    assert "execute_on_targets" not in commands


def test_harvest_crops_delegates_execution_to_mod_compound_and_verifies_once():
    world_before = _world(
        crops=[
            _crop("crop-ready-near", 11, 10, watered=True, ready=True),
            _crop("crop-ready-far", 80, 80, watered=True, ready=True),
            _crop("crop-growing", 12, 10, watered=True, ready=False),
        ]
    )
    world_after = _world(
        crops=[
            _crop("crop-ready-near", 11, 10, watered=True, ready=False),
            _crop("crop-ready-far", 80, 80, watered=True, ready=False),
            _crop("crop-growing", 12, 10, watered=True, ready=False),
        ]
    )
    bridge = DailyBridge(
        world=world_before,
        worlds=[world_before, world_after],
        harvest_result={
            "harvested": 2,
            "replanted": 0,
            "skipped": 0,
            "stopped_reason": "all_done",
        },
    )

    result = asyncio.run(harvest_crops(_ctx(bridge)))

    commands = [command for command, _params in bridge.commands]
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["harvested"] == 2
    assert result["data"]["remaining_harvestable"] == 0
    assert result["data"]["harvestable_before"] == 2
    assert commands.count("query_world_region") == 2
    assert "harvest_crops" in commands
    assert "move_to" not in commands
    assert "interact" not in commands
    assert "find_targets" not in commands
    assert "execute_on_targets" not in commands
    assert "do" not in commands


def test_pet_animals_supports_farm_animals_and_pets_without_say():
    bridge = DailyBridge(
        world=_world(
            characters=[
                _character("Daisy", 11, 10, petted=False),
                _character("Yoga", 12, 10, petted=False, is_pet=True),
                _character("Clucky", 13, 10, petted=True),
            ]
        )
    )

    result = asyncio.run(pet_animals(_ctx(bridge)))

    commands = [command for command, _params in bridge.commands]
    assert result["success"] is True
    assert result["data"]["petted"] == 2
    assert result["data"]["total"] == 2
    assert commands.count("interact") == 2
    assert "find_targets" not in commands
    assert "do" not in commands
    assert "say" not in commands


def test_pet_animals_falls_back_to_runtime_move_adjacent_when_tile_snapshot_rejects_all_sides():
    world = _world(
        characters=[_character("Yoga", 11, 10, petted=False, is_pet=True)]
    )
    blocked_tiles = []
    for x in range(8, 15):
        for y in range(8, 15):
            blocked_tiles.append({
                "position": {"map_id": "Farm", "x": x, "y": y},
                "passable": False,
                "occupied": True,
            })
    world["fact"]["tiles"] = blocked_tiles
    bridge = DailyBridge(world=world)

    result = asyncio.run(pet_animals(_ctx(bridge)))

    commands = bridge.commands
    move_calls = [params for command, params in commands if command == "move_to"]
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["petted"] == 1
    assert len(move_calls) >= 1
    assert any(command == "interact" for command, _params in commands)


def test_go_bed_uses_protocol_go_to_bed_without_legacy_or_tp():
    bridge = DailyBridge(world=_world())

    result = asyncio.run(go_bed(_ctx(bridge)))

    commands = [command for command, _params in bridge.commands]
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["used_tp"] is False
    assert result["data"]["day_advanced"] is True
    assert result["data"]["sleep_confirmed"] is True
    assert commands == ["go_to_bed"]
    assert "go_bed" not in commands
    assert "tp" not in commands
