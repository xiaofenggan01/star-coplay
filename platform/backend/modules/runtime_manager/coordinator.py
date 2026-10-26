"""Product composition for the provider-neutral Runtime Manager control plane."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import secrets
import stat
import time
from dataclasses import asdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional, Protocol

from modules.runtime_manager.async_application import (
    AsyncBindingService, AsyncRuntimeService, activation_intent_digest,
)
from modules.runtime_manager.adapter_config import AdapterConfigResolver, LegacyAdapterConfigResolver
from modules.gateway.runtime_contracts import GatewayEvent, GatewayEventType
from modules.runtime_manager.contracts import (
    AgentRuntimeAdapter, InputReceipt, OutputPage, OutputQuery, PersonaArtifact,
    ReconcileAction, RuntimeActualState, RuntimeHandle, RuntimeInput,
    RuntimeRecord, RuntimeSpec, TurnResult,
)
from modules.runtime_manager.domain import (
    AuthorityIssuanceIntent, AuthorityRevocationIntent, BindingState, Conflict, DesiredState, DomainError,
    InvalidSpec, ResourceExhausted,
    RuntimeProductRecord, RuntimeProductSpec, RuntimeState,
)
from common.observability import emit_metric, new_log_id, normalize_error_code
from modules.runtime_manager.runtime_status_view import (
    assemble_runtime_status, BindingLookup, RuntimeStatusView,
)
from modules.runtime_manager.persistence import (
    AsyncBindingRepository, AsyncRuntimeProductRepository, _runtime_from_row, _runtime_payload,
)
from modules.runtime_manager.persona import PersonaArtifactMaterializer, PersonaArtifactSnapshot
from modules.runtime_manager.product_repository import OperationResult
from modules.runtime_manager.emergency_terminator import EmergencyRuntimeTerminator

log = logging.getLogger("runtime_manager.product")


def _metric_adapter_kind(value: str) -> str:
    return value if value in {"claude", "fake"} else "unknown"


def _record_runtime_stop(record: RuntimeProductRecord, started: float, outcome: str,
                         error_code: str = "") -> None:
    duration_ms = (time.monotonic() - started) * 1000
    level = logging.INFO if outcome == "success" else logging.ERROR
    log.log(
        level, "runtime stop completed" if outcome == "success" else "runtime stop failed",
        extra={"event": "runtime.operation.stop" if outcome == "success" else "runtime.operation.stop_failed",
               "runtime_id": record.runtime_id, "generation": record.generation,
               "adapter_kind": _metric_adapter_kind(record.spec.adapter_kind),
               "outcome": outcome, "error_code": error_code,
               "duration_ms": duration_ms},
    )
    emit_metric(
        "runtime_stop_duration_ms", "timing", duration_ms, outcome=outcome,
        stage="stop", runtime_id=record.runtime_id, generation=record.generation,
        adapter_kind=_metric_adapter_kind(record.spec.adapter_kind), error_code=error_code,
    )


class PersonaArtifactReader(Protocol):
    async def read(self, reference: str, user_id: str, agentee_id: str) -> PersonaArtifactSnapshot: ...


class RuntimeAuthorityController(Protocol):
    """Fail-closed ingress/authority port. Implementations own Binding facts."""
    async def allow_ingress(self, runtime_id: str, generation: int) -> None: ...
    async def deny_and_revoke(self, runtime_id: str, generation: int, reason: str) -> str | None: ...
    async def is_bound(self, runtime_id: str, generation: int) -> bool: ...


class DbPersonaArtifactReader:
    """Reads immutable persona snapshots from Platform authority by explicit revision."""
    def __init__(self, pool) -> None:
        self._pool = pool

    async def read(self, reference: str, user_id: str, agentee_id: str) -> PersonaArtifactSnapshot:
        parts = reference.split("/", 2)
        if len(parts) != 3 or parts[0] != "persona" or not parts[1] or not parts[2]:
            raise ValueError("invalid persona_artifact_ref")
        row = await self._pool.fetchrow(
            """SELECT agentee_id::text,user_id,persona_card,updated_at,generated_at
               FROM public.agentee_personas WHERE agentee_id::text=$1 AND user_id=$2""", parts[1], user_id,
        )
        if row is None or not row["persona_card"]:
            raise ValueError("persona_artifact_not_found")
        revision = row["updated_at"].isoformat()
        if revision != parts[2]:
            raise ValueError("persona_revision_mismatch")
        if row["agentee_id"] != agentee_id:
            raise ValueError("persona_owner_mismatch")
        payload = row["persona_card"]
        return PersonaArtifactSnapshot(
            row["agentee_id"], revision, hashlib.sha256(payload.encode()).hexdigest(),
            (row["generated_at"] or row["updated_at"]).isoformat(), payload,
            (reference,),
        )


def _credential_file_is_ready(path: Path, *, platform_name: str = os.name) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        return False
    return platform_name == "nt" or stat.S_IMODE(info.st_mode) == 0o600


class BindingOutboxAuthorityController:
    """Persists fencing before process control; the outbox worker applies revocation."""
    def __init__(self, bindings: AsyncBindingRepository, workspace_root: Optional[Path] = None) -> None:
        self._bindings = bindings
        self._workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None

    async def allow_ingress(self, runtime_id: str, generation: int) -> None:
        # Ingress is authorized only by an independently ACTIVE Binding; READY_UNBOUND
        # needs no issuance and must not invent Gateway session facts here.
        return None

    async def is_bound(self, runtime_id: str, generation: int) -> bool:
        binding = await self._bindings.find_for_runtime(runtime_id, generation)
        return binding is not None and binding.status is BindingState.ACTIVE

    async def is_credential_issued(self, runtime_id: str, generation: int) -> bool:
        if self._workspace_root is None:
            return False
        path = (self._workspace_root / runtime_id / f"generation-{generation}" / "mcp-credential").resolve()
        if not path.is_relative_to(self._workspace_root):
            return False
        return _credential_file_is_ready(path)

    async def deny_and_revoke(self, runtime_id: str, generation: int, reason: str) -> str | None:
        binding = await self._bindings.find_for_runtime(runtime_id, generation)
        if binding is None:
            return None
        if binding.status in {BindingState.REVOKED, BindingState.EXPIRED}:
            reader = getattr(self._bindings, "latest_revocation_intent_id", None)
            if reader is None:
                raise RuntimeError("authority_revocation_unconfirmed")
            intent_id = await reader(
                binding.binding_id, binding.binding_version, binding.lease_epoch
            )
            if not intent_id:
                raise RuntimeError("authority_revocation_unconfirmed")
            return intent_id
        updated = replace(
            binding, status=BindingState.REVOKED,
            binding_version=binding.binding_version + 1,
            lease_epoch=binding.lease_epoch + 1,
            revoked_at=datetime.now(timezone.utc),
        )
        digest = hashlib.sha256(
            f"{binding.binding_id}:{binding.binding_version}:{reason}".encode()
        ).hexdigest()
        committed = await self._bindings.transition_with_operation(
            updated, binding.binding_version,
            OperationResult(
                f"binding:{binding.binding_id}",
                f"runtime-fence:{runtime_id}:{generation}:{reason}", digest, None,
            ),
            AuthorityRevocationIntent(
                f"revoke:{binding.binding_id}:{updated.binding_version}", binding.binding_id,
                updated.binding_version, updated.lease_epoch, reason,
            ),
        )
        if committed.binding_version > binding.binding_version:
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
        return f"revoke:{binding.binding_id}:{updated.binding_version}"


class HostedGatewayEventConsumer:
    """Projection + Binding reaction port; never owns Runtime lifecycle."""
    def __init__(self, projection, bindings: AsyncBindingRepository, runtimes=None) -> None:
        self._projection = projection
        self._bindings = bindings
        self._runtimes = runtimes

    async def consume(self, event: GatewayEvent, stage_recorder=None):
        result = await self._projection.consume(event)
        if stage_recorder is not None:
            await stage_recorder(event.message_id, "projected")
        projected_snapshot = getattr(result, "snapshot", None)
        requires_projection_snapshot = result.stale or (
            result.duplicate and (
                projected_snapshot is None
                or projected_snapshot.session_version != event.aggregate_version
            )
        )
        if requires_projection_snapshot and projected_snapshot is None:
            raise RuntimeError("gateway_projection_snapshot_unavailable")
        application_event = event
        if projected_snapshot is not None and projected_snapshot != event.snapshot:
            application_event = replace(
                event,
                event_type=self._event_type_for_snapshot(projected_snapshot),
                snapshot=projected_snapshot,
                aggregate_id=projected_snapshot.game_session_id,
                aggregate_version=projected_snapshot.session_version,
            )
        binding = (
            await self._find_projection_binding(application_event)
            if requires_projection_snapshot or result.duplicate
            else await self._find_binding(application_event)
        )
        if binding is None and result.duplicate:
            # Projection may have committed before an unbound PENDING row was
            # claimed.  Route-facing resolution is safe here because the
            # immutable event already passed the durable projection gate.
            binding = await self._find_binding(application_event)
        if binding is not None:
            binding = await self.converge_snapshot(
                application_event.snapshot, binding,
                operation_identity=f"gateway-event:{application_event.message_id}",
                occurred_at=application_event.occurred_at,
            )
        if stage_recorder is not None:
            await stage_recorder(event.message_id, "bound")
        if binding is not None:
            await self.project_runtime_state(binding)
        return result

    @staticmethod
    def _event_type_for_snapshot(snapshot) -> GatewayEventType:
        if snapshot.available:
            return GatewayEventType.RESUMED
        if snapshot.resumable:
            return GatewayEventType.SUSPENDED
        return GatewayEventType.CLOSED

    async def converge_snapshot(
        self, snapshot, binding: RuntimeBinding, *, operation_identity: str,
        occurred_at: Optional[datetime] = None,
    ) -> RuntimeBinding:
        """Apply one trusted snapshot through the sole Binding/Runtime authority.

        Idempotent: the underlying transition_with_operation returns the
        cached result when the same operation_id is replayed, so neither
        binding_version nor lease_epoch is bumped twice.
        """
        if binding is None or binding.status in {BindingState.REVOKED, BindingState.EXPIRED}:
            return binding
        revocation = None
        issuance = None
        transition_at = occurred_at or datetime.now(timezone.utc)
        if snapshot.available:
            event_type = (
                GatewayEventType.CAPABILITIES_CHANGED
                if binding.status is BindingState.ACTIVE
                and binding.capability_digest != snapshot.capability_digest
                else GatewayEventType.RESUMED
            )
        elif snapshot.resumable:
            event_type = GatewayEventType.SUSPENDED
        else:
            event_type = GatewayEventType.CLOSED
        if event_type in {GatewayEventType.AVAILABLE, GatewayEventType.RESUMED}:
            if (
                binding.status is BindingState.ACTIVE
                and binding.game_session_id == snapshot.game_session_id
                and binding.game_instance_nonce == snapshot.game_instance_nonce
                and binding.capability_digest == snapshot.capability_digest
                and binding.resume_identity == snapshot.resume_identity
            ):
                return binding
            if binding.status not in {BindingState.PENDING, BindingState.SUSPENDED}:
                return binding
            if binding.user_id != snapshot.owner_user_id:
                raise ValueError("gateway_binding_owner_mismatch")
            same_writer = (
                binding.status is BindingState.SUSPENDED
                and binding.game_instance_nonce == snapshot.game_instance_nonce
            )
            if same_writer and binding.resume_identity != snapshot.resume_identity:
                raise ValueError("gateway_resume_identity_mismatch")
            updated = replace(
                binding, status=BindingState.ACTIVE,
                binding_version=binding.binding_version + 1,
                lease_epoch=binding.lease_epoch if same_writer else binding.lease_epoch + 1,
                game_session_id=snapshot.game_session_id,
                game_instance_nonce=snapshot.game_instance_nonce,
                capability_digest=snapshot.capability_digest,
                resume_identity=snapshot.resume_identity,
                bound_at=transition_at, suspended_at=None,
            )
            runtime = await self._runtimes.get_generation(binding.runtime_id, binding.generation) if self._runtimes else None
            tools = tuple(runtime.spec.allowed_capabilities) if runtime else ()
            issuance = AuthorityIssuanceIntent(
                f"issue:{binding.binding_id}:{updated.binding_version}", updated,
                ("game:read", "game:write"), tools,
                f"{binding.binding_id}:{updated.binding_version}:{updated.lease_epoch}:{operation_identity}",
            )
            reason = "game_session_activated"
        elif event_type is GatewayEventType.CAPABILITIES_CHANGED and binding.status is BindingState.ACTIVE:
            if binding.capability_digest == snapshot.capability_digest:
                return binding
            updated = replace(
                binding, binding_version=binding.binding_version + 1,
                capability_digest=snapshot.capability_digest,
            )
            revocation = AuthorityRevocationIntent(
                f"revoke:{binding.binding_id}:{updated.binding_version}", binding.binding_id,
                binding.binding_version, binding.lease_epoch, "capabilities_changed",
            )
            runtime = await self._runtimes.get_generation(binding.runtime_id, binding.generation) if self._runtimes else None
            tools = tuple(runtime.spec.allowed_capabilities) if runtime else ()
            issuance = AuthorityIssuanceIntent(
                f"issue:{binding.binding_id}:{updated.binding_version}", updated,
                ("game:read", "game:write"), tools,
                f"{binding.binding_id}:{updated.binding_version}:{updated.lease_epoch}:{operation_identity}",
            )
            reason = "capabilities_changed"
        elif event_type is GatewayEventType.SUSPENDED and binding.status is BindingState.ACTIVE:
            updated = replace(
                binding, status=BindingState.SUSPENDED,
                binding_version=binding.binding_version + 1,
                suspended_at=transition_at,
            )
            reason = "game_session_suspended"
        elif event_type is GatewayEventType.CLOSED:
            updated = replace(
                binding, status=BindingState.REVOKED,
                binding_version=binding.binding_version + 1,
                lease_epoch=binding.lease_epoch + 1,
                revoked_at=transition_at,
            )
            reason = "game_session_closed"
        else:
            return binding
        fingerprint = hashlib.sha256(
            f"{operation_identity}:{binding.binding_version}:{event_type.value}".encode()
        ).hexdigest()
        committed = await self._bindings.transition_with_operation(
            updated, binding.binding_version,
            OperationResult(
                f"binding:{binding.binding_id}", operation_identity,
                fingerprint, None,
            ),
            revocation or (AuthorityRevocationIntent(
                f"revoke:{binding.binding_id}:{updated.binding_version}", binding.binding_id,
                updated.binding_version, updated.lease_epoch, reason,
            ) if event_type in {GatewayEventType.SUSPENDED, GatewayEventType.CLOSED} else None),
            issuance,
        )
        if committed.binding_version > binding.binding_version:
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

    async def _find_binding(self, event: GatewayEvent):
        if hasattr(self._bindings, "find_for_gateway_event"):
            return await self._bindings.find_for_gateway_event(
                event.snapshot.game_session_id, event.snapshot.owner_user_id
            )
        return await self._bindings.find_for_game_session(event.snapshot.game_session_id)

    async def _find_projection_binding(self, event: GatewayEvent):
        reader = getattr(self._bindings, "find_for_manager_projection", None)
        if reader is not None:
            return await reader(
                event.snapshot.game_session_id, event.snapshot.owner_user_id
            )
        return await self._find_binding(event)

    async def project_runtime_state(self, binding) -> bool:
        if self._runtimes is None:
            return False
        target = (
            RuntimeState.RUNNING_BOUND
            if binding.status is BindingState.ACTIVE else RuntimeState.IDLE
            if binding.status in {
                BindingState.SUSPENDED, BindingState.REVOKED, BindingState.EXPIRED,
            } else None
        )
        if target is None:
            return False
        for _ in range(3):
            current = await self._runtimes.get_generation(
                binding.runtime_id, binding.generation
            )
            if current is None or current.desired_state is DesiredState.STOPPED:
                return False
            if current.actual_state in {
                RuntimeState.DRAINING, RuntimeState.STOPPING, RuntimeState.STOPPED,
                RuntimeState.FAILED,
            }:
                return False
            if current.actual_state is target:
                return True
            if current.actual_state not in {
                RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
            }:
                return False
            try:
                await self._runtimes.compare_and_swap(
                    replace(current, actual_state=target), current.status_version
                )
                return True
            except Conflict:
                continue
        raise Conflict("runtime state projection CAS exhausted")


class DurableGatewayEventPublisher:
    """Publish GatewayEvents to the durable outbox or, without one, directly.

    When an outbox repository is provided every publish() is a non-blocking
    INSERT that returns before the consumer processes the event.  A
    background GatewayEventOutboxWorker is responsible for claiming and
    applying outbox rows stage by stage.
    """

    def __init__(self, outbox=None, consumer: HostedGatewayEventConsumer | None = None,
                 *, eager_apply: bool = True) -> None:
        self._outbox = outbox
        self.consumer = consumer
        self._eager_apply = eager_apply

    def bind(self, consumer: HostedGatewayEventConsumer) -> None:
        if self.consumer is not None:
            raise RuntimeError("Gateway event publisher is already bound")
        self.consumer = consumer

    async def publish(self, event: GatewayEvent) -> None:
        if self._outbox is not None:
            from modules.runtime_manager.persistence import _gateway_event_payload
            await self._outbox.publish(event.message_id, _gateway_event_payload(event))
            if self._eager_apply and self.consumer is not None:
                try:
                    await self.consumer.consume(event, self._outbox.update_stage)
                except Exception as error:
                    await self._outbox.release_claim(event.message_id, str(error))
                    # Durable enqueue is the producer acknowledgement boundary.
                    # Eager application is latency-only; the worker owns retry.
                    log.warning("eager GatewayEvent application deferred: %s", event.message_id, exc_info=True)
                    return
                await self._outbox.acknowledge(event.message_id)
        elif self.consumer is not None:
            await self.consumer.consume(event)
        else:
            raise RuntimeError("Gateway event publisher is not bound")


class GatewayEventOutboxWorker:
    """Retry durable Gateway events until Binding and Runtime converge."""

    def __init__(self, repository, consumer: HostedGatewayEventConsumer,
                 worker_id: Optional[str] = None) -> None:
        self.repository = repository
        self.consumer = consumer
        self.worker_id = worker_id or f"gateway-event-worker:{secrets.token_hex(8)}"

    async def run_once(self, limit: int = 50) -> int:
        from modules.runtime_manager.persistence import _gateway_event_from_payload
        rows = await self.repository.claim_pending(self.worker_id, limit)
        emit_metric(
            "runtime_outbox_claimed", "gauge", len(rows), outcome="current",
            outbox_kind="gateway", stage="claim",
        )
        completed = 0
        for row in rows:
            payload = row["event_payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            event = _gateway_event_from_payload(payload)
            try:
                await self.consumer.consume(event, self.repository.update_stage)
            except Exception as error:
                await self.repository.release_claim(event.message_id, str(error))
                log.warning(
                    "Gateway outbox event scheduled for retry",
                    extra={"event": "runtime.outbox.retry", "outbox_kind": "gateway",
                           "stage": "retry", "outcome": "retry",
                           "error_code": "gateway_outbox_apply_failed",
                           "exception_type": type(error).__name__},
                )
                emit_metric(
                    "runtime_outbox_retry_total", "counter", 1, outcome="retry",
                    outbox_kind="gateway", stage="retry", error_code="gateway_outbox_apply_failed",
                )
                continue
            await self.repository.acknowledge(event.message_id)
            completed += 1
        stats_reader = getattr(self.repository, "diagnostic_stats", None)
        if stats_reader is not None:
            try:
                stats = await stats_reader()
                emit_metric(
                    "runtime_outbox_pending", "gauge", int(stats.get("pending", 0)),
                    outcome="current", outbox_kind="gateway",
                )
                emit_metric(
                    "runtime_outbox_oldest_age_seconds", "gauge",
                    float(stats.get("oldest_age_seconds", 0.0)),
                    outcome="current", outbox_kind="gateway",
                )
            except Exception as error:
                log.warning(
                    "Gateway outbox diagnostics unavailable",
                    extra={"event": "runtime.outbox.diagnostics_unavailable",
                           "outbox_kind": "gateway", "outcome": "unavailable",
                           "error_code": "diagnostic_stats_unavailable",
                           "exception_type": type(error).__name__},
                )
                emit_metric(
                    "runtime_outbox_diagnostics_total", "counter", 1,
                    outcome="unavailable", outbox_kind="gateway",
                    error_code="diagnostic_stats_unavailable",
                )
        return completed


class TrustedGatewayEventSource(Protocol):
    async def event_for(self, connection_id: str, owner_user_id: str,
                        transition: str) -> GatewayEvent: ...


class GatewayEventLifecycleHook:
    """Registers the consumer on real authenticated Gateway lifecycle edges."""
    def __init__(self, source: TrustedGatewayEventSource,
                 consumer: HostedGatewayEventConsumer) -> None:
        self._source = source
        self._consumer = consumer

    async def on_authenticated(self, session, *, resumed: bool) -> None:
        transition = "resumed" if resumed else "available"
        await self._consume_trusted(session, transition)

    async def on_suspended(self, session) -> None:
        await self._consume_trusted(session, "suspended")

    async def _consume_trusted(self, session, transition: str) -> None:
        owner_user_id = session.instance.user_id
        if not owner_user_id:
            raise RuntimeError("gateway_event_fact_unavailable")
        event = await self._source.event_for(session.connection_id, owner_user_id, transition)
        expected = {
            "available": GatewayEventType.AVAILABLE,
            "resumed": GatewayEventType.RESUMED,
            "suspended": GatewayEventType.SUSPENDED,
        }[transition]
        connection_matches = (
            event.snapshot.connection_id == session.connection_id
            if transition != "suspended"
            else event.snapshot.connection_id in {None, session.connection_id}
        )
        if event.event_type is not expected or not connection_matches or event.snapshot.owner_user_id != owner_user_id:
            raise ValueError("untrusted_gateway_event_fact")
        await self._consumer.consume(event)


class ActivityKind(str, Enum):
    USER_INPUT = "user_input"
    AGENT_OUTPUT = "agent_output"
    MCP_SUCCESS = "mcp_success"
    GAME_RESULT_SUCCESS = "game_result_success"
    LOG = "log"
    HEARTBEAT = "heartbeat"
    FAILURE = "failure"

    @property
    def effective(self) -> bool:
        return self in {
            ActivityKind.USER_INPUT, ActivityKind.AGENT_OUTPUT,
            ActivityKind.MCP_SUCCESS, ActivityKind.GAME_RESULT_SUCCESS,
        }


class EffectiveActivityReporter:
    def __init__(self, repository: AsyncRuntimeProductRepository, on_effective=None) -> None:
        self._repository = repository
        self._on_effective = on_effective

    async def report(self, runtime_id: str, generation: int, kind: ActivityKind,
                     occurred_at: Optional[datetime] = None) -> bool:
        if not kind.effective:
            return False
        moment = occurred_at or datetime.now(timezone.utc)
        for _ in range(3):
            current = await self._repository.get_generation(runtime_id, generation)
            if current is None or not current.active or current.actual_state in {
                RuntimeState.DRAINING, RuntimeState.STOPPING,
            }:
                return False
            if current.last_effective_activity_at and moment <= current.last_effective_activity_at:
                if self._on_effective is not None:
                    self._on_effective(runtime_id)
                return True
            try:
                await self._repository.compare_and_swap(
                    replace(current, last_effective_activity_at=moment), current.status_version
                )
                if self._on_effective is not None:
                    self._on_effective(runtime_id)
                return True
            except Conflict:
                continue
        # Activity telemetry must not turn an otherwise successful user turn
        # into a failed API response; the next effective event will retry.
        return False


class HostedMcpActivityReporter:
    """Hosted composition adapter for MCP Core's deployment-neutral port."""

    def __init__(self, effective: EffectiveActivityReporter) -> None:
        self._effective = effective

    async def report_succeeded(self, runtime_ref: str, generation: int) -> None:
        await self._effective.report(runtime_ref, generation, ActivityKind.MCP_SUCCESS)


