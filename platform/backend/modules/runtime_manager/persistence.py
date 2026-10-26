"""Transactional asyncpg persistence for Runtime Manager product state.

All mutating methods own their transaction boundary.  In particular, a CAS
never publishes a snapshot without its idempotent operation result and any
authority intent that must follow it.
"""
from __future__ import annotations

import asyncio
import logging
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence

from modules.gateway.runtime_contracts import GATEWAY_EVENT_CONTRACT_VERSION, GameSessionSnapshot, GatewayEvent, GatewayEventType
from modules.runtime_manager.domain import (
    AuthorityIssuanceIntent,
    AuthorityRevocationIntent,
    BindingState,
    Conflict,
    DesiredState,
    RuntimeBinding,
    RuntimeProductRecord,
    RuntimeProductSpec,
    RuntimeState,
    ResourceExhausted,
)
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from common.observability import emit_metric

log = logging.getLogger("runtime_manager.persistence")


def _record_admission(record: RuntimeProductRecord) -> None:
    log.info(
        "runtime admission accepted",
        extra={"event": "runtime.admission.accepted", "runtime_id": record.runtime_id,
               "generation": record.generation, "outcome": "accepted"},
    )
    emit_metric(
        "runtime_admission_total", "counter", 1, outcome="accepted",
        runtime_id=record.runtime_id, generation=record.generation,
    )
    log.info(
        "runtime provisioned",
        extra={"event": "runtime.lifecycle.provisioned", "runtime_id": record.runtime_id,
               "generation": record.generation, "runtime_state": record.actual_state.value,
               "outcome": "success"},
    )
    emit_metric(
        "runtime_transition_total", "counter", 1, outcome="success",
        runtime_id=record.runtime_id, generation=record.generation,
        runtime_state=record.actual_state.value,
    )


def _record_capacity_denial() -> None:
    log.warning(
        "runtime admission rejected by capacity",
        extra={"event": "runtime.admission.rejected", "limit_kind": "runtime_capacity",
               "error_code": "resource_exhausted", "outcome": "rejected"},
    )
    emit_metric(
        "runtime_admission_total", "counter", 1, outcome="rejected",
        limit_kind="runtime_capacity", error_code="resource_exhausted",
    )
from modules.runtime_manager.product_repository import OperationResult
from modules.runtime_manager.gateway_projection import ProjectionResult


class AsyncRuntimeProductRepository(Protocol):
    async def find_activation(self, user_id: str, agentee_id: str, activation_key: str) -> Optional[RuntimeProductRecord]: ...
    async def get(self, runtime_id: str) -> Optional[RuntimeProductRecord]: ...
    async def get_generation(self, runtime_id: str, generation: int) -> Optional[RuntimeProductRecord]: ...
    async def list_active(self) -> Sequence[RuntimeProductRecord]: ...
    async def list_failed_desired_running(self) -> Sequence[RuntimeProductRecord]: ...
    async def diagnostic_quarantined_count(self) -> int: ...
    async def claim_input_operation(self, owner_ref: str, operation_id: str, fingerprint: str) -> Mapping[str, Any]: ...
    async def claim_roll_operation(self, owner_ref: str, operation_id: str, fingerprint: str,
                                   intent: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def complete_input_operation(self, owner_ref: str, operation_id: str, result: Mapping[str, Any]) -> None: ...
    async def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]: ...
    async def ensure(self, record: RuntimeProductRecord, activation_intent_digest: str) -> RuntimeProductRecord: ...
    async def compare_and_swap(self, record: RuntimeProductRecord, expected_version: int) -> RuntimeProductRecord: ...
    async def claim_idle_stop(
        self, runtime_id: str, generation: int, expected_version: int,
        expected_state: RuntimeState, cutoff: datetime,
    ) -> Optional[RuntimeProductRecord]: ...
    async def compare_and_swap_with_operation(
        self, record: RuntimeProductRecord, expected_version: int, operation: OperationResult
    ) -> RuntimeProductRecord: ...
    async def acquire_roll_lease(self, runtime_id: str, operation_id: str, holder_token: str,
                                 operation_fingerprint: str, expected_generation: int,
                                 target_generation: int, target_config_ref: str,
                                 target_config_hash: str) -> Optional[int]: ...
    async def renew_roll_lease(self, runtime_id: str, holder_token: str, fencing_epoch: int) -> bool: ...
    async def validate_roll_lease(self, runtime_id: str, holder_token: str, fencing_epoch: int) -> bool: ...
    async def release_roll_lease(self, runtime_id: str, holder_token: str, fencing_epoch: int) -> None: ...
    async def complete_roll_operation(self, runtime_id: str, holder_token: str,
                                      fencing_epoch: int, owner_ref: str,
                                      operation_id: str, operation_fingerprint: str,
                                      target_generation: int, target_config_ref: str,
                                      target_config_hash: str,
                                      result: Mapping[str, Any]) -> bool: ...
    async def fenced_compare_and_swap(self, record: RuntimeProductRecord, expected_version: int,
                                      holder_token: str, fencing_epoch: int) -> RuntimeProductRecord: ...
    async def fenced_ensure(self, record: RuntimeProductRecord, activation_intent_digest: str,
                            holder_token: str, fencing_epoch: int,
                            adapter_config_hash: str) -> RuntimeProductRecord: ...
    async def fenced_record_adapter_config_hash(self, runtime_id: str, generation: int,
                                                adapter_config_ref: str, adapter_config_hash: str,
                                                holder_token: str, fencing_epoch: int) -> bool: ...


class InMemoryRuntimeAdmissionRepository:
    """Small async reference repository for admission contract tests only."""

    def __init__(self, admission_policy: RuntimeAdmissionPolicy | None = None) -> None:
        self._admission_policy = admission_policy or RuntimeAdmissionPolicy()
        self._records: dict[tuple[str, int], RuntimeProductRecord] = {}
        self._digests: dict[tuple[str, int], str] = {}
        self._lock = asyncio.Lock()

    async def ensure(
        self, record: RuntimeProductRecord, activation_intent_digest: str
    ) -> RuntimeProductRecord:
        if len(activation_intent_digest) != 64:
            raise ValueError("activation intent digest must be sha256")
        async with self._lock:
            previous = next((
                item for item in sorted(
                    self._records.values(), key=lambda item: item.generation, reverse=True
                )
                if item.spec.user_id == record.spec.user_id
                and item.spec.agentee_id == record.spec.agentee_id
                and item.spec.activation_key == record.spec.activation_key
            ), None)
            if previous is not None and previous.actual_state is not RuntimeState.STOPPED:
                if self._digests[(previous.runtime_id, previous.generation)] != activation_intent_digest:
                    raise Conflict("activation key reused with a different stable startup intent")
                return previous
            if previous is not None:
                if previous.runtime_id != record.runtime_id:
                    raise Conflict("activation must retain the same runtime_id")
                if previous.generation + 1 != record.generation:
                    raise Conflict("activation generation must increment by exactly one")
            elif record.generation != 1:
                raise Conflict("first activation generation must be one")
            active_same_runtime = next((
                item for item in self._records.values()
                if item.runtime_id == record.runtime_id
                and item.actual_state is not RuntimeState.STOPPED
            ), None)
            if active_same_runtime is not None:
                raise Conflict("runtime generation or active activation already exists")
            if (record.runtime_id, record.generation) in self._records:
                raise Conflict("runtime generation or active activation already exists")
            active = tuple(item for item in self._records.values() if item.active)
            if (
                len(active) >= self._admission_policy.max_active_global
                or sum(item.spec.user_id == record.spec.user_id for item in active)
                >= self._admission_policy.max_active_per_user
            ):
                _record_capacity_denial()
                raise ResourceExhausted("runtime capacity is unavailable")
            self._records[(record.runtime_id, record.generation)] = record
            self._digests[(record.runtime_id, record.generation)] = activation_intent_digest
            _record_admission(record)
            return record

    async def list_active(self) -> Sequence[RuntimeProductRecord]:
        return tuple(item for item in self._records.values() if item.active)

    async def diagnostic_quarantined_count(self) -> int:
        latest: dict[str, RuntimeProductRecord] = {}
        for item in self._records.values():
            previous = latest.get(item.runtime_id)
            if previous is None or item.generation > previous.generation:
                latest[item.runtime_id] = item
        return sum(
            bool(item.exit_reason) and (
                item.exit_reason.startswith("quarantine:")
                or item.exit_reason == "quarantine_unknown"
            )
            for item in latest.values()
        )


