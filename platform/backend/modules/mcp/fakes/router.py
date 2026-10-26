from __future__ import annotations

from dataclasses import dataclass
import json

from modules.mcp.contracts.models import (
    CancelResult, CommandState, DispatchReceipt, DispatchState, ExecutionContext,
    GameCommand, GameCommandResult, RouteHealth, RouteStatus, SideEffect,
)
from modules.mcp.core.errors import McpError


@dataclass(frozen=True)
class RouteFence:
    game_session_ref: str
    game_instance_nonce: str
    generation: int
    binding_version: int
    lease_epoch: int
    capability_digest: str
    principal_id: str | None = None
    runtime_ref: str | None = None
    binding_ref: str | None = None


class FakeGameSessionRouter:
    def __init__(self, fence: RouteFence):
        self.fence = fence
        self.commands: dict[tuple[str, str, str], GameCommand] = {}
        self._domains: dict[tuple[str, str, str], tuple[object, ...]] = {}
        self.results: dict[tuple[str, str, str], GameCommandResult] = {}
        self.dispatch_count = 0

    @staticmethod
    def _key(context: ExecutionContext, command_id: str) -> tuple[str, str, str]:
        return context.game_session_ref, context.game_instance_nonce, command_id

    def _check(self, context: ExecutionContext) -> None:
        if (
            (self.fence.principal_id is not None and context.principal_id != self.fence.principal_id)
            or (self.fence.runtime_ref is not None and context.runtime_ref != self.fence.runtime_ref)
            or (self.fence.binding_ref is not None and context.binding_ref != self.fence.binding_ref)
        ):
            raise McpError("conflict", "context does not own this route")
        if context.game_session_ref != self.fence.game_session_ref:
            raise McpError("binding_unavailable", "binding is unavailable")
        if (context.game_instance_nonce != self.fence.game_instance_nonce
                or context.generation != self.fence.generation
                or context.binding_version != self.fence.binding_version
                or context.lease_epoch != self.fence.lease_epoch
                or context.capability_digest != self.fence.capability_digest):
            raise McpError("binding_unavailable", "binding snapshot is stale")

    async def resolve(self, context: ExecutionContext) -> RouteStatus:
        self._check(context)
        return RouteStatus(True, context.game_session_ref)

    async def dispatch(self, context: ExecutionContext, command: GameCommand) -> DispatchReceipt:
        self._check(context)
        if command.game_instance_nonce != context.game_instance_nonce or command.capability_digest != context.capability_digest:
            raise McpError("upstream_protocol_error", "command fencing fields are inconsistent")
        if command.operation is SideEffect.MUTATING and (
            command.lease_epoch != context.lease_epoch or context.lease_epoch != self.fence.lease_epoch
        ):
            raise McpError("conflict", "mutating lease is stale")
        domain = (
            context.game_session_ref, context.game_instance_nonce, context.generation,
            context.binding_ref, context.binding_version, context.lease_epoch,
            context.capability_digest, command.tool_call_id, command.capability_id,
            command.capability_version, command.operation.value,
            json.dumps(command.validated_payload, sort_keys=True, separators=(",", ":")),
            command.idempotency_key,
        )
        key = self._key(context, command.command_id)
        if key in self.commands:
            if self._domains[key] == domain:
                return DispatchReceipt(command.command_id, DispatchState.DUPLICATE)
            return DispatchReceipt(command.command_id, DispatchState.REJECTED, "idempotency collision")
        self.commands[key] = command
        self._domains[key] = domain
        self.dispatch_count += 1
        self.results.setdefault(key, GameCommandResult(
            command.command_id, CommandState.SUCCEEDED, {"capability": command.capability_id}
        ))
        return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)

    async def await_result(self, context: ExecutionContext, command_id: str, deadline: float) -> GameCommandResult:
        self._check(context)
        result = self.results.get(self._key(context, command_id), GameCommandResult(command_id, CommandState.UNKNOWN))
        if result.command_id != command_id:
            raise McpError("upstream_protocol_error", "fake result correlation failed")
        return result

    async def cancel(self, context: ExecutionContext, command_id: str) -> CancelResult:
        self._check(context)
        key = self._key(context, command_id)
        current = self.results.get(key)
        if current and current.state in {CommandState.SUCCEEDED, CommandState.FAILED, CommandState.CANCELLED}:
            return CancelResult(command_id, False, current.state)
        self.results[key] = GameCommandResult(command_id, CommandState.CANCELLED)
        return CancelResult(command_id, True, CommandState.CANCELLED)

    async def get_status(self, context: ExecutionContext, command_id: str) -> CommandState:
        self._check(context)
        return self.results.get(
            self._key(context, command_id), GameCommandResult(command_id, CommandState.UNKNOWN)
        ).state

    async def health(self, context: ExecutionContext) -> RouteHealth:
        self._check(context)
        return RouteHealth(True)
