"""Async Runtime Manager application boundary backed by product persistence."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import asdict, replace
from typing import Any, Mapping, Optional, Protocol

from modules.gateway.runtime_contracts import (
    GATEWAY_EVENT_CONTRACT_VERSION,
    GameSessionSnapshotProvider,
    GatewayEvent,
)
from modules.runtime_manager.authority import AuthorityMaterialIssuer
from modules.runtime_manager.domain import (
    AlreadyStopping,
    BindingState,
    Conflict,
    DesiredState,
    NotFound,
    RuntimeProductRecord,
    RuntimeProductSpec,
    RuntimeBinding,
    RuntimeState,
    StaleGeneration,
)
from modules.runtime_manager.gateway_projection import ProjectionResult
from modules.runtime_manager.persistence import (
    AsyncBindingRepository,
    AsyncGatewayProjectionRepository,
    AsyncRuntimeProductRepository,
    _binding_from_row,
)
from modules.runtime_manager.product_repository import OperationResult
from common.observability import emit_metric

log = logging.getLogger("runtime_manager.async_application")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def activation_intent_digest(spec: RuntimeProductSpec) -> str:
    """Hash stable startup intent, excluding server-assigned identity fields."""
    return _digest({
        "user_id": spec.user_id,
        "agentee_id": spec.agentee_id,
        "activation_key": spec.activation_key,
        "adapter_kind": spec.adapter_kind,
        "adapter_config_ref": spec.adapter_config_ref,
        "persona_artifact_ref": spec.persona_artifact_ref,
        "resource_budget": dict(spec.resource_budget),
        "idle_ttl_seconds": spec.idle_ttl_seconds,
        "allowed_capabilities": sorted(spec.allowed_capabilities),
        "contract_version": spec.contract_version,
    })


class AsyncRuntimeService:
    def __init__(self, repository: AsyncRuntimeProductRepository) -> None:
        self.repository = repository

    async def ensure_runtime(self, spec: RuntimeProductSpec) -> RuntimeProductRecord:
        spec.validate()
        return await self.repository.ensure(RuntimeProductRecord(spec=spec), activation_intent_digest(spec))

    async def set_desired_state(
        self,
        runtime_id: str,
        generation: int,
        desired_state: DesiredState,
        operation_id: str,
        expected_status_version: int,
    ) -> RuntimeProductRecord:
        owner = f"runtime:{runtime_id}:{generation}"
        fingerprint = _digest((generation, desired_state.value, expected_status_version))
        cached = await self.repository.get_operation(owner, operation_id)
        if cached is not None:
            if cached.fingerprint != fingerprint:
                raise Conflict("operation_id reused with different payload")
            return cached.result
        current = await self.repository.get_generation(runtime_id, generation)
        if current is None:
            latest = await self.repository.get(runtime_id)
            if latest is not None:
                raise StaleGeneration(runtime_id)
            raise NotFound(runtime_id)
        if desired_state is DesiredState.RUNNING and current.actual_state in {RuntimeState.DRAINING, RuntimeState.STOPPING}:
            raise AlreadyStopping("runtime is already stopping")
        actual = current.actual_state
        if desired_state is DesiredState.STOPPED and actual not in {RuntimeState.STOPPED, RuntimeState.FAILED}:
            actual = RuntimeState.DRAINING
        committed = await self.repository.compare_and_swap_with_operation(
            replace(current, desired_state=desired_state, actual_state=actual),
            expected_status_version,
            OperationResult(owner, operation_id, fingerprint, None),
        )
        if committed.actual_state is RuntimeState.DRAINING and current.actual_state is not RuntimeState.DRAINING:
            log.info(
                "runtime drain started",
                extra={"event": "runtime.lifecycle.transition", "runtime_id": runtime_id,
                       "generation": generation, "runtime_state": RuntimeState.DRAINING.value,
                       "outcome": "success", "from_state": current.actual_state.value},
            )
            emit_metric(
                "runtime_transition_total", "counter", 1, outcome="success",
                runtime_id=runtime_id, generation=generation,
                runtime_state=RuntimeState.DRAINING.value,
            )
        return committed


class AsyncBindingApplication:
    """Explicit async composition layer; never satisfies the legacy sync Protocol."""

    def __init__(self, repository: AsyncBindingRepository) -> None:
        self.repository = repository

    async def get(self, binding_id: str):
        return await self.repository.get(binding_id)

    async def get_operation(self, owner_ref: str, operation_id: str):
        return await self.repository.get_operation(owner_ref, operation_id)

    async def insert(self, binding, operation: OperationResult):
        return await self.repository.insert_with_operation(binding, operation)

    async def transition(self, binding, expected_version, operation, revocation=None, issuance=None):
        return await self.repository.transition_with_operation(
            binding, expected_version, operation, revocation, issuance
        )


class AsyncBindingService:
    """Manager-owned creation boundary for a Runtime's single Binding intent."""

    def __init__(self, runtimes: AsyncRuntimeProductRepository,
                 bindings: AsyncBindingRepository) -> None:
        self.runtimes = runtimes
        self.bindings = bindings

    async def ensure_binding(
        self,
        runtime_id: str,
        generation: int,
        user_id: str,
        agentee_id: str,
        operation_id: str,
        game_session_id: str | None = None,
    ) -> RuntimeBinding:
        if not operation_id:
            raise ValueError("operation_id is required")
        runtime = await self.runtimes.get_generation(runtime_id, generation)
        if runtime is None:
            latest = await self.runtimes.get(runtime_id)
            if latest is not None:
                raise StaleGeneration(runtime_id)
            raise NotFound(runtime_id)
        if not runtime.active:
            raise StaleGeneration(runtime_id)
        if runtime.spec.user_id != user_id or runtime.spec.agentee_id != agentee_id:
            raise NotFound(runtime_id)

        existing = await self.bindings.find_for_runtime(runtime_id, generation)
        if existing is not None:
            if existing.user_id != user_id or existing.agentee_id != agentee_id:
                raise Conflict("runtime Binding owner mismatch")
            # REVOKED and EXPIRED are terminal Binding states — they never
            # return to PENDING.  A new generation creates a new binding_id
            # naturally because find_for_runtime is scoped by generation.
            if existing.status in {BindingState.REVOKED, BindingState.EXPIRED}:
                if game_session_id is None or game_session_id == existing.game_session_id:
                    return existing
                raise Conflict(
                    "terminal binding cannot be reactivated; a new generation is required"
                )
            if game_session_id is not None and existing.game_session_id not in (None, game_session_id):
                raise Conflict("runtime already bound to another game session")
            candidate = existing
        else:
            candidate = RuntimeBinding(
                binding_id=str(uuid4()), runtime_id=runtime_id, generation=generation,
                user_id=user_id, agentee_id=agentee_id, game_session_id=game_session_id,
            )
        owner = f"runtime-binding:{runtime_id}:{generation}"
        fingerprint = _digest((runtime_id, generation, user_id, agentee_id, game_session_id))
        committed = await self.bindings.insert_with_operation(
            candidate, OperationResult(owner, operation_id, fingerprint, None)
        )
        # The generated id proves this invocation won the durable INSERT.  A
        # replay or a concurrent winner returns an already persisted row and
        # must not count another transition.
        if existing is None and committed.binding_id == candidate.binding_id:
            log.info(
                "runtime binding state changed",
                extra={"event": "runtime.binding.transition", "runtime_id": committed.runtime_id,
                       "generation": committed.generation, "binding_id": committed.binding_id,
                       "binding_state": committed.status.value, "outcome": "success"},
            )
            emit_metric(
                "runtime_binding_transition_total", "counter", 1, outcome="success",
                runtime_id=committed.runtime_id, generation=committed.generation,
                binding_id=committed.binding_id, binding_state=committed.status.value,
            )
        return committed


