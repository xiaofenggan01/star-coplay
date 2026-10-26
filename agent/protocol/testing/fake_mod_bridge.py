"""Agent-only fake Mod bridge for Protocol 2.4.5 flow tests and smoke runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from google.protobuf.json_format import MessageToDict

from agent.generated.protocol import capability_pb2, capability_results_pb2, common_pb2, query_pb2
from agent.generated.protocol.transport_pb2 import Envelope

from agent.protocol.command_builder import build_command_request
from agent.protocol.constants import PROTOCOL_VERSION
from agent.protocol.envelope_builder import build_command_envelope
from agent.protocol.result_parser import command_result_to_bridge_result
from agent.protocol.tool_mapper import MappedCommand, map_tool_call


@dataclass(frozen=True)
class ProtocolExchange:
    action_id: str
    action: str
    params: dict[str, Any]
    mapped_commands: tuple[MappedCommand, ...]
    command_envelopes: tuple[Envelope, ...]
    result_envelopes: tuple[Envelope, ...]


class FakeProtocolModBridge:
    """BridgeBackend-shaped fake that converts Agent actions to protocol bytes.

    It does not open WebSocket connections and does not require the game. Each
    ``write_command`` call is mapped to Protocol 2.4.5 ``CommandRequest``
    envelope(s), then immediately completed with fake successful
    ``CommandResult`` envelope(s). ``ActionDispatcher`` can consume the final
    aggregate as a normal ``BridgeResult``.
    """

    def __init__(
        self,
        *,
        enabled_capabilities: list[str] | None = None,
        game_state: dict[str, Any] | None = None,
        bridge_dir: str | Path | None = None,
        session_id: str = "fake-session",
    ) -> None:
        self.bridge_dir = Path(bridge_dir) if bridge_dir else Path.cwd()
        self.state_path = self.bridge_dir / "fake_game_state.json"
        self.events_path = self.bridge_dir / "fake_events.jsonl"
        self.commands_path = self.bridge_dir / "fake_commands.jsonl"
        self.results_path = self.bridge_dir / "fake_results.jsonl"
        self.status_path = self.bridge_dir / "fake_agent_status.json"
        self.target_player_id: Optional[int] = None

        self.session_id = session_id
        self._seq = 0
        self._results = {}
        self._status_writes: list[dict[str, Any]] = []
        self.exchanges: list[ProtocolExchange] = []
        self.game_state = game_state or {
            "time": "09:00",
            "player": {
                "location": "Farm",
                "x": 64,
                "y": 15,
                "facing": "down",
                "energy": 270,
            },
        }
        self._enabled_capabilities = enabled_capabilities or [
            "say",
            "emote",
            "face",
            "move_to",
            "go_to",
            "go_to_bed",
            "interact",
            "use_tool",
            "equip",
            "query_inventory",
            "query_location",
            "query_map_exits",
            "query_runtime_snapshot",
            "query_world_region",
            "query_inventory_snapshot",
            "query_ui_snapshot",
            "query_inspect_refs",
            "open_menu",
            "query_menu",
            "menu_click",
            "menu_close",
        ]

    @property
    def last_exchange(self) -> ProtocolExchange | None:
        return self.exchanges[-1] if self.exchanges else None

    @property
    def status_writes(self) -> list[dict[str, Any]]:
        return list(self._status_writes)

    def read_game_state(self) -> dict[str, Any]:
        return dict(self.game_state)

    def read_new_events(self, after_seq: int = 0) -> list[dict]:
        return []

    def write_command(
        self,
        action: str,
        params: dict,
        target_player_id: Optional[int] = None,
    ) -> str:
        del target_player_id
        self._seq += 1
        action_id = f"act_{self._seq:04d}_{action}"
        normalized_action, normalized_params = _normalize_agent_action(action, params)
        mapped_commands = tuple(map_tool_call(normalized_action, normalized_params))

        command_envelopes = []
        result_envelopes = []
        bridge_result = None
        for index, mapped in enumerate(mapped_commands, start=1):
            command_id = f"{action_id}_cmd_{index}_{mapped.capability}"
            request = build_command_request(mapped, command_id=command_id)
            command_envelope = build_command_envelope(
                request,
                session_id=self.session_id,
                request_id=action_id,
            )
            result_envelope = self._fake_success_result_envelope(
                command_envelope,
                seq=self._seq,
                action_id=action_id,
            )
            command_envelopes.append(command_envelope)
            result_envelopes.append(result_envelope)
            bridge_result = command_result_to_bridge_result(
                result_envelope.command_result,
                action_id=action_id,
                seq=self._seq,
            )

        self.exchanges.append(
            ProtocolExchange(
                action_id=action_id,
                action=action,
                params=dict(params),
                mapped_commands=mapped_commands,
                command_envelopes=tuple(command_envelopes),
                result_envelopes=tuple(result_envelopes),
            )
        )
        self._results[action_id] = bridge_result
        return action_id

    def read_command_result(self, action_id: str):
        return self._results.get(action_id)

    def cancel_command(self, action_id: str) -> None:
        self._results.pop(action_id, None)

    def write_status(self, status: str, detail: Optional[dict] = None) -> None:
        self._status_writes.append({"status": status, "detail": detail or {}})

    def load_adapter_info(self) -> dict:
        return {"capabilities": self.capabilities()}

    def reload_adapter_info(self) -> dict:
        return self.load_adapter_info()

    def capabilities(self) -> list[dict]:
        return [
            {"name": name, "description": f"fake protocol {name}"}
            for name in self._enabled_capabilities
        ]

    def capability_names(self) -> frozenset[str]:
        return frozenset(self._enabled_capabilities)

    def is_capability_supported(self, name: str) -> bool:
        return name in self.capability_names()

    def is_protocol_transport_enabled(self) -> bool:
        return True

    def is_command_timed_out(self, action_id: str, timeout_s: float) -> bool:
        del action_id, timeout_s
        return False

    def protocol_summary(self, exchange: ProtocolExchange | None = None) -> dict[str, Any]:
        exchange = exchange or self.last_exchange
        if exchange is None:
            return {}
        return {
            "action_id": exchange.action_id,
            "action": exchange.action,
            "capabilities": [env.command_request.capability for env in exchange.command_envelopes],
            "command_ids": [env.command_request.command_id for env in exchange.command_envelopes],
            "oneof_params": [
                env.command_request.WhichOneof("params")
                for env in exchange.command_envelopes
            ],
            "envelope_bytes": [
                len(env.SerializeToString())
                for env in exchange.command_envelopes
            ],
            "results": [
                {
                    "status": capability_pb2.CommandStatus.Name(env.command_result.status),
                    "data": _message_to_dict(env.command_result),
                }
                for env in exchange.result_envelopes
            ],
        }

    def _fake_success_result_envelope(
        self,
        command_envelope: Envelope,
        *,
        seq: int,
        action_id: str,
    ) -> Envelope:
        request = command_envelope.command_request
        result = capability_pb2.CommandResult(
            command_id=request.command_id,
            success=True,
            status=capability_pb2.COMMAND_STATUS_COMPLETED,
            observation=common_pb2.Observation(
                text=f"fake mod completed {request.capability} for {action_id}"
            ),
            capability=request.capability,
        )
        data_field, data_message = self._fake_result_data(request)
        getattr(result, data_field).CopyFrom(data_message)

        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=f"fake_result_{seq}_{request.capability}",
            request_id=command_envelope.request_id,
            session_id=self.session_id,
            timestamp_ms=command_envelope.timestamp_ms + 1,
        )
        envelope.command_result.CopyFrom(result)
        return envelope

    def _fake_result_data(self, request: capability_pb2.CommandRequest):
        params_field = request.WhichOneof("params")
        if params_field == "say":
            return "say", capability_results_pb2.SayResult(
                content_length=len(request.say.content)
            )
        if params_field == "emote":
            return "emote", capability_results_pb2.EmoteResult(
                emote_type=request.emote.emote_type
            )
        if params_field == "face":
            return "face", capability_results_pb2.FaceResult(
                final_direction=request.face.direction,
                already_facing=False,
            )
        if params_field == "move_to":
            return "move_to", capability_results_pb2.MoveToResult(
                mode=request.move_to.mode,
                target_x=request.move_to.x,
                target_y=request.move_to.y,
                resolved_destination_x=request.move_to.x,
                resolved_destination_y=request.move_to.y,
                final=self._location(request.move_to.x, request.move_to.y),
                reached_destination=True,
            )
        if params_field == "go_to":
            return "go_to", capability_results_pb2.GoToResult(
                target_map=request.go_to.map_id,
                target_x=request.go_to.x,
                target_y=request.go_to.y,
                final=self._location(
                    request.go_to.x,
                    request.go_to.y,
                    map_id=request.go_to.map_id,
                ),
                arrival_mode=request.go_to.arrival_mode,
                steps_total=1,
                steps_completed=1,
            )
        if params_field == "go_to_bed":
            return "go_to_bed", capability_results_pb2.GoToBedResult(
                date_before="Spring 1, Year 1",
                date_after="Spring 2, Year 1",
                day_advanced=True,
                player_can_move_after=True,
            )
        if params_field == "interact":
            return "interact", capability_results_pb2.InteractResult(
                effect=common_pb2.TileEffectResult(
                    target_tile=request.interact.target_tile
                )
            )
        if params_field == "use_tool":
            return "use_tool", capability_results_pb2.UseToolResult(
                effect=common_pb2.TileEffectResult(
                    target_tile=request.use_tool.target_tile
                ),
                tool_name="Fake Tool",
                charge_level=request.use_tool.charge_level,
            )
        if params_field == "equip":
            selected_by = "slot" if request.equip.HasField("slot") else "item_name"
            return "equip", capability_results_pb2.EquipResult(
                selected_by=selected_by,
                slot=request.equip.slot if request.equip.HasField("slot") else 1,
                item_name=request.equip.item_name or "Fake Item",
                item_display_name=request.equip.item_name or "Fake Item",
                method="fake_mod",
            )
        if params_field in {"open_menu", "menu_click", "menu_close"}:
            return "menu", capability_results_pb2.MenuResult(
                action=params_field,
                component_id=getattr(request.menu_click, "component_id", 0),
                source=getattr(request.menu_click, "source", 0),
            )
        if params_field == "query_menu":
            return "menu_snapshot", capability_results_pb2.MenuSnapshot(
                menu_type="GameMenu",
                active_tab="inventory",
                snapshot_id="fake-menu-snapshot",
            )
        if params_field == "query_inventory":
            return "query_inventory", capability_results_pb2.QueryInventoryResult(
                slots_used=1,
                slots_total=36,
            )
        if params_field == "query_location":
            return "query_location", capability_results_pb2.QueryLocationResult(
                location=self._location()
            )
        if params_field == "query_map_exits":
            return "query_map_exits", capability_results_pb2.QueryMapExitsResult(
                exits=[
                    capability_results_pb2.MapExit(
                        target_location="Town",
                        display_name="Town exit",
                        direction=common_pb2.DIRECTION_RIGHT,
                        exit_x=120,
                        exit_y=15,
                    )
                ]
            )
        if params_field == "query_runtime_snapshot":
            return "query_runtime_snapshot", query_pb2.QueryRuntimeSnapshotResult()
        if params_field == "query_world_region":
            return "query_world_region", query_pb2.QueryWorldRegionResult()
        if params_field == "query_inventory_snapshot":
            return "query_inventory_snapshot", query_pb2.QueryInventorySnapshotResult()
        if params_field == "query_ui_snapshot":
            return "query_ui_snapshot", query_pb2.QueryUiSnapshotResult()
        if params_field == "query_inspect_refs":
            return "query_inspect_refs", query_pb2.QueryInspectRefsResult()
        raise ValueError(f"no fake result data for params oneof: {params_field}")

    def _location(
        self,
        x: int | None = None,
        y: int | None = None,
        *,
        map_id: str | None = None,
    ) -> common_pb2.LocationSnapshot:
        player = self.game_state.get("player", {})
        return common_pb2.LocationSnapshot(
            map_token=map_id or str(player.get("location") or "Farm"),
            x=int(player.get("x", 64) if x is None else x),
            y=int(player.get("y", 15) if y is None else y),
            facing=common_pb2.DIRECTION_DOWN,
        )


def _normalize_agent_action(action: str, params: dict) -> tuple[str, dict]:
    normalized = dict(params or {})
    if action == "emote" and "type" in normalized and "emote_type" not in normalized:
        normalized["emote_type"] = normalized.pop("type")
    if action == "go_bed":
        return "go_to_bed", normalized
    return action, normalized


def _message_to_dict(message) -> dict[str, Any]:
    return MessageToDict(message, preserving_proto_field_name=True)
