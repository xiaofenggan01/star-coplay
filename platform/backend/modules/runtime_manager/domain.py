"""Versioned product-domain contracts owned by Runtime Manager.

These records deliberately live beside, rather than replace, the M1 adapter
contracts.  Provider-private process details never enter these public models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Tuple

RUNTIME_CONTRACT_VERSION = "starcoplay.runtime.v1"
BINDING_CONTRACT_VERSION = "starcoplay.binding.v1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DesiredState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    PROVISIONING = "provisioning"
    STARTING = "starting"
    READY_UNBOUND = "ready_unbound"
    RUNNING_BOUND = "running_bound"
    IDLE = "idle"
    DRAINING = "draining"
    STOPPING = "stopping"
    DEGRADED = "degraded"
    FAILED = "failed"


class BindingState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True)
class RuntimeProductSpec:
    runtime_id: str
    generation: int
    activation_key: str
    user_id: str
    agentee_id: str
    adapter_kind: str
    adapter_config_ref: str
    persona_artifact_ref: str
    resource_budget: Mapping[str, int] = field(default_factory=dict)
    # Online business silence TTL: how long a healthy (AVAILABLE + ACTIVE)
    # Runtime can sit idle with no effective business activity (user input,
    # agent output, MCP success) before the Manager reclaims it.
    # Independent from Gateway.resume_token_ttl_s (600 s connection recovery
    # window after abnormal transport disconnect).
    idle_ttl_seconds: int = 3600
    allowed_capabilities: Tuple[str, ...] = ()
    contract_version: str = RUNTIME_CONTRACT_VERSION

    def validate(self) -> None:
        if self.contract_version != RUNTIME_CONTRACT_VERSION:
            raise InvalidSpec("unsupported_contract_version")
        if not all((self.runtime_id, self.activation_key, self.user_id, self.agentee_id)):
            raise InvalidSpec("missing required runtime identity")
        if self.generation < 1 or self.idle_ttl_seconds < 1:
            raise InvalidSpec("invalid generation or idle TTL")


@dataclass(frozen=True)
class RuntimeProductRecord:
    spec: RuntimeProductSpec
    desired_state: DesiredState = DesiredState.RUNNING
    actual_state: RuntimeState = RuntimeState.PROVISIONING
    status_version: int = 1
    ready: bool = False
    last_effective_activity_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    exit_reason: Optional[str] = None

    @property
    def runtime_id(self) -> str:
        return self.spec.runtime_id

    @property
    def generation(self) -> int:
        return self.spec.generation

    @property
    def active(self) -> bool:
        return self.actual_state not in {RuntimeState.STOPPED, RuntimeState.FAILED}


@dataclass(frozen=True)
class RuntimeBinding:
    binding_id: str
    runtime_id: str
    generation: int
    user_id: str
    agentee_id: str
    binding_version: int = 1
    lease_epoch: int = 0
    status: BindingState = BindingState.PENDING
    game_session_id: Optional[str] = None
    game_instance_nonce: Optional[str] = None
    capability_digest: Optional[str] = None
    resume_identity: Optional[str] = None
    bound_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    suspended_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    contract_version: str = BINDING_CONTRACT_VERSION


@dataclass(frozen=True)
class SessionProof:
    game_session_id: str
    game_instance_nonce: str
    owner_user_id: str
    capability_digest: str
    resume_identity: Optional[str] = None


@dataclass(frozen=True)
class AuthorityRevocationIntent:
    intent_id: str
    binding_id: str
    through_binding_version: int
    through_lease_epoch: int
    reason: str


@dataclass(frozen=True)
class AuthorityIssuanceIntent:
    intent_id: str
    binding: RuntimeBinding
    scopes: Tuple[str, ...]
    allowed_tools: Tuple[str, ...]
    jti: str


class DomainError(Exception):
    code = "internal_error"


class InvalidSpec(DomainError):
    code = "invalid_spec"


class Conflict(DomainError):
    code = "conflict"


class StaleGeneration(DomainError):
    code = "stale_generation"


class NotFound(DomainError):
    code = "not_found"


class InvalidTransition(DomainError):
    code = "invalid_transition"


class AlreadyStopping(DomainError):
    code = "already_stopping"


class HostedRuntimeNotAllowed(DomainError):
    code = "hosted_runtime_not_allowed"


class ResourceExhausted(DomainError):
    code = "resource_exhausted"