class AsyncBindingRepository(Protocol):
    async def get(self, binding_id: str) -> Optional[RuntimeBinding]: ...
    async def find_for_runtime(self, runtime_id: str, generation: int) -> Optional[RuntimeBinding]: ...
    async def find_for_game_session(self, game_session_id: str) -> Optional[RuntimeBinding]: ...
    async def find_for_gateway_event(self, game_session_id: str, owner_user_id: str) -> Optional[RuntimeBinding]: ...
    async def find_for_manager_projection(
        self, game_session_id: str, owner_user_id: str
    ) -> Optional[RuntimeBinding]: ...
    async def expire_due(self, limit: int = 50) -> Sequence[RuntimeBinding]: ...
    async def list_expired_for_projection(self, limit: int = 50) -> Sequence[RuntimeBinding]: ...
    async def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]: ...
    async def insert_with_operation(self, binding: RuntimeBinding, operation: OperationResult) -> RuntimeBinding: ...
    async def transition_with_operation(
        self,
        binding: RuntimeBinding,
        expected_version: int,
        operation: OperationResult,
        revocation: Optional[AuthorityRevocationIntent],
        issuance: Optional[AuthorityIssuanceIntent] = None,
    ) -> RuntimeBinding: ...
    async def claim_pending_intents(self, limit: int = 50) -> Sequence[Mapping[str, Any]]: ...
    async def acknowledge_intent(self, intent_id: str) -> None: ...
    async def retry_intent(self, intent_id: str, error: str, delay_seconds: int) -> None: ...
    async def is_intent_processed(self, intent_id: str) -> bool: ...
    async def latest_revocation_intent_id(
        self, binding_id: str, through_version: int, through_epoch: int,
    ) -> Optional[str]: ...


class AsyncGatewayProjectionRepository(Protocol):
    async def has_message(self, message_id: str) -> bool: ...
    async def get(self, game_session_id: str) -> Optional[GameSessionSnapshot]: ...
    async def find_available_for_owner(self, owner_user_id: str) -> Optional[GameSessionSnapshot]: ...
    async def consume(
        self, event: GatewayEvent, recovery_snapshot: Optional[GameSessionSnapshot] = None
    ) -> ProjectionResult: ...


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _value(row: Mapping[str, Any], key: str) -> Any:
    return row[key]


def _datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _runtime_from_row(row: Mapping[str, Any]) -> RuntimeProductRecord:
    budget = _value(row, "resource_budget")
    capabilities = _value(row, "allowed_capabilities")
    if isinstance(budget, str):
        budget = json.loads(budget)
    if isinstance(capabilities, str):
        capabilities = json.loads(capabilities)
    spec = RuntimeProductSpec(
        runtime_id=_value(row, "runtime_id"),
        generation=_value(row, "generation"),
        activation_key=_value(row, "activation_key"),
        user_id=_value(row, "user_id"),
        agentee_id=_value(row, "agentee_id"),
        adapter_kind=_value(row, "adapter_kind"),
        adapter_config_ref=_value(row, "adapter_config_ref"),
        persona_artifact_ref=_value(row, "persona_artifact_ref"),
        resource_budget=budget,
        idle_ttl_seconds=_value(row, "idle_ttl_seconds"),
        allowed_capabilities=tuple(capabilities),
        contract_version=_value(row, "contract_version"),
    )
    return RuntimeProductRecord(
        spec=spec,
        desired_state=DesiredState(_value(row, "desired_state")),
        actual_state=RuntimeState(_value(row, "actual_state")),
        status_version=_value(row, "status_version"),
        ready=_value(row, "ready"),
        last_effective_activity_at=_datetime(_value(row, "last_effective_activity_at")),
        started_at=_datetime(_value(row, "started_at")),
        exit_reason=_value(row, "exit_reason"),
    )


def _binding_from_row(row: Mapping[str, Any]) -> RuntimeBinding:
    return RuntimeBinding(
        binding_id=_value(row, "binding_id"),
        runtime_id=_value(row, "runtime_id"),
        generation=_value(row, "generation"),
        user_id=_value(row, "user_id"),
        agentee_id=_value(row, "agentee_id"),
        binding_version=_value(row, "binding_version"),
        lease_epoch=_value(row, "lease_epoch"),
        status=BindingState(_value(row, "status")),
        game_session_id=_value(row, "game_session_id"),
        game_instance_nonce=_value(row, "game_instance_nonce"),
        capability_digest=_value(row, "capability_digest"),
        resume_identity=_value(row, "resume_identity"),
        bound_at=_datetime(_value(row, "bound_at")),
        expires_at=_datetime(_value(row, "expires_at")),
        suspended_at=_datetime(_value(row, "suspended_at")),
        revoked_at=_datetime(_value(row, "revoked_at")),
        contract_version=_value(row, "contract_version"),
    )


def _snapshot_from_row(row: Mapping[str, Any]) -> GameSessionSnapshot:
    return GameSessionSnapshot(
        row["game_session_id"], row["game_instance_nonce"], row["owner_user_id"], row["connection_id"],
        row["session_version"], row["capability_digest"], row["available"], row["resumable"],
        row["resume_identity"],
    )


def _runtime_payload(record: RuntimeProductRecord, activation_intent_digest: str = "") -> dict[str, Any]:
    return {
        "runtime_id": record.runtime_id,
        "generation": record.generation,
        "activation_key": record.spec.activation_key,
        "user_id": record.spec.user_id,
        "agentee_id": record.spec.agentee_id,
        "adapter_kind": record.spec.adapter_kind,
        "adapter_config_ref": record.spec.adapter_config_ref,
        "persona_artifact_ref": record.spec.persona_artifact_ref,
        "resource_budget": dict(record.spec.resource_budget),
        "idle_ttl_seconds": record.spec.idle_ttl_seconds,
        "allowed_capabilities": list(record.spec.allowed_capabilities),
        "activation_intent_digest": activation_intent_digest,
        "contract_version": record.spec.contract_version,
        "desired_state": record.desired_state.value,
        "actual_state": record.actual_state.value,
        "status_version": record.status_version,
        "ready": record.ready,
        "last_effective_activity_at": record.last_effective_activity_at,
        "started_at": record.started_at,
        "exit_reason": record.exit_reason,
    }


def _binding_payload(binding: RuntimeBinding) -> dict[str, Any]:
    payload = asdict(binding)
    payload["status"] = binding.status.value
    return payload


def _operation_result(kind: str, value: RuntimeProductRecord | RuntimeBinding) -> str:
    payload = _runtime_payload(value) if kind == "runtime" else _binding_payload(value)
    return _json(payload)


