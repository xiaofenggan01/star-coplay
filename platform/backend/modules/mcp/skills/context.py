"""M3.4 SkillExecutionContext — capability facade for Skill implementations.

Private module; the runtime constructs the context.  Implementations receive
it as the first argument to their ``run(ctx, arguments)`` coroutine.
"""
from __future__ import annotations

import asyncio
import dataclasses
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from modules.mcp.contracts.models import (
    CommandState,
    ExecutionContext,
    GameCommandResult,
    SideEffect,
)
from modules.mcp.capabilities.models import (
    CapabilityInvocationHandle,
    CapabilityRef,
    CapabilityRuntime,
    CapabilityRuntimeError,
)
from modules.mcp.skills.runtime_models import (
    SkillAuditEvent,
    SkillAuditSink,
    SkillExecutionAuthorization,
    SkillRuntimeErrorCode,
)


# ===================================================================
# CapabilityStepFailure — private, never escapes context
# ===================================================================


class _CapabilityStepFailure(Exception):
    """Private exception raised inside the context when a capability step
    cannot return a normal result.

    ``code`` is always a ``SkillRuntimeErrorCode`` member.  ``command_id``
    is ``None`` when no command was started.  ``unknown`` is ``True`` when
    the capability dispatch outcome cannot be determined.

    Exception message is ``code.value`` — it never carries arguments,
    output, underlying exception, or raw capability error code.
    """

    def __init__(
        self,
        code: SkillRuntimeErrorCode,
        command_id: str | None,
        unknown: bool = False,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.command_id = command_id
        self.unknown = unknown
        self.detail = dict(detail or {})


# ===================================================================
# Shared execution record — owned by runtime, shared with context
# ===================================================================


@dataclasses.dataclass
class _ExecutionRecord:
    """Mutable record shared between ``SkillRuntime`` and
    ``SkillExecutionContext``.  All mutations happen under ``lock``."""

    lock: asyncio.Lock
    cancellation_event: asyncio.Event
    active_handle: CapabilityInvocationHandle | None = None
    capability_start_in_flight: bool = False
    capability_start_settled: asyncio.Event = dataclasses.field(
        default_factory=asyncio.Event,
    )
    capability_start_attempted: bool = False
    command_ids: list[str] = dataclasses.field(default_factory=list)
    mutating_capability_may_have_started: bool = False
    consumed_handle: bool = False


# ===================================================================
# SkillExecutionContext
# ===================================================================


class SkillExecutionContext:
    """Per-execution facade to the Capability Runtime.

    Implementations receive one instance and must not store or share it
    across tasks.  All capability invocations are serialized — at most one
    active at a time.
    """

    def __init__(
        self,
        authorization: SkillExecutionAuthorization,
        execution_context: ExecutionContext,
        capability_runtime: CapabilityRuntime,
        skill_execution_id: str,
        tool_call_id: str,
        skill_deadline: float,
        clock: Callable[[], float],
        wait: Callable[[float], Awaitable[None]],
        audit: SkillAuditSink,
        record: _ExecutionRecord,
    ) -> None:
        self._authorization = authorization
        self._execution_context = execution_context
        self._capability_runtime = capability_runtime
        self._skill_execution_id = skill_execution_id
        self._tool_call_id = tool_call_id
        self._skill_deadline = skill_deadline
        self._clock = clock
        self._wait = wait
        self._audit = audit
        self._record = record

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def invoke(
        self,
        capability_id: str,
        version_range: str,
        arguments: Mapping[str, Any],
        *,
        deadline: float | None = None,
    ) -> GameCommandResult:
        """Convenience: ``start`` followed by ``await_result``."""
        handle = await self.start(
            capability_id, version_range, arguments, deadline=deadline,
        )
        return await self.await_result(handle, deadline=deadline)

    async def start(
        self,
        capability_id: str,
        version_range: str,
        arguments: Mapping[str, Any],
        *,
        deadline: float | None = None,
    ) -> CapabilityInvocationHandle:
        """Begin a capability invocation.

        Serialized — at most one active per execution.  The capability must
        be declared in the Skill's dependency bindings with an exact
        ``CapabilityRef`` and matching ``version_range``.
        """
        # --- Resolve capability binding ---------------------------------------
        cap_ref = self._resolve_binding(capability_id, version_range)
        effective_deadline = self._effective_capability_deadline(deadline)

        async with self._record.lock:
            # Check cancellation
            if self._record.cancellation_event.is_set():
                raise asyncio.CancelledError()

            # Check no active handle
            if self._record.active_handle is not None:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.EXECUTION_CONFLICT, None,
                )

            # Mark attempted and in-flight
            self._record.capability_start_attempted = True
            self._record.capability_start_in_flight = True
            self._record.capability_start_settled.clear()

        try:
            # Derive downward ExecutionContext
            derived_ctx = self._derive_execution_context()

            # Call CapabilityRuntime.start
            try:
                handle = await self._capability_runtime.start(
                    derived_ctx,
                    cap_ref,
                    arguments,
                    tool_call_id=self._tool_call_id,
                    now=self._clock(),
                    deadline=effective_deadline,
                )
            except CapabilityRuntimeError:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_FAILED, None,
                )
            except Exception:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_FAILED, None,
                )

            # Install handle under lock
            async with self._record.lock:
                if handle.side_effect is SideEffect.MUTATING:
                    self._record.mutating_capability_may_have_started = True
                # Check if cancelled during start
                if self._record.cancellation_event.is_set():
                    # Transfer to termination owner, never expose to implementation
                    self._record.active_handle = handle
                    self._record.command_ids.append(handle.command_id)
                    raise asyncio.CancelledError()

                self._record.active_handle = handle
                self._record.command_ids.append(handle.command_id)
                self._record.consumed_handle = False

            # Emit audit
            self._emit_capability_audit(
                "skill.capability.started",
                handle.command_id,
                {
                    "capability_id": capability_id,
                    "capability_version": cap_ref.contract_version,
                },
            )

            return handle
        finally:
            async with self._record.lock:
                self._record.capability_start_in_flight = False
                self._record.capability_start_settled.set()

    async def await_result(
        self,
        handle: CapabilityInvocationHandle,
        *,
        deadline: float | None = None,
    ) -> GameCommandResult:
        """Wait for a previously started capability to complete.

        Only the exact *handle* returned by ``start`` on this execution
        is accepted.  After cancellation, raises ``asyncio.CancelledError``.
        """
        # Validate ownership and check cancellation
        async with self._record.lock:
            self._check_cancelled_locked()
            if self._record.active_handle is None:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.EXECUTION_CONFLICT, None,
                )
            if handle is not self._record.active_handle:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.EXECUTION_CONFLICT, None,
                )
            if self._record.consumed_handle:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.EXECUTION_CONFLICT, None,
                )
            self._record.consumed_handle = True
            active_handle = self._record.active_handle
            command_id = active_handle.command_id

        effective_deadline = self._effective_capability_deadline(deadline)

        # Convergence loop
        try:
            result = await self._converge_capability(
                active_handle, command_id, effective_deadline,
            )
        except _CapabilityStepFailure as failure:
            # A started command always receives a terminal capability audit,
            # including UNKNOWN/transport failures.  Keep classification
            # sanitized and correlated to the exact command id.
            state = "UNKNOWN" if failure.unknown else "FAILED"
            self._emit_capability_audit(
                "skill.capability.result", command_id,
                {"state": state, "error_code": failure.code.value},
            )
            raise
        except asyncio.CancelledError:
            self._emit_capability_audit(
                "skill.capability.result", command_id,
                {"state": "CANCELLED", "error_code": "cancelled"},
            )
            raise

        # A normalized terminal result consumes and releases the exact active
        # handle, allowing the implementation to begin its next serial step.
        async with self._record.lock:
            if self._record.active_handle is not active_handle:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.EXECUTION_CONFLICT, command_id,
                )
            self._record.active_handle = None

        # Handle terminal result
        if result.state == CommandState.SUCCEEDED:
            self._emit_capability_audit(
                "skill.capability.result",
                command_id,
                {"state": result.state.value, "error_code": result.error_code},
            )
            return result

        # FAILED, CANCELLED, TIMED_OUT — raise
        self._emit_capability_audit(
            "skill.capability.result",
            command_id,
            {"state": result.state.value, "error_code": result.error_code},
        )
        raise _CapabilityStepFailure(
            SkillRuntimeErrorCode.CAPABILITY_FAILED,
            command_id,
            detail=_capability_failure_detail(result),
        )

    async def get_status(self, command_id: str) -> CommandState:
        """Get the current state of a command started by this execution."""
        async with self._record.lock:
            self._check_cancelled_locked()
            if command_id not in self._record.command_ids:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.EXECUTION_CONFLICT, None,
                )

        derived_ctx = self._derive_execution_context()
        try:
            return await self._capability_runtime.get_status(derived_ctx, command_id)
        except Exception:
            raise _CapabilityStepFailure(
                SkillRuntimeErrorCode.CAPABILITY_FAILED, command_id,
            )

    def is_cancelled(self) -> bool:
        """Return ``True`` when this execution has been cancelled."""
        return self._record.cancellation_event.is_set()

    def check_cancelled(self) -> None:
        """Raise ``asyncio.CancelledError`` when cancelled; no-op otherwise."""
        if self._record.cancellation_event.is_set():
            raise asyncio.CancelledError()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_binding(
        self, capability_id: str, version_range: str,
    ) -> CapabilityRef:
        """Find the exact ``CapabilityRef`` for *capability_id* in the
        authorization's dependency bindings, verifying the version range
        matches the declared requirement."""
        for binding in self._authorization.dependency_bindings:
            req = binding.requirement
            if req.capability_id == capability_id:
                if req.version_range != version_range:
                    raise _CapabilityStepFailure(
                        SkillRuntimeErrorCode.CAPABILITY_UNKNOWN, None,
                    )
                if binding.capability_ref is None:
                    raise _CapabilityStepFailure(
                        SkillRuntimeErrorCode.CAPABILITY_UNKNOWN, None,
                    )
                return binding.capability_ref

        # Not found in bindings
        raise _CapabilityStepFailure(
            SkillRuntimeErrorCode.CAPABILITY_UNKNOWN, None,
        )

    def _effective_capability_deadline(
        self, requested: float | None,
    ) -> float:
        """Compute ``min(skill deadline, requested deadline)``, ensuring
        the result never extends the Skill deadline."""
        try:
            now = self._clock()
        except Exception:
            raise _CapabilityStepFailure(
                SkillRuntimeErrorCode.CAPABILITY_FAILED, None,
            )
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise _CapabilityStepFailure(
                SkillRuntimeErrorCode.CAPABILITY_FAILED, None,
            )
        if requested is not None and (
            not isinstance(requested, (int, float))
            or isinstance(requested, bool)
            or not math.isfinite(requested)
        ):
            raise _CapabilityStepFailure(
                SkillRuntimeErrorCode.CAPABILITY_FAILED, None,
            )
        effective = self._skill_deadline if requested is None else min(
            self._skill_deadline, requested,
        )
        if not math.isfinite(effective) or effective <= now:
            raise _CapabilityStepFailure(
                SkillRuntimeErrorCode.CAPABILITY_FAILED, None,
            )
        return effective

    def _derive_execution_context(self) -> ExecutionContext:
        """Create a downward ``ExecutionContext`` preserving every authority
        field and adding ``skill_execution_id`` to trace_context."""
        new_trace = dict(self._execution_context.trace_context)
        new_trace["skill_execution_id"] = self._skill_execution_id
        return dataclasses.replace(
            self._execution_context,
            trace_context=new_trace,
        )

    async def _converge_capability(
        self,
        handle: CapabilityInvocationHandle,
        command_id: str,
        effective_deadline: float | None,
    ) -> GameCommandResult:
        """Converge a started capability to a terminal result.

        Implements the contract convergence loop:
        - SUCCEEDED/FAILED/CANCELLED/TIMED_OUT → call await_result once more
        - CREATED/ACCEPTED/RUNNING/UNKNOWN → poll get_status with wait intervals
        - EXPIRED_UNKNOWN or deadline → CAPABILITY_UNKNOWN
        """
        derived_ctx = self._derive_execution_context()

        # Always ask CapabilityRuntime for its normalized result first.  Only
        # a non-terminal answer enters same-command status convergence.
        try:
            initial = await self._capability_runtime.await_result(
                derived_ctx, handle, deadline=effective_deadline,
            )
        except Exception:
            raise _CapabilityStepFailure(
                SkillRuntimeErrorCode.CAPABILITY_FAILED, command_id,
            )
        if initial.state in (
            CommandState.SUCCEEDED,
            CommandState.FAILED,
            CommandState.CANCELLED,
            CommandState.TIMED_OUT,
        ):
            return initial

        while True:
            try:
                state = await self._capability_runtime.get_status(
                    derived_ctx, command_id,
                )
            except Exception:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_FAILED, command_id,
                )

            if state in (
                CommandState.SUCCEEDED,
                CommandState.FAILED,
                CommandState.CANCELLED,
                CommandState.TIMED_OUT,
            ):
                # Terminal — call await_result once more
                try:
                    result = await self._capability_runtime.await_result(
                        derived_ctx, handle, deadline=effective_deadline,
                    )
                except Exception:
                    raise _CapabilityStepFailure(
                        SkillRuntimeErrorCode.CAPABILITY_FAILED, command_id,
                    )
                if result.state in (
                    CommandState.SUCCEEDED,
                    CommandState.FAILED,
                    CommandState.CANCELLED,
                    CommandState.TIMED_OUT,
                ):
                    return result
                # Non-terminal after terminal get_status → UNKNOWN
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_UNKNOWN,
                    command_id, unknown=True,
                )

            if state == CommandState.EXPIRED_UNKNOWN:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_UNKNOWN,
                    command_id, unknown=True,
                )

            # state is CREATED/ACCEPTED/RUNNING/UNKNOWN — wait and retry
            if effective_deadline is not None:
                remaining = effective_deadline - self._clock()
                if remaining <= 0:
                    raise _CapabilityStepFailure(
                        SkillRuntimeErrorCode.CAPABILITY_UNKNOWN,
                        command_id, unknown=True,
                    )
                wait_seconds = min(0.05, max(0, remaining))
            else:
                wait_seconds = 0.05

            # Also check Skill-level deadline
            skill_remaining = self._skill_deadline - self._clock()
            if skill_remaining <= 0:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_UNKNOWN,
                    command_id, unknown=True,
                )
            wait_seconds = min(wait_seconds, max(0, skill_remaining))

            if wait_seconds <= 0:
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.CAPABILITY_UNKNOWN,
                    command_id, unknown=True,
                )

            # Check cancellation before waiting
            if self._record.cancellation_event.is_set():
                raise asyncio.CancelledError()

            await self._wait(wait_seconds)

    def _emit_capability_audit(
        self, event_name: str, command_id: str, detail: Mapping[str, Any],
    ) -> None:
        """Emit step audit; production durable sinks fail the step closed."""
        try:
            auth = self._authorization
            self._audit.emit(SkillAuditEvent(
                event=event_name,
                request_id=self._execution_context.trace_context.get("request_id"),
                log_id=self._execution_context.trace_context.get("log_id"),
                tool_call_id=self._tool_call_id,
                skill_execution_id=self._skill_execution_id,
                skill_id=auth.skill_ref.skill_id,
                skill_version=auth.skill_ref.version,
                manifest_digest=auth.manifest_digest,
                implementation_digest=auth.implementation_digest,
                command_id=command_id,
                runtime_ref=self._execution_context.runtime_ref,
                binding_ref=auth.binding_ref,
                binding_version=auth.binding_version,
                game_session_ref=auth.game_session_ref,
                capability_digest=auth.capability_digest,
                detail=detail,
            ))
        except Exception as exc:
            if getattr(self._audit, "fail_closed", False):
                raise _CapabilityStepFailure(
                    SkillRuntimeErrorCode.INTERNAL, command_id, unknown=True,
                ) from exc

    def _check_cancelled_locked(self) -> None:
        """Must be called under ``_record.lock``."""
        if self._record.cancellation_event.is_set():
            raise asyncio.CancelledError()


def _capability_failure_detail(result: GameCommandResult) -> Mapping[str, Any]:
    """Return only the Mod's safe structured error fields for MCP recovery."""
    nested = result.output.get("error")
    error = nested if isinstance(nested, Mapping) else {}
    code = result.error_code or error.get("code")
    detail: dict[str, Any] = {"state": result.state.value}
    if isinstance(code, str) and code:
        detail["code"] = code
    for field in ("message", "detail"):
        value = error.get(field)
        if isinstance(value, str) and value:
            detail[field] = value
    return {"capability_error": detail}
