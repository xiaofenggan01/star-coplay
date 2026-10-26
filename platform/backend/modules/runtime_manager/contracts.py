"""Provider-neutral data contracts for the AgentRuntimeAdapter spike.

Standard-library only. Nothing here may reference Claude CLI concepts
(``claude_session_id``) or Gateway concepts (``agent_instance_id``,
``AgentInstance``, ``Session``). The stable identity for a runtime process is
the ``(runtime_id, generation)`` pair.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Union

JSONValue = Union[None, bool, int, float, str, List["JSONValue"], Dict[str, "JSONValue"]]


class RuntimeManagerError(Exception):
    """Base error for the runtime_manager package."""


class ReadyTimeoutError(RuntimeManagerError):
    """Raised when a runtime process does not emit a matching ready frame in time."""


class RuntimeStoppingError(RuntimeManagerError):
    """Raised when an operation is attempted after the runtime entered STOPPING."""


class ProtocolViolationError(RuntimeManagerError):
    """Raised when a runtime process response cannot be parsed as a valid frame."""


class WorkdirConflictError(RuntimeManagerError):
    """Raised when prepare() would reuse or escape a runtime's isolated workdir."""


class RuntimeConflictError(RuntimeManagerError):
    """Raised when an active runtime identity is reused with a conflicting spec."""


class ReadinessFailureKind(str, Enum):
    """Provider-neutral reason a runtime failed its readiness handshake.

    This is deliberately a small, safe taxonomy. Provider response bodies,
    credentials, and provider-specific status fields must stay behind the
    adapter boundary.
    """

    TIMEOUT = "timeout"
    PROCESS_EXITED = "process_exited"
    PROVIDER_REJECTED = "provider_rejected"
    READINESS_FAILED = "readiness_failed"


class RuntimeActualState(str, Enum):
    STOPPED = "stopped"
    PROVISIONING = "provisioning"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    IDLE = "idle"
    DRAINING = "draining"
    STOPPING = "stopping"
    FAILED = "failed"


class ReconcileAction(str, Enum):
    ADOPT = "adopt"
    TERMINATE_ORPHAN = "terminate_orphan"
    QUARANTINE_UNKNOWN = "quarantine_unknown"
    MARK_FAILED = "mark_failed"
    MARK_STOPPED = "mark_stopped"
    IGNORE_STALE_GENERATION = "ignore_stale_generation"


@dataclass(frozen=True)
class PersonaArtifact:
    """Immutable, reconstructable persona snapshot supplied by Platform."""

    agentee_id: str
    revision: str
    content_hash: str
    instruction_payload: str


@dataclass(frozen=True)
class RuntimeSpec:
    runtime_id: str
    generation: int
    user_id: str
    agentee_id: str
    provider: str
    workspace_root: Path
    idle_ttl_seconds: float = 3600.0
    persona_artifact: Optional[PersonaArtifact] = None
    env: Mapping[str, str] = field(default_factory=dict)
    provider_private_metadata: Mapping[str, "JSONValue"] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedRuntime:
    spec: RuntimeSpec
    workdir: Path
    provider_private_metadata: Mapping[str, "JSONValue"] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeHandle:
    """Opaque-ish handle returned by ``start()``.

    ``transport`` is intentionally typed ``Any``: it is provider-private (for
    the Fake adapter, a ``ProcessSupervisor`` instance) and must never leak
    into a ``RuntimeRecord`` top-level field.
    """

    runtime_id: str
    generation: int
    workdir: Path
    transport: Any


@dataclass(frozen=True)
class RuntimeInput:
    request_id: str
    content: str
    trace_context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadyResult:
    ready: bool
    runtime_id: str
    generation: int
    detail: Mapping[str, "JSONValue"] = field(default_factory=dict)
    failure_kind: Optional[ReadinessFailureKind] = None