class AsyncGatewayProjectionService:
    def __init__(self, repository: AsyncGatewayProjectionRepository, snapshots: GameSessionSnapshotProvider) -> None:
        self.repository = repository
        self.snapshots = snapshots

    async def consume(self, event: GatewayEvent) -> ProjectionResult:
        if event.contract_version != GATEWAY_EVENT_CONTRACT_VERSION:
            raise ValueError("unsupported_contract_version")
        if (
            not event.message_id
            or event.occurred_at.tzinfo is None
            or event.aggregate_id != event.snapshot.game_session_id
            or event.aggregate_version != event.snapshot.session_version
        ):
            raise ValueError("invalid_gateway_event_envelope")
        try:
            return await self.repository.consume(event)
        except ValueError as error:
            if str(error) != "gateway_snapshot_unavailable":
                raise
            snapshot = self.snapshots.get_snapshot(event.aggregate_id)
            if snapshot is None or snapshot.session_version < event.aggregate_version:
                raise
            if snapshot.game_session_id != event.aggregate_id:
                raise ValueError("invalid_gateway_snapshot_identity")
            return await self.repository.consume(event, snapshot)


class AsyncAuthorityOutboxRepository(Protocol):
    async def claim_pending_intents(self, limit: int = 50): ...
    async def acknowledge_intent(self, intent_id: str) -> None: ...
    async def retry_intent(self, intent_id: str, error: str, delay_seconds: int) -> None: ...
    async def is_intent_processed(self, intent_id: str) -> bool: ...
    async def latest_revocation_intent_id(
        self, binding_id: str, through_version: int, through_epoch: int,
    ) -> str | None: ...
    async def diagnostic_stats(self) -> Mapping[str, float | int]: ...


