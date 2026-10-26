"""Additive Gateway boundaries for Hosted Runtime; not wired to legacy sockets."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import math
import time
from typing import Any, Dict, Mapping, Optional, Protocol
from uuid import uuid4

from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION, CancelResult, CommandState, DispatchReceipt, ExecutionContext, GameCommand,
    GameCommandResult, RouteHealth, RouteStatus,
)
from modules.mcp.core.errors import McpError
from common.observability import emit_metric, normalize_error_code

GATEWAY_EVENT_CONTRACT_VERSION = "starcoplay.gateway-event.v1"
log = logging.getLogger("platform_ws.gateway.hosted_router")


class GatewayEventType(str, Enum):
    AVAILABLE = "game_session.available"
    SUSPENDED = "game_session.suspended"
    RESUMED = "game_session.resumed"
    CLOSED = "game_session.closed"
    CAPABILITIES_CHANGED = "capabilities.changed"


@dataclass(frozen=True)
class GameSessionSnapshot:
    game_session_id: str
    game_instance_nonce: str
    owner_user_id: str
    connection_id: Optional[str]
    session_version: int
    capability_digest: str
    available: bool
    resumable: bool = False
    resume_identity: Optional[str] = None


@dataclass(frozen=True)
class GatewayEvent:
    event_type: GatewayEventType
    snapshot: GameSessionSnapshot
    aggregate_id: Optional[str] = None
    aggregate_version: Optional[int] = None
    message_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trace_context: Mapping[str, str] = field(default_factory=dict)
    contract_version: str = GATEWAY_EVENT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.aggregate_id is None:
            object.__setattr__(self, "aggregate_id", self.snapshot.game_session_id)
        if self.aggregate_version is None:
            object.__setattr__(self, "aggregate_version", self.snapshot.session_version)


class GameSessionSnapshotProvider(Protocol):
    def get_snapshot(self, game_session_id: str) -> Optional[GameSessionSnapshot]: ...


class GatewayEventPublisher(Protocol):
    async def publish(self, event: GatewayEvent) -> None: ...


class AuthenticatedSessionEventHook(Protocol):
    """Optional additive lifecycle hook; legacy Session is never a GameSession identity."""
    async def on_authenticated(self, session: Any, *, resumed: bool) -> None: ...
    async def on_suspended(self, session: Any) -> None: ...


class InMemoryGameSessionFacts(GameSessionSnapshotProvider, GatewayEventPublisher):
    def __init__(self) -> None:
        self.snapshots: Dict[str, GameSessionSnapshot] = {}
        self.events: list[GatewayEvent] = []
        self._message_ids: set[str] = set()

    async def publish(self, event: GatewayEvent) -> None:
        if event.contract_version != GATEWAY_EVENT_CONTRACT_VERSION:
            raise ValueError("unsupported_contract_version")
        if not event.message_id or event.snapshot.game_session_id != event.aggregate_id:
            raise ValueError("invalid_gateway_event_envelope")
        if event.message_id in self._message_ids:
            return
        self._message_ids.add(event.message_id)
        current = self.snapshots.get(event.aggregate_id)
        if current is not None and event.aggregate_version <= current.session_version:
            return
        if current is None or event.aggregate_version > current.session_version:
            self.snapshots[event.aggregate_id] = event.snapshot
        self.events.append(event)

    def get_snapshot(self, game_session_id: str) -> Optional[GameSessionSnapshot]:
        return self.snapshots.get(game_session_id)


class BindingProjectionValidator(Protocol):
    async def validate(self, context: ExecutionContext, snapshot: GameSessionSnapshot) -> None: ...


class HostedSessionRoute(Protocol):
    async def dispatch(self, command: GameCommand) -> DispatchReceipt: ...
    async def await_result(self, command_id: str, deadline: float) -> GameCommandResult: ...
    async def cancel(self, command_id: str) -> CancelResult: ...
    async def get_status(self, command_id: str) -> CommandState: ...
    async def health(self) -> RouteHealth: ...


class AuthenticatedSessionRegistry(Protocol):
    async def resolve_authenticated(self, game_session_ref: str) -> Optional[HostedSessionRoute]: ...


class GatewayRouter:
    """Revalidates shared context, then routes an opaque shared command."""

    def __init__(self, registry: AuthenticatedSessionRegistry, snapshots: GameSessionSnapshotProvider, validator: BindingProjectionValidator) -> None:
        self.registry = registry
        self.snapshots = snapshots
        self.validator = validator

    async def _route(self, context: ExecutionContext) -> HostedSessionRoute:
        snapshot = self.snapshots.get_snapshot(context.game_session_ref)
        if snapshot is None or not snapshot.available:
            self._record_route(context, False)
            raise McpError("route_unavailable", "game session is unavailable")
        try:
            await self.validator.validate(context, snapshot)
        except Exception:
            self._record_route(context, False)
            raise
        session = await self.registry.resolve_authenticated(context.game_session_ref)
        if session is None:
            self._record_route(context, False)
            raise McpError("route_unavailable", "authenticated game route is unavailable")
        self._record_route(context, True)
        return session

    @staticmethod
    def _record_route(context: ExecutionContext, resolved: bool) -> None:
        outcome = "resolved" if resolved else "unavailable"
        event = "gateway.session.route_resolved" if resolved else "gateway.session.route_unavailable"
        level = log.info if resolved else log.warning
        level(
            "hosted game session route %s", outcome,
            extra={"event": event, "log_id": context.trace_context.get("log_id"),
                   "runtime_id": context.runtime_ref, "generation": context.generation,
                   "game_session_id": context.game_session_ref, "outcome": outcome,
                   **({} if resolved else {"error_code": "route_unavailable"})},
        )
        emit_metric(
            "gateway_session_route_total", "counter", 1, outcome=outcome,
            log_id=context.trace_context.get("log_id"),
            runtime_id=context.runtime_ref, generation=context.generation,
            game_session_id=context.game_session_ref,
            **({} if resolved else {"error_code": "route_unavailable"}),
        )

    async def resolve(self, context: ExecutionContext) -> RouteStatus:
        await self._route(context)
        return RouteStatus(True, context.game_session_ref)

    async def dispatch(self, context: ExecutionContext, command: GameCommand) -> DispatchReceipt:
        started = time.monotonic()
        if (
            command.contract_version != GAME_COMMAND_VERSION
            or not all((command.message_id, command.idempotency_key, command.command_id,
                        command.tool_call_id, command.capability_id, command.capability_version))
            or not math.isfinite(command.occurred_at) or not math.isfinite(command.deadline_at)
            or command.occurred_at > command.deadline_at
            or command.game_instance_nonce != context.game_instance_nonce
            or command.capability_digest != context.capability_digest
            or (command.operation.value == "mutating" and command.lease_epoch != context.lease_epoch)
            or (command.operation.value == "read_only" and command.lease_epoch is not None)
        ):
            raise McpError("upstream_protocol_error", "GameCommand contract or fencing is invalid")
        try:
            result = await (await self._route(context)).dispatch(command)
        except Exception as error:
            code = normalize_error_code(getattr(error, "code", None), fallback="internal_error")
            duration_ms = (time.monotonic() - started) * 1000
            log.warning(
                "hosted command dispatch failed",
                extra={"event": "gateway.command.dispatch_failed",
                       "log_id": context.trace_context.get("log_id"),
                       "runtime_id": context.runtime_ref, "generation": context.generation,
                       "game_session_id": context.game_session_ref,
                       "tool_call_id": command.tool_call_id, "command_id": command.command_id,
                       "outcome": "failed", "error_code": code,
                       "exception_type": type(error).__name__, "duration_ms": duration_ms},
            )
            emit_metric(
                "gateway_command_total", "counter", 1, outcome="failed", stage="dispatch",
                command_id=command.command_id, tool_call_id=command.tool_call_id,
                error_code=code, runtime_id=context.runtime_ref, generation=context.generation,
            )
            emit_metric(
                "gateway_command_duration_ms", "timing", duration_ms,
                outcome="failed", stage="dispatch", command_id=command.command_id,
                tool_call_id=command.tool_call_id, error_code=code,
                runtime_id=context.runtime_ref, generation=context.generation,
            )
            raise
        if not isinstance(result, DispatchReceipt) or result.command_id != command.command_id:
            raise McpError("upstream_protocol_error", "dispatch receipt correlation failed")
        log.info(
            "hosted command dispatch state=%s", result.state.value,
            extra={
                "event": "gateway.command.dispatch",
                "log_id": context.trace_context.get("log_id"),
                "runtime_id": context.runtime_ref,
                "generation": context.generation,
                "game_session_id": context.game_session_ref,
                "tool_call_id": command.tool_call_id,
                "command_id": command.command_id,
                "outcome": result.state.value.lower(),
            },
        )
        emit_metric(
            "gateway_command_total", "counter", 1, outcome=result.state.value.lower(),
            stage="dispatch", command_id=command.command_id, tool_call_id=command.tool_call_id,
            runtime_id=context.runtime_ref, generation=context.generation,
        )
        emit_metric(
            "gateway_command_duration_ms", "timing", (time.monotonic() - started) * 1000,
            outcome=result.state.value.lower(), stage="dispatch", command_id=command.command_id,
            tool_call_id=command.tool_call_id, runtime_id=context.runtime_ref,
            generation=context.generation,
        )
        return result

    async def await_result(self, context: ExecutionContext, command_id: str, deadline: float) -> GameCommandResult:
        started = time.monotonic()
        try:
            result = await (await self._route(context)).await_result(command_id, deadline)
        except Exception as error:
            code = normalize_error_code(getattr(error, "code", None), fallback="internal_error")
            duration_ms = (time.monotonic() - started) * 1000
            log.warning(
                "hosted command result failed",
                extra={"event": "gateway.command.result_failed",
                       "log_id": context.trace_context.get("log_id"),
                       "runtime_id": context.runtime_ref, "generation": context.generation,
                       "game_session_id": context.game_session_ref, "command_id": command_id,
                       "outcome": "failed", "error_code": code,
                       "exception_type": type(error).__name__, "duration_ms": duration_ms},
            )
            emit_metric(
                "gateway_command_total", "counter", 1, outcome="failed", stage="result",
                command_id=command_id, runtime_id=context.runtime_ref, generation=context.generation,
                error_code=code,
            )
            emit_metric(
                "gateway_command_duration_ms", "timing", duration_ms,
                outcome="failed", stage="result", command_id=command_id,
                runtime_id=context.runtime_ref, generation=context.generation,
                error_code=code,
            )
            raise
        if not isinstance(result, GameCommandResult) or result.command_id != command_id:
            raise McpError("upstream_protocol_error", "game result correlation failed")
        level = logging.WARNING if result.state in {
            CommandState.TIMED_OUT, CommandState.UNKNOWN, CommandState.EXPIRED_UNKNOWN,
        } else logging.INFO
        stable_result_code = normalize_error_code(result.error_code) if result.error_code else ""
        log.log(
            level, "hosted command result state=%s", result.state.value,
            extra={
                "event": "gateway.command.result",
                "log_id": context.trace_context.get("log_id"),
                "runtime_id": context.runtime_ref,
                "generation": context.generation,
                "game_session_id": context.game_session_ref,
                "command_id": command_id,
                "outcome": result.state.value.lower(),
                "error_code": stable_result_code,
            },
        )
        emit_metric(
            "gateway_command_total", "counter", 1, outcome="completed", stage="result",
            result_state=result.state.value.lower(), command_id=command_id,
            runtime_id=context.runtime_ref, generation=context.generation,
            error_code=stable_result_code,
        )
        emit_metric(
            "gateway_command_duration_ms", "timing", (time.monotonic() - started) * 1000,
            outcome="completed", stage="result", result_state=result.state.value.lower(),
            command_id=command_id, runtime_id=context.runtime_ref, generation=context.generation,
            error_code=stable_result_code,
        )
        return result

    async def cancel(self, context: ExecutionContext, command_id: str) -> CancelResult:
        result = await (await self._route(context)).cancel(command_id)
        if not isinstance(result, CancelResult) or result.command_id != command_id:
            raise McpError("upstream_protocol_error", "cancel result correlation failed")
        log.info(
            "hosted command cancel accepted=%s state=%s", result.accepted, result.state.value,
            extra={
                "event": "gateway.command.cancel",
                "log_id": context.trace_context.get("log_id"),
                "runtime_id": context.runtime_ref,
                "generation": context.generation,
                "game_session_id": context.game_session_ref,
                "command_id": command_id,
                "outcome": "accepted" if result.accepted else "not_accepted",
            },
        )
        emit_metric(
            "gateway_command_total", "counter", 1,
            outcome="accepted" if result.accepted else "rejected", stage="cancel",
            result_state=result.state.value.lower(), command_id=command_id,
            runtime_id=context.runtime_ref, generation=context.generation,
        )
        return result

    async def get_status(self, context: ExecutionContext, command_id: str) -> CommandState:
        result = await (await self._route(context)).get_status(command_id)
        if not isinstance(result, CommandState):
            raise McpError("upstream_protocol_error", "command status shape is invalid")
        return result

    async def health(self, context: ExecutionContext) -> RouteHealth:
        result = await (await self._route(context)).health()
        if not isinstance(result, RouteHealth):
            raise McpError("upstream_protocol_error", "route health shape is invalid")
        return result
