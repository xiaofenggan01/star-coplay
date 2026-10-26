"""M3.4 Skill Runtime neutral models — dataclasses, enums, exceptions, protocols.

No transport, MCP, Router, or composition-root types.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Protocol

from modules.mcp.skills.models import _deep_freeze, SkillRef


# ===================================================================
# Enums
# ===================================================================


class SkillExecutionState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


class SkillRuntimeErrorCode(str, Enum):
    INVALID_ARGUMENTS = "invalid_arguments"
    OUTPUT_CONTRACT_VIOLATION = "output_contract_violation"
    SKILL_UNAVAILABLE = "skill_unavailable"
    EXECUTION_CONTEXT_MISMATCH = "execution_context_mismatch"
    AUTHORIZATION_STALE = "authorization_stale"
    CONFIRMATION_REQUIRED = "confirmation_required"
    IMPLEMENTATION_NOT_LOADED = "implementation_not_loaded"
    IMPLEMENTATION_ERROR = "implementation_error"
    UNDECLARED_FAILURE_CODE = "undeclared_failure_code"
    CAPABILITY_FAILED = "capability_failed"
    CAPABILITY_UNKNOWN = "capability_unknown"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    NOT_CANCELLABLE = "not_cancellable"
    EXECUTION_NOT_FOUND = "execution_not_found"
    EXECUTION_CONFLICT = "execution_conflict"
    INTERNAL = "internal"


# ===================================================================
# SkillRuntimeError
# ===================================================================

_RETRYABLE_CODES: frozenset[SkillRuntimeErrorCode] = frozenset({
    SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED,
    SkillRuntimeErrorCode.CAPABILITY_FAILED,
    SkillRuntimeErrorCode.TIMEOUT,
    SkillRuntimeErrorCode.INTERNAL,
})


class SkillRuntimeError(Exception):
    """Public neutral runtime error — never exposes exception text, traceback,
    source path, arguments or output.

    ``safe_message`` is always exactly ``code.value``.  ``retryable`` is
    ``True`` only for IMPLEMENTATION_NOT_LOADED, CAPABILITY_FAILED, TIMEOUT
    and INTERNAL.
    """

    def __init__(self, code: SkillRuntimeErrorCode) -> None:
        super().__init__(code.value)
        self.code: SkillRuntimeErrorCode = code
        self.safe_message: str = code.value
        self.retryable: bool = code in _RETRYABLE_CODES


# ===================================================================
# SkillFailure — public, implementation-facing
# ===================================================================


class SkillFailure(Exception):
    """Public exception raised by Skill implementations.

    ``code`` must be declared in ``manifest.failure_codes``.  Arbitrary
    exception text is never copied into the public surface.
    """

    def __init__(self, code: str, partial_output: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code: str = code
        self.partial_output: Mapping[str, Any] | None = partial_output


# ===================================================================
# Internal freeze helper
# ===================================================================


def _freeze_dc(instance: object, skip: frozenset[str] = frozenset()) -> None:
    """Apply ``_deep_freeze`` to every field, skipping named fields."""
    for f in fields(instance):
        if f.name in skip:
            continue
        value = getattr(instance, f.name)
        frozen_value = _deep_freeze(value)
        if frozen_value is not value:
            object.__setattr__(instance, f.name, frozen_value)


# ===================================================================
# Frozen dataclasses
# ===================================================================


@dataclass(frozen=True)
class SkillExecutionHandle:
    skill_execution_id: str
    skill_ref: SkillRef
    tool_call_id: str
    deadline_at: float

    def __post_init__(self) -> None:
        _freeze_dc(self)


@dataclass(frozen=True)
class SkillExecutionAuthorization:
    skill_ref: SkillRef
    manifest_digest: str
    implementation_digest: str
    mount_revision: str
    policy_revision: str
    capability_digest: str
    binding_ref: str
    binding_version: int
    game_session_ref: str
    effective_policy: Any  # EffectiveSkillPolicy — late-bound to avoid circular import
    dependency_bindings: tuple[Any, ...]  # tuple[DependencyBinding, ...]

    def __post_init__(self) -> None:
        _freeze_dc(self, skip=frozenset({"binding_version"}))


@dataclass(frozen=True)
class SkillResult:
    skill_execution_id: str
    skill_ref: SkillRef
    state: SkillExecutionState
    output: Mapping[str, Any]
    error_code: str | None
    partial_output: Mapping[str, Any]
    capability_command_ids: tuple[str, ...]
    side_effects_may_have_occurred: bool

    def __post_init__(self) -> None:
        _freeze_dc(self)


@dataclass(frozen=True)
class SkillCancelResult:
    skill_execution_id: str
    accepted: bool
    state: SkillExecutionState
    error_code: str | None

    def __post_init__(self) -> None:
        _freeze_dc(self)


@dataclass(frozen=True)
class SkillAuditEvent:
    event: str
    request_id: str | None
    tool_call_id: str
    skill_execution_id: str
    skill_id: str
    skill_version: str
    manifest_digest: str
    implementation_digest: str
    command_id: str | None
    runtime_ref: str
    binding_ref: str
    binding_version: int
    game_session_ref: str
    capability_digest: str
    detail: Mapping[str, Any]
    log_id: str | None = None

    def __post_init__(self) -> None:
        _freeze_dc(self, skip=frozenset({"binding_version"}))


# ===================================================================
# SkillAuditSink — Protocol
# ===================================================================


class SkillAuditSink(Protocol):
    def emit(self, event: SkillAuditEvent) -> None: ...
