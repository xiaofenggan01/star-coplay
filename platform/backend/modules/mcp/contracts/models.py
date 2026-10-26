"""Stable, provider-neutral MCP boundary contracts.

These DTOs deliberately contain no transport, Gateway registry, Runtime Manager,
token implementation, or provider SDK types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol

GAME_COMMAND_VERSION = "starcoplay.game-command.v1"


class SideEffect(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"


class DispatchState(str, Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class CommandState(str, Enum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"
    EXPIRED_UNKNOWN = "EXPIRED_UNKNOWN"


@dataclass(frozen=True)
class ExecutionContext:
    profile: str
    principal_id: str
    runtime_ref: str
    generation: int
    binding_ref: str
    binding_version: int
    game_session_ref: str
    game_instance_nonce: str
    lease_epoch: int
    scopes: frozenset[str]
    allowed_tools: frozenset[str]
    capability_digest: str
    issued_at: float
    expires_at: float | None = None
    trace_context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextHandle:
    value: str


@dataclass(frozen=True)
class ContextStatus:
    valid: bool
    reason: str | None = None


@dataclass(frozen=True)
class GameCommand:
    contract_version: str
    message_id: str
    occurred_at: float
    idempotency_key: str
    command_id: str
    tool_call_id: str
    capability_id: str
    capability_version: str
    capability_digest: str
    operation: SideEffect
    validated_payload: Mapping[str, Any]
    deadline_at: float
    execution_context_ref: str
    game_instance_nonce: str
    lease_epoch: int | None
    trace_context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchReceipt:
    command_id: str
    state: DispatchState
    detail: str | None = None


@dataclass(frozen=True)
class GameCommandResult:
    command_id: str
    state: CommandState
    output: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None


@dataclass(frozen=True)
class CancelResult:
    command_id: str
    accepted: bool
    state: CommandState


@dataclass(frozen=True)
class RouteStatus:
    available: bool
    game_session_ref: str


@dataclass(frozen=True)
class RouteHealth:
    healthy: bool
    detail: str = ""


class ContextAuthority(Protocol):
    async def establish(self, connection_metadata: Mapping[str, str]) -> ContextHandle: ...
    async def authorize(self, handle: ContextHandle, tool_name: str, capability: str) -> ExecutionContext: ...
    async def revalidate(self, handle: ContextHandle) -> ContextStatus: ...
    async def close(self, handle: ContextHandle) -> None: ...


class GameSessionRouter(Protocol):
    async def resolve(self, context: ExecutionContext) -> RouteStatus: ...
    async def dispatch(self, context: ExecutionContext, command: GameCommand) -> DispatchReceipt: ...
    async def await_result(self, context: ExecutionContext, command_id: str, deadline: float) -> GameCommandResult: ...
    async def cancel(self, context: ExecutionContext, command_id: str) -> CancelResult: ...
    async def get_status(self, context: ExecutionContext, command_id: str) -> CommandState: ...
    async def health(self, context: ExecutionContext) -> RouteHealth: ...
