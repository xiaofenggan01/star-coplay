"""M3.4 SkillRuntime — protocol-neutral coroutine execution lifecycle.

Owns Python coroutine execution; delegates every game operation to
``CapabilityRuntime``.  It has no concrete transport or composition dependencies.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any

from modules.mcp.contracts.models import ExecutionContext
from modules.mcp.capabilities.models import (
    CapabilityInvocationHandle,
    CapabilityRuntime,
    CapabilityRuntimeError,
)
from modules.mcp.skills.context import (
    SkillExecutionContext,
    _CapabilityStepFailure,
    _ExecutionRecord,
)
from modules.mcp.skills.loader import LoadedSkillProgram
from modules.mcp.skills.models import (
    EffectiveSkillPolicy,
    RiskDecision,
    ResolvedSkill,
    ResolvedSkillCatalog,
    SkillRef,
)
from modules.mcp.skills.runtime_models import (
    SkillAuditEvent,
    SkillAuditSink,
    SkillCancelResult,
    SkillExecutionAuthorization,
    SkillExecutionHandle,
    SkillExecutionState,
    SkillFailure,
    SkillResult,
    SkillRuntimeError,
    SkillRuntimeErrorCode,
)
from modules.mcp.capabilities.contracts import (
    JsonSchemaValidationError,
    validate_json_schema,
)
from common.observability import emit_metric, normalize_error_code

log = logging.getLogger("mcp.skill.runtime")
log.addHandler(logging.NullHandler())

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ID_LEN = 256
_MAX_REASON_LEN = 512
_MAX_JSON_DEPTH = 64


# ===================================================================
# In-memory store record
# ===================================================================


class _StoreRecord:
    """Single Skill execution tracked in the in-memory store."""

    __slots__ = (
        "handle", "state", "task", "cancellation_event", "exec_record",
        "terminal_result", "claimed", "termination_owner",
        "authorization", "execution_context",
        "started_at",
    )

    def __init__(
        self,
        handle: SkillExecutionHandle,
        task: asyncio.Task[None],
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        started_at: float,
    ) -> None:
        self.handle = handle
        self.state: SkillExecutionState = SkillExecutionState.CREATED
        self.task = task
        self.cancellation_event = asyncio.Event()
        self.exec_record = _ExecutionRecord(
            lock=asyncio.Lock(),
            cancellation_event=self.cancellation_event,
        )
        self.terminal_result: SkillResult | None = None
        self.claimed: bool = False
        self.termination_owner: bool = False
        self.authorization = authorization
        self.execution_context = execution_context
        self.started_at = started_at


# ===================================================================
# JSON-compatible tree gate
# ===================================================================


def _validate_json_compatible(
    value: Any, depth: int = 0, active: set[int] | None = None,
) -> None:
    """Recursively gate *value* as a finite JSON-compatible tree.

    Allowed: ``None``, ``bool``, ``str``, ``int``, finite ``float``,
    ``list``, ``Mapping`` with ``str`` keys.
    Rejected: ``bytes``, ``tuple``, ``set``, ``enum``, custom objects,
    non-finite numbers, cycles, and depth > 64.
    """
    if depth > _MAX_JSON_DEPTH:
        raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
        return
    if isinstance(value, bytes):
        raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
    if isinstance(value, tuple):
        raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
    if isinstance(value, (set, frozenset)):
        raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
    if isinstance(value, (list, Mapping)):
        if active is None:
            active = set()
        identity = id(value)
        if identity in active:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
        active.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_compatible(item, depth + 1, active)
            else:
                for k, v in value.items():
                    if not isinstance(k, str):
                        raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
                    _validate_json_compatible(v, depth + 1, active)
        finally:
            active.remove(identity)
        return

    # Any other type (enum, custom object, etc.)
    raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)


def _defensive_deep_copy(
    value: Any, depth: int = 0, active: set[int] | None = None,
) -> Any:
    """Deep-copy/freeze a JSON-compatible value into a privately-owned
    structure suitable for passing to implementation and schema validation."""
    if depth > _MAX_JSON_DEPTH:
        raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
        return value
    if isinstance(value, (list, Mapping)):
        if active is None:
            active = set()
        identity = id(value)
        if identity in active:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
        active.add(identity)
        try:
            if isinstance(value, list):
                return [_defensive_deep_copy(v, depth + 1, active) for v in value]
            copied: dict[str, Any] = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
                copied[key] = _defensive_deep_copy(child, depth + 1, active)
            return copied
        finally:
            active.remove(identity)
    raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)


# ===================================================================
# SkillRuntime
# ===================================================================


class SkillRuntime:
    """Protocol-neutral coroutine executor for Skill programs.

    Constructed with an immutable program mapping, a ``CapabilityRuntime``,
    an audit sink, and injectable clock / uuid / wait dependencies.
    """

    def __init__(
        self,
        programs: Mapping[SkillRef, LoadedSkillProgram],
        capability_runtime: CapabilityRuntime,
        audit: SkillAuditSink,
        clock: Callable[[], float],
        uuid_factory: Callable[[], str],
        wait: Callable[[float], Awaitable[None]],
    ) -> None:
        # Validate and copy programs
        if not isinstance(programs, Mapping):
            raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
        private: dict[SkillRef, LoadedSkillProgram] = {}
        try:
            for ref, prog in programs.items():
                if not isinstance(ref, SkillRef) or not isinstance(prog, LoadedSkillProgram):
                    raise ValueError
                expected_ref = SkillRef(
                    skill_id=prog.package.manifest.skill_id,
                    version=prog.package.manifest.version,
                )
                if ref != expected_ref or ref in private:
                    raise ValueError
                private[ref] = prog
        except Exception as exc:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL) from exc
        self._programs: Mapping[SkillRef, LoadedSkillProgram] = MappingProxyType(private)
        self._capability_runtime = capability_runtime
        self._audit = audit
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._wait = wait

        # In-memory store: skill_execution_id → _StoreRecord
        self._store: dict[str, _StoreRecord] = {}
        self._store_lock = asyncio.Lock()

    # ==================================================================
    # Public API
    # ==================================================================

    async def start(
        self,
        resolved_catalog: ResolvedSkillCatalog,
        resolved_skill: ResolvedSkill,
        authorization: SkillExecutionAuthorization,
        arguments: Mapping[str, Any],
        execution_context: ExecutionContext,
        *,
        tool_call_id: str,
        skill_execution_id: str | None = None,
        deadline: float | None = None,
    ) -> SkillExecutionHandle:
        """Validate, authorize, create an execution record, and begin the
        Skill coroutine.  Returns immediately with a handle."""
        now = self._clock()
        if not math.isfinite(now):
            raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)

        # --- Validate IDs ----------------------------------------------------
        if not isinstance(tool_call_id, str) or not tool_call_id or len(tool_call_id) > _MAX_ID_LEN:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

        if skill_execution_id is None:
            try:
                skill_execution_id = self._uuid_factory()
            except Exception:
                raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
            if (
                not isinstance(skill_execution_id, str)
                or not skill_execution_id
                or len(skill_execution_id) > _MAX_ID_LEN
            ):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
        elif (
            not isinstance(skill_execution_id, str)
            or not skill_execution_id
            or len(skill_execution_id) > _MAX_ID_LEN
        ):
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

        # --- Compute skill deadline -------------------------------------------
        pkg = resolved_skill.package
        skill_deadline = now + pkg.manifest.timeout_seconds
        if deadline is not None:
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
            skill_deadline = min(skill_deadline, deadline)
        if execution_context.expires_at is not None:
            if not math.isfinite(execution_context.expires_at):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
            skill_deadline = min(skill_deadline, execution_context.expires_at)
        if skill_deadline <= now:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

        # --- Authorization gates ---------------------------------------------
        self._authorize(
            resolved_catalog, resolved_skill, authorization, execution_context,
        )

        # --- Locate program ---------------------------------------------------
        skill_ref = SkillRef(
            skill_id=pkg.manifest.skill_id,
            version=pkg.manifest.version,
        )
        prog = self._programs.get(skill_ref)
        if prog is None:
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

        # Program ↔ authorization cross-check
        if prog.package.manifest_digest != authorization.manifest_digest:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if prog.implementation_digest != authorization.implementation_digest:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if prog.package.manifest != resolved_skill.package.manifest:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if prog.package.package_root != resolved_skill.package.package_root:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if prog.package.implementation_path != resolved_skill.package.implementation_path:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

        # --- Schema gate (before any async boundary) -------------------------
        copied_args = _defensive_deep_copy(arguments)
        _validate_json_compatible(copied_args)
        try:
            validate_json_schema(pkg.manifest.input_schema, copied_args)
        except JsonSchemaValidationError:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
        except Exception:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

        # --- Build handle & record -------------------------------------------
        handle = SkillExecutionHandle(
            skill_execution_id=skill_execution_id,
            skill_ref=skill_ref,
            tool_call_id=tool_call_id,
            deadline_at=skill_deadline,
        )

        async with self._store_lock:
            if skill_execution_id in self._store:
                raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_CONFLICT)

            # The start event is the only fail-closed audit event.  It must
            # succeed before either a task or store record exists.
            self._emit_started_audit(
                authorization, execution_context, skill_execution_id,
                tool_call_id, pkg.manifest.skill_id, pkg.manifest.version,
            )

            task = asyncio.create_task(
                self._run_implementation(
                    prog, resolved_skill, authorization, copied_args,
                    execution_context, skill_execution_id, tool_call_id,
                    skill_deadline, handle,
                )
            )

            record = _StoreRecord(
                handle=handle, task=task,
                authorization=authorization,
                execution_context=execution_context,
                started_at=now,
            )
            self._store[skill_execution_id] = record

        log.info(
            "skill execution started",
            extra={"event": "skill.execution.started", "log_id": execution_context.trace_context.get("log_id"),
                   "runtime_id": execution_context.runtime_ref, "generation": execution_context.generation,
                   "skill_execution_id": skill_execution_id, "tool_call_id": tool_call_id,
                   "outcome": "started"},
        )
        emit_metric(
            "skill_execution_total", "counter", 1, outcome="started",
            runtime_id=execution_context.runtime_ref, generation=execution_context.generation,
            skill_execution_id=skill_execution_id, tool_call_id=tool_call_id,
        )

        return handle

    async def await_result(
        self,
        handle: SkillExecutionHandle,
        *,
        deadline: float | None = None,
    ) -> SkillResult:
        """Wait for the execution identified by *handle* to complete.

        An optional *deadline* controls only this wait — the task continues
        running if the wait times out.
        """
        record = self._require_record(handle)

        if deadline is not None:
            if not math.isfinite(deadline):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

        effective_wait: float | None = None
        if deadline is not None:
            now = self._clock()
            effective_wait = max(0, deadline - now)

        try:
            if effective_wait is not None:
                await asyncio.wait_for(
                    asyncio.shield(record.task),
                    timeout=effective_wait,
                )
            else:
                await asyncio.shield(record.task)
        except asyncio.TimeoutError:
            raise SkillRuntimeError(SkillRuntimeErrorCode.TIMEOUT)

        # Task completed — extract terminal result
        async with self._store_lock:
            if record.terminal_result is None or record.claimed or self._store.get(handle.skill_execution_id) is not record:
                # Task completed but no terminal result was committed
                # (should not happen, but guard)
                raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_NOT_FOUND)

            result = record.terminal_result
            record.claimed = True
            self._store.pop(handle.skill_execution_id, None)
            return result

    async def execute(
        self,
        resolved_catalog: ResolvedSkillCatalog,
        resolved_skill: ResolvedSkill,
        authorization: SkillExecutionAuthorization,
        arguments: Mapping[str, Any],
        execution_context: ExecutionContext,
        *,
        tool_call_id: str,
        skill_execution_id: str | None = None,
        deadline: float | None = None,
    ) -> SkillResult:
        """Convenience: ``start`` followed by ``await_result``."""
        handle = await self.start(
            resolved_catalog, resolved_skill, authorization, arguments,
            execution_context,
            tool_call_id=tool_call_id,
            skill_execution_id=skill_execution_id,
            deadline=deadline,
        )
        return await self.await_result(handle)

    async def cancel(
        self,
        handle: SkillExecutionHandle,
        *,
        reason: str,
    ) -> SkillCancelResult:
        """Request cancellation of a running execution.

        Returns a ``SkillCancelResult`` with ``accepted`` indicating whether
        cancellation was initiated.  Non-cancellable Skills always return
        ``accepted=False``.
        """
        # Validate reason
        if not isinstance(reason, str) or not reason or len(reason) > _MAX_REASON_LEN:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)

        record = self._require_record(handle)

        # Check cancellable
        pkg = record.handle.skill_ref
        prog = self._programs.get(pkg)
        if prog is None or not prog.package.manifest.cancellable:
            return SkillCancelResult(
                skill_execution_id=handle.skill_execution_id,
                accepted=False,
                state=record.state,
                error_code=SkillRuntimeErrorCode.NOT_CANCELLABLE.value,
            )

        self._emit_audit(
            record.authorization, record.execution_context,
            "skill.execution.cancel_requested",
            handle.skill_execution_id, handle.tool_call_id,
            skill_id=pkg.skill_id, skill_version=pkg.version,
            command_id=None, detail={},
        )

        return await self._terminate(record, is_cancel=True)

    # ==================================================================
    # Authorization gates
    # ==================================================================

    def _authorize(
        self,
        resolved_catalog: ResolvedSkillCatalog,
        resolved_skill: ResolvedSkill,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
    ) -> None:
        """Apply all authorization gates before task creation.

        Raises ``SkillRuntimeError`` on the first violation found.
        Order follows the contract spec.
        """
        if not isinstance(authorization, SkillExecutionAuthorization):
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if not isinstance(authorization.skill_ref, SkillRef):
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        string_fields = (
            authorization.skill_ref.skill_id,
            authorization.skill_ref.version,
            authorization.manifest_digest,
            authorization.implementation_digest,
            authorization.mount_revision,
            authorization.policy_revision,
            authorization.capability_digest,
            authorization.binding_ref,
            authorization.game_session_ref,
        )
        if any(not isinstance(value, str) or not value for value in string_fields):
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if (
            not isinstance(authorization.binding_version, int)
            or isinstance(authorization.binding_version, bool)
            or authorization.binding_version <= 0
        ):
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

        if len(resolved_catalog.skills) != 1:
            raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)
        target = [
            skill for skill in resolved_catalog.skills
            if SkillRef(skill.package.manifest.skill_id, skill.package.manifest.version)
            == authorization.skill_ref
        ]
        if len(target) != 1 or target[0] != resolved_skill:
            raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)

        resolved_ref = SkillRef(
            resolved_skill.package.manifest.skill_id,
            resolved_skill.package.manifest.version,
        )
        if resolved_ref != authorization.skill_ref:
            raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)

        catalog_pins = (
            (authorization.mount_revision, resolved_catalog.mount_revision),
            (authorization.policy_revision, resolved_catalog.policy_revision),
            (authorization.capability_digest, resolved_catalog.capability_digest),
            (authorization.binding_ref, resolved_catalog.binding_ref),
            (authorization.binding_version, resolved_catalog.binding_version),
            (authorization.game_session_ref, resolved_catalog.game_session_ref),
        )
        if any(actual != expected for actual, expected in catalog_pins):
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

        # --- Skill availability ----------------------------------------------
        if not resolved_skill.available:
            raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)
        if resolved_skill.reasons:
            raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)

        # --- Dependency bindings — check for null refs ------------------------
        for binding in (*resolved_skill.dependency_bindings, *authorization.dependency_bindings):
            if binding.capability_ref is None:
                raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)

        # Policy gates are fail-closed on both independently supplied policy
        # values, before equality-pin checks can classify a forged DENY as
        # merely stale.
        for policy in (authorization.effective_policy, resolved_skill.effective_policy):
            if not isinstance(policy, EffectiveSkillPolicy):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
            if not isinstance(policy.decisions, Mapping) or not isinstance(policy.requires_confirmation, bool):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
            if any(
                not isinstance(key, str) or not isinstance(value, RiskDecision)
                for key, value in policy.decisions.items()
            ):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)

        for decision in authorization.effective_policy.decisions.values():
            if decision is RiskDecision.DENY:
                raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)
        if authorization.effective_policy.requires_confirmation:
            raise SkillRuntimeError(SkillRuntimeErrorCode.CONFIRMATION_REQUIRED)

        # --- DENY policy ------------------------------------------------------
        for decision in resolved_skill.effective_policy.decisions.values():
            if decision is RiskDecision.DENY:
                raise SkillRuntimeError(SkillRuntimeErrorCode.SKILL_UNAVAILABLE)

        # --- Confirmation required --------------------------------------------
        if resolved_skill.effective_policy.requires_confirmation:
            raise SkillRuntimeError(SkillRuntimeErrorCode.CONFIRMATION_REQUIRED)

        # --- Pin: manifest digest ---------------------------------------------
        if authorization.manifest_digest != resolved_skill.package.manifest_digest:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

        # --- Pin: implementation digest (skip if not present) -----------------
        # Verified later against loaded program

        # --- Pin: dependency bindings -----------------------------------------
        auth_bindings = authorization.dependency_bindings
        resolved_bindings = resolved_skill.dependency_bindings
        if len(auth_bindings) != len(resolved_bindings):
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        for a, r in zip(auth_bindings, resolved_bindings):
            if a.requirement != r.requirement:
                raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
            if a.capability_ref != r.capability_ref:
                raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

        # --- Pin: effective policy --------------------------------------------
        auth_policy = authorization.effective_policy
        resolved_policy = resolved_skill.effective_policy
        if auth_policy.decisions != resolved_policy.decisions:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
        if auth_policy.requires_confirmation != resolved_policy.requires_confirmation:
            raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

        # --- ExecutionContext mismatch ----------------------------------------
        if authorization.capability_digest != execution_context.capability_digest:
            raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_CONTEXT_MISMATCH)
        if authorization.binding_ref != execution_context.binding_ref:
            raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_CONTEXT_MISMATCH)
        if authorization.binding_version != execution_context.binding_version:
            raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_CONTEXT_MISMATCH)
        if authorization.game_session_ref != execution_context.game_session_ref:
            raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_CONTEXT_MISMATCH)

    # ==================================================================
    # Implementation execution coroutine
    # ==================================================================

    async def _run_implementation(
        self,
        prog: LoadedSkillProgram,
        resolved_skill: ResolvedSkill,
        authorization: SkillExecutionAuthorization,
        arguments: Mapping[str, Any],
        execution_context: ExecutionContext,
        skill_execution_id: str,
        tool_call_id: str,
        skill_deadline: float,
        handle: SkillExecutionHandle,
    ) -> None:
        """Outer execution coroutine — enforces total timeout, runs the
        implementation, and commits the terminal result."""
        record = self._store.get(skill_execution_id)
        if record is None:
            return  # store entry removed before task started

        pkg = prog.package
        manifest = pkg.manifest

        # Transition CREATED → RUNNING
        async with self._store_lock:
            if record.state != SkillExecutionState.CREATED:
                return
            record.state = SkillExecutionState.RUNNING

        # Build context
        ctx = SkillExecutionContext(
            authorization=authorization,
            execution_context=execution_context,
            capability_runtime=self._capability_runtime,
            skill_execution_id=skill_execution_id,
            tool_call_id=tool_call_id,
            skill_deadline=skill_deadline,
            clock=self._clock,
            wait=self._wait,
            audit=self._audit,
            record=record.exec_record,
        )

        total_timeout = skill_deadline - self._clock()

        try:
            if total_timeout <= 0:
                # Deadline already passed
                await self._commit_terminal(
                    record, authorization, execution_context,
                    skill_execution_id, tool_call_id,
                    SkillExecutionState.TIMED_OUT,
                    output={}, error_code=SkillRuntimeErrorCode.TIMEOUT.value,
                    partial_output={},
                    side_effects_may_have_occurred=False,
                )
                return

            # Run implementation with total timeout
            result = await asyncio.wait_for(
                prog.run(ctx, arguments),
                timeout=total_timeout,
            )

            # --- Normal return — validate output schema -----------------------
            output, partial_output, state, error_code = self._validate_implementation_return(
                result, manifest,
            )

            side_effects = record.exec_record.mutating_capability_may_have_started

            # If active capability remains unsettled, best-effort cleanup
            await self._cleanup_active_capability(record, authorization, execution_context)

            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                state, output, error_code, partial_output, side_effects,
            )

        except asyncio.TimeoutError:
            # Total timeout
            await self._handle_total_timeout(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
            )

        except asyncio.CancelledError:
            # Task was cancelled — termination owner commits the terminal
            # result.  Do not commit here; just return.
            return

        except SkillFailure as sf:
            # Business failure
            await self._handle_skill_failure(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id, manifest, sf,
            )

        except _CapabilityStepFailure as csf:
            # Capability failure not caught by implementation
            await self._handle_capability_failure(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id, csf,
            )

        except SkillRuntimeError as error:
            await self._cleanup_active_capability(
                record, authorization, execution_context,
            )
            side_effects = record.exec_record.mutating_capability_may_have_started
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.FAILED,
                output={}, error_code=error.code.value, partial_output={},
                side_effects_may_have_occurred=side_effects,
            )

        except Exception:
            # Any other exception → IMPLEMENTATION_ERROR
            await self._cleanup_active_capability(record, authorization, execution_context)
            side_effects = record.exec_record.mutating_capability_may_have_started
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.FAILED,
                output={},
                error_code=SkillRuntimeErrorCode.IMPLEMENTATION_ERROR.value,
                partial_output={},
                side_effects_may_have_occurred=side_effects,
            )

    # ==================================================================
    # Schema validation for implementation output
    # ==================================================================

    def _validate_implementation_return(
        self, result: Any, manifest: Any,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], SkillExecutionState, str | None]:
        """Validate normal implementation return against output schema."""
        # Must be a Mapping
        if not isinstance(result, Mapping):
            raise SkillRuntimeError(SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION)

        # JSON-compatible gate is part of the public output contract.  Do not
        # let the shared input sanitizer's INVALID_ARGUMENTS escape from the
        # implementation path.
        try:
            copied = _defensive_deep_copy(result)
            _validate_json_compatible(copied)
        except Exception:
            raise SkillRuntimeError(SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION)

        # Schema validation
        try:
            validate_json_schema(manifest.output_schema, copied)
        except (JsonSchemaValidationError, SkillRuntimeError):
            raise SkillRuntimeError(SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION)
        except Exception:
            raise SkillRuntimeError(SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION)

        return (
            MappingProxyType(copied),
            MappingProxyType({}),
            SkillExecutionState.SUCCEEDED,
            None,
        )

    # ==================================================================
    # SkillFailure handling
    # ==================================================================

    async def _handle_skill_failure(
        self,
        record: _StoreRecord,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        skill_execution_id: str,
        tool_call_id: str,
        manifest: Any,
        sf: SkillFailure,
    ) -> None:
        """Process a ``SkillFailure`` raised by the implementation."""
        failure_codes = set(manifest.failure_codes)
        declared = sf.code in failure_codes

        await self._cleanup_active_capability(record, authorization, execution_context)
        side_effects = record.exec_record.mutating_capability_may_have_started

        if not declared:
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.FAILED,
                output={},
                error_code=SkillRuntimeErrorCode.UNDECLARED_FAILURE_CODE.value,
                partial_output={},
                side_effects_may_have_occurred=side_effects,
            )
            return

        # Validate partial_output against output_schema
        partial_output: Mapping[str, Any] = MappingProxyType({})
        if sf.partial_output is not None:
            try:
                copied = _defensive_deep_copy(sf.partial_output)
                _validate_json_compatible(copied)
                validate_json_schema(manifest.output_schema, copied)
                partial_output = MappingProxyType(copied)
            except (SkillRuntimeError, JsonSchemaValidationError):
                await self._commit_terminal(
                    record, authorization, execution_context,
                    skill_execution_id, tool_call_id,
                    SkillExecutionState.FAILED,
                    output={},
                    error_code=SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION.value,
                    partial_output={},
                    side_effects_may_have_occurred=side_effects,
                )
                return
            except Exception:
                await self._commit_terminal(
                    record, authorization, execution_context,
                    skill_execution_id, tool_call_id,
                    SkillExecutionState.FAILED,
                    output={},
                    error_code=SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION.value,
                    partial_output={},
                    side_effects_may_have_occurred=side_effects,
                )
                return

        await self._commit_terminal(
            record, authorization, execution_context,
            skill_execution_id, tool_call_id,
            SkillExecutionState.FAILED,
            output={},
            error_code=sf.code,
            partial_output=partial_output,
            side_effects_may_have_occurred=side_effects,
        )

    # ==================================================================
    # Capability failure handling
    # ==================================================================

    async def _handle_capability_failure(
        self,
        record: _StoreRecord,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        skill_execution_id: str,
        tool_call_id: str,
        csf: _CapabilityStepFailure,
    ) -> None:
        """Process a ``_CapabilityStepFailure`` that escaped the implementation."""
        await self._cleanup_active_capability(record, authorization, execution_context)

        if csf.unknown:
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.UNKNOWN,
                output={},
                error_code=SkillRuntimeErrorCode.CAPABILITY_UNKNOWN.value,
                partial_output=csf.detail,
                side_effects_may_have_occurred=(
                    record.exec_record.mutating_capability_may_have_started
                ),
            )
        else:
            side_effects = record.exec_record.mutating_capability_may_have_started
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.FAILED,
                output={},
                error_code=SkillRuntimeErrorCode.CAPABILITY_FAILED.value,
                partial_output=csf.detail,
                side_effects_may_have_occurred=side_effects,
            )

    # ==================================================================
    # Total timeout handling
    # ==================================================================

    async def _handle_total_timeout(
        self,
        record: _StoreRecord,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        skill_execution_id: str,
        tool_call_id: str,
    ) -> None:
        """Handle total Skill timeout — best-effort capability cancel, then
        commit TIMED_OUT or PARTIAL."""
        # Check if capability was started or attempted
        had_capability = bool(record.exec_record.command_ids)
        mutating_may_have_started = (
            record.exec_record.mutating_capability_may_have_started
        )

        # Best-effort cancel any active capability
        await self._cleanup_active_capability(record, authorization, execution_context)

        if had_capability:
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.PARTIAL,
                output={},
                error_code=SkillRuntimeErrorCode.TIMEOUT.value,
                partial_output={},
                side_effects_may_have_occurred=mutating_may_have_started,
            )
        else:
            await self._commit_terminal(
                record, authorization, execution_context,
                skill_execution_id, tool_call_id,
                SkillExecutionState.TIMED_OUT,
                output={},
                error_code=SkillRuntimeErrorCode.TIMEOUT.value,
                partial_output={},
                side_effects_may_have_occurred=False,
            )

    # ==================================================================
    # Termination (shared by cancel and timeout)
    # ==================================================================

    async def _terminate(
        self,
        record: _StoreRecord,
        is_cancel: bool,
    ) -> SkillCancelResult:
        """Shared termination path for external cancel (and timeout).

        Acquires termination ownership, sets cancellation event, best-effort
        cancels active capability, cancels the Python task, and waits for
        terminal commit.
        """
        async with self._store_lock:
            # Check terminal already committed
            if record.terminal_result is not None:
                return SkillCancelResult(
                    skill_execution_id=record.handle.skill_execution_id,
                    accepted=False,
                    state=record.terminal_result.state,
                    error_code=record.terminal_result.error_code,
                )

            # Check non-cancellable (only for external cancel)
            if is_cancel:
                pkg_ref = record.handle.skill_ref
                prog = self._programs.get(pkg_ref)
                if prog is not None and not prog.package.manifest.cancellable:
                    return SkillCancelResult(
                        skill_execution_id=record.handle.skill_execution_id,
                        accepted=False,
                        state=record.state,
                        error_code=SkillRuntimeErrorCode.NOT_CANCELLABLE.value,
                    )

            # Take termination ownership
            if record.termination_owner:
                return SkillCancelResult(
                    skill_execution_id=record.handle.skill_execution_id,
                    accepted=False,
                    state=record.state,
                    error_code=None,
                )
            record.termination_owner = True

        # Set cancellation event
        record.cancellation_event.set()

        # Wait for capability_start_settled if in-flight
        if record.exec_record.capability_start_in_flight:
            try:
                remaining = record.handle.deadline_at - self._clock()
                if remaining > 0:
                    await asyncio.wait_for(
                        record.exec_record.capability_start_settled.wait(),
                        timeout=remaining,
                    )
            except asyncio.TimeoutError:
                pass

        # Best-effort cancel active capability
        await self._cancel_active_capability(record)

        # Cancel Python task
        if not record.task.done():
            record.task.cancel()
            try:
                await record.task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        # Commit terminal result (may already be committed by a race-winner)
        async with self._store_lock:
            if record.terminal_result is not None:
                # Normal completion won the race
                terminal = record.terminal_result
                return SkillCancelResult(
                    skill_execution_id=record.handle.skill_execution_id,
                    accepted=False,
                    state=terminal.state,
                    error_code=terminal.error_code,
                )
            # Commit under our ownership
            had_capability = (
                len(record.exec_record.command_ids) > 0
                or record.exec_record.capability_start_attempted
            )
            mutating_may_have_started = (
                record.exec_record.mutating_capability_may_have_started
            )
            state = SkillExecutionState.PARTIAL if had_capability else SkillExecutionState.CANCELLED
            error_code = (
                SkillRuntimeErrorCode.CANCELLED.value
                if is_cancel
                else SkillRuntimeErrorCode.TIMEOUT.value
            )
            result = SkillResult(
                skill_execution_id=record.handle.skill_execution_id,
                skill_ref=record.handle.skill_ref,
                state=state,
                output=MappingProxyType({}),
                error_code=error_code,
                partial_output=MappingProxyType({}),
                capability_command_ids=tuple(record.exec_record.command_ids),
                side_effects_may_have_occurred=mutating_may_have_started,
            )
            record.terminal_result = result
            terminal = result

        # Emit finished audit with the exact correlation captured at start.
        pkg = record.handle.skill_ref
        self._emit_audit(
            record.authorization, record.execution_context,
            "skill.execution.finished",
            record.handle.skill_execution_id, record.handle.tool_call_id,
            skill_id=pkg.skill_id, skill_version=pkg.version,
            command_id=None,
            detail={
                "state": state.value,
                "error_code": error_code,
                "side_effects_may_have_occurred": mutating_may_have_started,
            },
        )
        self._record_terminal_observation(record, state, error_code)

        return SkillCancelResult(
            skill_execution_id=record.handle.skill_execution_id,
            accepted=True,
            state=terminal.state,
            error_code=terminal.error_code,
        )

    # ==================================================================
    # Active capability cleanup
    # ==================================================================

    async def _cleanup_active_capability(
        self,
        record: _StoreRecord,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
    ) -> None:
        """Best-effort cancel active capability when implementation returns
        with an unsettled capability."""
        async with record.exec_record.lock:
            if record.exec_record.capability_start_in_flight:
                # If start is in flight, wait for it to settle
                pass  # handled by the caller checking
            handle = record.exec_record.active_handle
            if handle is not None:
                record.exec_record.active_handle = None

        if handle is not None:
            try:
                derived_ctx = dataclasses.replace(
                    execution_context,
                    trace_context={
                        **execution_context.trace_context,
                        "skill_execution_id": record.handle.skill_execution_id,
                    },
                )
                await self._capability_runtime.cancel(
                    derived_ctx, handle, reason="skill_cleanup",
                )
            except Exception:
                pass

    async def _cancel_active_capability(self, record: _StoreRecord) -> None:
        """Best-effort cancel the active capability during termination."""
        async with record.exec_record.lock:
            handle = record.exec_record.active_handle
            record.exec_record.active_handle = None

        if handle is not None:
            try:
                derived_ctx = dataclasses.replace(
                    record.execution_context,
                    trace_context={
                        **record.execution_context.trace_context,
                        "skill_execution_id": record.handle.skill_execution_id,
                    },
                )
                await self._capability_runtime.cancel(
                    derived_ctx, handle, reason="skill_termination",
                )
            except Exception:
                pass

    # ==================================================================
    # Terminal commit
    # ==================================================================

    async def _commit_terminal(
        self,
        record: _StoreRecord,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        skill_execution_id: str,
        tool_call_id: str,
        state: SkillExecutionState,
        output: Mapping[str, Any],
        error_code: str | None,
        partial_output: Mapping[str, Any],
        side_effects_may_have_occurred: bool,
    ) -> None:
        """Atomically commit the terminal result under the store lock."""
        pkg = record.handle.skill_ref

        async with self._store_lock:
            if record.terminal_result is not None:
                return  # already committed (race with cancel)

            command_ids = tuple(record.exec_record.command_ids)
            result = SkillResult(
                skill_execution_id=skill_execution_id,
                skill_ref=SkillRef(skill_id=pkg.skill_id, version=pkg.version),
                state=state,
                output=output,
                error_code=error_code,
                partial_output=partial_output,
                capability_command_ids=command_ids,
                side_effects_may_have_occurred=side_effects_may_have_occurred,
            )
            record.terminal_result = result

        # Emit skill.execution.finished
        self._emit_audit(
            authorization, execution_context,
            "skill.execution.finished",
            skill_execution_id, tool_call_id,
            skill_id=pkg.skill_id, skill_version=pkg.version,
            command_id=None,
            detail={
                "state": state.value,
                "error_code": error_code,
                "side_effects_may_have_occurred": side_effects_may_have_occurred,
            },
        )
        self._record_terminal_observation(record, state, error_code)

    def _record_terminal_observation(
        self, record: _StoreRecord, state: SkillExecutionState, error_code: str | None,
    ) -> None:
        context = record.execution_context
        error_code = normalize_error_code(error_code) if error_code else ""
        duration_ms = max(0.0, (self._clock() - record.started_at) * 1000)
        level = logging.WARNING if state in {
            SkillExecutionState.FAILED, SkillExecutionState.CANCELLED,
            SkillExecutionState.TIMED_OUT, SkillExecutionState.UNKNOWN,
        } else logging.INFO
        log.log(
            level, "skill execution finished",
            extra={"event": "skill.execution.finished", "log_id": context.trace_context.get("log_id"),
                   "runtime_id": context.runtime_ref, "generation": context.generation,
                   "skill_execution_id": record.handle.skill_execution_id,
                   "tool_call_id": record.handle.tool_call_id,
                   "result_state": state.value.lower(), "outcome": "completed",
                   "error_code": error_code or "", "duration_ms": duration_ms},
        )
        dimensions = {
            "result_state": state.value.lower(), "runtime_id": context.runtime_ref,
            "generation": context.generation,
            "skill_execution_id": record.handle.skill_execution_id,
            "tool_call_id": record.handle.tool_call_id, "error_code": error_code or "",
        }
        emit_metric("skill_execution_total", "counter", 1, outcome="completed", **dimensions)
        emit_metric(
            "skill_execution_duration_ms", "timing", duration_ms,
            outcome="completed", **dimensions,
        )

    # ==================================================================
    # Store helpers
    # ==================================================================

    def _require_record(self, handle: SkillExecutionHandle) -> _StoreRecord:
        """Return the ``_StoreRecord`` for *handle*, validating every handle
        field matches the canonical record."""
        record = self._store.get(handle.skill_execution_id)
        if record is None:
            raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_NOT_FOUND)

        canonical = record.handle
        if (
            handle.skill_execution_id != canonical.skill_execution_id
            or handle.skill_ref != canonical.skill_ref
            or handle.tool_call_id != canonical.tool_call_id
            or handle.deadline_at != canonical.deadline_at
        ):
            raise SkillRuntimeError(SkillRuntimeErrorCode.EXECUTION_NOT_FOUND)

        return record

    # ==================================================================
    # Audit helpers
    # ==================================================================

    def _emit_audit(
        self,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        event_name: str,
        skill_execution_id: str,
        tool_call_id: str,
        skill_id: str,
        skill_version: str,
        command_id: str | None,
        detail: Mapping[str, Any],
    ) -> None:
        """Emit audit; production durable sinks fail the execution closed."""
        try:
            self._audit.emit(SkillAuditEvent(
                event=event_name,
                request_id=execution_context.trace_context.get("request_id"),
                log_id=execution_context.trace_context.get("log_id"),
                tool_call_id=tool_call_id,
                skill_execution_id=skill_execution_id,
                skill_id=skill_id,
                skill_version=skill_version,
                manifest_digest=authorization.manifest_digest,
                implementation_digest=authorization.implementation_digest,
                command_id=command_id,
                runtime_ref=execution_context.runtime_ref,
                binding_ref=authorization.binding_ref,
                binding_version=authorization.binding_version,
                game_session_ref=authorization.game_session_ref,
                capability_digest=authorization.capability_digest,
                detail=detail,
            ))
        except Exception as exc:
            if getattr(self._audit, "fail_closed", False):
                raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL) from exc

    def _emit_started_audit(
        self,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        skill_execution_id: str,
        tool_call_id: str,
        skill_id: str,
        skill_version: str,
    ) -> None:
        """Emit the sole fail-closed audit event before task creation."""
        try:
            self._audit.emit(SkillAuditEvent(
                event="skill.execution.started",
                request_id=execution_context.trace_context.get("request_id"),
                log_id=execution_context.trace_context.get("log_id"),
                tool_call_id=tool_call_id,
                skill_execution_id=skill_execution_id,
                skill_id=skill_id,
                skill_version=skill_version,
                manifest_digest=authorization.manifest_digest,
                implementation_digest=authorization.implementation_digest,
                command_id=None,
                runtime_ref=execution_context.runtime_ref,
                binding_ref=authorization.binding_ref,
                binding_version=authorization.binding_version,
                game_session_ref=authorization.game_session_ref,
                capability_digest=authorization.capability_digest,
                detail={"state": SkillExecutionState.CREATED.value},
            ))
        except Exception:
            raise SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
