"""DB-neutral repositories and atomic in-memory reference implementations."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Protocol, Tuple

from modules.runtime_manager.domain import (
    AuthorityIssuanceIntent,
    AuthorityRevocationIntent,
    Conflict,
    RuntimeBinding,
    RuntimeProductRecord,
)


@dataclass(frozen=True)
class OperationResult:
    owner_ref: str
    operation_id: str
    fingerprint: str
    result: Any


class RuntimeProductRepository(Protocol):
    def find_activation(self, user_id: str, agentee_id: str, activation_key: str) -> Optional[RuntimeProductRecord]: ...
    def get(self, runtime_id: str) -> Optional[RuntimeProductRecord]: ...
    def get_generation(self, runtime_id: str, generation: int) -> Optional[RuntimeProductRecord]: ...
    def insert(self, record: RuntimeProductRecord) -> RuntimeProductRecord: ...
    def compare_and_swap(self, record: RuntimeProductRecord, expected_version: int) -> RuntimeProductRecord: ...
    def compare_and_swap_with_operation(
        self, record: RuntimeProductRecord, expected_version: int, operation: OperationResult
    ) -> RuntimeProductRecord: ...
    def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]: ...
    def save_operation(self, operation: OperationResult) -> None: ...


class BindingRepository(Protocol):
    def get(self, binding_id: str) -> Optional[RuntimeBinding]: ...
    def find_for_runtime(self, runtime_id: str, generation: int) -> Optional[RuntimeBinding]: ...
    def insert_with_operation(self, binding: RuntimeBinding, operation: OperationResult) -> RuntimeBinding: ...
    def transition_with_operation(
        self,
        binding: RuntimeBinding,
        expected_version: int,
        operation: OperationResult,
        revocation: Optional[AuthorityRevocationIntent],
        issuance: Optional[AuthorityIssuanceIntent] = None,
    ) -> RuntimeBinding: ...
    def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]: ...
    def save_operation(self, operation: OperationResult) -> None: ...
    def pending_revocations(self) -> tuple[AuthorityRevocationIntent, ...]: ...
    def acknowledge_revocation(self, intent_id: str) -> None: ...
    def pending_issuances(self) -> tuple[AuthorityIssuanceIntent, ...]: ...
    def acknowledge_issuance(self, intent_id: str) -> None: ...


class _OperationStore:
    def __init__(self) -> None:
        self.operations: Dict[Tuple[str, str], OperationResult] = {}

    def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]:
        return self.operations.get((owner_ref, operation_id))

    def save_operation(self, operation: OperationResult) -> None:
        key = (operation.owner_ref, operation.operation_id)
        current = self.operations.get(key)
        if current is not None:
            if current.fingerprint != operation.fingerprint:
                raise Conflict("operation_id reused with different payload")
            return
        self.operations[key] = operation


class InMemoryRuntimeProductRepository(_OperationStore):
    def __init__(self) -> None:
        super().__init__()
        self.records: Dict[Tuple[str, int], RuntimeProductRecord] = {}
        self.latest: Dict[str, int] = {}
        self.activations: Dict[Tuple[str, str, str], Tuple[str, int]] = {}

    def find_activation(self, user_id: str, agentee_id: str, activation_key: str) -> Optional[RuntimeProductRecord]:
        identity = self.activations.get((user_id, agentee_id, activation_key))
        return self.records.get(identity) if identity else None

    def get(self, runtime_id: str) -> Optional[RuntimeProductRecord]:
        generation = self.latest.get(runtime_id)
        return self.records.get((runtime_id, generation)) if generation is not None else None

    def get_generation(self, runtime_id: str, generation: int) -> Optional[RuntimeProductRecord]:
        return self.records.get((runtime_id, generation))

    def insert(self, record: RuntimeProductRecord) -> RuntimeProductRecord:
        activation = (record.spec.user_id, record.spec.agentee_id, record.spec.activation_key)
        existing_activation = self.find_activation(*activation)
        existing_runtime = self.get(record.runtime_id)
        if existing_activation is not None:
            if existing_activation.actual_state.value != "stopped":
                raise Conflict("previous activation runtime must reach STOPPED")
            if record.runtime_id != existing_activation.runtime_id:
                raise Conflict("activation must retain the same runtime_id")
            if record.generation != existing_activation.generation + 1:
                raise Conflict("activation generation must increment by exactly one")
        if existing_runtime is not None and existing_runtime.actual_state.value != "stopped":
            raise Conflict("previous runtime generation must reach STOPPED")
        if existing_runtime is not None and record.generation <= existing_runtime.generation:
            raise Conflict("new runtime generation must increase")
        identity = (record.runtime_id, record.generation)
        if identity in self.records:
            raise Conflict("runtime generation already exists")
        self.records[identity] = record
        self.latest[record.runtime_id] = record.generation
        self.activations[activation] = identity
        return record

    def compare_and_swap(self, record: RuntimeProductRecord, expected_version: int) -> RuntimeProductRecord:
        identity = (record.runtime_id, record.generation)
        current = self.records.get(identity)
        if current is None or current.status_version != expected_version:
            raise Conflict("runtime status_version mismatch")
        updated = replace(record, status_version=expected_version + 1)
        self.records[identity] = updated
        return updated

    def compare_and_swap_with_operation(
        self, record: RuntimeProductRecord, expected_version: int, operation: OperationResult
    ) -> RuntimeProductRecord:
        identity = (record.runtime_id, record.generation)
        current = self.records.get(identity)
        if current is None or current.status_version != expected_version:
            raise Conflict("runtime status_version mismatch")
        updated = replace(record, status_version=expected_version + 1)
        self.save_operation(replace(operation, result=updated))
        self.records[identity] = updated
        return updated


class InMemoryBindingRepository(_OperationStore):
    def __init__(self) -> None:
        super().__init__()
        self.records: Dict[str, RuntimeBinding] = {}
        self.by_runtime: Dict[Tuple[str, int], str] = {}
        self.revocation_outbox: Dict[str, AuthorityRevocationIntent] = {}
        self.issuance_outbox: Dict[str, AuthorityIssuanceIntent] = {}

    def get(self, binding_id: str) -> Optional[RuntimeBinding]:
        return self.records.get(binding_id)

    def find_for_runtime(self, runtime_id: str, generation: int) -> Optional[RuntimeBinding]:
        binding_id = self.by_runtime.get((runtime_id, generation))
        return self.records.get(binding_id) if binding_id else None

    def insert_with_operation(self, binding: RuntimeBinding, operation: OperationResult) -> RuntimeBinding:
        key = (binding.runtime_id, binding.generation)
        if binding.binding_id in self.records or key in self.by_runtime:
            raise Conflict("binding already exists")
        self.save_operation(replace(operation, result=binding))
        self.records[binding.binding_id] = binding
        self.by_runtime[key] = binding.binding_id
        return binding

    def transition_with_operation(
        self,
        binding: RuntimeBinding,
        expected_version: int,
        operation: OperationResult,
        revocation: Optional[AuthorityRevocationIntent],
        issuance: Optional[AuthorityIssuanceIntent] = None,
    ) -> RuntimeBinding:
        current = self.records.get(binding.binding_id)
        if current is None or current.binding_version != expected_version:
            raise Conflict("binding_version mismatch")
        # The reference implementation models one DB transaction: validation
        # precedes all writes, then snapshot, operation and outbox commit together.
        self.save_operation(replace(operation, result=binding))
        self.records[binding.binding_id] = binding
        if revocation is not None:
            self.revocation_outbox[revocation.intent_id] = revocation
        if issuance is not None:
            self.issuance_outbox[issuance.intent_id] = issuance
        return binding

    def pending_revocations(self) -> tuple[AuthorityRevocationIntent, ...]:
        return tuple(self.revocation_outbox.values())

    def acknowledge_revocation(self, intent_id: str) -> None:
        self.revocation_outbox.pop(intent_id, None)

    def pending_issuances(self) -> tuple[AuthorityIssuanceIntent, ...]:
        return tuple(self.issuance_outbox.values())

    def acknowledge_issuance(self, intent_id: str) -> None:
        self.issuance_outbox.pop(intent_id, None)
