"""Runtime and Binding application services; Manager is the sole writer."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any, Optional
from uuid import uuid4

from modules.runtime_manager.authority import AuthorityMaterialIssuer
from modules.runtime_manager.domain import (
    AlreadyStopping,
    AuthorityIssuanceIntent,
    AuthorityRevocationIntent,
    BindingState,
    Conflict,
    DesiredState,
    InvalidTransition,
    NotFound,
    RuntimeBinding,
    RuntimeProductRecord,
    RuntimeProductSpec,
    RuntimeState,
    SessionProof,
    StaleGeneration,
    utc_now,
)
from modules.runtime_manager.product_repository import (
    BindingRepository,
    OperationResult,
    RuntimeProductRepository,
)


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _activation_intent(spec: RuntimeProductSpec) -> tuple[Any, ...]:
    """Stable user intent excludes server-assigned runtime identity/generation."""
    return (
        spec.user_id,
        spec.agentee_id,
        spec.activation_key,
        spec.adapter_kind,
        spec.adapter_config_ref,
        spec.persona_artifact_ref,
        tuple(sorted(spec.resource_budget.items())),
        spec.idle_ttl_seconds,
        tuple(sorted(spec.allowed_capabilities)),
        spec.contract_version,
    )


class RuntimeService:
    def __init__(self, repository: RuntimeProductRepository) -> None:
        self.repository = repository

    def ensure_runtime(self, spec: RuntimeProductSpec) -> RuntimeProductRecord:
        spec.validate()
        existing = self.repository.find_activation(spec.user_id, spec.agentee_id, spec.activation_key)
        if existing is not None and existing.active:
            if _activation_intent(existing.spec) != _activation_intent(spec):
                raise Conflict("activation key reused with a different stable startup intent")
            if existing.actual_state in {RuntimeState.DRAINING, RuntimeState.STOPPING} and (
                spec.runtime_id != existing.runtime_id or spec.generation != existing.generation
            ):
                raise Conflict("stopping activation cannot be replaced")
            return existing
        current = self.repository.get(spec.runtime_id)
        if current is not None and current.active:
            raise Conflict("runtime already has an active generation")
        return self.repository.insert(RuntimeProductRecord(spec=spec))

    def set_desired_state(
        self,
        runtime_id: str,
        generation: int,
        desired_state: DesiredState,
        operation_id: str,
        expected_status_version: int,
    ) -> RuntimeProductRecord:
        owner = f"runtime:{runtime_id}:{generation}"
        fingerprint = _fingerprint((generation, desired_state.value, expected_status_version))
        cached = self.repository.get_operation(owner, operation_id)
        if cached is not None:
            if cached.fingerprint != fingerprint:
                raise Conflict("operation_id reused with different payload")
            return cached.result
        current = self.repository.get_generation(runtime_id, generation)
        if current is None:
            latest = self.repository.get(runtime_id)
            if latest is not None and latest.generation != generation:
                raise StaleGeneration(runtime_id)
            raise NotFound(runtime_id)
        if current.status_version != expected_status_version:
            raise Conflict("runtime status_version mismatch")
        if desired_state is DesiredState.RUNNING and current.actual_state in {
            RuntimeState.DRAINING,
            RuntimeState.STOPPING,
        }:
            raise AlreadyStopping("runtime is already stopping")
        actual = current.actual_state
        if desired_state is DesiredState.STOPPED and actual not in {RuntimeState.STOPPED, RuntimeState.FAILED}:
            actual = RuntimeState.DRAINING
        operation = OperationResult(owner, operation_id, fingerprint, None)
        return self.repository.compare_and_swap_with_operation(
            replace(current, desired_state=desired_state, actual_state=actual),
            expected_status_version,
            operation,
        )

    def transition_actual(
        self,
        runtime_id: str,
        generation: int,
        actual_state: RuntimeState,
        expected_status_version: int,
        *,
        ready: Optional[bool] = None,
        exit_reason: Optional[str] = None,
    ) -> RuntimeProductRecord:
        current = self.repository.get_generation(runtime_id, generation)
        if current is None:
            latest = self.repository.get(runtime_id)
            if latest is not None:
                raise StaleGeneration(runtime_id)
            raise NotFound(runtime_id)
        if current.status_version != expected_status_version:
            raise Conflict("runtime status_version mismatch")
        allowed = {
            RuntimeState.PROVISIONING: {RuntimeState.STARTING, RuntimeState.DRAINING, RuntimeState.FAILED},
            RuntimeState.STARTING: {RuntimeState.READY_UNBOUND, RuntimeState.DRAINING, RuntimeState.FAILED},
            RuntimeState.READY_UNBOUND: {RuntimeState.RUNNING_BOUND, RuntimeState.IDLE, RuntimeState.DRAINING, RuntimeState.DEGRADED},
            RuntimeState.RUNNING_BOUND: {RuntimeState.READY_UNBOUND, RuntimeState.IDLE, RuntimeState.DRAINING, RuntimeState.DEGRADED},
            RuntimeState.IDLE: {RuntimeState.RUNNING_BOUND, RuntimeState.DRAINING, RuntimeState.DEGRADED},
            RuntimeState.DRAINING: {RuntimeState.STOPPING, RuntimeState.FAILED},
            RuntimeState.STOPPING: {RuntimeState.STOPPED, RuntimeState.FAILED},
            RuntimeState.DEGRADED: {RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.DRAINING, RuntimeState.FAILED},
            RuntimeState.FAILED: {RuntimeState.STOPPED},
            RuntimeState.STOPPED: set(),
        }
        if actual_state != current.actual_state and actual_state not in allowed[current.actual_state]:
            raise InvalidTransition(f"{current.actual_state.value} -> {actual_state.value}")
        return self.repository.compare_and_swap(
            replace(
                current,
                actual_state=actual_state,
                ready=current.ready if ready is None else ready,
                exit_reason=exit_reason,
                started_at=current.started_at or (utc_now() if actual_state is RuntimeState.STARTING else None),
            ),
            expected_status_version,
        )


class BindingService:
    def __init__(self, runtime_repository: RuntimeProductRepository, binding_repository: BindingRepository, authority: AuthorityMaterialIssuer) -> None:
        self.runtime_repository = runtime_repository
        self.repository = binding_repository
        self.authority = authority

    def ensure_binding(self, runtime_id: str, generation: int, game_session_id: Optional[str], operation_id: str) -> RuntimeBinding:
        owner = f"runtime-binding:{runtime_id}:{generation}"
        fingerprint = _fingerprint((runtime_id, generation, game_session_id))
        cached = self._operation(owner, operation_id, fingerprint)
        if cached is not None:
            return cached
        runtime = self.runtime_repository.get_generation(runtime_id, generation)
        if runtime is None or not runtime.active:
            raise StaleGeneration(runtime_id)
        existing = self.repository.find_for_runtime(runtime_id, generation)
        if existing is not None:
            if existing.game_session_id not in (None, game_session_id):
                raise Conflict("runtime already bound to another game session")
            result = existing
            self.repository.save_operation(OperationResult(owner, operation_id, fingerprint, result))
            return result
        result = RuntimeBinding(
            binding_id=str(uuid4()), runtime_id=runtime_id, generation=generation,
            user_id=runtime.spec.user_id, agentee_id=runtime.spec.agentee_id,
            game_session_id=game_session_id,
        )
        return self.repository.insert_with_operation(result, OperationResult(owner, operation_id, fingerprint, result))

    def activate(self, binding_id: str, expected_version: int, proof: SessionProof, operation_id: str) -> RuntimeBinding:
        fingerprint = _fingerprint((expected_version, asdict(proof)))
        cached = self._operation(binding_id, operation_id, fingerprint)
        if cached is not None:
            self.flush_authority_intents()
            return cached
        current = self._current(binding_id, expected_version)
        if current.status not in {BindingState.PENDING, BindingState.SUSPENDED}:
            raise InvalidTransition(f"cannot activate {current.status.value}")
        if proof.owner_user_id != current.user_id or current.game_session_id not in (None, proof.game_session_id):
            raise Conflict("GameSession identity/owner mismatch")
        if not proof.resume_identity:
            raise Conflict("resume identity required")
        same_writer_resume = current.status is BindingState.SUSPENDED and current.game_instance_nonce == proof.game_instance_nonce
        if same_writer_resume and current.resume_identity != proof.resume_identity:
            raise Conflict("resume identity mismatch")
        result = replace(
            current, status=BindingState.ACTIVE, binding_version=current.binding_version + 1,
            lease_epoch=current.lease_epoch if same_writer_resume else current.lease_epoch + 1,
            game_session_id=proof.game_session_id, game_instance_nonce=proof.game_instance_nonce,
            capability_digest=proof.capability_digest, resume_identity=proof.resume_identity,
            bound_at=utc_now(), suspended_at=None,
        )
        result = self.repository.transition_with_operation(
            result,
            expected_version,
            OperationResult(binding_id, operation_id, fingerprint, result),
            None,
            self._issuance_intent(result),
        )
        self.flush_authority_intents()
        return result

    def suspend(self, binding_id: str, expected_version: int, reason: str, operation_id: str) -> RuntimeBinding:
        return self._closed_transition(binding_id, expected_version, reason, operation_id, BindingState.SUSPENDED, False)

    def update_capabilities(self, binding_id: str, expected_version: int, capability_digest: str, operation_id: str) -> RuntimeBinding:
        fingerprint = _fingerprint((expected_version, capability_digest))
        cached = self._operation(binding_id, operation_id, fingerprint)
        if cached is not None:
            self.flush_authority_intents()
            return cached
        current = self._current(binding_id, expected_version)
        if current.status not in {BindingState.ACTIVE, BindingState.SUSPENDED}:
            raise InvalidTransition("capabilities cannot change on closed binding")
        result = replace(current, binding_version=current.binding_version + 1, capability_digest=capability_digest)
        result = self._commit_with_revoke(
            current, result, operation_id, fingerprint, "capabilities_changed",
            current.lease_epoch, issue=result.status is BindingState.ACTIVE,
        )
        return result

    def revoke(self, binding_id: str, expected_version: int, reason: str, operation_id: str) -> RuntimeBinding:
        return self._closed_transition(binding_id, expected_version, reason, operation_id, BindingState.REVOKED, True)

    def _closed_transition(self, binding_id: str, expected_version: int, reason: str, operation_id: str, target: BindingState, fence_writer: bool) -> RuntimeBinding:
        fingerprint = _fingerprint((expected_version, reason, target.value))
        cached = self._operation(binding_id, operation_id, fingerprint)
        if cached is not None:
            self.flush_authority_intents()
            return cached
        current = self._current(binding_id, expected_version)
        if target is BindingState.SUSPENDED and current.status is not BindingState.ACTIVE:
            raise InvalidTransition(f"cannot suspend {current.status.value}")
        if target is BindingState.REVOKED and current.status in {BindingState.REVOKED, BindingState.EXPIRED}:
            raise InvalidTransition(f"already {current.status.value}")
        epoch = current.lease_epoch + (1 if fence_writer else 0)
        result = replace(
            current, status=target, binding_version=current.binding_version + 1, lease_epoch=epoch,
            suspended_at=utc_now() if target is BindingState.SUSPENDED else current.suspended_at,
            revoked_at=utc_now() if target is BindingState.REVOKED else current.revoked_at,
        )
        return self._commit_with_revoke(current, result, operation_id, fingerprint, reason, epoch)

    def _commit_with_revoke(
        self,
        current: RuntimeBinding,
        result: RuntimeBinding,
        operation_id: str,
        fingerprint: str,
        reason: str,
        epoch: int,
        *,
        issue: bool = False,
    ) -> RuntimeBinding:
        intent = AuthorityRevocationIntent(
            intent_id=f"{current.binding_id}:{result.binding_version}:{operation_id}",
            binding_id=current.binding_id,
            through_binding_version=current.binding_version,
            through_lease_epoch=epoch,
            reason=reason,
        )
        committed = self.repository.transition_with_operation(
            result, current.binding_version,
            OperationResult(current.binding_id, operation_id, fingerprint, result),
            intent,
            self._issuance_intent(result) if issue else None,
        )
        self.flush_authority_intents()
        return committed

    def flush_authority_intents(self) -> None:
        for intent in self.repository.pending_revocations():
            self.authority.revoke(intent.binding_id, intent.through_binding_version, intent.through_lease_epoch)
            self.repository.acknowledge_revocation(intent.intent_id)
        for intent in self.repository.pending_issuances():
            self.authority.issue(
                intent.binding,
                scopes=intent.scopes,
                allowed_tools=intent.allowed_tools,
                jti=intent.jti,
            )
            self.repository.acknowledge_issuance(intent.intent_id)

    def _issuance_intent(self, binding: RuntimeBinding) -> AuthorityIssuanceIntent:
        runtime = self.runtime_repository.get_generation(binding.runtime_id, binding.generation)
        if runtime is None:
            raise StaleGeneration(binding.runtime_id)
        jti = f"{binding.binding_id}:{binding.binding_version}:{binding.lease_epoch}"
        return AuthorityIssuanceIntent(
            intent_id=f"issue:{jti}",
            binding=binding,
            scopes=("game:read", "game:write"),
            allowed_tools=tuple(sorted(runtime.spec.allowed_capabilities)),
            jti=jti,
        )

    def _current(self, binding_id: str, expected_version: int) -> RuntimeBinding:
        current = self.repository.get(binding_id)
        if current is None:
            raise NotFound(binding_id)
        if current.binding_version != expected_version:
            raise Conflict("binding_version mismatch")
        return current

    def _operation(self, owner: str, operation_id: str, fingerprint: str) -> Optional[RuntimeBinding]:
        cached = self.repository.get_operation(owner, operation_id)
        if cached is None:
            return None
        if cached.fingerprint != fingerprint:
            raise Conflict("operation_id reused with different payload")
        return cached.result