class AuthorityOutboxWorker:
    """Processes DB-ordered intents sequentially; retry leaves later intents blocked."""

    def __init__(self, repository: AsyncAuthorityOutboxRepository, authority: AuthorityMaterialIssuer) -> None:
        self.repository = repository
        self.authority = authority

    async def run_once(self, limit: int = 50) -> int:
        intents = await self.repository.claim_pending_intents(limit)
        emit_metric(
            "runtime_outbox_claimed", "gauge", len(intents), outcome="current",
            outbox_kind="authority", stage="claim",
        )
        completed = 0
        for intent in intents:
            try:
                await self._apply(intent)
            except Exception as error:
                await self.repository.retry_intent(intent["intent_id"], str(error), 1)
                log.warning(
                    "authority outbox intent scheduled for retry",
                    extra={"event": "runtime.outbox.retry", "outbox_kind": "authority",
                           "stage": "retry", "outcome": "retry", "error_code": "internal_error"},
                )
                emit_metric(
                    "runtime_outbox_retry_total", "counter", 1, outcome="retry",
                    outbox_kind="authority", stage="retry", error_code="internal_error",
                )
                continue
            await self.repository.acknowledge_intent(intent["intent_id"])
            completed += 1
        stats_reader = getattr(self.repository, "diagnostic_stats", None)
        if stats_reader is not None:
            try:
                stats = await stats_reader()
                emit_metric(
                    "runtime_outbox_pending", "gauge", int(stats.get("pending", 0)),
                    outcome="current", outbox_kind="authority",
                )
                emit_metric(
                    "runtime_outbox_oldest_age_seconds", "gauge",
                    float(stats.get("oldest_age_seconds", 0.0)),
                    outcome="current", outbox_kind="authority",
                )
            except Exception as error:
                log.warning(
                    "authority outbox diagnostics unavailable",
                    extra={"event": "runtime.outbox.diagnostics_unavailable",
                           "outbox_kind": "authority", "outcome": "unavailable",
                           "error_code": "diagnostic_stats_unavailable",
                           "exception_type": type(error).__name__},
                )
                emit_metric(
                    "runtime_outbox_diagnostics_total", "counter", 1,
                    outcome="unavailable", outbox_kind="authority",
                    error_code="diagnostic_stats_unavailable",
                )
        return completed

    async def _apply(self, intent: Mapping[str, Any]) -> None:
        payload = intent["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if intent["intent_kind"] == "revoke":
            result = self.authority.revoke(intent["binding_id"], intent["binding_version"], intent["lease_epoch"])
            if inspect.isawaitable(result):
                await result
            return
        binding = _binding_from_row(payload["binding"])
        result = self.authority.issue(
            binding,
            scopes=tuple(payload["scopes"]),
            allowed_tools=tuple(payload["allowed_tools"]),
            jti=payload["jti"],
        )
        if inspect.isawaitable(result):
            await result
