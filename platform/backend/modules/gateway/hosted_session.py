"""Feature-gated Hosted GameSession registry and Protocol/V2 route adapter."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from google.protobuf.json_format import MessageToDict, ParseDict

from agent.generated.protocol import capability_pb2, common_pb2, transport_pb2
from agent.protocol.canonical_surface import map_canonical_command
from agent.protocol.envelope_builder import build_command_envelope, serialize_envelope
from modules.mcp.contracts.models import (
    CancelResult, CommandState, DispatchReceipt, DispatchState, GameCommand,
    GameCommandResult, RouteHealth,
)
from modules.mcp.core.errors import McpError
from modules.gateway.runtime_contracts import (
    GameSessionSnapshot, GatewayEvent, GatewayEventPublisher, GatewayEventType,
)


class ClosePublishDeferred(RuntimeError):
    """CLOSED is fenced and retryable, but its durable publish is incomplete."""


@dataclass(frozen=True)
class HostedHelloFacts:
    route_contract_version: str
    game_instance_nonce: str
    capability_digest: str
    capability_entries: tuple[tuple[str, str, str], ...]
    player_id: str = "0"
    agent_control_enabled: bool = True
    agent_control_target_player_id: str | None = "0"
    agent_control_revision: int = 0


@dataclass(frozen=True)
class HostedControlSelection:
    owner_user_id: str
    revision: int
    enabled: bool
    target_player_id: str | None


class ControlSelectionConflict(ValueError):
    pass


class HostedControlSelectionStore:
    """Owner-scoped durable desired-control projection.

    The in-memory path is used by isolated tests. With a DSN, the single SQL
    statement is the arbitration boundary, so a Gateway restart cannot accept
    an older Mod snapshot.
    """
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._pool: Any | None = None
        self._values: dict[str, HostedControlSelection] = {}
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> Any | None:
        if self._pool is not None or not self._dsn:
            return self._pool
        import asyncpg
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def accept(self, owner_user_id: str, *, enabled: bool,
                     target_player_id: str | None, revision: int) -> HostedControlSelection:
        pool = await self._get_pool()
        if pool is None:
            async with self._lock:
                current = self._values.get(owner_user_id)
                proposed = HostedControlSelection(owner_user_id, revision, enabled, target_player_id)
                if current is None or revision > current.revision:
                    self._values[owner_user_id] = proposed
                    return proposed
                if revision == current.revision and (
                    enabled != current.enabled or target_player_id != current.target_player_id
                ):
                    raise ControlSelectionConflict("same control revision has a different payload")
                return current
        row = await pool.fetchrow(
            """
            INSERT INTO public.hosted_control_selections
                (owner_user_id, revision, enabled, target_player_id, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (owner_user_id) DO UPDATE SET
                revision = EXCLUDED.revision,
                enabled = EXCLUDED.enabled,
                target_player_id = EXCLUDED.target_player_id,
                updated_at = NOW()
            WHERE hosted_control_selections.revision < EXCLUDED.revision
            RETURNING owner_user_id, revision, enabled, target_player_id
            """, owner_user_id, revision, enabled, target_player_id,
        )
        if row is None:
            row = await pool.fetchrow(
                "SELECT owner_user_id, revision, enabled, target_player_id "
                "FROM public.hosted_control_selections WHERE owner_user_id = $1",
                owner_user_id,
            )
        current = HostedControlSelection(str(row["owner_user_id"]), int(row["revision"]),
                                         bool(row["enabled"]), row["target_player_id"])
        if revision == current.revision and (enabled != current.enabled
                                              or target_player_id != current.target_player_id):
            raise ControlSelectionConflict("same control revision has a different payload")
        return current

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def compute_capability_digest(entries: tuple[tuple[str, str, str], ...]) -> str:
    from modules.mcp.capabilities.snapshot import compute_capability_snapshot_digest
    return compute_capability_snapshot_digest(entries)


def parse_hosted_hello(hello: Mapping[str, Any]) -> HostedHelloFacts:
    values: dict[str, str] = {}
    for key in ("route_contract_version", "game_instance_nonce", "capability_digest"):
        value = hello.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise McpError("upstream_protocol_error", f"missing or invalid hosted hello field: {key}")
        values[key] = value.strip()
    if values["route_contract_version"] != "starcoplay.hosted-route.v1":
        raise McpError("upstream_protocol_error", "unsupported hosted route contract version")
    raw_entries = hello.get("capability_entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise McpError("upstream_protocol_error", "missing or invalid hosted capability entries")
    entries: list[tuple[str, str, str]] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise McpError("upstream_protocol_error", "invalid hosted capability entry")
        entry = tuple(raw.get(key) for key in ("source_surface", "capability_id", "capability_contract_version"))
        if any(not isinstance(value, str) or not value for value in entry):
            raise McpError("upstream_protocol_error", "invalid hosted capability entry")
        entries.append(entry)  # type: ignore[arg-type]
    normalized = tuple(sorted(set(entries)))
    if compute_capability_digest(normalized) != values["capability_digest"]:
        raise McpError("upstream_protocol_error", "hosted capability digest mismatch")
    player_id = hello.get("player_id")
    enabled = hello.get("agent_control_enabled")
    target = hello.get("agent_control_target_player_id")
    revision = hello.get("agent_control_revision")
    if not isinstance(player_id, str) or not player_id.isdecimal() or len(player_id) > 32:
        raise McpError("upstream_protocol_error", "missing or invalid hosted hello field: player_id")
    if not isinstance(enabled, bool):
        raise McpError("upstream_protocol_error", "missing or invalid hosted hello field: agent_control_enabled")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise McpError("upstream_protocol_error", "missing or invalid hosted hello field: agent_control_revision")
    if enabled:
        if not isinstance(target, str) or not target.isdecimal() or len(target) > 32:
            raise McpError("upstream_protocol_error", "invalid agent control target player id")
    elif target is not None:
        raise McpError("upstream_protocol_error", "disabled agent control must not name a target")
    return HostedHelloFacts(**values, capability_entries=normalized, player_id=player_id,
                            agent_control_enabled=enabled,
                            agent_control_target_player_id=target,
                            agent_control_revision=revision)


@dataclass
class _CommandRecord:
    fingerprint: str
    expected_request_id: str
    dispatch_message_id: str
    wire_format: str
    capability_id: str
    operation_id: str
    state: CommandState = CommandState.CREATED
    result: GameCommandResult | None = None
    status_request_ids: set[str] = None  # type: ignore[assignment]
    cancel_request_ids: set[str] = None  # type: ignore[assignment]
    control_responses: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.status_request_ids = set()
        self.cancel_request_ids = set()
        self.control_responses = {}


_TERMINAL_STATES = frozenset({
    CommandState.SUCCEEDED, CommandState.FAILED, CommandState.CANCELLED,
    CommandState.TIMED_OUT, CommandState.EXPIRED_UNKNOWN,
})

_HOSTED_RESULT_FRAME_TYPES = frozenset({"command_result", "protocol_envelope"})


class WebSocketHostedSessionRoute:
    """One resumable command domain, rebound only by its registry."""

    def __init__(self, game_session_id: str, resume_identity: str, facts: HostedHelloFacts,
                 session: Any, *, clock: Callable[[], float] = time.time,
                 control_timeout_s: float = 5.0, say_reflections: Any | None = None) -> None:
        self.game_session_id = game_session_id
        self.resume_identity = resume_identity
        self.facts = facts
        self._session = session
        self._connection_id = session.connection_id
        self._records: dict[str, _CommandRecord] = {}
        self._condition = asyncio.Condition()
        self._subscriber = session.subscribe(frame_types=_HOSTED_RESULT_FRAME_TYPES)
        self._clock = clock
        self._control_timeout_s = control_timeout_s
        self._operation_lock = asyncio.Lock()
        self._receive_lock = asyncio.Lock()
        self._seen_message_ids: set[str] = set()
        self._message_id_order: deque[str] = deque()
        self._message_id_limit = 1024
        self._say_reflections = say_reflections

    def rebind(self, facts: HostedHelloFacts, session: Any) -> None:
        self._session.unsubscribe(self._subscriber)
        self.facts = facts
        self._session = session
        self._connection_id = session.connection_id
        self._subscriber = session.subscribe(frame_types=_HOSTED_RESULT_FRAME_TYPES)

    def detach(self, connection_id: str) -> None:
        if self._connection_id == connection_id:
            self._session.unsubscribe(self._subscriber)
            self._connection_id = ""
            # Transport loss is an epistemic terminal boundary.  Commands
            # without an observed terminal result become UNKNOWN immediately
            # and must never be replayed on a resumed socket.
            for command_id, record in self._records.items():
                if record.result is None:
                    record.state = CommandState.UNKNOWN
                    record.result = GameCommandResult(command_id, CommandState.UNKNOWN)
            # Wake any waiter currently blocked on the detached subscriber so
            # it can observe the terminal UNKNOWN snapshot without waiting for
            # the original command deadline.  Once transport is gone, queued
            # frames are stale: discard them so a full bounded queue can never
            # prevent the authoritative SUSPENDED lifecycle from publishing.
            while True:
                try:
                    self._subscriber.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._subscriber.put_nowait({"type": "hosted_session_detached"})

    @property
    def connection_id(self) -> str:
        return self._connection_id

    def _require_current(self) -> Any:
        if not self._connection_id or self._session.connection_id != self._connection_id:
            raise McpError("route_unavailable", "hosted socket is stale or disconnected")
        return self._session

    @staticmethod
    def _fingerprint(command: GameCommand) -> str:
        payload = json.dumps({
            "capability": command.capability_id,
            "version": command.capability_version,
            "payload": command.validated_payload,
            "nonce": command.game_instance_nonce,
            "lease": command.lease_epoch,
            "digest": command.capability_digest,
        }, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    async def dispatch(self, command: GameCommand) -> DispatchReceipt:
        if command.deadline_at <= self._clock():
            return DispatchReceipt(command.command_id, DispatchState.REJECTED, "command deadline expired")
        session = self._require_current()
        if (command.game_instance_nonce != self.facts.game_instance_nonce
                or command.capability_digest != self.facts.capability_digest):
            raise McpError("route_unavailable", "hosted command fence is stale")
        fingerprint = self._fingerprint(command)
        prior = self._records.get(command.command_id)
        if prior is not None:
            state = DispatchState.DUPLICATE if prior.fingerprint == fingerprint else DispatchState.REJECTED
            return DispatchReceipt(command.command_id, state, None if state is DispatchState.DUPLICATE else "command_id collision")
        try:
            mapped = map_canonical_command(command.capability_id, command.validated_payload, command_id=command.command_id)
        except Exception as exc:
            return DispatchReceipt(command.command_id, DispatchState.REJECTED, str(exc))
        record = _CommandRecord(
            fingerprint=fingerprint,
            expected_request_id=command.tool_call_id,
            dispatch_message_id=command.message_id,
            wire_format=mapped.wire_format,
            capability_id=command.capability_id,
            operation_id=str(command.trace_context.get("operation_id", "")),
            state=CommandState.ACCEPTED,
        )
        self._records[command.command_id] = record
        if command.capability_id == "say" and self._say_reflections is not None:
            message = command.validated_payload.get("message")
            if isinstance(message, str):
                self._say_reflections.register(
                    owner_user_id=str(session.instance.user_id or ""),
                    game_session_id=self.game_session_id,
                    target_player_id=str(self.facts.agent_control_target_player_id or ""),
                    message=message,
                    command_id=command.command_id,
                    tool_call_id=command.tool_call_id,
                    operation_id=record.operation_id,
                )
        fence = {
            "contract_version": command.contract_version,
            "message_id": command.message_id,
            "deadline_at": command.deadline_at,
            "game_instance_nonce": command.game_instance_nonce,
            "capability_digest": command.capability_digest,
            "lease_epoch": command.lease_epoch,
        }
        if mapped.wire_format == "protocol-2.4.5":
            request = ParseDict(dict(mapped.payload), capability_pb2.CommandRequest())
            envelope = build_command_envelope(request, session_id=self.game_session_id,
                                              request_id=command.tool_call_id,
                                              message_id=command.message_id)
            frame = {"type": "protocol_envelope", "encoding": "base64",
                     "action_id": command.command_id, "protocol_command_id": command.command_id,
                     "capability": mapped.capability,
                     "payload": base64.b64encode(serialize_envelope(envelope)).decode("ascii"),
                     "hosted_fence": fence}
        else:
            frame = {"type": "command", **dict(mapped.payload), "hosted_fence": fence}
        async with self._operation_lock:
            session = self._require_current()
            if command.deadline_at <= self._clock():
                self._records.pop(command.command_id, None)
                return DispatchReceipt(command.command_id, DispatchState.REJECTED, "command deadline expired")
            await session.outbound.put(frame)
        return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)

    async def _ingest_until(self, command_id: str, deadline: float) -> None:
        async with self._receive_lock:
            while self._clock() < deadline and self._connection_id:
                timeout = max(0.0, deadline - self._clock())
                try:
                    frame = await asyncio.wait_for(self._subscriber.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    return
                self._ingest(frame)
                record = self._records.get(command_id)
                if record and record.result is not None:
                    return

    def _ingest(self, frame: Mapping[str, Any]) -> tuple[str, str, str] | None:
        if frame.get("type") == "command_result":
            command_id = str(frame.get("action_id") or "")
            if command_id not in self._records:
                return None
            if self._records[command_id].state in _TERMINAL_STATES:
                return ("command_result", command_id, "")
            status = str(frame.get("status") or "").upper()
            nested_error = frame.get("error")
            nested_code = nested_error.get("code") if isinstance(nested_error, Mapping) else None
            if frame.get("success"):
                state = _WIRE_STATES.get(status, CommandState.SUCCEEDED)
            elif nested_code == "CANCELLED":
                state = CommandState.CANCELLED
            elif isinstance(nested_code, str) and (
                nested_code == "TIMEOUT" or nested_code.endswith("_TIMEOUT")
            ):
                state = CommandState.TIMED_OUT
            elif status in _WIRE_STATES:
                state = _WIRE_STATES[status]
            else:
                state = CommandState.FAILED
            # Preserve the complete V2 source result. Canonical capability
            # semantics belong to the single deployment-neutral Result Adapter
            # in MCP Core, not to this Hosted transport/router.
            output = {
                key: value for key, value in frame.items()
                if key != "type"
            }
            self._records[command_id].state = state
            route_error = frame.get("error_code") if isinstance(frame.get("error_code"), str) else nested_code
            self._records[command_id].result = GameCommandResult(
                command_id, state, dict(output), route_error if isinstance(route_error, str) else None,
            )
            self._mark_say_delivered(command_id, state)
            return ("command_result", command_id, "")
        if frame.get("type") != "protocol_envelope" or not isinstance(frame.get("payload"), str):
            return None
        try:
            envelope = transport_pb2.Envelope()
            envelope.ParseFromString(base64.b64decode(frame["payload"]))
        except Exception as exc:
            raise McpError("upstream_protocol_error", "invalid protocol envelope") from exc
        body = envelope.WhichOneof("body")
        if (envelope.protocol_version != "2.4.5" or not envelope.message_id
                or envelope.session_id != self.game_session_id):
            return None
        if envelope.message_id in self._seen_message_ids:
            return None
        message = getattr(envelope, body) if body else None
        command_id = getattr(message, "command_id", "")
        record = self._records.get(command_id)
        if record is None:
            return None
        expected_request_ids = {record.expected_request_id}
        if body == "command_status_response":
            expected_request_ids = record.status_request_ids
        elif body == "command_accepted" and record.cancel_request_ids:
            expected_request_ids = record.cancel_request_ids | {record.expected_request_id}
        elif body == "command_result":
            expected_request_ids |= record.cancel_request_ids
        if envelope.request_id not in expected_request_ids:
            return None
        self._seen_message_ids.add(envelope.message_id)
        self._message_id_order.append(envelope.message_id)
        if len(self._message_id_order) > self._message_id_limit:
            self._seen_message_ids.discard(self._message_id_order.popleft())
        if envelope.request_id in record.status_request_ids | record.cancel_request_ids:
            record.control_responses[envelope.request_id] = body or ""
        if record.state in _TERMINAL_STATES:
            # Late delivery is expected around reconnect/control races.  The
            # terminal snapshot is authoritative and must never regress.
            return (body or "", command_id, envelope.request_id)
        if body == "command_accepted":
            if record.state in {CommandState.CREATED, CommandState.ACCEPTED}:
                record.state = CommandState.ACCEPTED
        elif body == "command_progress":
            record.state = _PROTO_STATES.get(message.status, CommandState.RUNNING)
        elif body == "command_status_response":
            record.state = _PROTO_STATES.get(message.status, CommandState.UNKNOWN)
            if record.state in _TERMINAL_STATES:
                record.result = GameCommandResult(command_id, record.state)
        elif body == "command_result":
            state = _PROTO_STATES.get(message.status, CommandState.UNKNOWN)
            output = MessageToDict(message, preserving_proto_field_name=True)
            error_code = (
                common_pb2.ErrorCode.Name(message.error.code)
                if not message.success and message.error.code else None
            )
            record.state = state
            record.result = GameCommandResult(command_id, state, output, error_code)
            self._mark_say_delivered(command_id, state)
        return (body or "", command_id, envelope.request_id)

    def _mark_say_delivered(self, command_id: str, state: CommandState) -> None:
        if state is not CommandState.SUCCEEDED or self._say_reflections is None:
            return
        record = self._records.get(command_id)
        if record is None or record.capability_id != "say" or not record.operation_id:
            return
        self._say_reflections.mark_delivered(
            owner_user_id=str(self._session.instance.user_id or ""),
            game_session_id=self.game_session_id,
            operation_id=record.operation_id,
        )

    async def _await_control(self, command_id: str, bodies: frozenset[str],
                             connection_id: str, request_id: str) -> str | None:
        deadline = self._clock() + self._control_timeout_s
        async with self._receive_lock:
            while self._clock() < deadline:
                cached = self._records[command_id].control_responses.pop(request_id, None)
                if cached in bodies:
                    return cached
                if self._connection_id != connection_id:
                    return None
                try:
                    frame = await asyncio.wait_for(
                        self._subscriber.get(), max(0.0, deadline - self._clock())
                    )
                except asyncio.TimeoutError:
                    return None
                correlated = self._ingest(frame)
                if (correlated is not None and correlated[0] in bodies
                        and correlated[1] == command_id and correlated[2] == request_id):
                    self._records[command_id].control_responses.pop(request_id, None)
                    return correlated[0]
        return None

    async def await_result(self, command_id: str, deadline: float) -> GameCommandResult:
        record = self._records.get(command_id)
        if record is None:
            return GameCommandResult(command_id, CommandState.UNKNOWN)
        if record.result is None:
            await self._ingest_until(command_id, deadline)
        if record.result is not None:
            return record.result
        return GameCommandResult(command_id, CommandState.UNKNOWN)

    async def cancel(self, command_id: str) -> CancelResult:
        record = self._records.get(command_id)
        if record is None:
            return CancelResult(command_id, False, CommandState.UNKNOWN)
        if record.result is not None:
            return CancelResult(command_id, False, record.result.state)
        if record.wire_format == "v2-json":
            async with self._operation_lock:
                session = self._require_current()
                await session.outbound.put({
                    "type": "command", "action_id": command_id, "cancel": True,
                })
            return CancelResult(command_id, True, record.state)
        request_id = f"cancel:{uuid.uuid4().hex}"
        envelope = transport_pb2.Envelope(protocol_version="2.4.5", message_id=f"msg:{uuid.uuid4().hex}",
            request_id=request_id, session_id=self.game_session_id, timestamp_ms=int(time.time() * 1000))
        envelope.command_cancel.command_id = command_id
        record.cancel_request_ids.add(request_id)
        async with self._operation_lock:
            session = self._require_current()
            connection_id = self._connection_id
            await session.outbound.put({"type": "protocol_envelope", "encoding": "base64", "action_id": command_id,
                "payload": base64.b64encode(envelope.SerializeToString()).decode("ascii")})
        response = await self._await_control(command_id, frozenset({
            "command_accepted", "command_status_response", "command_result",
        }), connection_id, request_id)
        if response is None:
            return CancelResult(command_id, False, CommandState.UNKNOWN)
        record = self._records[command_id]
        accepted = response == "command_accepted" or record.state is CommandState.CANCELLED
        return CancelResult(command_id, accepted, record.state)

    async def get_status(self, command_id: str) -> CommandState:
        record = self._records.get(command_id)
        if record is None:
            return CommandState.UNKNOWN
        if record.state in _TERMINAL_STATES:
            return record.state
        if record.wire_format == "v2-json":
            if not self._connection_id or self._session.connection_id != self._connection_id:
                return CommandState.UNKNOWN
            return record.state
        request_id = f"status:{uuid.uuid4().hex}"
        envelope = transport_pb2.Envelope(protocol_version="2.4.5", message_id=f"msg:{uuid.uuid4().hex}",
            request_id=request_id, session_id=self.game_session_id, timestamp_ms=int(time.time() * 1000))
        envelope.command_status_request.command_id = command_id
        record.status_request_ids.add(request_id)
        async with self._operation_lock:
            session = self._require_current()
            connection_id = self._connection_id
            await session.outbound.put({"type": "protocol_envelope", "encoding": "base64", "action_id": command_id,
                "payload": base64.b64encode(envelope.SerializeToString()).decode("ascii")})
        response = await self._await_control(command_id, frozenset({
            "command_status_response", "command_result",
        }), connection_id, request_id)
        return self._records[command_id].state if response is not None else CommandState.UNKNOWN

    async def health(self) -> RouteHealth:
        return RouteHealth(bool(self._connection_id), "connected" if self._connection_id else "disconnected")


_WIRE_STATES = {state.value: state for state in CommandState}
_PROTO_STATES = {
    capability_pb2.COMMAND_STATUS_ACCEPTED: CommandState.ACCEPTED,
    capability_pb2.COMMAND_STATUS_RUNNING: CommandState.RUNNING,
    capability_pb2.COMMAND_STATUS_COMPLETED: CommandState.SUCCEEDED,
    capability_pb2.COMMAND_STATUS_REJECTED: CommandState.FAILED,
    capability_pb2.COMMAND_STATUS_FAILED: CommandState.FAILED,
    capability_pb2.COMMAND_STATUS_TIMEOUT: CommandState.TIMED_OUT,
    capability_pb2.COMMAND_STATUS_CANCELLED: CommandState.CANCELLED,
}


class HostedSessionRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.time,
                 control_timeout_s: float = 5.0, say_reflections: Any | None = None) -> None:
        self._routes: dict[str, WebSocketHostedSessionRoute] = {}
        self._owner_routes: dict[str, set[str]] = {}
        self._closed_routes: set[str] = set()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._control_timeout_s = control_timeout_s
        self._say_reflections = say_reflections

    async def attach(self, facts: HostedHelloFacts, session: Any, *, resumed: bool) -> WebSocketHostedSessionRoute:
        game_session_id = session.hosted_game_session_id
        resume_identity = session.hosted_resume_identity
        owner = session.instance.user_id
        if not owner:
            raise McpError("route_unavailable", "authenticated owner is unavailable")
        stale_sessions: list[Any] = []
        async with self._lock:
            route = self._routes.get(game_session_id)
            if resumed:
                if (route is None or route.resume_identity != resume_identity
                        or route.facts.game_instance_nonce != facts.game_instance_nonce
                        or game_session_id in self._closed_routes):
                    raise McpError("route_unavailable", "resume identity does not match hosted GameSession")
                stale_session = route._session
                route.rebind(facts, session)
                self._owner_routes.setdefault(owner, set()).add(game_session_id)
                if stale_session.connection_id != session.connection_id:
                    stale_sessions.append(stale_session)
                resumed_route = route
            else:
                resumed_route = None
            if resumed_route is not None:
                route = resumed_route
            else:
                for stale_id in tuple(self._owner_routes.get(owner, set())):
                    stale = self._routes.get(stale_id)
                    if stale is not None and stale.connection_id:
                        stale_sessions.append(stale._session)
                        stale.detach(stale.connection_id)
                    self._routes.pop(stale_id, None)
                self._owner_routes[owner] = {game_session_id}
                route = WebSocketHostedSessionRoute(
                    game_session_id, resume_identity, facts, session,
                    clock=self._clock, control_timeout_s=self._control_timeout_s,
                    say_reflections=self._say_reflections,
                )
                self._routes[game_session_id] = route
                self._closed_routes.discard(game_session_id)
        # Losing the controller route does not terminate the authenticated
        # transport. The old Mod connection remains an observer and may later
        # submit a newer owner snapshot.
        return route

    async def detach(self, game_session_id: str, connection_id: str) -> None:
        route = self._routes.get(game_session_id)
        if route is not None:
            route.detach(connection_id)

    async def tombstone(self, game_session_id: str, connection_id: str) -> None:
        """Fence a terminal GameSession before its durable CLOSED publish."""
        async with self._lock:
            route = self._routes.get(game_session_id)
            if route is not None:
                route.detach(connection_id)
            self._closed_routes.add(game_session_id)

    async def resolve_owner_retained(self, owner: str) -> WebSocketHostedSessionRoute | None:
        """Return the sole non-closed owner route, including a detached route."""
        async with self._lock:
            route_ids = tuple(
                route_id for route_id in self._owner_routes.get(owner, ())
                if route_id not in self._closed_routes
            )
            if len(route_ids) != 1:
                return None
            return self._routes.get(route_ids[0])

    async def resolve_authenticated(self, game_session_ref: str) -> WebSocketHostedSessionRoute | None:
        route = self._routes.get(game_session_ref)
        return route if route is not None and route.connection_id else None

    async def resolve_recoverable(
        self, owner: str, facts: HostedHelloFacts,
    ) -> WebSocketHostedSessionRoute | None:
        """Return the sole detached route that can safely retain its identity.

        Absence means this owner has no prior in-process Hosted identity.  Any
        existing but live, ambiguous, incomplete, or fact-mismatched route is
        an explicit fail-closed condition rather than permission to mint a new
        GameSession against an already-bound Runtime.
        """
        async with self._lock:
            route_ids = tuple(self._owner_routes.get(owner, ()))
            route_ids = tuple(route_id for route_id in route_ids if route_id not in self._closed_routes)
            if not route_ids:
                return None
            routes = tuple(self._routes.get(route_id) for route_id in route_ids)
            if any(route is None for route in routes) or len(routes) != 1:
                raise McpError("route_unavailable", "hosted recovery route is ambiguous")
            route = routes[0]
            assert route is not None
            if route.connection_id:
                raise McpError("route_unavailable", "hosted recovery route is still connected")
            if not route.game_session_id or not route.resume_identity:
                raise McpError("route_unavailable", "hosted recovery identity is incomplete")
            if (
                route.facts.game_instance_nonce != facts.game_instance_nonce
            ):
                raise McpError("route_unavailable", "hosted recovery facts do not match")
            return route

    async def capability_entries(self, game_session_ref: str) -> tuple[dict[str, str], ...] | None:
        """Return the verified Proto entries retained by the authenticated route."""
        route = await self.resolve_authenticated(game_session_ref)
        if route is None:
            return None
        return tuple(
            {"source_surface": s, "capability_id": c, "capability_contract_version": v}
            for s, c, v in route.facts.capability_entries
        )


class HostedSessionCoordinator:
    """Publishes authoritative snapshots while registry owns live sockets."""
    def __init__(self, registry: HostedSessionRegistry, publisher: GatewayEventPublisher) -> None:
        self.registry, self.publisher = registry, publisher
        self._snapshots: dict[str, GameSessionSnapshot] = {}
        self._pending_closed: dict[str, GatewayEvent] = {}

    async def on_authenticated(self, session: Any, *, resumed: bool) -> None:
        facts: HostedHelloFacts = session.hosted_facts
        game_session_id: str = session.hosted_game_session_id
        resume_identity: str = session.hosted_resume_identity
        owner = session.instance.user_id
        if not owner:
            raise McpError("route_unavailable", "authenticated owner is unavailable")
        old = self._snapshots.get(game_session_id)
        await self.registry.attach(facts, session, resumed=resumed)
        version = (old.session_version if old else 0) + 1
        if old and old.capability_digest != facts.capability_digest:
            changed = GameSessionSnapshot(game_session_id, facts.game_instance_nonce, owner,
                session.connection_id, version, facts.capability_digest, True, True, resume_identity)
            await self.publisher.publish(GatewayEvent(GatewayEventType.CAPABILITIES_CHANGED, changed))
            version += 1
        snapshot = GameSessionSnapshot(game_session_id, facts.game_instance_nonce, owner,
            session.connection_id, version, facts.capability_digest, True, True, resume_identity)
        self._snapshots[game_session_id] = snapshot
        await self.publisher.publish(GatewayEvent(GatewayEventType.RESUMED if resumed else GatewayEventType.AVAILABLE, snapshot))

    async def on_suspended(self, session: Any) -> None:
        facts: HostedHelloFacts = session.hosted_facts
        game_session_id: str = session.hosted_game_session_id
        old = self._snapshots.get(game_session_id)
        # A displaced socket may reach its finally block after its logical
        # GameSession has already been rebound.  Only the connection recorded
        # by the authoritative snapshot may make that snapshot unavailable.
        if old is not None and old.connection_id != session.connection_id:
            return
        await self.registry.detach(game_session_id, session.connection_id)
        if old is None:
            # on_authenticated was never called (e.g. activation prepare
            # failed), so no snapshot or Binding transition exists yet.
            # Synthesise a minimal SUSPENDED snapshot from the session facts
            # so the projection can converge any PENDING binding that was
            # already durably created by ensure_binding.
            owner = session.instance.user_id
            if not owner:
                return
            resume_identity: str = session.hosted_resume_identity
            snapshot = GameSessionSnapshot(
                game_session_id, facts.game_instance_nonce, owner,
                None, 1, facts.capability_digest, False, True, resume_identity,
            )
        else:
            snapshot = GameSessionSnapshot(old.game_session_id, old.game_instance_nonce, old.owner_user_id,
                None, old.session_version + 1, old.capability_digest, False, True, old.resume_identity)
        self._snapshots[game_session_id] = snapshot
        await self.publisher.publish(GatewayEvent(GatewayEventType.SUSPENDED, snapshot))

    async def on_capabilities_changed(self, session: Any, payload: Mapping[str, Any]) -> None:
        current: HostedHelloFacts = session.hosted_facts
        proposed = parse_hosted_hello({
            "route_contract_version": current.route_contract_version,
            "game_instance_nonce": current.game_instance_nonce,
            "capability_entries": payload.get("capability_entries"),
            "capability_digest": payload.get("capability_digest"),
            "player_id": current.player_id,
            "agent_control_enabled": current.agent_control_enabled,
            "agent_control_target_player_id": current.agent_control_target_player_id,
            "agent_control_revision": current.agent_control_revision,
        })
        game_session_id: str = session.hosted_game_session_id
        old = self._snapshots[game_session_id]
        if proposed.capability_digest == old.capability_digest:
            return
        route = await self.registry.resolve_authenticated(game_session_id)
        if route is None or route.connection_id != session.connection_id:
            raise McpError("route_unavailable", "stale socket cannot change capabilities")
        route.facts = proposed
        session.hosted_facts = proposed
        session.instance.meta["hosted_capability_digest"] = proposed.capability_digest
        snapshot = GameSessionSnapshot(game_session_id, old.game_instance_nonce, old.owner_user_id,
            session.connection_id, old.session_version + 1, proposed.capability_digest,
            True, True, old.resume_identity)
        self._snapshots[game_session_id] = snapshot
        await self.publisher.publish(GatewayEvent(GatewayEventType.CAPABILITIES_CHANGED, snapshot))

    async def on_closed(self, session: Any) -> None:
        game_session_id = session.hosted_game_session_id
        if not game_session_id or game_session_id not in self._snapshots:
            return
        old = self._snapshots[game_session_id]
        await self.registry.tombstone(game_session_id, session.connection_id)
        event = self._pending_closed.get(game_session_id)
        if event is None:
            snapshot = GameSessionSnapshot(game_session_id, old.game_instance_nonce, old.owner_user_id,
                None, old.session_version + 1, old.capability_digest, False, False, old.resume_identity)
            event = GatewayEvent(GatewayEventType.CLOSED, snapshot)
            self._pending_closed[game_session_id] = event
        try:
            await self.publisher.publish(event)
        except Exception as exc:
            raise ClosePublishDeferred(
                f"CLOSED publish deferred for {game_session_id}"
            ) from exc
        self._snapshots[game_session_id] = event.snapshot
        self._pending_closed.pop(game_session_id, None)

    async def retry_pending_for_owner(self, owner_user_id: str) -> bool:
        """Replay an unreachable CLOSED edge before processing a later hello."""
        pending = next((
            event for event in self._pending_closed.values()
            if event.snapshot.owner_user_id == owner_user_id
        ), None)
        if pending is None:
            return False
        await self.publisher.publish(pending)
        self._snapshots[pending.snapshot.game_session_id] = pending.snapshot
        self._pending_closed.pop(pending.snapshot.game_session_id, None)
        return True

    def get_snapshot(self, game_session_id: str) -> GameSessionSnapshot | None:
        return self._snapshots.get(game_session_id)