class AsyncpgRuntimeProductRepository:
    _ADMISSION_LOCK_KEY = 0x53544152434F504C  # "STARCOPL", stable signed bigint.

    def __init__(self, pool: Any, admission_policy: RuntimeAdmissionPolicy | None = None) -> None:
        self._pool = pool
        self._admission_policy = admission_policy or RuntimeAdmissionPolicy()

    async def _acquire_global_admission_lock(self, conn: Any) -> None:
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1::bigint)", self._ADMISSION_LOCK_KEY
        )

    async def _enforce_admission_capacity(self, conn: Any, user_id: str) -> None:
        active_global, active_for_user = await conn.fetchrow(
            """SELECT
                 count(*) FILTER (WHERE actual_state NOT IN ('stopped','failed')) AS active_global,
                 count(*) FILTER (
                   WHERE user_id=$1 AND actual_state NOT IN ('stopped','failed')
                 ) AS active_for_user
               FROM public.runtime_instances""",
            user_id,
        )
        if (
            active_global >= self._admission_policy.max_active_global
            or active_for_user >= self._admission_policy.max_active_per_user
        ):
            _record_capacity_denial()
            raise ResourceExhausted("runtime capacity is unavailable")

    async def find_activation(self, user_id: str, agentee_id: str, activation_key: str) -> Optional[RuntimeProductRecord]:
        row = await self._pool.fetchrow(
            """SELECT * FROM public.runtime_instances
               WHERE user_id=$1 AND agentee_id=$2 AND activation_key=$3
               ORDER BY generation DESC LIMIT 1""",
            user_id, agentee_id, activation_key,
        )
        return _runtime_from_row(row) if row else None

    async def get(self, runtime_id: str) -> Optional[RuntimeProductRecord]:
        row = await self._pool.fetchrow(
            "SELECT * FROM public.runtime_instances WHERE runtime_id=$1 ORDER BY generation DESC LIMIT 1",
            runtime_id,
        )
        return _runtime_from_row(row) if row else None

    async def get_generation(self, runtime_id: str, generation: int) -> Optional[RuntimeProductRecord]:
        row = await self._pool.fetchrow(
            "SELECT * FROM public.runtime_instances WHERE runtime_id=$1 AND generation=$2", runtime_id, generation
        )
        return _runtime_from_row(row) if row else None

    async def list_active(self) -> Sequence[RuntimeProductRecord]:
        rows = await self._pool.fetch(
            """SELECT * FROM public.runtime_instances
               WHERE actual_state NOT IN ('stopped','failed') ORDER BY runtime_id,generation"""
        )
        return tuple(_runtime_from_row(row) for row in rows)

    async def list_failed_desired_running(self) -> Sequence[RuntimeProductRecord]:
        rows = await self._pool.fetch(
            """SELECT * FROM public.runtime_instances
               WHERE actual_state = 'failed' AND desired_state = 'running'
               ORDER BY runtime_id,generation"""
        )
        return tuple(_runtime_from_row(row) for row in rows)

    async def diagnostic_quarantined_count(self) -> int:
        return int(await self._pool.fetchval(
            """SELECT count(*)::bigint FROM (
                 SELECT DISTINCT ON (runtime_id) runtime_id, exit_reason
                 FROM public.runtime_instances ORDER BY runtime_id, generation DESC
               ) latest
               WHERE exit_reason LIKE 'quarantine:%' OR exit_reason = 'quarantine_unknown'"""
        ))

    async def claim_input_operation(self, owner_ref: str, operation_id: str, fingerprint: str) -> Mapping[str, Any]:
        async with self._pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchrow(
                """INSERT INTO public.runtime_operations(owner_ref,operation_id,fingerprint,result_kind,result_payload)
                   VALUES($1,$2,$3,'input','{"status":"pending"}'::jsonb)
                   ON CONFLICT(owner_ref,operation_id) DO NOTHING RETURNING result_payload""",
                owner_ref, operation_id, fingerprint,
            )
            if inserted:
                return {"status": "claimed"}
            row = await conn.fetchrow(
                """SELECT fingerprint,result_kind,result_payload FROM public.runtime_operations
                   WHERE owner_ref=$1 AND operation_id=$2 FOR UPDATE""", owner_ref, operation_id,
            )
            if row["fingerprint"] != fingerprint or row["result_kind"] != "input":
                raise Conflict("operation_id reused with different input")
            payload = row["result_payload"]
            return json.loads(payload) if isinstance(payload, str) else dict(payload)

    async def claim_roll_operation(self, owner_ref: str, operation_id: str, fingerprint: str,
                                   intent: Mapping[str, Any]) -> Mapping[str, Any]:
        pending = {"status":"pending", "intent":dict(intent)}
        async with self._pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchrow(
                """INSERT INTO public.runtime_operations(owner_ref,operation_id,fingerprint,result_kind,result_payload)
                   VALUES($1,$2,$3,'input',$4::jsonb) ON CONFLICT(owner_ref,operation_id) DO NOTHING
                   RETURNING result_payload""", owner_ref, operation_id, fingerprint, _json(pending))
            if inserted: return {"status":"claimed", "intent":dict(intent)}
            row = await conn.fetchrow(
                """SELECT fingerprint,result_kind,result_payload FROM public.runtime_operations
                   WHERE owner_ref=$1 AND operation_id=$2 FOR UPDATE""", owner_ref, operation_id)
            payload = row["result_payload"]
            payload = json.loads(payload) if isinstance(payload, str) else dict(payload)
            if row["fingerprint"] != fingerprint or row["result_kind"] != "input":
                raise Conflict("operation_id reused with different input")
            if payload.get("status") == "pending" and payload.get("intent") != dict(intent):
                raise Conflict("roll operation durable intent mismatch")
            return payload

    async def complete_input_operation(self, owner_ref: str, operation_id: str,
                                       result: Mapping[str, Any]) -> None:
        updated = await self._pool.execute(
            """UPDATE public.runtime_operations SET result_payload=$3::jsonb
               WHERE owner_ref=$1 AND operation_id=$2 AND result_kind='input'
                 AND result_payload->>'status'='pending'""",
            owner_ref, operation_id, _json({"status": "completed", "result": dict(result)}),
        )
        if updated != "UPDATE 1":
            row = await self._pool.fetchrow(
                "SELECT result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2",
                owner_ref, operation_id,
            )
            if row is None:
                raise Conflict("input operation claim missing")

    async def acquire_roll_lease(self, runtime_id: str, operation_id: str, holder_token: str,
                                 operation_fingerprint: str, expected_generation: int,
                                 target_generation: int, target_config_ref: str,
                                 target_config_hash: str) -> Optional[int]:
        row = await self._pool.fetchrow(
            """INSERT INTO public.runtime_config_roll_leases
                 (runtime_id,operation_id,holder_token,fencing_epoch,expires_at,operation_fingerprint,
                  expected_generation,target_generation,target_config_ref,target_config_hash)
               VALUES($1,$2,$3,1,now()+interval '30 seconds',$4,$5,$6,$7,$8)
               ON CONFLICT(runtime_id) DO UPDATE SET operation_id=excluded.operation_id,
                 holder_token=excluded.holder_token,
                 fencing_epoch=runtime_config_roll_leases.fencing_epoch+1,
                 expires_at=excluded.expires_at,operation_fingerprint=excluded.operation_fingerprint,
                 expected_generation=excluded.expected_generation,target_generation=excluded.target_generation,
                 target_config_ref=excluded.target_config_ref,target_config_hash=excluded.target_config_hash
               WHERE runtime_config_roll_leases.expires_at < now()
               RETURNING fencing_epoch""", runtime_id, operation_id, holder_token,
            operation_fingerprint, expected_generation, target_generation, target_config_ref, target_config_hash,
        )
        return int(row["fencing_epoch"]) if row else None

    async def renew_roll_lease(self, runtime_id: str, holder_token: str, fencing_epoch: int) -> bool:
        result = await self._pool.execute(
            """UPDATE public.runtime_config_roll_leases SET expires_at=now()+interval '30 seconds'
               WHERE runtime_id=$1 AND holder_token=$2 AND fencing_epoch=$3 AND expires_at>now()""",
            runtime_id, holder_token, fencing_epoch,
        )
        return result == "UPDATE 1"

    async def validate_roll_lease(self, runtime_id: str, holder_token: str, fencing_epoch: int) -> bool:
        return bool(await self._pool.fetchval(
            """SELECT EXISTS(SELECT 1 FROM public.runtime_config_roll_leases
               WHERE runtime_id=$1 AND holder_token=$2 AND fencing_epoch=$3 AND expires_at>now())""",
            runtime_id, holder_token, fencing_epoch,
        ))

    async def release_roll_lease(self, runtime_id: str, holder_token: str, fencing_epoch: int) -> None:
        await self._pool.execute(
            """DELETE FROM public.runtime_config_roll_leases
               WHERE runtime_id=$1 AND holder_token=$2 AND fencing_epoch=$3""",
            runtime_id, holder_token, fencing_epoch,
        )

    async def complete_roll_operation(self, runtime_id: str, holder_token: str,
                                      fencing_epoch: int, owner_ref: str,
                                      operation_id: str, operation_fingerprint: str,
                                      target_generation: int, target_config_ref: str,
                                      target_config_hash: str,
                                      result: Mapping[str, Any]) -> bool:
        updated = await self._pool.execute(
            """UPDATE public.runtime_operations op SET result_payload=$10::jsonb
               WHERE owner_ref=$4 AND operation_id=$5 AND result_kind='input'
                 AND fingerprint=$6
                 AND result_payload->>'status'='pending'
                 AND (result_payload->'intent'->>'target_generation')::bigint=$7
                 AND result_payload->'intent'->>'target_config_ref'=$8
                 AND result_payload->'intent'->>'target_config_hash'=$9
                 AND EXISTS(SELECT 1 FROM public.runtime_config_roll_leases
                   WHERE runtime_id=$1 AND holder_token=$2 AND fencing_epoch=$3 AND expires_at>now()
                     AND operation_fingerprint=$6 AND target_generation=$7
                     AND target_config_ref=$8 AND target_config_hash=$9)
                 AND EXISTS(SELECT 1 FROM public.runtime_instances r
                   WHERE r.runtime_id=$1 AND r.generation=$7 AND r.adapter_config_ref=$8
                     AND r.adapter_config_hash=$9
                     AND r.ready=true AND r.actual_state IN ('ready_unbound','running_bound','idle'))""",
            runtime_id, holder_token, fencing_epoch, owner_ref, operation_id, operation_fingerprint,
            target_generation, target_config_ref, target_config_hash,
            _json({"status":"completed","result":dict(result)}),
        )
        return updated == "UPDATE 1"

    async def fenced_compare_and_swap(self, record: RuntimeProductRecord, expected_version: int,
                                      holder_token: str, fencing_epoch: int) -> RuntimeProductRecord:
        values = _runtime_payload(record)
        row = await self._pool.fetchrow(
            """UPDATE public.runtime_instances r SET desired_state=$3,actual_state=$4,ready=$5,
               last_effective_activity_at=$6,started_at=$7,exit_reason=$8,
               status_version=status_version+1,updated_at=now()
               WHERE r.runtime_id=$1 AND r.generation=$2 AND r.status_version=$9
                 AND EXISTS(SELECT 1 FROM public.runtime_config_roll_leases l
                   WHERE l.runtime_id=$1 AND l.holder_token=$10 AND l.fencing_epoch=$11 AND l.expires_at>now())
               RETURNING r.*""", record.runtime_id, record.generation, values["desired_state"],
            values["actual_state"], values["ready"], values["last_effective_activity_at"],
            values["started_at"], values["exit_reason"], expected_version, holder_token, fencing_epoch,
        )
        if not row: raise Conflict("roll lease lost or runtime status_version mismatch")
        return _runtime_from_row(row)

    async def fenced_ensure(self, record: RuntimeProductRecord, activation_intent_digest: str,
                            holder_token: str, fencing_epoch: int,
                            adapter_config_hash: str) -> RuntimeProductRecord:
        if not await self.validate_roll_lease(record.runtime_id, holder_token, fencing_epoch):
            raise Conflict("roll lease lost")
        # ensure owns an advisory-lock transaction; re-checking the lease in that transaction
        # prevents an expired holder from inserting after a successor acquires the fence.
        values = _runtime_payload(record, activation_intent_digest)
        async with self._pool.acquire() as conn, conn.transaction():
            # Keep one global-first lock order across normal and fenced inserts.
            await self._acquire_global_admission_lock(conn)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", record.runtime_id)
            valid = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM public.runtime_config_roll_leases
                   WHERE runtime_id=$1 AND holder_token=$2 AND fencing_epoch=$3 AND expires_at>now())""",
                record.runtime_id, holder_token, fencing_epoch)
            if not valid: raise Conflict("roll lease lost")
            await self._enforce_admission_capacity(conn, record.spec.user_id)
            row = await conn.fetchrow(
                """INSERT INTO public.runtime_instances(runtime_id,generation,activation_key,user_id,agentee_id,
                   adapter_kind,adapter_config_ref,persona_artifact_ref,resource_budget,idle_ttl_seconds,
                   allowed_capabilities,activation_intent_digest,contract_version,desired_state,actual_state,
                   status_version,ready,last_effective_activity_at,started_at,exit_reason,adapter_config_hash)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
                   RETURNING *""", values["runtime_id"], values["generation"], values["activation_key"],
                values["user_id"], values["agentee_id"], values["adapter_kind"], values["adapter_config_ref"],
                values["persona_artifact_ref"], _json(values["resource_budget"]), values["idle_ttl_seconds"],
                _json(values["allowed_capabilities"]), values["activation_intent_digest"], values["contract_version"],
                values["desired_state"], values["actual_state"], values["status_version"], values["ready"],
                values["last_effective_activity_at"], values["started_at"], values["exit_reason"], adapter_config_hash)
        admitted = _runtime_from_row(row)
        _record_admission(admitted)
        return admitted

    async def fenced_record_adapter_config_hash(self, runtime_id: str, generation: int,
                                                adapter_config_ref: str, adapter_config_hash: str,
                                                holder_token: str, fencing_epoch: int) -> bool:
        result = await self._pool.execute(
            """UPDATE public.runtime_instances r SET adapter_config_hash=$4,updated_at=now()
               WHERE r.runtime_id=$1 AND r.generation=$2 AND r.adapter_config_ref=$3
                 AND (r.adapter_config_hash IS NULL OR r.adapter_config_hash=$4)
                 AND EXISTS(SELECT 1 FROM public.runtime_config_roll_leases l
                   WHERE l.runtime_id=$1 AND l.holder_token=$5 AND l.fencing_epoch=$6 AND l.expires_at>now())""",
            runtime_id, generation, adapter_config_ref, adapter_config_hash, holder_token, fencing_epoch)
        return result == "UPDATE 1"

    async def compare_and_swap(self, record: RuntimeProductRecord, expected_version: int) -> RuntimeProductRecord:
        values = _runtime_payload(record)
        row = await self._pool.fetchrow(
            """UPDATE public.runtime_instances SET desired_state=$3,actual_state=$4,ready=$5,
               last_effective_activity_at=$6,started_at=$7,exit_reason=$8,status_version=status_version+1,updated_at=now()
               WHERE runtime_id=$1 AND generation=$2 AND status_version=$9 RETURNING *""",
            record.runtime_id, record.generation, values["desired_state"], values["actual_state"], values["ready"],
            values["last_effective_activity_at"], values["started_at"], values["exit_reason"], expected_version,
        )
        if not row:
            raise Conflict("runtime status_version mismatch")
        return _runtime_from_row(row)

    async def claim_idle_stop(
        self, runtime_id: str, generation: int, expected_version: int,
        expected_state: RuntimeState, cutoff: datetime,
    ) -> Optional[RuntimeProductRecord]:
        """Atomically fence an idle runtime before process-side stop work.

        The exact state predicate is deliberate: a candidate snapshot cannot
        claim a runtime that concurrently changed state without also changing
        status_version due to a future faulty writer.
        """
        if expected_state not in {
            RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
        }:
            return None
        row = await self._pool.fetchrow(
            """UPDATE public.runtime_instances SET desired_state='stopped',actual_state='draining',
                 status_version=status_version+1,updated_at=now()
               WHERE runtime_id=$1 AND generation=$2 AND status_version=$3
                 AND desired_state='running' AND actual_state=$4
                 AND actual_state IN ('ready_unbound','running_bound','idle')
                 AND COALESCE(last_effective_activity_at,started_at) IS NOT NULL
                 AND COALESCE(last_effective_activity_at,started_at) <= $5
               RETURNING *""",
            runtime_id, generation, expected_version, expected_state.value, cutoff,
        )
        return _runtime_from_row(row) if row else None

    async def ensure(self, record: RuntimeProductRecord, activation_intent_digest: str) -> RuntimeProductRecord:
        if len(activation_intent_digest) != 64:
            raise ValueError("activation intent digest must be sha256")
        values = _runtime_payload(record, activation_intent_digest)
        async with self._pool.acquire() as conn, conn.transaction():
            # Every new activation takes this fixed lock first.  This serializes
            # quota reads across workers and establishes one lock order before
            # the narrower logical-activation lock below.
            await self._acquire_global_admission_lock(conn)
            # Serialize all generations of the same logical activation even when no row exists yet.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"{record.spec.user_id}\x1f{record.spec.agentee_id}\x1f{record.spec.activation_key}",
            )
            previous = await conn.fetchrow(
                """SELECT * FROM public.runtime_instances
                   WHERE user_id=$1 AND agentee_id=$2 AND activation_key=$3
                   ORDER BY generation DESC LIMIT 1 FOR UPDATE""",
                record.spec.user_id, record.spec.agentee_id, record.spec.activation_key,
            )
            if previous:
                if previous["actual_state"] != RuntimeState.STOPPED.value:
                    if previous["activation_intent_digest"] != activation_intent_digest:
                        raise Conflict("activation key reused with a different stable startup intent")
                    return _runtime_from_row(previous)
                if previous["runtime_id"] != record.runtime_id:
                    raise Conflict("activation must retain the same runtime_id")
                if previous["generation"] + 1 != record.generation:
                    raise Conflict("activation generation must increment by exactly one")
            elif record.generation != 1:
                raise Conflict("first activation generation must be one")
            await self._enforce_admission_capacity(conn, record.spec.user_id)
            try:
                row = await conn.fetchrow(
                    """INSERT INTO public.runtime_instances (
                      runtime_id,generation,activation_key,user_id,agentee_id,adapter_kind,adapter_config_ref,
                      persona_artifact_ref,resource_budget,idle_ttl_seconds,allowed_capabilities,activation_intent_digest,contract_version,
                      desired_state,actual_state,status_version,ready,last_effective_activity_at,started_at,exit_reason
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                    RETURNING *""",
                    values["runtime_id"], values["generation"], values["activation_key"], values["user_id"],
                    values["agentee_id"], values["adapter_kind"], values["adapter_config_ref"],
                    values["persona_artifact_ref"], _json(values["resource_budget"]), values["idle_ttl_seconds"],
                    _json(values["allowed_capabilities"]), values["activation_intent_digest"], values["contract_version"],
                    values["desired_state"], values["actual_state"], values["status_version"], values["ready"],
                    values["last_effective_activity_at"], values["started_at"], values["exit_reason"],
                )
            except Exception as error:
                if getattr(error, "sqlstate", None) == "23505":
                    raise Conflict("runtime generation or active activation already exists") from error
                raise
        admitted = _runtime_from_row(row)
        _record_admission(admitted)
        return admitted

    async def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]:
        row = await self._pool.fetchrow(
            "SELECT fingerprint,result_kind,result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2",
            owner_ref, operation_id,
        )
        if not row:
            return None
        payload = row["result_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if row["result_kind"] == "runtime":
            result = _runtime_from_row(payload)
        elif row["result_kind"] == "binding":
            result = _binding_from_row(payload)
        else:
            result = payload
        return OperationResult(owner_ref, operation_id, row["fingerprint"], result)

    async def compare_and_swap_with_operation(
        self, record: RuntimeProductRecord, expected_version: int, operation: OperationResult
    ) -> RuntimeProductRecord:
        values = _runtime_payload(record)
        async with self._pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                "SELECT fingerprint,result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2 FOR UPDATE",
                operation.owner_ref, operation.operation_id,
            )
            if existing:
                if existing["fingerprint"] != operation.fingerprint:
                    raise Conflict("operation_id reused with different payload")
                payload = existing["result_payload"]
                return _runtime_from_row(json.loads(payload) if isinstance(payload, str) else payload)
            row = await conn.fetchrow(
                """UPDATE public.runtime_instances SET desired_state=$3,actual_state=$4,ready=$5,
                   last_effective_activity_at=$6,started_at=$7,exit_reason=$8,status_version=status_version+1,updated_at=now()
                   WHERE runtime_id=$1 AND generation=$2 AND status_version=$9 RETURNING *""",
                record.runtime_id, record.generation, values["desired_state"], values["actual_state"], values["ready"],
                values["last_effective_activity_at"], values["started_at"], values["exit_reason"], expected_version,
            )
            if not row:
                duplicate = await conn.fetchrow(
                    "SELECT fingerprint,result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2",
                    operation.owner_ref, operation.operation_id,
                )
                if duplicate and duplicate["fingerprint"] == operation.fingerprint:
                    payload = duplicate["result_payload"]
                    return _runtime_from_row(json.loads(payload) if isinstance(payload, str) else payload)
                raise Conflict("runtime status_version mismatch")
            result = _runtime_from_row(row)
            await conn.execute(
                """INSERT INTO public.runtime_operations(owner_ref,operation_id,fingerprint,result_kind,result_payload)
                   VALUES($1,$2,$3,'runtime',$4::jsonb)""",
                operation.owner_ref, operation.operation_id, operation.fingerprint, _operation_result("runtime", result),
            )
            return result


class AsyncpgBindingRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(self, binding_id: str) -> Optional[RuntimeBinding]:
        row = await self._pool.fetchrow("SELECT * FROM public.runtime_bindings WHERE binding_id=$1", binding_id)
        return _binding_from_row(row) if row else None

    async def find_for_runtime(self, runtime_id: str, generation: int) -> Optional[RuntimeBinding]:
        row = await self._pool.fetchrow(
            "SELECT * FROM public.runtime_bindings WHERE runtime_id=$1 AND generation=$2", runtime_id, generation
        )
        return _binding_from_row(row) if row else None

    async def find_for_game_session(self, game_session_id: str) -> Optional[RuntimeBinding]:
        row = await self._pool.fetchrow(
            """SELECT * FROM public.runtime_bindings WHERE game_session_id=$1
               AND status IN ('pending','active','suspended') ORDER BY binding_version DESC LIMIT 1""",
            game_session_id,
        )
        return _binding_from_row(row) if row else None

    async def find_for_gateway_event(self, game_session_id: str, owner_user_id: str) -> Optional[RuntimeBinding]:
        """Resolve an existing session binding or the owner's sole unbound intent.

        Gateway owns ``game_session_id`` so a PENDING binding cannot contain it
        before the authenticated hello.  Ambiguous pending intents fail closed.
        The subsequent versioned transition remains the atomic claim.
        """
        rows = await self._pool.fetch(
            """SELECT * FROM public.runtime_bindings
               WHERE status IN ('pending','active','suspended')
                 AND (game_session_id=$1 OR (status='pending' AND game_session_id IS NULL AND user_id=$2))
               ORDER BY (game_session_id=$1) DESC, binding_version DESC LIMIT 2""",
            game_session_id, owner_user_id,
        )
        exact = [row for row in rows if row["game_session_id"] == game_session_id]
        if exact:
            return _binding_from_row(exact[0])
        if len(rows) > 1:
            raise Conflict("ambiguous pending binding for gateway owner")
        return _binding_from_row(rows[0]) if rows else None

    async def find_for_manager_projection(
        self, game_session_id: str, owner_user_id: str
    ) -> Optional[RuntimeBinding]:
        """Read the latest persisted Binding, including terminal states.

        This is a Manager recovery reader only.  Route-facing resolution keeps
        using ``find_for_gateway_event`` and therefore cannot route revoked or
        expired Bindings.
        """
        row = await self._pool.fetchrow(
            """SELECT * FROM public.runtime_bindings
               WHERE game_session_id=$1 AND user_id=$2
               ORDER BY generation DESC, binding_version DESC LIMIT 1""",
            game_session_id, owner_user_id,
        )
        return _binding_from_row(row) if row else None

    async def list_active(self):
        rows = await self._pool.fetch(
            "SELECT * FROM public.runtime_bindings WHERE status='active' ORDER BY binding_id"
        )
        return tuple(_binding_from_row(row) for row in rows)

    async def expire_due(self, limit: int = 50) -> Sequence[RuntimeBinding]:
        """Claim due Bindings using database time and expire them transactionally."""
        async with self._pool.acquire() as conn, conn.transaction():
            candidates = await conn.fetch(
                """SELECT * FROM public.runtime_bindings
                   WHERE status IN ('pending','active','suspended')
                     AND expires_at IS NOT NULL AND expires_at <= now()
                   ORDER BY expires_at,binding_id
                   FOR UPDATE SKIP LOCKED LIMIT $1""",
                limit,
            )
            expired = []
            for candidate in candidates:
                row = await conn.fetchrow(
                    """UPDATE public.runtime_bindings
                       SET status='expired',binding_version=binding_version+1,
                           lease_epoch=lease_epoch+1,updated_at=now()
                       WHERE binding_id=$1 AND binding_version=$2
                         AND status IN ('pending','active','suspended')
                         AND expires_at IS NOT NULL AND expires_at <= now()
                       RETURNING *""",
                    candidate["binding_id"], candidate["binding_version"],
                )
                if row is None:
                    continue
                binding = _binding_from_row(row)
                intent = AuthorityRevocationIntent(
                    f"expire:{binding.binding_id}:{binding.binding_version}",
                    binding.binding_id, binding.binding_version,
                    binding.lease_epoch, "binding_expired",
                )
                await self._insert_intent(conn, intent)
                expired.append(binding)
            return tuple(expired)

    async def list_expired_for_projection(self, limit: int = 50) -> Sequence[RuntimeBinding]:
        rows = await self._pool.fetch(
            """SELECT b.* FROM public.runtime_bindings b
               JOIN public.runtime_instances r
                 ON r.runtime_id=b.runtime_id AND r.generation=b.generation
               WHERE b.status='expired' AND r.desired_state='running'
                 AND r.actual_state IN ('ready_unbound','running_bound')
               ORDER BY b.updated_at,b.binding_id LIMIT $1""",
            limit,
        )
        return tuple(_binding_from_row(row) for row in rows)

    async def get_operation(self, owner_ref: str, operation_id: str) -> Optional[OperationResult]:
        return await AsyncpgRuntimeProductRepository(self._pool).get_operation(owner_ref, operation_id)

    async def insert_with_operation(self, binding: RuntimeBinding, operation: OperationResult) -> RuntimeBinding:
        values = _binding_payload(binding)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"{binding.runtime_id}\x1f{binding.generation}"
            )
            existing = await conn.fetchrow(
                "SELECT fingerprint,result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2 FOR UPDATE",
                operation.owner_ref, operation.operation_id,
            )
            if existing:
                if existing["fingerprint"] != operation.fingerprint:
                    raise Conflict("operation_id reused with different payload")
                payload = existing["result_payload"]
                return _binding_from_row(json.loads(payload) if isinstance(payload, str) else payload)
            current = await conn.fetchrow(
                "SELECT * FROM public.runtime_bindings WHERE runtime_id=$1 AND generation=$2 FOR UPDATE",
                binding.runtime_id, binding.generation,
            )
            if current:
                result = _binding_from_row(current)
                if (
                    result.user_id != binding.user_id
                    or result.agentee_id != binding.agentee_id
                    or (
                        binding.game_session_id is not None
                        and result.game_session_id not in (None, binding.game_session_id)
                    )
                ):
                    raise Conflict("runtime already has a different Binding")
                await conn.execute(
                    """INSERT INTO public.runtime_operations(owner_ref,operation_id,fingerprint,result_kind,result_payload)
                       VALUES($1,$2,$3,'binding',$4::jsonb)""",
                    operation.owner_ref, operation.operation_id, operation.fingerprint,
                    _operation_result("binding", result),
                )
                return result
            row = await conn.fetchrow(
                """INSERT INTO public.runtime_bindings (
                  binding_id,runtime_id,generation,user_id,agentee_id,binding_version,lease_epoch,status,
                  game_session_id,game_instance_nonce,capability_digest,resume_identity,bound_at,expires_at,
                  suspended_at,revoked_at,contract_version
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17) RETURNING *""",
                values["binding_id"], values["runtime_id"], values["generation"], values["user_id"],
                values["agentee_id"], values["binding_version"], values["lease_epoch"], values["status"],
                values["game_session_id"], values["game_instance_nonce"], values["capability_digest"],
                values["resume_identity"], values["bound_at"], values["expires_at"], values["suspended_at"],
                values["revoked_at"], values["contract_version"],
            )
            result = _binding_from_row(row)
            await conn.execute(
                """INSERT INTO public.runtime_operations(owner_ref,operation_id,fingerprint,result_kind,result_payload)
                   VALUES($1,$2,$3,'binding',$4::jsonb)""",
                operation.owner_ref, operation.operation_id, operation.fingerprint, _operation_result("binding", result),
            )
            return result

    async def transition_with_operation(
        self,
        binding: RuntimeBinding,
        expected_version: int,
        operation: OperationResult,
        revocation: Optional[AuthorityRevocationIntent],
        issuance: Optional[AuthorityIssuanceIntent] = None,
    ) -> RuntimeBinding:
        values = _binding_payload(binding)
        async with self._pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                "SELECT fingerprint,result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2 FOR UPDATE",
                operation.owner_ref, operation.operation_id,
            )
            if existing:
                if existing["fingerprint"] != operation.fingerprint:
                    raise Conflict("operation_id reused with different payload")
                payload = existing["result_payload"]
                return _binding_from_row(json.loads(payload) if isinstance(payload, str) else payload)
            row = await conn.fetchrow(
                """UPDATE public.runtime_bindings SET binding_version=$2,lease_epoch=$3,status=$4,
                  game_session_id=$5,game_instance_nonce=$6,capability_digest=$7,resume_identity=$8,bound_at=$9,
                  expires_at=$10,suspended_at=$11,revoked_at=$12,updated_at=now()
                  WHERE binding_id=$1 AND binding_version=$13 RETURNING *""",
                binding.binding_id, values["binding_version"], values["lease_epoch"], values["status"],
                values["game_session_id"], values["game_instance_nonce"], values["capability_digest"],
                values["resume_identity"], values["bound_at"], values["expires_at"], values["suspended_at"],
                values["revoked_at"], expected_version,
            )
            if not row:
                duplicate = await conn.fetchrow(
                    "SELECT fingerprint,result_payload FROM public.runtime_operations WHERE owner_ref=$1 AND operation_id=$2",
                    operation.owner_ref, operation.operation_id,
                )
                if duplicate and duplicate["fingerprint"] == operation.fingerprint:
                    payload = duplicate["result_payload"]
                    return _binding_from_row(json.loads(payload) if isinstance(payload, str) else payload)
                raise Conflict("binding_version mismatch")
            result = _binding_from_row(row)
            await conn.execute(
                """INSERT INTO public.runtime_operations(owner_ref,operation_id,fingerprint,result_kind,result_payload)
                   VALUES($1,$2,$3,'binding',$4::jsonb)""",
                operation.owner_ref, operation.operation_id, operation.fingerprint, _operation_result("binding", result),
            )
            for intent in (revocation, issuance):
                if intent is not None:
                    await self._insert_intent(conn, intent)
            return result

    @staticmethod
    async def _insert_intent(conn: Any, intent: AuthorityRevocationIntent | AuthorityIssuanceIntent) -> None:
        if isinstance(intent, AuthorityRevocationIntent):
            kind = "revoke"
            version = intent.through_binding_version
            epoch = intent.through_lease_epoch
            payload = {"reason": intent.reason}
            binding_id = intent.binding_id
        else:
            kind = "issue"
            version = intent.binding.binding_version
            epoch = intent.binding.lease_epoch
            binding_id = intent.binding.binding_id
            payload = {
                "binding": _binding_payload(intent.binding),
                "scopes": list(intent.scopes),
                "allowed_tools": list(intent.allowed_tools),
                "jti": intent.jti,
            }
        await conn.execute(
            """INSERT INTO public.runtime_authority_outbox(
                 intent_id,intent_kind,binding_id,binding_version,lease_epoch,payload)
               VALUES($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT(intent_id) DO NOTHING""",
            intent.intent_id, kind, binding_id, version, epoch, _json(payload),
        )

    async def claim_pending_intents(self, limit: int = 50) -> Sequence[Mapping[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            return await conn.fetch(
                """WITH pending AS (
                     SELECT candidate.intent_id FROM public.runtime_authority_outbox candidate
                     WHERE candidate.processed_at IS NULL AND candidate.available_at <= now()
                       AND (candidate.claimed_at IS NULL OR candidate.claimed_at < now() - interval '5 minutes')
                       AND NOT EXISTS (
                         SELECT 1 FROM public.runtime_authority_outbox earlier
                         WHERE earlier.binding_id=candidate.binding_id AND earlier.processed_at IS NULL
                           AND (earlier.binding_version,
                                CASE earlier.intent_kind WHEN 'revoke' THEN 0 ELSE 1 END,
                                earlier.created_at, earlier.intent_id)
                             < (candidate.binding_version,
                                CASE candidate.intent_kind WHEN 'revoke' THEN 0 ELSE 1 END,
                                candidate.created_at, candidate.intent_id)
                       )
                     ORDER BY candidate.created_at FOR UPDATE OF candidate SKIP LOCKED LIMIT $1
                   ) UPDATE public.runtime_authority_outbox o
                     SET claimed_at=now(),attempt_count=attempt_count+1
                   FROM pending WHERE o.intent_id=pending.intent_id RETURNING o.*""",
                limit,
            )

    async def acknowledge_intent(self, intent_id: str) -> None:
        await self._pool.execute(
            "UPDATE public.runtime_authority_outbox SET processed_at=now(),claimed_at=NULL,last_error=NULL WHERE intent_id=$1",
            intent_id,
        )

    async def is_intent_processed(self, intent_id: str) -> bool:
        return bool(await self._pool.fetchval(
            """SELECT EXISTS(
                 SELECT 1 FROM public.runtime_authority_outbox
                  WHERE intent_id=$1 AND processed_at IS NOT NULL
               )""",
            intent_id,
        ))

    async def latest_revocation_intent_id(
        self, binding_id: str, through_version: int, through_epoch: int,
    ) -> Optional[str]:
        return await self._pool.fetchval(
            """SELECT intent_id FROM public.runtime_authority_outbox
                WHERE binding_id=$1 AND intent_kind='revoke'
                  AND binding_version >= $2 AND lease_epoch >= $3
                ORDER BY binding_version DESC, lease_epoch DESC, created_at DESC
                LIMIT 1""",
            binding_id, through_version, through_epoch,
        )

    async def retry_intent(self, intent_id: str, error: str, delay_seconds: int) -> None:
        await self._pool.execute(
            """UPDATE public.runtime_authority_outbox SET claimed_at=NULL,last_error=$2,
               available_at=now()+make_interval(secs => $3) WHERE intent_id=$1 AND processed_at IS NULL""",
            intent_id, error[:1000], max(0, delay_seconds),
        )

    async def diagnostic_stats(self) -> Mapping[str, float | int]:
        row = await self._pool.fetchrow(
            """SELECT count(*)::bigint AS pending,
                      count(*) FILTER (WHERE claimed_at IS NOT NULL)::bigint AS claimed,
                      COALESCE(EXTRACT(EPOCH FROM (now()-min(created_at))),0)::double precision AS oldest_age_seconds
               FROM public.runtime_authority_outbox WHERE processed_at IS NULL"""
        )
        return {"pending": int(row["pending"]), "claimed": int(row["claimed"]),
                "oldest_age_seconds": max(0.0, float(row["oldest_age_seconds"]))}


class AsyncpgGatewayProjectionRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def has_message(self, message_id: str) -> bool:
        return bool(await self._pool.fetchval("SELECT EXISTS(SELECT 1 FROM public.gateway_event_dedupe WHERE message_id=$1)", message_id))

    async def get(self, game_session_id: str) -> Optional[GameSessionSnapshot]:
        row = await self._pool.fetchrow(
            "SELECT * FROM public.gateway_session_projection WHERE game_session_id=$1", game_session_id
        )
        if not row:
            return None
        return _snapshot_from_row(row)

    async def find_available_for_owner(self, owner_user_id: str) -> Optional[GameSessionSnapshot]:
        rows = await self._pool.fetch(
            """SELECT * FROM public.gateway_session_projection
               WHERE owner_user_id=$1 AND available=true
               ORDER BY updated_at DESC LIMIT 2""",
            owner_user_id,
        )
        # Multiple simultaneously available sessions are ambiguous.  Binding
        # authority must wait for an explicit session fact instead of guessing.
        return _snapshot_from_row(rows[0]) if len(rows) == 1 else None

    async def consume(
        self, event: GatewayEvent, recovery_snapshot: Optional[GameSessionSnapshot] = None
    ) -> ProjectionResult:
        digest = hashlib.sha256(_json({
            "aggregate_id": event.aggregate_id,
            "aggregate_version": event.aggregate_version,
            "event_type": event.event_type.value,
            "snapshot": asdict(event.snapshot),
        }).encode()).hexdigest()
        async with self._pool.acquire() as conn, conn.transaction():
            claimed = await conn.fetchrow(
                """INSERT INTO public.gateway_event_dedupe(message_id,game_session_id,aggregate_version,payload_digest)
                   VALUES($1,$2,$3,$4) ON CONFLICT(message_id) DO NOTHING RETURNING message_id""",
                event.message_id, event.aggregate_id, event.aggregate_version, digest,
            )
            if not claimed:
                duplicate = await conn.fetchrow(
                    """SELECT game_session_id,aggregate_version,payload_digest FROM public.gateway_event_dedupe
                       WHERE message_id=$1 FOR UPDATE""",
                    event.message_id,
                )
                if (
                    duplicate["game_session_id"] != event.aggregate_id
                    or duplicate["aggregate_version"] != event.aggregate_version
                    or duplicate["payload_digest"] != digest
                ):
                    raise Conflict("gateway message_id reused with different payload")
                current = await conn.fetchrow(
                    "SELECT * FROM public.gateway_session_projection WHERE game_session_id=$1", event.aggregate_id
                )
                return ProjectionResult(_snapshot_from_row(current) if current else None, duplicate=True)

            # A row lock cannot serialize the first event when no projection
            # exists yet, so lock the logical GameSession aggregate as well.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"gateway-session\x1f{event.aggregate_id}",
            )
            current = await conn.fetchrow(
                "SELECT * FROM public.gateway_session_projection WHERE game_session_id=$1 FOR UPDATE",
                event.aggregate_id,
            )
            current_snapshot = _snapshot_from_row(current) if current else None
            current_version = current_snapshot.session_version if current_snapshot else 0
            if event.aggregate_version <= current_version:
                return ProjectionResult(current_snapshot, stale=True)
            target = event.snapshot
            converged = False
            if event.aggregate_version > current_version + 1:
                if recovery_snapshot is None or recovery_snapshot.session_version < event.aggregate_version:
                    # Raising rolls back the message claim, making a later snapshot retry safe.
                    raise ValueError("gateway_snapshot_unavailable")
                target = recovery_snapshot
                converged = True
            await conn.execute(
                """INSERT INTO public.gateway_session_projection(
                     game_session_id,game_instance_nonce,owner_user_id,connection_id,session_version,
                     capability_digest,resume_identity,available,resumable)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT(game_session_id) DO UPDATE SET
                     game_instance_nonce=excluded.game_instance_nonce,owner_user_id=excluded.owner_user_id,
                     connection_id=excluded.connection_id,session_version=excluded.session_version,
                     capability_digest=excluded.capability_digest,resume_identity=excluded.resume_identity,
                     available=excluded.available,resumable=excluded.resumable,updated_at=now()""",
                target.game_session_id, target.game_instance_nonce, target.owner_user_id, target.connection_id,
                target.session_version, target.capability_digest, target.resume_identity,
                target.available, target.resumable,
            )
            return ProjectionResult(target, converged_from_snapshot=converged)


# ── GatewayEvent outbox: durable publish + stage-gated application ──────────


def _gateway_event_payload(event: GatewayEvent) -> dict[str, Any]:
    """Serialize a GatewayEvent to a JSON-safe dict for outbox storage."""
    return {
        "event_type": event.event_type.value,
        "snapshot": asdict(event.snapshot),
        "aggregate_id": event.aggregate_id,
        "aggregate_version": event.aggregate_version,
        "message_id": event.message_id,
        "occurred_at": event.occurred_at.isoformat(),
        "trace_context": dict(event.trace_context),
        "contract_version": event.contract_version,
    }


def _gateway_event_from_payload(payload: Mapping[str, Any]) -> GatewayEvent:
    """Deserialize a GatewayEvent from an outbox payload dict."""
    snap = payload["snapshot"]
    snapshot = GameSessionSnapshot(
        game_session_id=snap["game_session_id"],
        game_instance_nonce=snap["game_instance_nonce"],
        owner_user_id=snap["owner_user_id"],
        connection_id=snap.get("connection_id"),
        session_version=snap["session_version"],
        capability_digest=snap["capability_digest"],
        available=snap["available"],
        resumable=snap.get("resumable", False),
        resume_identity=snap.get("resume_identity"),
    )
    occurred_at = payload["occurred_at"]
    if isinstance(occurred_at, str):
        occurred_at = datetime.fromisoformat(occurred_at)
    return GatewayEvent(
        event_type=GatewayEventType(payload["event_type"]),
        snapshot=snapshot,
        aggregate_id=payload.get("aggregate_id"),
        aggregate_version=payload.get("aggregate_version"),
        message_id=payload["message_id"],
        occurred_at=occurred_at,
        trace_context=dict(payload.get("trace_context", {})),
        contract_version=payload.get("contract_version", GATEWAY_EVENT_CONTRACT_VERSION),
    )


class AsyncGatewayEventOutboxRepository(Protocol):
    async def publish(self, message_id: str, event_payload: Mapping[str, Any]) -> None: ...
    async def claim_pending(self, worker_id: str, limit: int = 50) -> Sequence[Mapping[str, Any]]: ...
    async def update_stage(self, message_id: str, stage: str) -> None: ...
    async def acknowledge(self, message_id: str) -> None: ...
    async def release_claim(self, message_id: str, error: str) -> None: ...
    async def diagnostic_stats(self) -> Mapping[str, float | int]: ...


class AsyncpgGatewayEventOutboxRepository:
    """PostgreSQL-backed durable GatewayEvent outbox with stage tracking."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def publish(self, message_id: str, event_payload: Mapping[str, Any]) -> None:
        encoded = _json(dict(event_payload))
        async with self._pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchval(
                """INSERT INTO public.gateway_event_outbox (message_id, event_payload)
                   VALUES ($1, $2::jsonb) ON CONFLICT (message_id) DO NOTHING
                   RETURNING message_id""",
                message_id, encoded,
            )
            if inserted is not None:
                return
            existing = await conn.fetchval(
                "SELECT event_payload FROM public.gateway_event_outbox WHERE message_id=$1 FOR UPDATE",
                message_id,
            )
            if isinstance(existing, str):
                existing = json.loads(existing)
            if existing != json.loads(encoded):
                raise Conflict("gateway outbox message_id reused with different payload")

    async def claim_pending(self, worker_id: str, limit: int = 50) -> Sequence[Mapping[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """WITH pending AS (
                     SELECT message_id FROM public.gateway_event_outbox
                     WHERE application_stage != 'completed'
                       AND (claimed_at IS NULL OR claimed_at < now() - interval '5 minutes')
                     ORDER BY created_at
                     FOR UPDATE SKIP LOCKED LIMIT $1
                   )
                   UPDATE public.gateway_event_outbox SET
                     claimed_by = $2, claimed_at = now(), attempt_count = attempt_count + 1
                   FROM pending WHERE gateway_event_outbox.message_id = pending.message_id
                   RETURNING gateway_event_outbox.*""",
                limit, worker_id,
            )
            return rows

    async def update_stage(self, message_id: str, stage: str) -> None:
        if stage not in {"projected", "bound"}:
            raise ValueError("invalid gateway event application stage")
        await self._pool.execute(
            """UPDATE public.gateway_event_outbox
               SET application_stage = $2, updated_at = now()
               WHERE message_id = $1 AND application_stage != 'completed'
                 AND array_position(ARRAY['pending','projected','bound'], application_stage)
                     < array_position(ARRAY['pending','projected','bound'], $2::text)""",
            message_id, stage,
        )

    async def acknowledge(self, message_id: str) -> None:
        await self._pool.execute(
            """UPDATE public.gateway_event_outbox
               SET application_stage = 'completed', claimed_by = NULL, claimed_at = NULL,
                   last_error = NULL, updated_at = now()
               WHERE message_id = $1""",
            message_id,
        )

    async def release_claim(self, message_id: str, error: str) -> None:
        await self._pool.execute(
            """UPDATE public.gateway_event_outbox
               SET claimed_by = NULL, claimed_at = NULL, last_error = $2, updated_at = now()
               WHERE message_id = $1""",
            message_id, error[:1000],
        )

    async def diagnostic_stats(self) -> Mapping[str, float | int]:
        row = await self._pool.fetchrow(
            """SELECT count(*)::bigint AS pending,
                      count(*) FILTER (WHERE claimed_at IS NOT NULL)::bigint AS claimed,
                      COALESCE(EXTRACT(EPOCH FROM (now()-min(created_at))),0)::double precision AS oldest_age_seconds
               FROM public.gateway_event_outbox WHERE application_stage != 'completed'"""
        )
        return {"pending": int(row["pending"]), "claimed": int(row["claimed"]),
                "oldest_age_seconds": max(0.0, float(row["oldest_age_seconds"]))}
