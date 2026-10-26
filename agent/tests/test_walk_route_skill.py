from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from bridge.bridge_result import BridgeResult
from v2_skills.builtin.walk_route.script import walk_route
from v2_skills.context import SkillContext
from v2_skills.registry import SkillRegistry


class RouteBridge:
    def __init__(self, failures: set[tuple[str, int, int]] | None = None):
        self.failures = failures or set()
        self.commands: list[tuple[str, dict]] = []
        self._results: dict[str, BridgeResult] = {}
        self._seq = 0

    def is_protocol_transport_enabled(self) -> bool:
        return True

    def write_command(self, capability: str, params: dict) -> str:
        self._seq += 1
        action_id = f"act_{self._seq}"
        self.commands.append((capability, params))
        key = (str(params["map_id"]), int(params["x"]), int(params["y"]))
        if key in self.failures:
            self._results[action_id] = BridgeResult(
                success=False,
                status="failed",
                data={},
                observation="go_to failed",
                side_effects=[],
                error_code="PATH_BLOCKED",
                message="Path blocked",
                action_id=action_id,
                seq=self._seq,
            )
        else:
            self._results[action_id] = BridgeResult(
                success=True,
                status="ok",
                data={"arrived": True},
                observation="ok",
                side_effects=[],
                error_code=None,
                message=None,
                action_id=action_id,
                seq=self._seq,
            )
        return action_id

    def read_command_result(self, action_id: str) -> BridgeResult | None:
        return self._results.get(action_id)

    def cancel_command(self, action_id: str) -> None:
        self._results.pop(action_id, None)


def _ctx(bridge: Any) -> SkillContext:
    return SkillContext(bridge, Path("unused_game_state.json"))


def test_walk_route_visits_waypoints_in_order():
    bridge = RouteBridge()

    result = asyncio.run(
        walk_route(
            _ctx(bridge),
            [
                {"map_id": "Farm", "x": 64, "y": 15},
                {"map_id": "Farm", "x": 70, "y": 20},
                {"map_id": "Town", "x": 31, "y": 67},
            ],
        )
    )

    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["data"]["completed_count"] == 3
    assert [params for command, params in bridge.commands if command == "go_to"] == [
        {"map_id": "Farm", "x": 64, "y": 15, "arrival_mode": "exact"},
        {"map_id": "Farm", "x": 70, "y": 20, "arrival_mode": "exact"},
        {"map_id": "Town", "x": 31, "y": 67, "arrival_mode": "exact"},
    ]


def test_walk_route_stops_at_failed_waypoint():
    bridge = RouteBridge(failures={("Farm", 70, 20)})

    result = asyncio.run(
        walk_route(
            _ctx(bridge),
            [
                {"map_id": "Farm", "x": 64, "y": 15},
                {"map_id": "Farm", "x": 70, "y": 20},
                {"map_id": "Town", "x": 31, "y": 67},
            ],
        )
    )

    assert result["success"] is True
    assert result["status"] == "partial"
    assert result["data"]["completed_count"] == 1
    assert result["data"]["failed_waypoint"]["map_id"] == "Farm"
    assert result["data"]["failed_waypoint"]["x"] == 70
    assert len([params for command, params in bridge.commands if command == "go_to"]) == 2


def test_registry_exposes_walk_route_skill():
    registry = SkillRegistry(Path(__file__).parent.parent / "v2_skills")
    entry = registry.get("walk_route")

    assert entry is not None
    assert entry.fc_schema["function"]["name"] == "skill-walk_route"
