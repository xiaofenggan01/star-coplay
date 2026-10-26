"""Loopback-only Local Direct router with command and owner fencing."""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION, CancelResult, CommandState, DispatchReceipt, DispatchState, ExecutionContext,
    GameCommand, GameCommandResult, RouteHealth, RouteStatus, SideEffect,
)
from modules.mcp.context import EphemeralSecret
from modules.mcp.core.errors import McpError


class ModConnection(Protocol):
    @property
    def connected(self) -> bool: ...
    async def dispatch(self, command: GameCommand) -> None: ...
    async def result(self, command_id: str, deadline: float) -> GameCommandResult: ...
    async def status(self, command_id: str) -> CommandState: ...
    async def cancel(self, command_id: str) -> CancelResult: ...
    async def health(self) -> RouteHealth: ...


@dataclass(frozen=True)
class LocalRouteFence:
    context_ref: str
    principal_ref: str
    runtime_ref: str
    game_session_ref: str
    game_instance_nonce: str
    generation: int
    binding_ref: str
    binding_version: int
    lease_epoch: int
    capability_digest: str


class LocalModRouter:
    def __init__(
        self,
        fence: LocalRouteFence,
        connection: ModConnection,
        *,
        boot_secret: EphemeralSecret,
        presented_secret: str,
        clock: Callable[[], float] = time.time,
    ):
        if not boot_secret.matches(presented_secret):
            raise McpError("capability_denied", "local Mod binding credential is invalid")
        self._fence = fence
        self._connection = connection
        self._clock = clock
        self._commands: dict[tuple[str, str, str], GameCommand] = {}
        self._domains: dict[tuple[str, str, str], tuple[object, ...]] = {}
        self._dispatch_lock = asyncio.Lock()
        self._invalidated = False

    @staticmethod
    def _key(context: ExecutionContext, command_id: str) -> tuple[str, str, str]:
        return context.game_session_ref, context.game_instance_nonce, command_id

    def _validate_context(self, context: ExecutionContext) -> None:
        f = self._fence
        if self._invalidated:
            raise McpError("context_expired", "local boot has been invalidated")
        if context.profile != "local_direct":
            raise McpError("binding_unavailable", "context belongs to another profile")
        if context.principal_id != f.principal_ref or context.runtime_ref != f.runtime_ref:
            raise McpError("conflict", "context does not own this local boot")
        if context.game_session_ref != f.game_session_ref or context.binding_ref != f.binding_ref:
            raise McpError("binding_unavailable", "local binding is unavailable")
        if (
            context.game_instance_nonce != f.game_instance_nonce
            or context.generation != f.generation
            or context.binding_version != f.binding_version
            or context.capability_digest != f.capability_digest
        ):
            raise McpError("binding_unavailable", "local binding snapshot is stale")
        if context.lease_epoch != f.lease_epoch:
            raise McpError("conflict", "local lease is stale")

    def _validate_command(self, context: ExecutionContext, command: GameCommand) -> None:
        if command.contract_version != GAME_COMMAND_VERSION:
            raise McpError("upstream_protocol_error", "unsupported GameCommand contract")
        if not all((command.message_id, command.idempotency_key, command.command_id,
                    command.tool_call_id, command.capability_id, command.capability_version)):
            raise McpError("upstream_protocol_error", "GameCommand identity fields are incomplete")
        if not isinstance(command.operation, SideEffect) or not isinstance(command.validated_payload, Mapping):
            raise McpError("upstream_protocol_error", "GameCommand shape is invalid")
        if (not math.isfinite(command.occurred_at) or not math.isfinite(command.deadline_at)
                or command.occurred_at > command.deadline_at):
            raise McpError("upstream_protocol_error", "GameCommand deadline precedes occurrence")
        if command.execution_context_ref != self._fence.context_ref:
            raise McpError("upstream_protocol_error", "GameCommand context correlation failed")
        if command.game_instance_nonce != context.game_instance_nonce or command.capability_digest != context.capability_digest:
            raise McpError("upstream_protocol_error", "command fencing fields are inconsistent")
        if command.operation is SideEffect.READ_ONLY and command.lease_epoch is not None:
            raise McpError("upstream_protocol_error", "read-only command must not carry a lease")
        if command.operation is SideEffect.MUTATING and command.lease_epoch != context.lease_epoch:
            raise McpError("conflict", "mutating lease is stale")

    async def resolve(self, context: ExecutionContext) -> RouteStatus:
        self._validate_context(context)
        return RouteStatus(self._connection.connected, context.game_session_ref)

    async def dispatch(self, context: ExecutionContext, command: GameCommand) -> DispatchReceipt:
        self._validate_context(context)
        self._validate_command(context, command)
        if not self._connection.connected:
            raise McpError("route_unavailable", "local Mod is not running")
        domain = (
            context.game_session_ref, context.game_instance_nonce, context.generation,
            context.binding_ref, context.binding_version, context.lease_epoch,
            context.capability_digest, command.tool_call_id, command.capability_id,
            command.capability_version, command.operation.value,
            json.dumps(command.validated_payload, sort_keys=True, separators=(",", ":")),
            command.idempotency_key,
        )
        async with self._dispatch_lock:
            if command.operation is SideEffect.MUTATING:
                if command.lease_epoch != context.lease_epoch or context.lease_epoch != self._fence.lease_epoch:
                    raise McpError("conflict", "mutating lease is stale")
            key = self._key(context, command.command_id)
            if key in self._commands:
                if self._domains[key] == domain:
                    return DispatchReceipt(command.command_id, DispatchState.DUPLICATE)
                return DispatchReceipt(command.command_id, DispatchState.REJECTED, "idempotency collision")
            # Check at the actual downstream-write boundary, after any time
            # spent waiting for the duplicate/collision lock.
            if command.deadline_at <= self._clock():
                raise McpError("conflict", "command deadline has expired")
            # Identity ownership is frozen in LocalRouteFence.  This lock only
            # makes duplicate/collision recording atomic across concurrent
            # calls from that already-validated owner.
            try:
                await self._connection.dispatch(command)
            except McpError:
                raise
            except Exception as exc:
                raise McpError("internal_error", "local Mod dispatch failed") from exc
            self._commands[key] = command
            self._domains[key] = domain
            return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)

    async def await_result(self, context: ExecutionContext, command_id: str, deadline: float) -> GameCommandResult:
        self._validate_context(context)
        if not self._connection.connected:
            return GameCommandResult(command_id, CommandState.UNKNOWN, error_code="route_unavailable")
        if not math.isfinite(deadline):
            raise McpError("upstream_protocol_error", "result deadline is invalid")
        remaining = deadline - self._clock()
        if remaining <= 0:
            return GameCommandResult(command_id, CommandState.UNKNOWN, error_code="command_timeout")
        try:
            result = await asyncio.wait_for(self._connection.result(command_id, deadline), timeout=remaining)
        except (asyncio.TimeoutError, TimeoutError):
            # A transport deadline never proves that a mutating command failed.
            return GameCommandResult(command_id, CommandState.UNKNOWN, error_code="command_timeout")
        except McpError:
            raise
        except Exception as exc:
            raise McpError("internal_error", "local Mod result failed") from exc
        if (not isinstance(result, GameCommandResult)
                or not isinstance(result.state, CommandState)
                or result.command_id != command_id):
            raise McpError("upstream_protocol_error", "local Mod result correlation failed")
        return result

    async def cancel(self, context: ExecutionContext, command_id: str) -> CancelResult:
        self._validate_context(context)
        if not self._connection.connected:
            return CancelResult(command_id, False, CommandState.UNKNOWN)
        try:
            result = await self._connection.cancel(command_id)
        except McpError:
            raise
        except Exception as exc:
            raise McpError("internal_error", "local Mod cancellation failed") from exc
        if (not isinstance(result, CancelResult)
                or not isinstance(result.state, CommandState)
                or result.command_id != command_id):
            raise McpError("upstream_protocol_error", "local Mod cancellation correlation failed")
        return result

    async def get_status(self, context: ExecutionContext, command_id: str) -> CommandState:
        self._validate_context(context)
        if not self._connection.connected:
            return CommandState.UNKNOWN
        try:
            state = await self._connection.status(command_id)
        except (asyncio.TimeoutError, TimeoutError):
            return CommandState.UNKNOWN
        except McpError:
            raise
        except Exception as exc:
            raise McpError("internal_error", "local Mod status failed") from exc
        if not isinstance(state, CommandState):
            raise McpError("upstream_protocol_error", "local Mod status shape is invalid")
        return state

    async def health(self, context: ExecutionContext) -> RouteHealth:
        self._validate_context(context)
        if not self._connection.connected:
            return RouteHealth(False, "local Mod is not running")
        try:
            result = await self._connection.health()
        except McpError:
            raise
        except Exception as exc:
            raise McpError("internal_error", "local Mod health check failed") from exc
        if not isinstance(result, RouteHealth):
            raise McpError("upstream_protocol_error", "local Mod health shape is invalid")
        return result

    def invalidate_boot(self) -> None:
        """Launcher shutdown hook; old router references fail closed."""
        self._invalidated = True
