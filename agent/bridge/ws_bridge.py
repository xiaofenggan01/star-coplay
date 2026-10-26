from __future__ import annotations

import logging
import base64
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable, Optional

from bridge.bridge_result import BridgeResult
from text_sanitize import sanitize_for_utf8_json

try:
    from agent.generated.protocol import transport_pb2
    from agent.protocol.command_builder import build_command_request
    from agent.protocol.envelope_builder import build_command_envelope, serialize_envelope
    from agent.protocol.result_parser import command_result_to_bridge_result, extract_command_result
    from agent.protocol.tool_mapper import ProtocolMappingError, map_tool_call
except Exception:  # pragma: no cover - protocol mode reports this at runtime if enabled.
    transport_pb2 = None  # type: ignore[assignment]
    build_command_request = None  # type: ignore[assignment]
    build_command_envelope = None  # type: ignore[assignment]
    serialize_envelope = None  # type: ignore[assignment]
    command_result_to_bridge_result = None  # type: ignore[assignment]
    extract_command_result = None  # type: ignore[assignment]
    map_tool_call = None  # type: ignore[assignment]

    class ProtocolMappingError(ValueError):  # type: ignore[no-redef]
        """Raised when protocol command mapping cannot be completed."""

log = logging.getLogger("bridge.ws_bridge")

_RESULTS_CACHE_MAX = 200
_EVENTS_BUFFER_MAX = 2000


