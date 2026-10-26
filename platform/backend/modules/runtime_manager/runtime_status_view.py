"""Immutable product View and assembler for the M2B RuntimeStatus GET endpoint.

Owns the read-side projection contract.  Never mutates RuntimeProductRecord,
DB state, or Binding authority.  Process health is best-effort and never
written back to durable storage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Protocol

from modules.runtime_manager.contracts import AgentRuntimeAdapter, RuntimeHandle
from modules.runtime_manager.domain import (
    RUNTIME_CONTRACT_VERSION,
    RuntimeBinding,
    RuntimeProductRecord,
    RuntimeState,
)


class ProcessHealthView(str, Enum):
    """Best-effort process inspection result.  Exactly five states."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    NOT_OWNED = "not_owned"
    NOT_RUNNING = "not_running"


@dataclass(frozen=True)
class BindingProjectionView:
    """Safe, fenced projection of RuntimeBinding for the product View.

    Exact minimal fencing projection frozen for RuntimeStatus.
    """

    contract_version: str
    binding_id: str
    binding_version: int
    lease_epoch: int
    status: str
    game_session_id: Optional[str]
    game_instance_nonce: Optional[str]
    capability_digest: Optional[str]
    expires_at: Optional[datetime] = None

    @classmethod
    def from_binding(cls, binding: RuntimeBinding) -> "BindingProjectionView":
        return cls(
            contract_version=binding.contract_version,
            binding_id=binding.binding_id,
            binding_version=binding.binding_version,
            lease_epoch=binding.lease_epoch,
            status=binding.status.value,
            game_session_id=binding.game_session_id,
            game_instance_nonce=binding.game_instance_nonce,
            capability_digest=binding.capability_digest,
            expires_at=binding.expires_at,
        )


class BindingLookup(Protocol):
    """Narrow protocol so the assembler never imports BindingRepository."""
    async def find_for_runtime(self, runtime_id: str, generation: int) -> Optional[RuntimeBinding]: ...


@dataclass(frozen=True)
class RuntimeStatusView:
    """Immutable product View for GET /internal/runtime-manager/v1/runtimes/{id}.

    Exactly 12 top-level fields.  No spec extension, no provider-private
    fields, no DB mutation.

    Field contract (every field MUST be present, even when None):
      contract_version          — always RUNTIME_CONTRACT_VERSION
      runtime_id                — from RuntimeProductRecord
      generation                — from RuntimeProductRecord
      status_version            — from RuntimeProductRecord (never bumped by GET)
      desired_state             — from RuntimeProductRecord
      actual_state              — from RuntimeProductRecord
      ready                     — from RuntimeProductRecord
      binding_projection        — safe fenced Binding fields, or null
      last_effective_activity_at — from RuntimeProductRecord
      started_at                — from RuntimeProductRecord
      exit_reason               — from RuntimeProductRecord
      process_health            — exactly one of ProcessHealthView values
    """

    contract_version: str
    runtime_id: str
    generation: int
    status_version: int
    desired_state: str
    actual_state: str
    ready: bool
    binding_projection: Optional[BindingProjectionView] = None
    last_effective_activity_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    exit_reason: Optional[str] = None
    process_health: str = ProcessHealthView.NOT_OWNED.value


async def _resolve_process_health(
    record: RuntimeProductRecord,
    adapter: Optional[AgentRuntimeAdapter],
    handle: Optional[RuntimeHandle],
) -> ProcessHealthView:
    """Best-effort process inspection — only inspects an owned matching handle.

    Health state machine (first-match wins):
      stopped => NOT_RUNNING
      no handle => NOT_OWNED
      inspect exception => UNKNOWN
      returncode or !healthy => UNHEALTHY
      else HEALTHY

    Never writes back to durable storage.  Returns exactly one of the five
    ProcessHealthView states.
    """
    # stopped => not_running
    if record.actual_state is RuntimeState.STOPPED:
        return ProcessHealthView.NOT_RUNNING
    # no handle => not_owned
    if handle is None:
        return ProcessHealthView.NOT_OWNED
    if (handle.runtime_id, handle.generation) != (record.runtime_id, record.generation):
        return ProcessHealthView.NOT_OWNED
    if adapter is None:
        return ProcessHealthView.UNKNOWN
    try:
        status = await adapter.inspect(handle)
    except Exception:
        return ProcessHealthView.UNKNOWN
    # returncode or !healthy => unhealthy
    if status.returncode is not None or not status.healthy:
        return ProcessHealthView.UNHEALTHY
    return ProcessHealthView.HEALTHY


def _validate_binding(
    binding: Optional[RuntimeBinding],
    runtime_id: str,
    generation: int,
    user_id: str,
) -> Optional[RuntimeBinding]:
    """Fail closed: return None if binding identity doesn't match runtime record.

    Validates runtime_id, generation, and owner match.  Any mismatch or
    missing field returns None — never leaks another runtime's binding.
    """
    if binding is None:
        return None
    if binding.runtime_id != runtime_id:
        return None
    if binding.generation != generation:
        return None
    if binding.user_id != user_id:
        return None
    return binding


async def assemble_runtime_status(
    record: RuntimeProductRecord,
    *,
    binding_lookup: Optional[BindingLookup] = None,
    adapter: Optional[AgentRuntimeAdapter] = None,
    handle: Optional[RuntimeHandle] = None,
) -> RuntimeStatusView:
    """Assemble a read-only RuntimeStatusView from persisted and live state.

    *Never* mutates DB, never bumps status_version, never creates or
    transitions Binding authority.  Process health is best-effort live
    inspection — failures return UNKNOWN without affecting the HTTP response
    or durable state.
    """
    # ── Binding projection (safe fenced fields only) ──────────────────
    binding_projection: Optional[BindingProjectionView] = None
    if binding_lookup is not None:
        try:
            binding = await binding_lookup.find_for_runtime(
                record.runtime_id, record.generation
            )
            # Fail closed: validate binding identity matches runtime record
            binding = _validate_binding(
                binding, record.runtime_id, record.generation, record.spec.user_id,
            )
            binding_projection = (
                BindingProjectionView.from_binding(binding) if binding is not None else None
            )
        except Exception:
            # Binding lookup is best-effort for the View; never fail the GET.
            binding_projection = None

    # ── Process health (best-effort live inspection) ──────────────────
    process_health = await _resolve_process_health(
        record, adapter, handle,
    )

    return RuntimeStatusView(
        contract_version=RUNTIME_CONTRACT_VERSION,
        runtime_id=record.runtime_id,
        generation=record.generation,
        status_version=record.status_version,
        desired_state=record.desired_state.value,
        actual_state=record.actual_state.value,
        ready=record.ready,
        binding_projection=binding_projection,
        last_effective_activity_at=record.last_effective_activity_at,
        started_at=record.started_at,
        exit_reason=record.exit_reason,
        process_health=process_health.value,
    )
