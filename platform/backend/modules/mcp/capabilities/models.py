"""Frozen M3.2 Capability Runtime contracts — no transport, MCP, or Gateway types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from modules.mcp.contracts.models import (
    CancelResult,
    CommandState,
    DispatchReceipt,
    ExecutionContext,
    GameCommandResult,
    SideEffect,
)


# ---------------------------------------------------------------------------
# Protected authority fields — identical to McpCore._PROTECTED_ARGUMENTS.
# McpCore keeps its own copy until M3.5; this is the authoritative constant
# for the new Capability Runtime.
# ---------------------------------------------------------------------------
PROTECTED_ARGUMENTS: frozenset[str] = frozenset({
    "profile", "principal_id", "runtime_ref", "runtime_id", "generation",
    "binding_ref", "binding_id", "binding_version", "game_session_ref",
    "game_session_id", "game_instance_nonce", "lease_epoch", "capability_digest",
    "execution_context_ref", "user_id", "agentee_id",
})


# ===================================================================
# Entry / Diagnostic
# ===================================================================

@dataclass(frozen=True)
class ProtocolCapabilityEntry:
    source_surface: str
    capability_id: str
    capability_contract_version: str


@dataclass(frozen=True)
class CapabilityDiagnostic:
    code: str
    capability_id: str
    advertised_version: str | None
    detail: str


# ===================================================================
# Error codes & runtime exception
# ===================================================================

class CapabilityErrorCode(str, Enum):
    INVALID_ARGUMENTS = "invalid_arguments"
    UNAVAILABLE = "unavailable"
    VERSION_UNSUPPORTED = "version_unsupported"
    SNAPSHOT_CONFLICT = "snapshot_conflict"
    SCOPE_DENIED = "scope_denied"
    INTERNAL_CONTROL_UNAVAILABLE = "internal_control_unavailable"
    CONTEXT_STALE = "context_stale"
    BINDING_UNAVAILABLE = "binding_unavailable"
    ROUTE_UNAVAILABLE = "route_unavailable"
    DISPATCH_CONFLICT = "dispatch_conflict"
    UPSTREAM_CONTRACT_VIOLATION = "upstream_contract_violation"
    INTERNAL = "internal"


class CapabilityRuntimeError(Exception):
    """Neutral runtime error — never exposes provider/router exception text."""

    def __init__(self, code: CapabilityErrorCode, safe_message: str, retryable: bool) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


# ===================================================================
# Audit / Activity ports
# ===================================================================

class CapabilityAuditSink(Protocol):
    def emit(self, event: Any) -> None: ...


class CapabilityActivityReporter(Protocol):
    async def report_succeeded(self, runtime_ref: str, generation: int) -> None: ...


# ===================================================================
# Adapter
# ===================================================================

@dataclass(frozen=True)
class PreparedCapabilityCall:
    capability_id: str
    contract_version: str
    validated_arguments: Mapping[str, Any]


class CapabilityAdapter(Protocol):
    def prepare(self, arguments: Mapping[str, Any], command_id: str) -> PreparedCapabilityCall: ...
    def normalize(self, handle: Any, result: GameCommandResult) -> GameCommandResult: ...


# ===================================================================
# Contract & Snapshot
# ===================================================================

@dataclass(frozen=True)
class CapabilityContract:
    capability_id: str
    contract_version: str
    source_surface: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    required_scope: str
    side_effect: SideEffect
    timeout_seconds: float
    cancellable: bool
    skill_eligible: bool
    internal_only: bool
    adapter: CapabilityAdapter | None


@dataclass(frozen=True)
class CapabilitySnapshot:
    raw_entries: tuple[ProtocolCapabilityEntry, ...]
    known_contracts: tuple[CapabilityContract, ...]
    diagnostics: tuple[CapabilityDiagnostic, ...]
    capability_digest: str


@dataclass(frozen=True)
class CapabilityRef:
    capability_id: str
    contract_version: str


@dataclass(frozen=True)
class CapabilityInvocationHandle:
    command_id: str
    tool_call_id: str
    deadline_at: float
    dispatch_receipt: DispatchReceipt
    capability_ref: CapabilityRef
    side_effect: SideEffect
    validated_arguments: Mapping[str, Any]


# ===================================================================
# Manifest provider (session-bound)
# ===================================================================

class CapabilityManifestProvider(Protocol):
    async def entries(self, context: ExecutionContext) -> Sequence[ProtocolCapabilityEntry | Mapping[str, str]]: ...


# ===================================================================
# Contract catalog
# ===================================================================

class CapabilityContractCatalog:
    """Immutable catalog; rejects duplicate (capability_id, contract_version)."""

    def __init__(self, contracts: Sequence[CapabilityContract]) -> None:
        seen: set[tuple[str, str]] = set()
        for c in contracts:
            key = (c.capability_id, c.contract_version)
            if key in seen:
                raise ValueError(
                    f"duplicate contract: {c.capability_id} v{c.contract_version}"
                )
            seen.add(key)
        self.contracts: tuple[CapabilityContract, ...] = tuple(contracts)
        self._by_id: dict[str, CapabilityContract] = {
            c.capability_id: c for c in self.contracts
        }

    def find_exact(self, capability_id: str, version: str) -> CapabilityContract | None:
        c = self._by_id.get(capability_id)
        if c is not None and c.contract_version == version:
            return c
        return None

    def has_capability(self, capability_id: str) -> bool:
        return capability_id in self._by_id


# ===================================================================
# Runtime protocol
# ===================================================================

class CapabilityRuntime(Protocol):
    async def snapshot(self, context: ExecutionContext) -> CapabilitySnapshot: ...
    async def start(
        self, context: ExecutionContext, capability_ref: CapabilityRef,
        arguments: Mapping[str, Any], *,
        tool_call_id: str, command_id: str | None = None,
        now: float | None = None, deadline: float | None = None,
    ) -> CapabilityInvocationHandle: ...
    async def await_result(
        self, context: ExecutionContext, handle: CapabilityInvocationHandle, *,
        deadline: float | None = None,
    ) -> GameCommandResult: ...
    async def invoke(
        self, context: ExecutionContext, capability_ref: CapabilityRef,
        arguments: Mapping[str, Any], *,
        tool_call_id: str, command_id: str | None = None,
        now: float | None = None, deadline: float | None = None,
    ) -> GameCommandResult: ...
    async def cancel(
        self, context: ExecutionContext, handle: CapabilityInvocationHandle, *,
        reason: str,
    ) -> CancelResult: ...
    async def get_status(
        self, context: ExecutionContext, command_id: str,
    ) -> CommandState: ...