class WSBridge:
    """Server-side bridge backed by a WebSocket message stream.

    Transport-agnostic: a runtime (Gateway or test harness) drives this by
    calling ``handle_message`` for incoming frames and setting ``sender``
    for outgoing frames.
    """

    def __init__(
        self,
        sender: Callable[[dict[str, Any]], None] | None = None,
        bridge_dir: str | Path | None = None,
        protocol_version: int = 1,
        target_player_id: Optional[int] = None,
        results_cache_max: int = _RESULTS_CACHE_MAX,
        protocol_transport: bool = True,
        protocol_session_id: str = "ws-session",
    ) -> None:
        self._sender = sender
        self.bridge_dir = Path(bridge_dir) if bridge_dir else Path.cwd()
        self.state_path = self.bridge_dir / "game_state.json"
        self.events_path = self.bridge_dir / "events.jsonl"
        self.commands_path = self.bridge_dir / "commands.jsonl"
        self.results_path = self.bridge_dir / "results.jsonl"
        self.status_path = self.bridge_dir / "agent_status.json"

        self._protocol_version = protocol_version
        self.target_player_id = target_player_id

        self._lock = threading.Lock()
        self._cmd_seq = 0
        self._latest_state: dict[str, Any] | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=_EVENTS_BUFFER_MAX)
        self._results_cache: OrderedDict[str, BridgeResult] = OrderedDict()
        self._results_cache_max = results_cache_max
        self._pending_commands: dict[str, float] = {}
        self._adapter_info: dict[str, Any] = {"capabilities": []}
        self._capability_names: frozenset[str] = frozenset()
        self._adapter_info_ready = threading.Event()
        self._protocol_transport = protocol_transport
        self._protocol_session_id = protocol_session_id
        self._protocol_command_to_action: dict[str, str] = {}

    def set_sender(self, sender: Callable[[dict[str, Any]], None]) -> None:
        self._sender = sender

    def _send(self, msg: dict[str, Any]) -> None:
        if self._sender is None:
            log.warning("WSBridge sender not set; dropping %s", msg.get("type"))
            return
        try:
            self._sender(msg)
        except Exception as e:
            log.error("WSBridge send failed: %s", e)

    def _next_seq(self) -> int:
        with self._lock:
            self._cmd_seq += 1
            return self._cmd_seq

    @staticmethod
    def _make_action_id(seq: int) -> str:
        return f"act_{seq:04d}_{uuid.uuid4().hex[:4]}"

    # incoming

    def handle_message(self, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == "state_snapshot":
            state = sanitize_for_utf8_json(msg.get("state") or {})
            with self._lock:
                self._latest_state = state
        elif t == "game_event":
            evt = sanitize_for_utf8_json(msg.get("event") or {})
            if "seq" not in evt and "seq" in msg:
                evt["seq"] = msg["seq"]
            with self._lock:
                self._events.append(evt)
        elif t == "command_result":
            self._ingest_result(sanitize_for_utf8_json(msg))
        elif t == "agent_status":
            pass
        elif t == "adapter_info":
            info = msg.get("adapter_info") or msg.get("data") or {}
            self._set_adapter_info(info)
        elif t == "hello":
            info = msg.get("adapter_info")
            if isinstance(info, dict):
                self._set_adapter_info(info)
        elif t == "protocol_envelope":
            self._ingest_protocol_envelope(msg)
        else:
            log.debug("WSBridge: unhandled message type=%s", t)

    def _ingest_protocol_envelope(self, msg: dict[str, Any]) -> None:
        if transport_pb2 is None or extract_command_result is None:
            log.warning("WSBridge: protocol envelope received but protocol modules are unavailable")
            return
        payload = msg.get("payload")
        if not isinstance(payload, str) or not payload:
            log.warning("WSBridge: protocol_envelope missing base64 payload")
            return
        try:
            envelope = transport_pb2.Envelope()
            envelope.ParseFromString(base64.b64decode(payload))
            body = envelope.WhichOneof("body")
            if body != "command_result":
                log.debug("WSBridge: ignoring protocol envelope body=%s", body)
                return
            result = extract_command_result(envelope)
            action_id = envelope.request_id or self._protocol_command_to_action.get(result.command_id) or result.command_id
            bridge_result = command_result_to_bridge_result(
                result,
                action_id=action_id,
                seq=int(msg.get("seq", -1) or -1),
            )
            with self._lock:
                self._results_cache[action_id] = bridge_result
                self._pending_commands.pop(action_id, None)
                self._protocol_command_to_action.pop(result.command_id, None)
        except Exception as exc:
            log.warning("WSBridge: failed to parse protocol envelope: %s", exc)

    def _ingest_result(self, msg: dict[str, Any]) -> None:
        action_id = msg.get("action_id")
        if not action_id:
            return
        result = BridgeResult.from_dict(msg)
        log.debug(
            "WSBridge: command_result action_id=%s success=%s status=%s err=%s",
            action_id,
            result.success,
            result.status,
            result.error_code,
            extra={
                "event": "agent.command.result",
                "action_id": action_id,
                "error_code": result.error_code or "",
            },
        )
        if not result.success or result.error_code:
            log.warning(
                "WSBridge: command failed action_id=%s status=%s err=%s",
                action_id,
                result.status,
                result.error_code,
                extra={
                    "event": "agent.command.failed",
                    "action_id": action_id,
                    "error_code": result.error_code or "command_failed",
                },
            )
        with self._lock:
            if action_id in self._results_cache:
                self._results_cache.move_to_end(action_id)
            self._results_cache[action_id] = result
            while len(self._results_cache) > self._results_cache_max:
                self._results_cache.popitem(last=False)
            self._pending_commands.pop(action_id, None)

    def _set_adapter_info(self, info: dict[str, Any]) -> None:
        with self._lock:
            self._adapter_info = info or {"capabilities": []}
            self._capability_names = frozenset(
                c["name"] for c in self._adapter_info.get("capabilities", [])
                if isinstance(c, dict) and "name" in c
            )
        self._adapter_info_ready.set()

    # backend API

    def read_game_state(self) -> Optional[dict]:
        with self._lock:
            return dict(self._latest_state) if self._latest_state else None

    def read_new_events(self, after_seq: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e.get("seq", 0) > after_seq]

    def read_command_result(self, action_id: str) -> BridgeResult | None:
        with self._lock:
            return self._results_cache.get(action_id)

    def write_command(
        self,
        action: str,
        params: dict,
        target_player_id: Optional[int] = None,
    ) -> str:
        if self._protocol_transport:
            return self._write_protocol_command(action, params, target_player_id)

        return self.write_legacy_command(action, params, target_player_id)

    def write_legacy_command(
        self,
        action: str,
        params: dict,
        target_player_id: Optional[int] = None,
    ) -> str:
        effective_pid = target_player_id if target_player_id is not None else self.target_player_id
        seq = self._next_seq()
        action_id = self._make_action_id(seq)
        msg: dict[str, Any] = {
            "type": "command",
            "seq": seq,
            "action_id": action_id,
            "capability": action,
            "params": params,
        }
        if effective_pid is not None:
            msg["target_player_id"] = int(effective_pid) & 0xFFFFFFFFFFFFFFFF
        self._send(msg)
        log.info(
            "WSBridge: send command seq=%s cap=%s action_id=%s",
            seq,
            action,
            action_id,
            extra={
                "event": "agent.command.sent",
                "action_id": action_id,
                "capability": action,
                "target_player_id": effective_pid,
            },
        )
        self._pending_commands[action_id] = time.monotonic()
        return action_id

    def _write_protocol_command(
        self,
        action: str,
        params: dict,
        target_player_id: Optional[int] = None,
    ) -> str:
        effective_pid = target_player_id if target_player_id is not None else self.target_player_id
        if effective_pid is not None:
            log.warning("protocol transport ignores target_player_id=%s", effective_pid)
        if (
            map_tool_call is None
            or build_command_request is None
            or build_command_envelope is None
            or serialize_envelope is None
        ):
            raise RuntimeError("protocol transport requested but protocol modules are unavailable")

        seq = self._next_seq()
        action_id = self._make_action_id(seq)
        normalized_action, normalized_params = _normalize_protocol_action(action, params)
        mapped_commands = map_tool_call(normalized_action, normalized_params)

        for index, mapped in enumerate(mapped_commands, start=1):
            command_id = f"{action_id}_cmd_{index}_{mapped.capability}"
            request = build_command_request(mapped, command_id=command_id)
            envelope = build_command_envelope(
                request,
                session_id=self._protocol_session_id,
                request_id=action_id,
            )
            payload = base64.b64encode(serialize_envelope(envelope)).decode("ascii")
            msg = {
                "type": "protocol_envelope",
                "encoding": "base64",
                "seq": seq,
                "action_id": action_id,
                "capability": mapped.capability,
                "payload": payload,
                "protocol_command_id": command_id,
                "protocol_request_id": action_id,
            }
            text_preview = _protocol_text_preview(mapped)
            if text_preview:
                msg["text_preview"] = text_preview
            self._protocol_command_to_action[command_id] = action_id
            log.info(
                "WSBridge: send protocol_envelope seq=%s cap=%s action_id=%s command_id=%s request_id=%s",
                seq,
                mapped.capability,
                action_id,
                command_id,
                action_id,
                extra={
                    "event": "agent.protocol_command.sent",
                    "action_id": action_id,
                    "request_id": action_id,
                    "capability": mapped.capability,
                    "target_player_id": effective_pid,
                },
            )
            self._send(msg)

        self._pending_commands[action_id] = time.monotonic()
        return action_id

    def cancel_command(self, action_id: str) -> None:
        seq = self._next_seq()
        msg = {
            "type": "command",
            "seq": seq,
            "action_id": action_id,
            "capability": "",
            "params": {},
            "cancel": True,
        }
        self._send(msg)
        self._pending_commands.pop(action_id, None)

    def write_status(self, status: str, detail: Optional[dict] = None) -> None:
        self._send({"type": "agent_status", "status": status, "detail": detail or {}})

    def load_adapter_info(self) -> dict:
        if not self._adapter_info_ready.is_set():
            self._adapter_info_ready.wait(timeout=6.0)
        with self._lock:
            return dict(self._adapter_info)

    def reload_adapter_info(self) -> dict:
        return self.load_adapter_info()

    def capabilities(self) -> list[dict]:
        return list(self.load_adapter_info().get("capabilities") or [])

    def capability_names(self) -> frozenset[str]:
        self.load_adapter_info()
        return self._capability_names

    def is_capability_supported(self, name: str) -> bool:
        return name in self.capability_names()

    def is_protocol_transport_enabled(self) -> bool:
        return self._protocol_transport

    def is_command_timed_out(self, action_id: str, timeout_s: float) -> bool:
        send_time = self._pending_commands.get(action_id)
        if send_time is None:
            return False
        return (time.monotonic() - send_time) >= timeout_s


def _normalize_protocol_action(action: str, params: dict) -> tuple[str, dict]:
    normalized = dict(params or {})
    if action == "query_location_info":
        return "query_location", normalized
    if action == "emote" and "type" in normalized and "emote_type" not in normalized:
        normalized["emote_type"] = normalized.pop("type")
    if action == "equip" and "item" in normalized and "item_name" not in normalized:
        normalized["item_name"] = normalized.pop("item")
    if action == "move_to":
        if "mode" not in normalized:
            normalized["mode"] = "exact"
        location = normalized.pop("location", None) or normalized.pop("map_id", None)
        if location:
            return "go_to", {
                "map_id": str(location),
                "x": normalized.get("x"),
                "y": normalized.get("y"),
                "arrival_mode": "exact",
                **({"face_direction": normalized["face_direction"]} if "face_direction" in normalized else {}),
                **({"timeout_frames": normalized["timeout_frames"]} if "timeout_frames" in normalized else {}),
            }
    if action == "go_to" and "target" in normalized:
        structured = _parse_structured_target(str(normalized["target"]))
        if structured is None:
            raise ProtocolMappingError(
                "protocol go_to requires structured map/tile params; "
                "legacy target must be in 'Map:x,y' form"
            )
        _, map_id, x, y = structured
        return "go_to", {
            "map_id": map_id,
            "x": x,
            "y": y,
            "arrival_mode": "exact",
        }
    if action == "go_bed":
        return "go_to_bed", normalized
    return action, normalized


def _parse_structured_target(target: str) -> tuple[str, str, int, int] | None:
    text = target.strip()
    if not text or ":" not in text:
        return None
    map_id, rest = text.split(":", 1)
    map_id = map_id.strip()
    rest = rest.strip()
    if not map_id or "," not in rest:
        return None
    x_text, y_text = rest.split(",", 1)
    try:
        return text, map_id, int(x_text.strip()), int(y_text.strip())
    except ValueError:
        return None


def _protocol_text_preview(mapped: Any) -> str | None:
    """Return display-only text derived from generated protocol params."""

    if getattr(mapped, "capability", None) != "say":
        return None
    params = getattr(mapped, "params", None)
    text = str(getattr(params, "content", "") or "").strip()
    return text or None