class RuntimeManagerCoordinator:
    """Durable state owns intent; live handles are reconstructable process projections."""
    def __init__(self, repository: AsyncRuntimeProductRepository,
                 persona_reader: PersonaArtifactReader,
                 authority: RuntimeAuthorityController,
                 adapters: Mapping[str, AgentRuntimeAdapter], workspace_root: Path,
                 *, ready_timeout_seconds: float = 30.0,
                 drain_timeout_seconds: float = 5.0,
                 stop_grace_seconds: float = 5.0,
                 emergency_terminator: Optional[EmergencyRuntimeTerminator] = None,
                 adapter_metadata: Optional[Mapping[str, Mapping[str, object]]] = None,
                 adapter_config_resolver: Optional[AdapterConfigResolver] = None,
                 runtime_env_resolver=None,
                 binding_repository: Optional[AsyncBindingRepository] = None,
                 gateway_projection_repository=None,
                 gateway_snapshot_converger: Optional[HostedGatewayEventConsumer] = None,
                 turn_correlation_registry=None,
                 turn_completion_hook=None,
                 max_input_bytes: int = 64 * 1024,
                 max_inflight_inputs: int = 1) -> None:
        if max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
        if max_inflight_inputs != 1:
            raise ValueError("M5 supports exactly one in-flight Runtime input")
        self.repository = repository
        self.runtime_service = AsyncRuntimeService(repository)
        self.persona_reader = persona_reader
        self.authority = authority
        self.adapters = dict(adapters)
        self.workspace_root = Path(workspace_root).resolve()
        self.ready_timeout_seconds = ready_timeout_seconds
        self.drain_timeout_seconds = drain_timeout_seconds
        self.stop_grace_seconds = stop_grace_seconds
        self.emergency_terminator = emergency_terminator
        self.adapter_metadata = {key: dict(value) for key, value in (adapter_metadata or {}).items()}
        self.adapter_config_resolver = adapter_config_resolver or LegacyAdapterConfigResolver()
        self.runtime_env_resolver = runtime_env_resolver
        self.activity = EffectiveActivityReporter(repository, self._reset_rollover_budget)
        self.binding_service = (
            AsyncBindingService(repository, binding_repository)
            if binding_repository is not None else None
        )
        self.gateway_projection_repository = gateway_projection_repository
        self.gateway_snapshot_converger = gateway_snapshot_converger
        self.turn_correlation_registry = turn_correlation_registry
        self.turn_completion_hook = turn_completion_hook
        self.max_input_bytes = max_input_bytes
        self._handles: dict[tuple[str, int], RuntimeHandle] = {}
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._inflight: dict[tuple[str, int], int] = {}
        self._drained: dict[tuple[str, int], asyncio.Event] = {}
        self._input_tasks: dict[tuple[str, int, str], tuple[str, asyncio.Task]] = {}
        self._input_reservations: dict[
            tuple[str, int], tuple[tuple[str, int, str], str, asyncio.Task]
        ] = {}
        self._input_available: dict[tuple[str, int], asyncio.Event] = {}
        self._roll_locks: dict[str, asyncio.Lock] = {}
        # Bounded rollover tracker: runtime_id -> (attempt_count, last_attempt_monotonic)
        self._rollover_tracker: dict[str, tuple[int, float]] = {}
        self._rollover_exhausted_logged: set[str] = set()
        self._recovery_authority_worker = None
        self._provider_state: dict[str, object] = {
            "last_failure_at": None, "last_failure_code": None, "adapter_kind": None,
        }

    def owned_handle_keys_snapshot(self) -> frozenset[tuple[str, int]]:
        return frozenset(self._handles)

    def provider_state_snapshot(self) -> Mapping[str, object]:
        return dict(self._provider_state)

    def _reset_rollover_budget(self, runtime_id: str) -> None:
        self._rollover_tracker.pop(runtime_id, None)
        self._rollover_exhausted_logged.discard(runtime_id)

    def _record_provider_failure(self, adapter_kind: str | None, error_code: object) -> None:
        self._provider_state = {
            "last_failure_at": datetime.now(timezone.utc).isoformat(),
            "last_failure_code": normalize_error_code(error_code, fallback="unknown"),
            "adapter_kind": _metric_adapter_kind(adapter_kind or "unknown"),
        }

    async def provision(
        self,
        spec: RuntimeProductSpec,
        *,
        allow_terminal_intent_change: bool = False,
    ) -> RuntimeProductRecord:
        """Persist an idempotent Runtime intent without spawning its Agent.

        Hosted product startup uses this boundary to create a pending Binding,
        let the authenticated Gateway activate it and issue MCP authority, and
        only then call ``ensure`` to spawn an Agent whose MCP surface is
        callable from its first protocol initialization.

        Generation contract: FAILED and STOPPED are terminal for their
        generation.  A new activation always creates generation+1; same-
        generation resurrection is never permitted.  FAILED is finalized to
        STOPPED (with deny/revoke + workspace cleanup) before generation+1 is
        inserted.
        """
        spec.validate()
        existing = await self.repository.get(spec.runtime_id)
        if existing is not None and existing.actual_state in {
            RuntimeState.FAILED, RuntimeState.STOPPED,
        }:
            if spec.generation == existing.generation:
                # Terminal generations must never be resurrected in-place.
                raise Conflict(
                    "terminal generation requires rollover to generation+1"
                )
            if spec.generation != existing.generation + 1:
                raise Conflict(
                    "generation gap too large for rollover"
                )
            # ── Rollover: finalize old terminal generation, create gen+1 ──
            old_key = (existing.runtime_id, existing.generation)
            async with self._locks.setdefault(old_key, asyncio.Lock()):
                current = await self.repository.get_generation(*old_key)
                if current is None:
                    raise Conflict("terminal runtime generation disappeared")
                if (
                    current.runtime_id != spec.runtime_id
                    or current.spec.user_id != spec.user_id
                    or current.spec.agentee_id != spec.agentee_id
                    or current.spec.activation_key != spec.activation_key
                    or (
                        activation_intent_digest(current.spec) != activation_intent_digest(spec)
                        and current.exit_reason != "adapter_config_revision_changed"
                        and not allow_terminal_intent_change
                    )
                ):
                    raise Conflict("runtime activation intent mismatch")
                if current.actual_state not in {RuntimeState.FAILED, RuntimeState.STOPPED}:
                    # Already recovered by another path; spec generation may
                    # be stale.  Return the current latest.
                    latest = await self.repository.get(spec.runtime_id)
                    if latest is not None and latest.generation >= spec.generation:
                        return latest
                    raise Conflict("runtime activation intent mismatch")
                handle = self._handles.get(old_key)
                revocation_intent_id = None
                if current.actual_state is RuntimeState.FAILED:
                    revocation_intent_id = await self.authority.deny_and_revoke(
                        current.runtime_id, current.generation,
                        f"rollover_to_generation_{spec.generation}",
                    )
                if handle is not None:
                    adapter = self.adapters.get(current.spec.adapter_kind)
                    if adapter is None:
                        raise Conflict("terminal runtime provider adapter unavailable")
                    await adapter.stop(handle, "generation_rollover", self.stop_grace_seconds)
                if current.actual_state is RuntimeState.FAILED:
                    await self._confirm_authority_revoked(revocation_intent_id)
                    # Finalize FAILED → STOPPED: revoke authority, clean
                    # workspace, then transition the old generation to
                    # terminal STOPPED so repository.ensure accepts gen+1.
                    await self._cleanup(old_key)
                    await self._transition(
                        current, RuntimeState.STOPPED, ready=False,
                        exit_reason=f"rollover_to_generation_{spec.generation}",
                    )
                else:
                    # STOPPED is already process/workspace terminal, but a
                    # previously enqueued revoke/expire fence still must be
                    # durably acknowledged before a successor can exist.
                    revocation_intent_id = await self.authority.deny_and_revoke(
                        current.runtime_id, current.generation,
                        f"rollover_to_generation_{spec.generation}",
                    )
                    await self._confirm_authority_revoked(revocation_intent_id)
            # Old generation is now terminal STOPPED.  Insert generation+1.
            return await self.runtime_service.ensure_runtime(spec)
        return await self.runtime_service.ensure_runtime(spec)

    async def ensure_binding(self, runtime_id: str, generation: int, user_id: str,
                             agentee_id: str, operation_id: str,
                             game_session_id: str | None = None):
        if self.binding_service is None:
            raise RuntimeError("binding_service_unavailable")
        binding = await self.binding_service.ensure_binding(
            runtime_id, generation, user_id, agentee_id, operation_id,
            game_session_id,
        )
        if self.gateway_projection_repository is None or self.gateway_snapshot_converger is None:
            return binding
        current = await self.repository.get_generation(runtime_id, generation)
        if current is None or current.desired_state is DesiredState.STOPPED or current.actual_state not in {
            RuntimeState.PROVISIONING, RuntimeState.READY_UNBOUND,
            RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
        }:
            return binding
        latest_binding = await self.binding_service.bindings.find_for_runtime(runtime_id, generation)
        if latest_binding is None:
            return binding
        binding = latest_binding
        for _ in range(3):
            latest = await self.binding_service.bindings.find_for_runtime(runtime_id, generation)
            if latest is None:
                return binding
            snapshot = (
                await self.gateway_projection_repository.get(latest.game_session_id)
                if latest.game_session_id is not None
                else await self.gateway_projection_repository.find_available_for_owner(user_id)
            )
            if snapshot is None:
                return latest
            if snapshot.owner_user_id != user_id:
                raise ValueError("gateway_binding_owner_mismatch")
            operation_identity = (
                f"binding-snapshot:{latest.binding_id}:{snapshot.game_session_id}:"
                f"{snapshot.session_version}"
            )
            try:
                converged = await self.gateway_snapshot_converger.converge_snapshot(
                    snapshot, latest, operation_identity=operation_identity,
                )
                await self.gateway_snapshot_converger.project_runtime_state(converged)
                return converged
            except Conflict:
                continue
        latest = await self.binding_service.bindings.find_for_runtime(runtime_id, generation)
        snapshot = (
            await self.gateway_projection_repository.get(latest.game_session_id)
            if latest is not None and latest.game_session_id is not None
            else await self.gateway_projection_repository.find_available_for_owner(user_id)
        )
        if latest is not None and snapshot is not None and (
            latest.status in {BindingState.SUSPENDED, BindingState.REVOKED, BindingState.EXPIRED}
            or (
                latest.status is BindingState.ACTIVE
                and latest.game_session_id == snapshot.game_session_id
                and latest.game_instance_nonce == snapshot.game_instance_nonce
                and latest.capability_digest == snapshot.capability_digest
                and latest.resume_identity == snapshot.resume_identity
            )
        ):
            await self.gateway_snapshot_converger.project_runtime_state(latest)
            return latest
        raise Conflict("binding snapshot convergence CAS exhausted")

    async def ensure(self, spec: RuntimeProductSpec, *, roll_fence=None) -> RuntimeProductRecord:
        if roll_fence is None:
            record = await self.provision(spec)
        else:
            spec.validate()
            holder_token, fencing_epoch, lost = roll_fence
            await self._assert_roll_fence(spec.runtime_id, roll_fence)
            config_snapshot = await self.adapter_config_resolver.resolve(
                spec.adapter_config_ref, spec.runtime_id, spec.user_id, spec.agentee_id, spec.adapter_kind)
            record = await self.repository.fenced_ensure(
                RuntimeProductRecord(spec=spec), activation_intent_digest(spec), holder_token,
                fencing_epoch, config_snapshot.content_hash)
        key = (record.runtime_id, record.generation)
        async with self._locks.setdefault(key, asyncio.Lock()):
            current = await self.repository.get_generation(*key)
            assert current is not None
            if current.actual_state in {RuntimeState.DRAINING, RuntimeState.STOPPING}:
                raise RuntimeError("already_stopping")
            if self.binding_service is not None:
                binding = await self.binding_service.bindings.find_for_runtime(*key)
                if (
                    binding is None
                    or binding.status is not BindingState.ACTIVE
                    or binding.runtime_id != current.runtime_id
                    or binding.generation != current.generation
                    or binding.user_id != current.spec.user_id
                    or binding.agentee_id != current.spec.agentee_id
                ):
                    raise RuntimeError("binding_not_active")
                issued = getattr(self.authority, "is_credential_issued", None)
                if issued is None or not await issued(*key):
                    raise RuntimeError("authority_credential_not_issued")
            if current.ready and current.actual_state in {
                RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
            }:
                return current
            adapter = self.adapters.get(current.spec.adapter_kind)
            if adapter is None:
                self._record_provider_failure(current.spec.adapter_kind, "unavailable")
                await self._fail(current, "adapter_unavailable")
                raise RuntimeError("adapter_unavailable")
            handle: Optional[RuntimeHandle] = None
            try:
                artifact = await self.persona_reader.read(
                    current.spec.persona_artifact_ref, current.spec.user_id, current.spec.agentee_id
                )
                if artifact.agentee_id != current.spec.agentee_id:
                    raise ValueError("persona_owner_mismatch")
                PersonaArtifactMaterializer(self.workspace_root).materialize(
                    current.runtime_id, current.generation, artifact
                )
                runtime_start_started = time.monotonic()
                current = await self._transition(current, RuntimeState.STARTING, roll_fence=roll_fence)
                config_snapshot = await self.adapter_config_resolver.resolve(
                    current.spec.adapter_config_ref, current.runtime_id, current.spec.user_id,
                    current.spec.agentee_id, current.spec.adapter_kind,
                )
                if config_snapshot.allowed_tools and tuple(sorted(current.spec.allowed_capabilities)) != config_snapshot.allowed_tools:
                    raise ValueError("adapter_config_allowed_tools_mismatch")
                runtime_env = {}
                if self.runtime_env_resolver is not None:
                    runtime_env = dict(await self.runtime_env_resolver.resolve(
                        runtime_id=current.runtime_id,
                        generation=current.generation,
                        user_id=current.spec.user_id,
                        agentee_id=current.spec.agentee_id,
                        provider=current.spec.adapter_kind,
                        adapter_config=config_snapshot,
                    ))
                runtime_spec = RuntimeSpec(
                    runtime_id=current.runtime_id, generation=current.generation,
                    user_id=current.spec.user_id, agentee_id=current.spec.agentee_id,
                    provider=current.spec.adapter_kind, workspace_root=self.workspace_root,
                    idle_ttl_seconds=float(current.spec.idle_ttl_seconds),
                    persona_artifact=PersonaArtifact(
                        artifact.agentee_id, artifact.persona_revision,
                        artifact.content_hash, artifact.instruction_payload,
                    ),
                    env=runtime_env,
                    provider_private_metadata={
                        "adapter_config_ref": current.spec.adapter_config_ref,
                        "adapter_config_snapshot": config_snapshot.document(),
                        **config_snapshot.metadata(),
                        **self.adapter_metadata.get(current.spec.adapter_kind, {}),
                    },
                )
                prepared = await adapter.prepare(runtime_spec)
                await self._assert_roll_fence(current.runtime_id, roll_fence)
                handle = await adapter.start(prepared)
                # Ownership begins when the provider process starts, not when
                # readiness completes.  Otherwise reconcile_all can observe a
                # persisted STARTING record with an owner/PID but no local
                # handle and incorrectly terminate it as an orphan.
                self._handles[key] = handle
                ready_started = time.monotonic()
                if roll_fence is None:
                    ready = await adapter.wait_ready(handle, self.ready_timeout_seconds)
                else:
                    ready_task = asyncio.create_task(adapter.wait_ready(handle, self.ready_timeout_seconds))
                    lost_task = asyncio.create_task(roll_fence[2].wait())
                    done, _ = await asyncio.wait(
                        {ready_task, lost_task}, return_when=asyncio.FIRST_COMPLETED)
                    if lost_task in done and roll_fence[2].is_set():
                        ready_task.cancel()
                        await asyncio.gather(ready_task, return_exceptions=True)
                        raise RuntimeError("operation_lease_lost")
                    lost_task.cancel()
                    await asyncio.gather(lost_task, return_exceptions=True)
                    ready = await ready_task
                if not ready.ready:
                    # Adapter-provided failure classification is the only
                    # readiness detail allowed to cross into the product API.
                    # Missing/unknown classifications fail closed as a generic
                    # readiness failure, never as a misleading timeout.
                    failure_code = getattr(ready.failure_kind, "value", None)
                    if failure_code not in {
                        "timeout", "process_exited", "provider_rejected", "readiness_failed",
                    }:
                        failure_code = "readiness_failed"
                    self._record_provider_failure(current.spec.adapter_kind, failure_code)
                    duration_ms = (time.monotonic() - ready_started) * 1000
                    log.warning(
                        "runtime adapter readiness failed",
                        extra={"event": "runtime.adapter.readiness", "runtime_id": current.runtime_id,
                               "generation": current.generation, "adapter_kind": _metric_adapter_kind(current.spec.adapter_kind),
                               "failure_kind": failure_code, "outcome": "failed",
                               "error_code": failure_code, "duration_ms": duration_ms},
                    )
                    emit_metric(
                        "runtime_adapter_readiness_total", "counter", 1, outcome="failed",
                        adapter_kind=_metric_adapter_kind(current.spec.adapter_kind), failure_kind=failure_code,
                        runtime_id=current.runtime_id, generation=current.generation,
                        error_code=failure_code,
                    )
                    emit_metric(
                        "runtime_adapter_readiness_duration_ms", "timing", duration_ms,
                        outcome="failed", adapter_kind=_metric_adapter_kind(current.spec.adapter_kind),
                        failure_kind=failure_code, runtime_id=current.runtime_id,
                        generation=current.generation, error_code=failure_code,
                    )
                    raise RuntimeError(
                        "ready_timeout" if failure_code == "timeout" else failure_code
                    )
                ready_duration_ms = (time.monotonic() - ready_started) * 1000
                log.info(
                    "runtime adapter readiness succeeded",
                    extra={"event": "runtime.adapter.readiness", "runtime_id": current.runtime_id,
                           "generation": current.generation, "adapter_kind": _metric_adapter_kind(current.spec.adapter_kind),
                           "outcome": "success", "duration_ms": ready_duration_ms},
                )
                emit_metric(
                    "runtime_adapter_readiness_total", "counter", 1, outcome="success",
                    adapter_kind=_metric_adapter_kind(current.spec.adapter_kind), runtime_id=current.runtime_id,
                    generation=current.generation,
                )
                emit_metric(
                    "runtime_adapter_readiness_duration_ms", "timing", ready_duration_ms,
                    outcome="success", adapter_kind=_metric_adapter_kind(current.spec.adapter_kind),
                    runtime_id=current.runtime_id, generation=current.generation,
                )
                await self._assert_roll_fence(current.runtime_id, roll_fence)
                self._inflight[key] = 0
                self._drained.setdefault(key, asyncio.Event()).set()
                bound = await self.authority.is_bound(*key) if hasattr(self.authority, "is_bound") else False
                current = await self._transition(
                    current, RuntimeState.RUNNING_BOUND if bound else RuntimeState.READY_UNBOUND, ready=True,
                    started_at=datetime.now(timezone.utc),
                    last_effective_activity_at=datetime.now(timezone.utc),
                    roll_fence=roll_fence,
                )
                await self._assert_roll_fence(current.runtime_id, roll_fence)
                await self.authority.allow_ingress(*key)
                try:
                    await self._assert_roll_fence(current.runtime_id, roll_fence)
                except Exception:
                    await self.authority.deny_and_revoke(*key, "roll_lease_lost_after_ingress")
                    raise
                start_duration_ms = (time.monotonic() - runtime_start_started) * 1000
                log.info(
                    "runtime start completed",
                    extra={"event": "runtime.operation.start", "runtime_id": current.runtime_id,
                           "generation": current.generation,
                           "adapter_kind": _metric_adapter_kind(current.spec.adapter_kind),
                           "outcome": "success", "duration_ms": start_duration_ms},
                )
                emit_metric(
                    "runtime_start_duration_ms", "timing", start_duration_ms,
                    outcome="success", stage="start", runtime_id=current.runtime_id,
                    generation=current.generation,
                    adapter_kind=_metric_adapter_kind(current.spec.adapter_kind),
                )
                return current
            except asyncio.CancelledError:
                cleanup = asyncio.create_task(self._cleanup_cancelled_start(
                    current, adapter, handle, key, roll_fence))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # Additional cancel(s) arrived during cleanup.  Every
                    # critical step inside _cleanup_cancelled_start is itself
                    # shielded, so the cleanup task continues running.  Loop
                    # with asyncio.shield until cleanup is fully done; never
                    # leave a background cleanup task behind.
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            pass  # keep shielding until cleanup is complete
                raise
            except Exception as error:
                if "runtime_start_started" in locals():
                    start_duration_ms = (time.monotonic() - runtime_start_started) * 1000
                    stable_code = normalize_error_code(
                        getattr(error, "code", None), fallback="runtime_start_failed"
                    )
                    self._record_provider_failure(current.spec.adapter_kind, stable_code)
                    log.error(
                        "runtime start failed",
                        extra={"event": "runtime.operation.start_failed", "runtime_id": current.runtime_id,
                               "generation": current.generation,
                               "adapter_kind": _metric_adapter_kind(current.spec.adapter_kind),
                               "outcome": "failed", "error_code": stable_code,
                               "exception_type": type(error).__name__, "duration_ms": start_duration_ms},
                    )
                    emit_metric(
                        "runtime_start_duration_ms", "timing", start_duration_ms,
                        outcome="failed", stage="start", runtime_id=current.runtime_id,
                        generation=current.generation,
                        adapter_kind=_metric_adapter_kind(current.spec.adapter_kind),
                        error_code=stable_code,
                    )
                await self.authority.deny_and_revoke(*key, "startup_failed")
                if handle is not None:
                    try:
                        await adapter.stop(handle, "startup_failed", self.stop_grace_seconds)
                    except Exception:
                        pass
                await self._cleanup(key)
                # A stale roll holder is not allowed to publish FAILED either.
                if roll_fence is None or not roll_fence[2].is_set():
                    try: await self._fail(current, str(error), roll_fence=roll_fence)
                    except (Conflict, RuntimeError): pass
                raise

    async def get(self, runtime_id: str) -> Optional[RuntimeProductRecord]:
        return await self.repository.get(runtime_id)

    async def status_for(self, record: RuntimeProductRecord) -> RuntimeStatusView:
        """Assemble a read-only RuntimeStatusView for an already-fetched record.

        Never mutates DB, never bumps status_version, never creates or
        transitions Binding authority.  Binding is looked up by exact
        (runtime_id, generation) and validated fail-closed.
        """
        # ── Binding lookup (exact generation, fail-closed validation) ──
        binding_lookup: Optional[BindingLookup] = None
        if hasattr(self, 'binding_service') and self.binding_service is not None:
            binding_lookup = self.binding_service.bindings
        elif hasattr(self, '_binding_repository') and self._binding_repository is not None:
            binding_lookup = self._binding_repository

        # ── Handle lookup (exact identity match only) ──
        key = (record.runtime_id, record.generation)
        handle = self._handles.get(key)
        if handle is not None and (handle.runtime_id, handle.generation) != key:
            handle = None  # fail closed on identity mismatch

        adapter = self.adapters.get(record.spec.adapter_kind)

        return await assemble_runtime_status(
            record,
            binding_lookup=binding_lookup,
            adapter=adapter,
            handle=handle,
        )

    async def get_status(self, runtime_id: str) -> RuntimeStatusView:
        """Durable get → owner-scoped status view.

        Raises LookupError when no record exists for runtime_id.
        """
        record = await self.repository.get(runtime_id)
        if record is None:
            raise LookupError(runtime_id)
        return await self.status_for(record)

    async def roll_adapter_config(self, runtime_id: str, new_reference: str,
                                  operation_id: str, expected_generation: int | None = None) -> RuntimeProductRecord:
        """Fence and replace a live generation when its config snapshot changes."""
        if not operation_id:
            raise ValueError("operation_id is required")
        initial = await self.repository.get(runtime_id)
        if initial is None: raise LookupError(runtime_id)
        if expected_generation is None:
            expected_generation = initial.generation
        new_snapshot = await self.adapter_config_resolver.resolve(
            new_reference, initial.runtime_id, initial.spec.user_id,
            initial.spec.agentee_id, initial.spec.adapter_kind)
        initial_snapshot = await self.adapter_config_resolver.resolve(
            initial.spec.adapter_config_ref, initial.runtime_id, initial.spec.user_id,
            initial.spec.agentee_id, initial.spec.adapter_kind)
        target_generation = (
            expected_generation
            if initial.generation == expected_generation
            and initial.ready
            and initial.actual_state in {RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE}
            and initial_snapshot.content_hash == new_snapshot.content_hash
            else expected_generation + 1
        )
        target_config_ref = initial.spec.adapter_config_ref if target_generation == expected_generation else new_reference
        fingerprint = hashlib.sha256(
            f"{new_reference}:{expected_generation}:{target_generation}:{new_snapshot.content_hash}".encode()
        ).hexdigest()
        owner_ref = f"runtime-roll:{runtime_id}"
        intent = {
            "operation_fingerprint": fingerprint, "expected_generation": expected_generation,
            "target_generation": target_generation, "requested_config_ref": new_reference,
            "target_config_ref": target_config_ref, "target_config_hash": new_snapshot.content_hash,
        }
        claim_roll = getattr(self.repository, "claim_roll_operation", None)
        claimed = (await claim_roll(owner_ref, operation_id, fingerprint, intent)
                   if claim_roll is not None else
                   await self.repository.claim_input_operation(owner_ref, operation_id, fingerprint))
        if claimed.get("status") == "completed":
            payload = claimed["result"]
            return _runtime_from_row(payload["runtime_result"])
        async with self._roll_locks.setdefault(runtime_id, asyncio.Lock()):
            acquire = getattr(self.repository, "acquire_roll_lease", None)
            renew = getattr(self.repository, "renew_roll_lease", None)
            validate = getattr(self.repository, "validate_roll_lease", None)
            release = getattr(self.repository, "release_roll_lease", None)
            holder_token, fencing_epoch, heartbeat = secrets.token_urlsafe(24), None, None
            lease_lost = asyncio.Event()
            if acquire is not None:
                fencing_epoch = await acquire(
                    runtime_id, operation_id, holder_token, fingerprint, expected_generation,
                    target_generation, target_config_ref, new_snapshot.content_hash)
                if fencing_epoch is None: raise RuntimeError("operation_in_progress")
                if renew is not None:
                    async def heartbeat_loop():
                        try:
                            while True:
                                await asyncio.sleep(10)
                                if not await renew(runtime_id, holder_token, fencing_epoch):
                                    lease_lost.set(); return
                        except asyncio.CancelledError: raise
                        except Exception:
                            lease_lost.set()
                    heartbeat = asyncio.create_task(heartbeat_loop())
            roll_fence = (holder_token, fencing_epoch, lease_lost) if fencing_epoch is not None else None
            try:
                current = await self.repository.get(runtime_id)
                if current is None: raise LookupError(runtime_id)
                crash_recovery = (
                    current.generation == target_generation
                    and current.spec.adapter_config_ref == target_config_ref
                    and current.ready
                    and current.actual_state in {RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE}
                )
                if expected_generation is not None and current.generation != expected_generation and not crash_recovery:
                    raise Conflict("runtime generation changed during config roll")
                old_snapshot = await self.adapter_config_resolver.resolve(
                    current.spec.adapter_config_ref, current.runtime_id, current.spec.user_id,
                    current.spec.agentee_id, current.spec.adapter_kind,
                )
                if crash_recovery:
                    result = current
                elif current.actual_state is not RuntimeState.FAILED and old_snapshot.content_hash == new_snapshot.content_hash:
                    result = current
                else:
                    if current.actual_state is RuntimeState.FAILED:
                        current = await self.repository.fenced_compare_and_swap(
                            replace(current, actual_state=RuntimeState.STOPPED, ready=False),
                            current.status_version, holder_token, fencing_epoch,
                        )
                    elif current.actual_state is not RuntimeState.STOPPED:
                        current = await self.stop(
                            current.runtime_id, current.generation,
                            f"{operation_id}:stop-old", "adapter_config_revision_changed", roll_fence=roll_fence,
                        )
                    next_spec = replace(
                        current.spec, generation=current.generation + 1,
                        adapter_config_ref=new_reference,
                    )
                    await self._assert_roll_fence(runtime_id, roll_fence)
                    result = await self.ensure(next_spec, roll_fence=roll_fence)
                operation_result = {"ok": True, "runtime_id": result.runtime_id,
                    "generation": result.generation, "adapter_config_ref": new_reference,
                    "fencing_epoch": fencing_epoch, "runtime_result": _runtime_payload(result)}
                complete_roll = getattr(self.repository, "complete_roll_operation", None)
                if complete_roll is not None:
                    record_hash = getattr(self.repository, "fenced_record_adapter_config_hash", None)
                    if record_hash is not None and not await record_hash(
                        runtime_id, result.generation, target_config_ref, new_snapshot.content_hash,
                        holder_token, fencing_epoch):
                        raise RuntimeError("operation_lease_lost")
                    if not await complete_roll(runtime_id, holder_token, fencing_epoch,
                                               owner_ref, operation_id, fingerprint, target_generation,
                                               target_config_ref, new_snapshot.content_hash, operation_result):
                        raise RuntimeError("operation_lease_lost")
                else:
                    await self.repository.complete_input_operation(owner_ref, operation_id, operation_result)
                return result
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    try: await heartbeat
                    except asyncio.CancelledError: pass
                if release is not None and fencing_epoch is not None:
                    try: await release(runtime_id, holder_token, fencing_epoch)
                    except Exception: log.exception("best-effort roll lease release failed")

    async def submit_input(self, runtime_id: str, generation: int,
                           value: RuntimeInput):
        if not isinstance(value.content, str):
            raise InvalidSpec("runtime input content must be a string")
        try:
            encoded = value.content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InvalidSpec("runtime input content must be valid UTF-8") from exc
        if len(encoded) > self.max_input_bytes:
            self.record_input_rejection(runtime_id, generation, "runtime_input_bytes")
            raise ResourceExhausted("runtime input exceeds configured byte limit")
        key = (runtime_id, generation)
        operation_key = (runtime_id, generation, value.request_id)
        fingerprint = hashlib.sha256(encoded).hexdigest()
        async with self._locks.setdefault(key, asyncio.Lock()):
            existing = self._input_tasks.get(operation_key)
            if existing is not None:
                if existing[0] != fingerprint:
                    raise Conflict("operation_id reused with different input")
                task = existing[1]
            else:
                reservation = self._input_reservations.get(key)
                if reservation is not None:
                    owner_ref = f"runtime-input:{runtime_id}:{generation}"
                    read_operation = getattr(self.repository, "get_operation", None)
                    durable = (
                        await read_operation(owner_ref, value.request_id)
                        if read_operation is not None else None
                    )
                    if durable is not None:
                        if durable.fingerprint != fingerprint:
                            raise Conflict("operation_id reused with different input")
                        payload = durable.result
                        if isinstance(payload, Mapping) and payload.get("status") == "completed":
                            return self._turn_result_from_operation(payload["result"])
                        if isinstance(payload, Mapping) and payload.get("status") == "pending":
                            raise RuntimeError("operation_in_progress")
                    self.record_input_rejection(
                        runtime_id, generation, "runtime_input_inflight"
                    )
                    raise ResourceExhausted("runtime input is already in flight")
                task = asyncio.create_task(
                    self._submit_input_claimed(key, value, fingerprint),
                    name=f"runtime-input:{runtime_id}:{generation}:{value.request_id}",
                )
                self._input_tasks[operation_key] = (fingerprint, task)
                self._input_reservations[key] = (operation_key, fingerprint, task)
                self._input_available.setdefault(key, asyncio.Event()).clear()
                task.add_done_callback(
                    lambda done, runtime_key=key, op=operation_key, input_value=value:
                    self._release_input_reservation(runtime_key, op, input_value, done)
                )
        return await asyncio.shield(task)

    async def submit_input_async(self, runtime_id: str, generation: int,
                                  value: RuntimeInput) -> InputReceipt:
        """Validate, admit, and enqueue input; return an ``InputReceipt`` immediately.

        The background Turn continues executing independently.  Callers
        receive HTTP 202 and use the receipt to poll ``read_output``.
        Idempotency, single-inflight fencing, and byte limits are identical
        to the synchronous ``submit_input`` path.
        """
        if not isinstance(value.content, str):
            raise InvalidSpec("runtime input content must be a string")
        try:
            encoded = value.content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InvalidSpec("runtime input content must be valid UTF-8") from exc
        if len(encoded) > self.max_input_bytes:
            self.record_input_rejection(runtime_id, generation, "runtime_input_bytes")
            raise ResourceExhausted("runtime input exceeds configured byte limit")
        key = (runtime_id, generation)
        operation_key = (runtime_id, generation, value.request_id)
        fingerprint = hashlib.sha256(encoded).hexdigest()
        # Snapshot provider/session/transcript position before the background
        # task is scheduled. Taking this later would race with Claude writing
        # the new frames and could skip the beginning of the accepted turn.
        record = await self.repository.get_generation(*key)
        if record is None:
            raise LookupError(runtime_id)
        handle = self._handles.get(key)
        adapter = self.adapters.get(record.spec.adapter_kind) if handle is not None else None
        if adapter is None or not hasattr(adapter, "receipt_context"):
            raise RuntimeError("transcript_not_supported")
        receipt_ctx: Mapping[str, str] = adapter.receipt_context(handle)
        if not receipt_ctx.get("provider") or not receipt_ctx.get("session_id"):
            raise RuntimeError("transcript_not_available")
        async with self._locks.setdefault(key, asyncio.Lock()):
            existing = self._input_tasks.get(operation_key)
            if existing is not None:
                if existing[0] != fingerprint:
                    raise Conflict("operation_id reused with different input")
                # Task already running; surface a receipt immediately for the
                # same operation (idempotent re-delivery).
                pass
            else:
                # When a durable completed result exists for this reservation
                # we must NOT create a duplicate task — just return a receipt.
                must_spawn = True
                owner_ref = f"runtime-input:{runtime_id}:{generation}"
                read_operation = getattr(self.repository, "get_operation", None)
                reservation = self._input_reservations.get(key)
                if reservation is not None:
                    durable = (
                        await read_operation(owner_ref, value.request_id)
                        if read_operation is not None else None
                    )
                    if durable is not None:
                        if durable.fingerprint != fingerprint:
                            raise Conflict("operation_id reused with different input")
                        payload = durable.result
                        if isinstance(payload, Mapping) and payload.get("status") == "completed":
                            # Already completed — return a receipt pointing
                            # past the completed turn.  The caller can use
                            # read_output to retrieve the result.
                            must_spawn = False
                        elif isinstance(payload, Mapping) and payload.get("status") == "pending":
                            raise RuntimeError("operation_in_progress")
                    else:
                        self.record_input_rejection(
                            runtime_id, generation, "runtime_input_inflight"
                        )
                        raise ResourceExhausted("runtime input is already in flight")
                elif read_operation is not None:
                    # Reservation was cleaned up (task completed); check the
                    # durable store for idempotent replay before spawning.
                    durable = await read_operation(owner_ref, value.request_id)
                    if durable is not None:
                        if durable.fingerprint != fingerprint:
                            raise Conflict("operation_id reused with different input")
                        payload = durable.result
                        if isinstance(payload, Mapping) and payload.get("status") == "completed":
                            must_spawn = False
                        elif isinstance(payload, Mapping) and payload.get("status") == "pending":
                            raise RuntimeError("operation_in_progress")
                if must_spawn:
                    task = asyncio.create_task(
                        self._submit_input_claimed(key, value, fingerprint),
                        name=f"runtime-input:{runtime_id}:{generation}:{value.request_id}",
                    )
                    self._input_tasks[operation_key] = (fingerprint, task)
                    self._input_reservations[key] = (operation_key, fingerprint, task)
                    self._input_available.setdefault(key, asyncio.Event()).clear()
                    task.add_done_callback(
                        lambda done, runtime_key=key, op=operation_key, input_value=value:
                        self._release_input_reservation(runtime_key, op, input_value, done)
                    )
        return InputReceipt(
            runtime_id=runtime_id,
            generation=generation,
            provider=receipt_ctx.get("provider", ""),
            session_id=receipt_ctx.get("session_id", ""),
            accepted_at=datetime.now(timezone.utc).isoformat(),
            transcript_cursor=receipt_ctx.get("transcript_cursor", "0"),
            operation_id=value.request_id,
        )

    async def steer_input(self, runtime_id: str, generation: int,
                          value: RuntimeInput) -> bool:
        """Deliver supplemental input to the one active provider turn.

        Steering creates no second Turn, receipt, or stdout consumer.  It is
        available only while this coordinator owns an admitted input for the
        exact runtime generation and the adapter explicitly supports it.
        """
        if not isinstance(value.content, str):
            raise InvalidSpec("runtime input content must be a string")
        try:
            encoded = value.content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InvalidSpec("runtime input content must be valid UTF-8") from exc
        if len(encoded) > self.max_input_bytes:
            self.record_input_rejection(runtime_id, generation, "runtime_input_bytes")
            raise ResourceExhausted("runtime input exceeds configured byte limit")

        key = (runtime_id, generation)
        async with self._locks.setdefault(key, asyncio.Lock()):
            current = await self.repository.get_generation(runtime_id, generation)
            if current is None or current.desired_state is DesiredState.STOPPED or current.actual_state in {
                RuntimeState.DRAINING, RuntimeState.STOPPING, RuntimeState.STOPPED,
            }:
                return False
            if key not in self._input_reservations:
                return False
            handle = self._handles.get(key)
            adapter = self.adapters.get(current.spec.adapter_kind) if handle is not None else None
            steer = getattr(adapter, "steer_input", None)
            if steer is None:
                return False
            steered = await steer(handle, value)
        if not steered:
            return False

        await self.activity.report(runtime_id, generation, ActivityKind.USER_INPUT)
        log.info(
            "runtime input steered into active turn",
            extra={
                "event": "runtime.input.steered",
                "runtime_id": runtime_id,
                "generation": generation,
                "request_id": value.request_id,
                "outcome": "steered",
            },
        )
        emit_metric(
            "runtime_input_total", "counter", 1,
            outcome="steered", runtime_id=runtime_id, generation=generation,
            request_id=value.request_id,
        )
        return True

    async def interrupt_input(self, runtime_id: str, generation: int,
                              reason: str = "player_superseded") -> str | None:
        """Fence the active turn and request a native provider interrupt."""
        key = (runtime_id, generation)
        async with self._locks.setdefault(key, asyncio.Lock()):
            current = await self.repository.get_generation(runtime_id, generation)
            if current is None or current.desired_state is DesiredState.STOPPED or current.actual_state in {
                RuntimeState.DRAINING, RuntimeState.STOPPING, RuntimeState.STOPPED,
            }:
                return None
            reservation = self._input_reservations.get(key)
            if reservation is None:
                return None
            operation_key = reservation[0]
            active_operation_id = str(operation_key[2])
            if self.turn_correlation_registry is not None:
                self.turn_correlation_registry.mark_interrupted(
                    runtime_id, generation, reason or "player_superseded",
                )
            handle = self._handles.get(key)
            adapter = self.adapters.get(current.spec.adapter_kind) if handle is not None else None
            interrupt = getattr(adapter, "interrupt_input", None)
            if interrupt is None:
                return None
            interrupted = await interrupt(handle)
        if not interrupted:
            return None
        log.info(
            "runtime input interrupted by newer player input",
            extra={
                "event": "runtime.input.interrupted",
                "runtime_id": runtime_id,
                "generation": generation,
                "request_id": active_operation_id,
                "reason": reason or "player_superseded",
            },
        )
        emit_metric(
            "runtime_input_total", "counter", 1,
            outcome="interrupted", runtime_id=runtime_id,
            generation=generation, request_id=active_operation_id,
        )
        return active_operation_id

    async def read_output(self, runtime_id: str, generation: int,
                          query: OutputQuery) -> OutputPage:
        """Read and filter the generation-local transcript for *runtime_id*.

        When *query.receipt* is supplied it must be a JSON-serialised
        ``InputReceipt``.  Every identity field is validated before reading.
        Cross-runtime, cross-generation, wrong-provider, wrong-session and
        malformed/out-of-range cursor receipts fail closed.

        Never accepts a caller-controlled filesystem path.
        """
        key = (runtime_id, generation)
        record = await self.repository.get_generation(*key)
        if record is None:
            raise LookupError(runtime_id)
        handle = self._handles.get(key)
        adapter = self.adapters.get(record.spec.adapter_kind)
        if adapter is None or handle is None:
            raise RuntimeError("adapter_unavailable")

        # ── Resolve adapter identity for cross-receipt validation ──
        if not hasattr(adapter, "receipt_context"):
            raise RuntimeError("transcript_not_supported")
        receipt_ctx = adapter.receipt_context(handle)
        current_provider = receipt_ctx.get("provider", "")
        current_session_id = receipt_ctx.get("session_id", "")
        if record.spec.adapter_kind == "claude" and current_provider != "claude":
            raise RuntimeError("receipt_provider_mismatch")
        if not current_provider or not current_session_id:
            raise RuntimeError("transcript_not_available")

        # ── Full receipt validation ──
        if query.receipt is not None:
            try:
                receipt_data = json.loads(query.receipt)
            except (json.JSONDecodeError, TypeError, ValueError):
                raise ValueError("receipt must be a valid JSON InputReceipt")
            if not isinstance(receipt_data, Mapping):
                raise ValueError("receipt must be a JSON object")
            # Validate every identity field; any mismatch fails closed.
            if receipt_data.get("runtime_id") != runtime_id:
                raise RuntimeError("receipt_runtime_mismatch")
            if receipt_data.get("generation") != generation:
                raise RuntimeError("receipt_generation_mismatch")
            if receipt_data.get("provider") != current_provider:
                raise RuntimeError("receipt_provider_mismatch")
            receipt_session = receipt_data.get("session_id")
            if receipt_session != current_session_id:
                raise RuntimeError("receipt_session_mismatch")
            # Cursor validation — must be a non-negative bounded integer.
            try:
                cursor_int = int(str(receipt_data.get("transcript_cursor", "0")))
            except (ValueError, TypeError):
                raise ValueError("receipt cursor must be an integer")
            if cursor_int < 0:
                raise ValueError("receipt cursor must be non-negative")
            # Re-encode the cursor as a plain decimal string for the reader.
            query = OutputQuery(
                receipt=str(cursor_int),
                since=query.since,
                limit=query.limit,
            )

        # ── Locate the native Claude session JSONL ──
        if not hasattr(adapter, "claude_projects_dir"):
            raise RuntimeError("transcript_not_supported")
        projects_dir = adapter.claude_projects_dir(handle)
        if projects_dir is None or not current_session_id:
            raise RuntimeError("transcript_not_available")
        from modules.runtime_manager.transcript import TranscriptReader
        reader = TranscriptReader(projects_dir, current_session_id)
        page = reader.query(query)
        # Stamp the runtime/generation identity onto the page so callers
        # never receive a page with ambiguous provenance.
        return OutputPage(
            entries=page.entries,
            runtime_id=runtime_id,
            generation=generation,
            has_more=page.has_more,
            next_cursor=page.next_cursor,
        )

    def _release_input_reservation(
        self, key: tuple[str, int], operation_key: tuple[str, int, str],
        value: RuntimeInput, task: asyncio.Task,
    ) -> None:
        current = self._input_tasks.get(operation_key)
        if current is not None and current[1] is task:
            self._input_tasks.pop(operation_key, None)
        reservation = self._input_reservations.get(key)
        if reservation is not None and reservation[0] == operation_key and reservation[2] is task:
            self._input_reservations.pop(key, None)
            self._input_available.setdefault(key, asyncio.Event()).set()
        if self.turn_completion_hook is not None:
            asyncio.create_task(
                self._notify_released_turn(key, value, task),
                name=f"runtime-input-completed:{value.request_id}",
            )

    async def wait_input_available(self, runtime_id: str, generation: int,
                                   *, timeout: float) -> bool:
        """Wait until this generation can admit one new Runtime input."""
        if timeout <= 0:
            return False
        key = (runtime_id, generation)
        async with self._locks.setdefault(key, asyncio.Lock()):
            if key not in self._input_reservations:
                return True
            available = self._input_available.setdefault(key, asyncio.Event())
            available.clear()
        try:
            await asyncio.wait_for(available.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def _notify_released_turn(self, key: tuple[str, int], value: RuntimeInput,
                                    task: asyncio.Task) -> None:
        result = None
        error: BaseException | None = None
        try:
            result = task.result()
        except BaseException as task_error:
            error = task_error
        await self._notify_turn_completion(
            key[0], key[1], value, result=result, error=error,
        )

    @staticmethod
    def record_input_rejection(runtime_id: str, generation: int, limit_kind: str) -> None:
        log.warning(
            "runtime input rejected by bounded ingress",
            extra={
                "event": "runtime.input.rejected",
                "runtime_id": runtime_id,
                "generation": generation,
                "limit_kind": limit_kind,
                "error_code": "resource_exhausted",
            },
        )
        emit_metric(
            "runtime_input_rejected_total", "counter", 1,
            outcome="rejected", limit_kind=limit_kind,
            runtime_id=runtime_id, generation=generation,
            error_code="resource_exhausted",
        )

    async def _submit_input_claimed(
        self, key: tuple[str, int], value: RuntimeInput, fingerprint: str
    ):
        runtime_id, generation = key
        owner_ref = f"runtime-input:{runtime_id}:{generation}"
        persisted = await self.repository.claim_input_operation(
            owner_ref, value.request_id, fingerprint
        )
        if persisted.get("status") == "completed":
            payload = persisted["result"]
            return self._turn_result_from_operation(payload)
        if persisted.get("status") != "claimed":
            raise RuntimeError("operation_in_progress")
        log.info(
            "runtime input accepted",
            extra={"event": "runtime.input.accepted", "runtime_id": runtime_id,
                   "generation": generation, "request_id": value.request_id,
                   "outcome": "accepted"},
        )
        emit_metric(
            "runtime_input_total", "counter", 1, outcome="accepted",
            runtime_id=runtime_id, generation=generation, request_id=value.request_id,
        )
        started = time.monotonic()
        try:
            result = await self._submit_input_owned(key, value)
        except Exception as error:
            code = normalize_error_code(getattr(error, "code", None), fallback="internal_error")
            log.warning(
                "runtime input failed",
                extra={"event": "runtime.input.failed", "runtime_id": runtime_id,
                       "generation": generation, "request_id": value.request_id,
                       "outcome": "failed", "error_code": code,
                       "exception_type": type(error).__name__},
            )
            emit_metric(
                "runtime_input_total", "counter", 1, outcome="failed",
                runtime_id=runtime_id, generation=generation, request_id=value.request_id,
                error_code=code,
            )
            emit_metric(
                "runtime_input_duration_ms", "timing", (time.monotonic() - started) * 1000,
                outcome="failed", runtime_id=runtime_id, generation=generation,
                request_id=value.request_id, error_code=code,
            )
            raise
        duration_ms = (time.monotonic() - started) * 1000
        log.info(
            "runtime input completed",
            extra={"event": "runtime.input.completed", "runtime_id": runtime_id,
                   "generation": generation, "request_id": value.request_id,
                   "outcome": "completed", "duration_ms": duration_ms},
        )
        emit_metric(
            "runtime_input_total", "counter", 1, outcome="completed",
            runtime_id=runtime_id, generation=generation, request_id=value.request_id,
        )
        emit_metric(
            "runtime_input_duration_ms", "timing", duration_ms, outcome="completed",
            runtime_id=runtime_id, generation=generation, request_id=value.request_id,
        )
        return result

    @staticmethod
    def _turn_result_from_operation(payload: Mapping[str, object]) -> TurnResult:
        if not payload.get("ok", True):
            raise RuntimeError(str(payload.get("error_code", "internal_error")))
        return TurnResult(
            str(payload["request_id"]), str(payload["runtime_id"]),
            int(payload["generation"]), int(payload["turn"]),
            str(payload["content"]), payload.get("detail", {}),
        )

    async def _submit_input_owned(self, key: tuple[str, int], value: RuntimeInput):
        runtime_id, generation = key
        async with self._locks[key]:
            current = await self.repository.get_generation(runtime_id, generation)
            if current is None or current.desired_state is DesiredState.STOPPED or current.actual_state in {
                RuntimeState.DRAINING, RuntimeState.STOPPING, RuntimeState.STOPPED,
            }:
                raise RuntimeError("already_stopping")
            handle = self._handles.get(key)
            if handle is None:
                raise RuntimeError("adapter_unavailable")
            drained = self._drained.setdefault(key, asyncio.Event())
            drained.clear()
            self._inflight[key] = self._inflight.get(key, 0) + 1
            emit_metric(
                "runtime_input_inflight", "gauge", self._inflight[key],
                outcome="current", runtime_id=runtime_id, generation=generation,
            )
        await self.activity.report(runtime_id, generation, ActivityKind.USER_INPUT)
        log_id = value.trace_context.get("log_id", "") or new_log_id()
        if self.turn_correlation_registry is not None:
            self.turn_correlation_registry.register(
                runtime_id, generation, log_id, value.request_id,
            )
        try:
            try:
                result = await self.adapters[current.spec.adapter_kind].submit_input(handle, value)
            except Exception as error:
                self._record_provider_failure(
                    current.spec.adapter_kind,
                    normalize_error_code(
                        getattr(error, "code", None), fallback="internal_error"
                    ),
                )
                await self.repository.complete_input_operation(
                    f"runtime-input:{runtime_id}:{generation}", value.request_id,
                    {"ok": False, "error_code": "internal_error"},
                )
                raise
            await self.activity.report(runtime_id, generation, ActivityKind.AGENT_OUTPUT)
            await self.repository.complete_input_operation(
                f"runtime-input:{runtime_id}:{generation}", value.request_id,
                {
                    "ok": True, "request_id": result.request_id, "runtime_id": result.runtime_id,
                    "generation": result.generation, "turn": result.turn,
                    "content": result.content, "detail": dict(result.detail),
                },
            )
            return result
        finally:
            if self.turn_correlation_registry is not None:
                self.turn_correlation_registry.clear_if_matches(
                    runtime_id, generation, value.request_id,
                )
            async with self._locks[key]:
                self._inflight[key] -= 1
                emit_metric(
                    "runtime_input_inflight", "gauge", self._inflight[key],
                    outcome="current", runtime_id=runtime_id, generation=generation,
                )
                if self._inflight[key] == 0:
                    drained.set()

    async def _notify_turn_completion(self, runtime_id: str, generation: int,
                                      value: RuntimeInput, *, result, error) -> None:
        if self.turn_completion_hook is None:
            return
        try:
            await self.turn_completion_hook(
                runtime_id, generation, value, result, error,
            )
        except Exception:
            log.exception(
                "runtime turn completion hook failed",
                extra={"event": "runtime.input.completion_hook_failed",
                       "runtime_id": runtime_id, "generation": generation,
                       "request_id": value.request_id, "outcome": "failed"},
            )

    async def stop(self, runtime_id: str, generation: int, operation_id: str,
                   reason: str = "desired_stopped", *, roll_fence=None,
                   require_durable_revocation: bool = False) -> RuntimeProductRecord:
        key = (runtime_id, generation)
        async with self._locks.setdefault(key, asyncio.Lock()):
            current = await self.repository.get_generation(*key)
            if current is None:
                raise LookupError(runtime_id)
            if current.actual_state is RuntimeState.STOPPED:
                return current
            stop_started = time.monotonic()
            already_claimed = (
                current.desired_state is DesiredState.STOPPED
                and current.actual_state is RuntimeState.DRAINING
            )
            if already_claimed and roll_fence is None:
                # idle sweep already performed the durable ingress fence with
                # an atomic activity recheck; do not CAS a second time.
                pass
            elif roll_fence is None:
                current = await self.runtime_service.set_desired_state(
                    runtime_id, generation, DesiredState.STOPPED, operation_id, current.status_version)
            else:
                await self._assert_roll_fence(runtime_id, roll_fence)
                current = await self.repository.fenced_compare_and_swap(
                    replace(current, desired_state=DesiredState.STOPPED, actual_state=RuntimeState.DRAINING),
                    current.status_version, roll_fence[0], roll_fence[1])
            # Ordering is intentional: reject ingress and revoke/fence before drain/process control.
            revocation_intent_id = await self.authority.deny_and_revoke(*key, reason)
            if require_durable_revocation:
                # Controller replacement and explicit control shutdown are
                # security boundaries: the old credential must be durably
                # rejected before process control or successor creation.
                await self._confirm_authority_revoked(revocation_intent_id)
            handle = self._handles.get(key)
            adapter = self.adapters.get(current.spec.adapter_kind)
            drained = self._drained.setdefault(key, asyncio.Event())
            if self._inflight.get(key, 0) == 0:
                drained.set()
        # Do not hold the admission lock while an admitted turn completes.
        if handle is not None and adapter is not None:
            try:
                await asyncio.wait_for(drained.wait(), timeout=self.drain_timeout_seconds)
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.wait_for(
                    adapter.checkpoint(handle, self.drain_timeout_seconds),
                    timeout=self.drain_timeout_seconds,
                )
            except Exception:
                pass
            current = await self._transition(current, RuntimeState.STOPPING, ready=False, roll_fence=roll_fence)
            await self._assert_roll_fence(runtime_id, roll_fence)
            stop_error: Optional[Exception] = None
            for _ in range(2):
                try:
                    await adapter.stop(handle, reason, self.stop_grace_seconds)
                    stop_error = None
                    break
                except Exception as error:
                    stop_error = error
            if stop_error is not None:
                self._handles.pop(key, None)
                if self.emergency_terminator is None:
                    await self._transition(
                        current, RuntimeState.STOPPING, ready=False,
                        exit_reason=f"stop_failed:{stop_error}"[:250],
                        roll_fence=roll_fence,
                    )
                    _record_runtime_stop(current, stop_started, "failed", "adapter_stop_failed")
                    raise RuntimeError("adapter_unavailable") from stop_error
                emergency = await self.emergency_terminator.terminate(
                    runtime_id, generation, handle.workdir
                )
                if not emergency.terminated:
                    await self._transition(
                        current, RuntimeState.FAILED, ready=False,
                        exit_reason=f"quarantine:{emergency.reason}"[:250],
                        roll_fence=roll_fence,
                    )
                    _record_runtime_stop(current, stop_started, "failed", "runtime_quarantined")
                    raise RuntimeError("runtime_quarantined") from stop_error
                # Only verified process disappearance authorizes evidence cleanup.
                await self._cleanup(key)
                stopped = await self._transition(
                    current, RuntimeState.STOPPED, ready=False,
                    exit_reason=f"emergency_{emergency.reason}",
                    roll_fence=roll_fence,
                )
                _record_runtime_stop(stopped, stop_started, "success")
                return stopped
        elif current.actual_state is not RuntimeState.STOPPED:
            # A restarted Manager has no driveable handle. Leave durable desired
            # STOPPED+DRAINING for reconcile; never claim a process was stopped.
            return current
        await self._cleanup(key)
        stopped = await self._transition(current, RuntimeState.STOPPED, ready=False, exit_reason=reason,
                                         roll_fence=roll_fence)
        _record_runtime_stop(stopped, stop_started, "success")
        return stopped

    async def sweep_idle(self, now: Optional[datetime] = None) -> int:
        moment = now or datetime.now(timezone.utc)
        stopped = 0
        for record in await self.repository.list_active():
            if record.desired_state is not DesiredState.RUNNING or record.actual_state not in {
                RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
            }:
                continue
            last = record.last_effective_activity_at or record.started_at
            ttl = record.spec.idle_ttl_seconds
            if last is None or (moment - last).total_seconds() < ttl:
                continue
            claimed = await self._claim_idle_stop(
                record, moment - timedelta(seconds=ttl)
            )
            if claimed is None:
                continue
            await self.stop(record.runtime_id, record.generation,
                            f"idle:{record.runtime_id}:{record.generation}", "idle_ttl_expired")
            stopped += 1
        return stopped

    async def _claim_idle_stop(
        self, candidate: RuntimeProductRecord, cutoff: datetime,
    ) -> Optional[RuntimeProductRecord]:
        """Re-read and rejudge bounded CAS misses without crossing activity."""
        for _ in range(3):
            last = candidate.last_effective_activity_at or candidate.started_at
            if (
                candidate.desired_state is not DesiredState.RUNNING
                or candidate.actual_state not in {
                    RuntimeState.READY_UNBOUND, RuntimeState.RUNNING_BOUND, RuntimeState.IDLE,
                }
                or last is None
                or last > cutoff
            ):
                return None
            claim_idle_stop = getattr(self.repository, "claim_idle_stop", None)
            if claim_idle_stop is not None:
                claimed = await claim_idle_stop(
                    candidate.runtime_id, candidate.generation, candidate.status_version,
                    candidate.actual_state, cutoff,
                )
            else:
                # In-memory/legacy repositories linearize through their CAS.
                try:
                    claimed = await self.repository.compare_and_swap(
                        replace(
                            candidate, desired_state=DesiredState.STOPPED,
                            actual_state=RuntimeState.DRAINING,
                        ),
                        candidate.status_version,
                    )
                except Conflict:
                    claimed = None
            if claimed is not None:
                return claimed
            current = await self.repository.get_generation(
                candidate.runtime_id, candidate.generation
            )
            if current is None:
                return None
            candidate = current
        return None

    async def reconcile_all(self) -> int:
        changed = 0
        for current in await self.repository.list_active():
            key = (current.runtime_id, current.generation)
            if key in self._handles:
                adapter = self.adapters.get(current.spec.adapter_kind)
                if adapter is not None:
                    try:
                        if await self._reconcile_owned_health(key, self._handles[key], adapter):
                            changed += 1
                    except Exception:
                        log.exception("owned health reconcile failed for %s/%s", *key)
                continue
            # `provision` deliberately persists intent before the product has
            # an ACTIVE Binding and credential, so no Agent process or owner
            # file exists yet.  This is not an orphan.  Leave the exact
            # never-started state for the explicit desired-state `ensure`
            # boundary.  This slice adds no provisioning TTL: an abandoned
            # zero-resource intent converges only through explicit desired
            # STOPPED/operator cancellation.
            # Any startup trace (started_at or owner file), any later state,
            # or desired STOPPED still goes through fail-closed reconcile.
            workdir = self.workspace_root / current.runtime_id / f"generation-{current.generation}"
            if (
                current.desired_state is DesiredState.RUNNING
                and current.actual_state is RuntimeState.PROVISIONING
                and current.started_at is None
                and not (workdir / "runtime-owner.json").exists()
            ):
                continue
            adapter = self.adapters.get(current.spec.adapter_kind)
            if adapter is None:
                await self.authority.deny_and_revoke(current.runtime_id, current.generation, "adapter_unavailable")
                await self._fail(current, "adapter_unavailable")
                changed += 1
                continue
            try:
                artifact = await self.persona_reader.read(
                    current.spec.persona_artifact_ref, current.spec.user_id, current.spec.agentee_id
                )
                if artifact.agentee_id != current.spec.agentee_id:
                    raise ValueError("persona_owner_mismatch")
                PersonaArtifactMaterializer(self.workspace_root).materialize(
                    current.runtime_id, current.generation, artifact
                )
                config_snapshot = await self.adapter_config_resolver.resolve(
                    current.spec.adapter_config_ref, current.runtime_id, current.spec.user_id,
                    current.spec.agentee_id, current.spec.adapter_kind,
                )
            except Exception as error:
                await self.authority.deny_and_revoke(current.runtime_id, current.generation, "persona_rebuild_failed")
                await self._fail(current, str(error))
                changed += 1
                continue
            owner_nonce = ""
            config_changed = False
            try:
                owner = json.loads((workdir / "runtime-owner.json").read_text(encoding="utf-8"))
                if owner.get("runtime_id") == current.runtime_id and owner.get("generation") == current.generation:
                    owner_nonce = str(owner.get("nonce", ""))
                    persisted_config_hash = owner.get("adapter_config_hash")
                    if persisted_config_hash is not None and persisted_config_hash != config_snapshot.content_hash:
                        config_changed = True
            except (OSError, ValueError, TypeError):
                pass
            if config_changed:
                await self.authority.deny_and_revoke(
                    current.runtime_id, current.generation, "adapter_config_revision_changed"
                )
                await self._fail(current, "adapter_config_revision_changed_requires_new_generation")
                changed += 1
                continue
            decision = await adapter.reconcile(RuntimeRecord(
                current.runtime_id, current.generation,
                RuntimeActualState.STOPPED if current.desired_state is DesiredState.STOPPED else RuntimeActualState.RUNNING,
                RuntimeActualState(current.actual_state.value) if current.actual_state.value in RuntimeActualState._value2member_map_ else RuntimeActualState.FAILED,
                workdir, owner_nonce, exit_reason=current.exit_reason,
            ))
            if decision.action is ReconcileAction.ADOPT:
                adopt = getattr(adapter, "adopt", None)
                if adopt is None:
                    await self.authority.deny_and_revoke(
                        current.runtime_id, current.generation, "adapter_not_reconnectable"
                    )
                    await self._fail(current, "adapter_not_reconnectable")
                    changed += 1
                    continue
                handle = await adopt(current, decision)
                status = await adapter.inspect(handle)
                if not status.healthy or status.returncode is not None:
                    await self.authority.deny_and_revoke(
                        current.runtime_id, current.generation, "adopt_unhealthy"
                    )
                    await self._fail(current, "adopt_unhealthy")
                    changed += 1
                    continue
                if current.desired_state is DesiredState.STOPPED or current.actual_state in {
                    RuntimeState.DRAINING, RuntimeState.STOPPING,
                }:
                    await self.authority.deny_and_revoke(
                        current.runtime_id, current.generation, "reconcile_desired_stop"
                    )
                    try:
                        await adapter.stop(handle, "reconcile_desired_stop", self.stop_grace_seconds)
                    except Exception as error:
                        latest = await self.repository.get_generation(current.runtime_id, current.generation)
                        if latest is not None:
                            await self._transition(
                                latest, RuntimeState.STOPPING, ready=False,
                                exit_reason=f"stop_failed:{error}"[:250],
                            )
                        changed += 1
                        continue
                    await self._cleanup((current.runtime_id, current.generation))
                    latest = await self.repository.get_generation(current.runtime_id, current.generation)
                    if latest is not None:
                        await self._transition(
                            latest, RuntimeState.STOPPED, ready=False,
                            exit_reason="reconciled_stop",
                        )
                    changed += 1
                    continue
                key = (current.runtime_id, current.generation)
                self._handles[key] = handle
                self._inflight[key] = 0
                self._drained.setdefault(key, asyncio.Event()).set()
                changed += 1
                continue
            if decision.action in {ReconcileAction.MARK_FAILED, ReconcileAction.QUARANTINE_UNKNOWN,
                                   ReconcileAction.TERMINATE_ORPHAN}:
                await self.authority.deny_and_revoke(current.runtime_id, current.generation, "reconcile_failed")
                if decision.action is ReconcileAction.TERMINATE_ORPHAN and current.desired_state is DesiredState.STOPPED:
                    await self._cleanup((current.runtime_id, current.generation))
                    await self._transition(current, RuntimeState.STOPPED, ready=False, exit_reason="reconciled_stop")
                else:
                    await self._fail(current, decision.action.value)
                changed += 1
            elif decision.action is ReconcileAction.MARK_STOPPED:
                await self.authority.deny_and_revoke(current.runtime_id, current.generation, "reconcile_stopped")
                await self._cleanup((current.runtime_id, current.generation))
                await self._transition(current, RuntimeState.STOPPED, ready=False, exit_reason="reconciled")
                changed += 1
        # After normal reconcile, attempt bounded rollover for FAILED runtimes
        # that still have desired RUNNING.
        changed += await self._recover_failed_generations()
        return changed

    async def _recover_failed_generations(self) -> int:
        """Bounded rollover: finalize FAILED → STOPPED, create generation+1.

        Retries are finite (max 3 per runtime_id) with exponential backoff
        tracked in-memory (resets on Manager restart).  Idempotent: if a
        user reconnection or another reconciler already handled the rollover,
        the CAS-based transitions will fail with Conflict and this path
        becomes a no-op.
        """
        MAX_ATTEMPTS = 3
        now = time.monotonic()
        recovered = 0
        list_failed = getattr(self.repository, "list_failed_desired_running", None)
        if list_failed is None:
            return 0

        for record in await list_failed():
            rt_id = record.runtime_id
            attempts, last_attempt = self._rollover_tracker.get(rt_id, (0, 0.0))

            if attempts >= MAX_ATTEMPTS:
                if rt_id not in self._rollover_exhausted_logged:
                    self._rollover_exhausted_logged.add(rt_id)
                    log.warning(
                        "runtime automatic rollover exhausted",
                        extra={"event": "runtime.rollover.exhausted", "runtime_id": rt_id,
                               "generation": record.generation, "attempts": attempts,
                               "outcome": "exhausted", "error_code": "rollover_retry_exhausted"},
                    )
                    emit_metric(
                        "runtime_rollover_exhausted_total", "counter", 1,
                        outcome="exhausted", runtime_id=rt_id,
                        generation=record.generation,
                        error_code="rollover_retry_exhausted",
                    )
                continue

            # The first, second and third automatic attempts wait 1s, 2s and
            # 4s respectively.  A Manager restart intentionally resets this
            # in-memory product budget.
            backoff = float(2 ** attempts)
            if last_attempt == 0.0:
                self._rollover_tracker[rt_id] = (attempts, now)
                continue
            if now - last_attempt < backoff:
                continue

            try:
                snapshot = await self._recovery_snapshot(record)
            except RuntimeError:
                # An offline or unauthenticated session cannot authorize a
                # successor.  Keep the failed generation intact for a later
                # bounded attempt; never revoke/stop merely to create an
                # unusable successor.
                continue

            self._rollover_tracker[rt_id] = (attempts + 1, now)

            try:
                old_key = (record.runtime_id, record.generation)
                revocation_intent_id = await self.authority.deny_and_revoke(
                    *old_key, "rollover_from_failed"
                )
                handle = self._handles.get(old_key)
                if handle is not None:
                    adapter = self.adapters.get(record.spec.adapter_kind)
                    if adapter is None:
                        raise RuntimeError("recovery_adapter_unavailable")
                    await adapter.stop(handle, "rollover_from_failed", self.stop_grace_seconds)
                await self._confirm_authority_revoked(revocation_intent_id)
                await self._cleanup(old_key)
                await self._transition(
                    record, RuntimeState.STOPPED, ready=False,
                    exit_reason=f"rollover_to_generation_{record.generation + 1}",
                )
            except Conflict:
                # Another reconciler or user reconnection already finalized
                # this generation.  Skip to the next record.
                continue
            except Exception:
                log.exception(
                    "rollover finalization failed for %s gen %d",
                    rt_id, record.generation,
                )
                continue

            next_spec = replace(record.spec, generation=record.generation + 1)
            try:
                successor = await self.runtime_service.ensure_runtime(next_spec)
            except Conflict:
                successor = await self.repository.get_generation(
                    record.runtime_id, record.generation + 1
                )
                if (
                    successor is None
                    or successor.spec.user_id != next_spec.user_id
                    or successor.spec.agentee_id != next_spec.agentee_id
                    or successor.spec.activation_key != next_spec.activation_key
                    or activation_intent_digest(successor.spec) != activation_intent_digest(next_spec)
                ):
                    raise
            try:
                await self._activate_recovered_generation(record, successor, snapshot)
                recovered += 1
                log.info(
                    "runtime generation rollover completed",
                    extra={
                        "event": "runtime.rollover.completed",
                        "runtime_id": rt_id,
                        "old_generation": record.generation,
                        "new_generation": record.generation + 1,
                        "outcome": "success",
                    },
                )
            except Exception:
                log.exception(
                    "rollover ensure failed for %s gen %d -> %d",
                    rt_id, record.generation, record.generation + 1,
                )
                latest = await self.repository.get(rt_id)
                if (
                    latest is not None
                    and latest.generation == record.generation + 1
                    and latest.actual_state not in {RuntimeState.FAILED, RuntimeState.STOPPED}
                ):
                    await self.authority.deny_and_revoke(
                        latest.runtime_id, latest.generation, "automatic_rollover_failed"
                    )
                    await self._cleanup((latest.runtime_id, latest.generation))
                    try:
                        await self._fail(latest, "automatic_rollover_failed")
                    except Conflict:
                        pass
                continue

        return recovered

    async def _confirm_authority_revoked(self, intent_id: str | None) -> None:
        if intent_id is None:
            return
        if self._recovery_authority_worker is None:
            raise RuntimeError("authority_revocation_unconfirmed")
        outbox = getattr(self._recovery_authority_worker, "outbox", None)
        repository = getattr(outbox, "repository", None)
        if repository is None:
            repository = getattr(self._recovery_authority_worker, "repository", None)
        processed = getattr(repository, "is_intent_processed", None)
        if processed is None:
            raise RuntimeError("authority_revocation_unconfirmed")
        for _ in range(3):
            await self._recovery_authority_worker.run_once()
            if await processed(intent_id):
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("authority_revocation_unconfirmed")

    async def _recovery_snapshot(self, failed: RuntimeProductRecord):
        if self.binding_service is None or self.gateway_projection_repository is None:
            raise RuntimeError("recovery_projection_unavailable")
        old_binding = await self.binding_service.bindings.find_for_runtime(
            failed.runtime_id, failed.generation
        )
        if old_binding is None or not old_binding.game_session_id:
            raise RuntimeError("recovery_game_session_unavailable")
        snapshot = await self.gateway_projection_repository.get(old_binding.game_session_id)
        if (
            snapshot is None or not snapshot.available
            or snapshot.owner_user_id != failed.spec.user_id
            or not snapshot.game_session_id
            or not snapshot.game_instance_nonce
            or not snapshot.connection_id
            or not snapshot.resume_identity
        ):
            raise RuntimeError("recovery_game_session_not_available")
        return snapshot

    async def _activate_recovered_generation(
        self, failed: RuntimeProductRecord, successor: RuntimeProductRecord, snapshot,
    ) -> None:
        """Continue rollover through Binding, credential and process readiness.

        Recovery reuses only the authenticated GameSession projection carried
        by the terminal generation's Binding.  It never mutates that Binding:
        generation+1 receives a new binding_id and a newly fenced credential.
        """
        if self.binding_service is None:
            raise RuntimeError("binding_service_unavailable")
        binding = await self.binding_service.ensure_binding(
            successor.runtime_id, successor.generation, successor.spec.user_id,
            successor.spec.agentee_id,
            f"rollover-binding:{successor.runtime_id}:{successor.generation}",
            snapshot.game_session_id,
        )
        if self.gateway_snapshot_converger is None:
            raise RuntimeError("gateway_snapshot_converger_unavailable")
        binding = await self.gateway_snapshot_converger.converge_snapshot(
            snapshot, binding,
            operation_identity=(
                f"rollover-snapshot:{binding.binding_id}:"
                f"{snapshot.game_session_id}:{snapshot.session_version}"
            ),
        )
        await self.gateway_snapshot_converger.project_runtime_state(binding)
        if self._recovery_authority_worker is None:
            raise RuntimeError("recovery_authority_worker_unavailable")
        await self._recovery_authority_worker.run_once()
        await self.ensure(successor.spec)

    async def _reconcile_owned_health(self, key: tuple[str, int], handle: RuntimeHandle,
                                       adapter) -> bool:
        """Inspect an owned handle's live-process health and converge durable state.

        Returns True when durable state was changed.  Respects terminal/fenced
        states (DRAINING, STOPPING, STOPPED, FAILED) and desired STOPPED —
        health reconcile must never resurrect a runtime that is already exiting.
        """
        runtime_id, generation = key
        if (handle.runtime_id, handle.generation) != key:
            log.error(
                "owned handle identity mismatch: key=%s/%s handle=%s/%s",
                runtime_id, generation, handle.runtime_id, handle.generation,
            )
            return False

        # Check durable state first — never inspect a terminal/fenced runtime.
        current = await self.repository.get_generation(runtime_id, generation)
        if current is None:
            return False
        if current.actual_state in {
            RuntimeState.DRAINING, RuntimeState.STOPPING,
            RuntimeState.STOPPED, RuntimeState.FAILED,
        }:
            return False
        if current.desired_state is DesiredState.STOPPED:
            return False

        # Inspect the live process.
        try:
            status = await adapter.inspect(handle)
        except Exception as exc:
            status = None
            inspect_error = str(exc)
        else:
            inspect_error = None

        process_alive = (
            status is not None
            and status.healthy
            and status.returncode is None
        )

        if process_alive:
            # Healthy owned process — no state change, no version bump.
            return False

        self._record_provider_failure(
            current.spec.adapter_kind,
            "process_exited" if status is not None and status.returncode is not None else "unknown",
        )

        # Process is dead or uninspectable — fail closed.
        if status is not None and status.returncode is not None:
            exit_reason = f"process_exited:returncode={status.returncode}"
        elif inspect_error:
            exit_reason = f"inspect_failed:{inspect_error}"
        else:
            exit_reason = "process_unhealthy"
        exit_reason = exit_reason[:250]

        # CAS loop: re-read durable state, re-evaluate fencing, attempt transition.
        for _ in range(3):
            current = await self.repository.get_generation(runtime_id, generation)
            if current is None:
                return False
            # Respect terminal/fenced states — never resurrect.
            if current.actual_state in {
                RuntimeState.DRAINING, RuntimeState.STOPPING,
                RuntimeState.STOPPED, RuntimeState.FAILED,
            }:
                return False
            if current.desired_state is DesiredState.STOPPED:
                return False
            try:
                await self.authority.deny_and_revoke(runtime_id, generation, exit_reason)
                failed = await self._transition(
                    current, RuntimeState.FAILED, ready=False,
                    exit_reason=exit_reason,
                )
                failure_kind = "process_exited" if status is not None and status.returncode is not None else "unknown"
                log.warning(
                    "runtime process exit detected",
                    extra={"event": "runtime.adapter.process_exit", "runtime_id": runtime_id,
                           "generation": generation,
                           "adapter_kind": _metric_adapter_kind(failed.spec.adapter_kind),
                           "runtime_state": failed.actual_state.value,
                           "failure_kind": failure_kind, "outcome": "failed",
                           "error_code": "runtime_process_exited"},
                )
                emit_metric(
                    "runtime_adapter_process_exit_total", "counter", 1,
                    outcome="failed", stage="exit", runtime_id=runtime_id,
                    generation=generation,
                    adapter_kind=_metric_adapter_kind(failed.spec.adapter_kind),
                    runtime_state=failed.actual_state.value,
                    failure_kind=failure_kind, error_code="runtime_process_exited",
                )
                return True
            except Conflict:
                continue
        return False

    async def _assert_roll_fence(self, runtime_id: str, roll_fence) -> None:
        if roll_fence is None: return
        holder_token, fencing_epoch, lost = roll_fence
        if lost.is_set() or not await self.repository.validate_roll_lease(runtime_id, holder_token, fencing_epoch):
            lost.set()
            raise RuntimeError("operation_lease_lost")

    async def _cleanup_cancelled_start(self, current: RuntimeProductRecord, adapter,
                                       handle: Optional[RuntimeHandle], key: tuple[str, int],
                                       roll_fence) -> None:
        # Every critical step is shielded so that even if this task receives a
        # CancelledError mid-cleanup, revoke, real-subprocess-stop, handle/workdir
        # teardown, and the best-effort FAILED audit all complete.
        await asyncio.shield(self.authority.deny_and_revoke(*key, "startup_cancelled"))
        if handle is not None:
            try:
                await asyncio.shield(adapter.stop(handle, "startup_cancelled", self.stop_grace_seconds))
            except BaseException:
                log.exception("cancelled startup provider cleanup failed")
        # _cleanup is synchronous (no await points) so it is inherently atomic.
        await self._cleanup(key)
        try:
            await asyncio.shield(self._fail(current, "startup_cancelled", roll_fence=roll_fence))
        except Exception:
            # A successor fence owns future state transitions; durable state is
            # intentionally left for that owner rather than permitting stale writes.
            log.warning("cancelled startup lost roll fence before FAILED audit", exc_info=True)

    async def _transition(self, record: RuntimeProductRecord, state: RuntimeState,
                          *, roll_fence=None, **changes) -> RuntimeProductRecord:
        updated = replace(record, actual_state=state, **changes)
        if roll_fence is None:
            committed = await self.repository.compare_and_swap(updated, record.status_version)
        else:
            await self._assert_roll_fence(record.runtime_id, roll_fence)
            committed = await self.repository.fenced_compare_and_swap(
                updated, record.status_version, roll_fence[0], roll_fence[1])
        log.info(
            "runtime lifecycle transition",
            extra={"event": "runtime.lifecycle.transition", "runtime_id": record.runtime_id,
                   "generation": record.generation, "runtime_state": state.value,
                   "outcome": "success", "from_state": record.actual_state.value},
        )
        emit_metric(
            "runtime_transition_total", "counter", 1, outcome="success",
            runtime_id=record.runtime_id, generation=record.generation,
            runtime_state=state.value,
        )
        return committed

    async def _fail(self, record: RuntimeProductRecord, reason: str, *, roll_fence=None) -> RuntimeProductRecord:
        latest = await self.repository.get_generation(record.runtime_id, record.generation)
        if latest is None or latest.actual_state in {RuntimeState.FAILED, RuntimeState.STOPPED}:
            return latest or record
        return await self._transition(latest, RuntimeState.FAILED, ready=False,
                                      exit_reason=reason[:250], roll_fence=roll_fence)

    async def _cleanup(self, key: tuple[str, int]) -> None:
        self._handles.pop(key, None)
        self._inflight.pop(key, None)
        self._drained.pop(key, None)
        self._input_available.pop(key, None)
        for operation_key in [candidate for candidate in self._input_tasks if candidate[:2] == key]:
            task = self._input_tasks[operation_key][1]
            if task.done():
                self._input_tasks.pop(operation_key, None)
        workdir = (self.workspace_root / key[0] / f"generation-{key[1]}").resolve()
        if workdir.is_relative_to(self.workspace_root):
            if os.name == "nt":
                def remove_readonly(function, path, _exc_info):
                    os.chmod(path, stat.S_IWRITE)
                    function(path)
                try:
                    shutil.rmtree(workdir, onerror=remove_readonly)
                except OSError:
                    pass
            else:
                shutil.rmtree(workdir, ignore_errors=True)

    async def shutdown(self) -> None:
        """Fence and drain every locally-owned handle before process exit."""
        identities = tuple(self._handles)
        for runtime_id, generation in identities:
            try:
                await self.stop(
                    runtime_id, generation,
                    f"manager-shutdown:{runtime_id}:{generation}", "manager_shutdown",
                )
            except Exception:
                log.exception("runtime shutdown failed; scheduling orphan reconcile")
        # Stop failures intentionally retained their owner files. With handles
        # detached, provider reconcile can escalate/terminate those orphans.
        await self.reconcile_all()
        remaining = [
            record for record in await self.repository.list_active()
            if record.desired_state is DesiredState.STOPPED
        ]
        if self._handles or remaining:
            raise RuntimeError("runtime_shutdown_incomplete")


class BindingExpiryWorker:
    """Durably expires Binding authority and repairs Runtime projection gaps."""

    def __init__(self, bindings: AsyncBindingRepository, projector: HostedGatewayEventConsumer) -> None:
        self.bindings = bindings
        self.projector = projector

    async def run_once(self, limit: int = 50) -> int:
        expired = await self.bindings.expire_due(limit)
        projected_ids = set()
        for binding in expired:
            log.info(
                "runtime binding state changed",
                extra={"event": "runtime.binding.transition", "runtime_id": binding.runtime_id,
                       "generation": binding.generation, "binding_id": binding.binding_id,
                       "binding_state": binding.status.value, "outcome": "success"},
            )
            emit_metric(
                "runtime_binding_transition_total", "counter", 1, outcome="success",
                runtime_id=binding.runtime_id, generation=binding.generation,
                binding_id=binding.binding_id, binding_state=binding.status.value,
            )
            projected_ids.add(binding.binding_id)
            try:
                await self.projector.project_runtime_state(binding)
            except Exception:
                log.exception("Binding expiry Runtime projection failed: %s", binding.binding_id)
        for binding in await self.bindings.list_expired_for_projection(limit):
            if binding.binding_id in projected_ids:
                continue
            try:
                await self.projector.project_runtime_state(binding)
            except Exception:
                log.exception("Binding expiry recovery projection failed: %s", binding.binding_id)
        return len(expired)


class RuntimeManagerWorkers:
    def __init__(self, coordinator: RuntimeManagerCoordinator, authority_worker,
                 interval_seconds: float = 5.0, gateway_event_worker=None,
                 binding_expiry_worker=None) -> None:
        self.coordinator = coordinator
        self.authority_worker = authority_worker
        coordinator._recovery_authority_worker = authority_worker
        self.gateway_event_worker = gateway_event_worker
        self.binding_expiry_worker = binding_expiry_worker
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._worker_state: dict[str, dict[str, object]] = {}

    def worker_state_snapshot(self) -> Mapping[str, Mapping[str, object]]:
        """Read-only copy for the later health composition; never drives work."""
        return {key: dict(value) for key, value in self._worker_state.items()}

    async def start(self) -> None:
        await self.coordinator.reconcile_all()
        self._tasks = [asyncio.create_task(self._loop("idle_sweep", self.coordinator.sweep_idle)),
                       asyncio.create_task(self._loop("reconcile", self.coordinator.reconcile_all)),
                       asyncio.create_task(self._loop("authority", self.authority_worker.run_once))]
        if self.gateway_event_worker is not None:
            self._tasks.append(asyncio.create_task(self._loop("gateway_outbox", self.gateway_event_worker.run_once)))
        if self.binding_expiry_worker is not None:
            self._tasks.append(asyncio.create_task(self._loop("binding_expiry", self.binding_expiry_worker.run_once)))

    async def _loop(self, worker_kind: str, operation) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            log.info(
                "Runtime Manager background batch started",
                extra={"event": "runtime.worker.batch.started", "worker_kind": worker_kind,
                       "outcome": "started"},
            )
            emit_metric(
                "runtime_worker_batch_total", "counter", 1,
                outcome="started", worker_kind=worker_kind,
            )
            try:
                result = await operation()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                duration_ms = (time.monotonic() - started) * 1000
                error_code = normalize_error_code(
                    getattr(error, "code", None), fallback="internal_error"
                )
                state = dict(self._worker_state.get(worker_kind, {}))
                state.update({
                    "last_error_at": datetime.now(timezone.utc).isoformat(),
                    "last_error_code": error_code,
                    "last_duration_ms": duration_ms,
                })
                self._worker_state[worker_kind] = state
                log.error(
                    "Runtime Manager background operation failed",
                    extra={"event": "runtime.worker.batch.failed", "worker_kind": worker_kind,
                           "outcome": "failed", "error_code": error_code,
                           "exception_type": type(error).__name__, "duration_ms": duration_ms},
                )
                emit_metric(
                    "runtime_worker_batch_total", "counter", 1, outcome="failed",
                    worker_kind=worker_kind, error_code=error_code,
                )
                emit_metric(
                    "runtime_worker_batch_duration_ms", "timing", duration_ms,
                    outcome="failed", worker_kind=worker_kind, error_code=error_code,
                )
            else:
                duration_ms = (time.monotonic() - started) * 1000
                batch_size = result if isinstance(result, int) and not isinstance(result, bool) else 0
                state = dict(self._worker_state.get(worker_kind, {}))
                state.update({
                    "last_success_at": datetime.now(timezone.utc).isoformat(),
                    "last_duration_ms": duration_ms,
                    "last_batch_size": batch_size,
                })
                self._worker_state[worker_kind] = state
                log.info(
                    "Runtime Manager background batch completed",
                    extra={"event": "runtime.worker.batch.completed", "worker_kind": worker_kind,
                           "outcome": "success", "duration_ms": duration_ms,
                           "batch_size": batch_size},
                )
                emit_metric(
                    "runtime_worker_batch_total", "counter", 1, outcome="success",
                    worker_kind=worker_kind,
                )
                emit_metric(
                    "runtime_worker_batch_size", "gauge", batch_size, outcome="current",
                    worker_kind=worker_kind,
                )
                emit_metric(
                    "runtime_worker_batch_duration_ms", "timing", duration_ms,
                    outcome="success", worker_kind=worker_kind,
                )
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.coordinator.shutdown()