@dataclass(frozen=True)
class TurnResult:
    request_id: str
    runtime_id: str
    generation: int
    turn: int
    content: str
    detail: Mapping[str, "JSONValue"] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStatus:
    pid: Optional[int]
    returncode: Optional[int]
    ready: bool
    healthy: bool
    last_frame_monotonic: Optional[float]
    protocol_errors: int = 0
    detail: Mapping[str, "JSONValue"] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckpointResult:
    runtime_id: str
    generation: int
    path: Path
    detail: Mapping[str, "JSONValue"] = field(default_factory=dict)


@dataclass(frozen=True)
class StopResult:
    stopped: bool
    exit_code: Optional[int]
    escalation: str  # "app_stop" | "term" | "kill" | "already_stopped"
    reason: str


@dataclass(frozen=True)
class RuntimeRecord:
    """Minimal in-memory registry record, reserved for the reconcile phase.

    Not exercised by phase-1 tests; kept here so the ``AgentRuntimeAdapter``
    Protocol signature is stable across phases.
    """

    runtime_id: str
    generation: int
    desired_state: RuntimeActualState
    actual_state: RuntimeActualState
    workdir: Path
    owner_nonce: str
    last_effective_activity_monotonic: Optional[float] = None
    exit_reason: Optional[str] = None


@dataclass(frozen=True)
class ReconcileDecision:
    action: ReconcileAction
    runtime_id: str
    generation: int
    detail: Mapping[str, "JSONValue"] = field(default_factory=dict)


@dataclass(frozen=True)
class InputReceipt:
    """Returned immediately when an input is accepted for background execution.

    Contains no file paths, credentials, or database table references.
    The ``transcript_cursor`` is provider-private and only meaningful to the
    output reader of the same adapter instance.  It does not support
    multi-turn concurrent positioning (single-inflight constraint unchanged).
    """

    runtime_id: str
    generation: int
    provider: str
    session_id: str
    accepted_at: str  # ISO 8601 with timezone
    transcript_cursor: str
    operation_id: str


@dataclass(frozen=True)
class TranscriptEntry:
    """One sanitised transcript record, safe for the external output API.

    Only visible user/assistant text and standardised metadata are included.
    Thinking blocks, tool-call requests, tool results, attachments, env vars,
    filesystem paths and secrets are never present.
    """

    role: str  # "user" | "assistant"
    content: str
    uuid: Optional[str] = None
    parent_uuid: Optional[str] = None
    timestamp: Optional[str] = None  # ISO 8601 with timezone
    model: Optional[str] = None


@dataclass(frozen=True)
class OutputQuery:
    """Query parameters for the read-only transcript output endpoint.

    At most one positioning mode may be supplied; ``receipt`` takes
    precedence over ``since``.
    """

    receipt: Optional[str] = None
    since: Optional[str] = None  # RFC 3339
    limit: int = 50

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 200:
            raise ValueError("limit must be 1-200")


@dataclass(frozen=True)
class OutputPage:
    """Paginated transcript output. ``next_cursor`` is None when ``has_more`` is False."""

    entries: List["TranscriptEntry"]
    runtime_id: str
    generation: int
    has_more: bool = False
    next_cursor: Optional[str] = None


class AgentRuntimeAdapter(Protocol):
    """Provider-neutral adapter interface. Harness depends on this, not a concrete provider."""

    async def prepare(self, spec: RuntimeSpec) -> PreparedRuntime: ...

    async def start(self, prepared: PreparedRuntime) -> RuntimeHandle: ...

    async def wait_ready(self, handle: RuntimeHandle, deadline: float) -> ReadyResult: ...

    async def submit_input(self, handle: RuntimeHandle, value: RuntimeInput) -> TurnResult: ...

    async def inspect(self, handle: RuntimeHandle) -> ProviderStatus: ...

    async def checkpoint(self, handle: RuntimeHandle, deadline: float) -> Optional[CheckpointResult]: ...

    async def stop(self, handle: RuntimeHandle, reason: str, grace_period: float) -> StopResult: ...

    async def reconcile(self, record: RuntimeRecord) -> ReconcileDecision: ...
