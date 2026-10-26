"""RouterBackedCapabilityRuntime — M3.2 Proto-only capability execution."""
from __future__ import annotations

import inspect
import logging
import math
import time
import uuid
from typing import Any, Callable, Mapping

from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION,
    CancelResult,
    CommandState,
    DispatchState,
    ExecutionContext,
    GameCommand,
    GameCommandResult,
    GameSessionRouter,
    SideEffect,
)
from modules.mcp.core.audit import AuditEvent, MemoryAuditSink
from modules.mcp.capabilities.models import (
    PROTECTED_ARGUMENTS,
    CapabilityActivityReporter,
    CapabilityAuditSink,
    CapabilityContractCatalog,
    CapabilityErrorCode,
    CapabilityInvocationHandle,
    CapabilityManifestProvider,
    CapabilityRef,
    CapabilityRuntimeError,
    CapabilitySnapshot,
)
from modules.mcp.capabilities.snapshot import build_capability_snapshot
from common.observability import emit_metric, normalize_error_code

log = logging.getLogger("mcp.capability.runtime")
log.addHandler(logging.NullHandler())

# Terminal lifecycle states
_TERMINAL = frozenset({
    CommandState.SUCCEEDED, CommandState.FAILED, CommandState.CANCELLED,
    CommandState.TIMED_OUT, CommandState.EXPIRED_UNKNOWN,
})

# Boundary exception-code → neutral error-code mapping
_CODE_MAP: dict[str, CapabilityErrorCode] = {
    "invalid_arguments": CapabilityErrorCode.INVALID_ARGUMENTS,
    "capability_denied": CapabilityErrorCode.SCOPE_DENIED,
    "context_expired": CapabilityErrorCode.CONTEXT_STALE,
    "binding_unavailable": CapabilityErrorCode.BINDING_UNAVAILABLE,
    "route_unavailable": CapabilityErrorCode.ROUTE_UNAVAILABLE,
    "conflict": CapabilityErrorCode.DISPATCH_CONFLICT,
    "upstream_protocol_error": CapabilityErrorCode.UPSTREAM_CONTRACT_VIOLATION,
}

# States that the orchestrator normalises
_NORMALIZE_STATES = frozenset({CommandState.SUCCEEDED, CommandState.FAILED})
_PARTIAL_NORMALIZE_STATES = frozenset({CommandState.TIMED_OUT, CommandState.CANCELLED})
_PASSTHROUGH_STATES = frozenset({
    CommandState.UNKNOWN, CommandState.CREATED, CommandState.ACCEPTED,
    CommandState.RUNNING, CommandState.EXPIRED_UNKNOWN,
})


# ---------------------------------------------------------------------------
# Private boundary helper
# ---------------------------------------------------------------------------

def _map_exception(exc: Exception) -> CapabilityRuntimeError:
    """Convert a provider/router exception into a neutral runtime error.

    Reads only a string *code* attribute when present.  Exception text is
    **never** copied into ``safe_message``.
    """
    code: str | None = getattr(exc, "code", None)
    if isinstance(code, str):
        mapped = _CODE_MAP.get(code)
        if mapped is not None:
            return CapabilityRuntimeError(mapped, mapped.value, _is_retryable(mapped))
    return CapabilityRuntimeError(
        CapabilityErrorCode.INTERNAL, "internal error", True,
    )


def _is_retryable(code: CapabilityErrorCode) -> bool:
    return code in {
        CapabilityErrorCode.BINDING_UNAVAILABLE,
        CapabilityErrorCode.ROUTE_UNAVAILABLE,
        CapabilityErrorCode.INTERNAL,
    }


def _record_capability_failure(
    context: ExecutionContext, *, stage: str, error: Exception,
    tool_call_id: str = "", command_id: str = "", capability: str = "",
    duration_ms: float | None = None,
) -> CapabilityRuntimeError:
    mapped = _map_exception(error)
    duration = max(0.0, duration_ms or 0.0)
    log.warning(
        "capability boundary operation failed",
        extra={"event": f"capability.execution.{stage}_failed",
               "log_id": context.trace_context.get("log_id"),
               "runtime_id": context.runtime_ref, "generation": context.generation,
               "tool_call_id": tool_call_id, "command_id": command_id,
               "capability": capability, "stage": stage, "outcome": "failed",
               "error_code": mapped.code.value, "exception_type": type(error).__name__,
               "duration_ms": duration},
    )
    emit_metric(
        "capability_execution_duration_ms", "timing", duration,
        outcome="failed", stage=stage, runtime_id=context.runtime_ref,
        generation=context.generation, tool_call_id=tool_call_id,
        command_id=command_id, capability=capability, error_code=mapped.code.value,
    )
    emit_metric(
        "capability_execution_total", "counter", 1, outcome="failed", stage=stage,
        runtime_id=context.runtime_ref, generation=context.generation,
        tool_call_id=tool_call_id, command_id=command_id, capability=capability,
        error_code=mapped.code.value,
    )
    return mapped


# ---------------------------------------------------------------------------
# Runtime implementation
# ---------------------------------------------------------------------------

class RouterBackedCapabilityRuntime:
    """Proto-only Runtime backed by a :class:`GameSessionRouter`.

    Every method may raise :class:`CapabilityRuntimeError`.  This runtime
    contains no MCP Tool names, V2 branches, Gateway/Local imports, or
    in-memory invocation registries.
    """

    def __init__(
        self,
        router: GameSessionRouter,
        manifest_provider: CapabilityManifestProvider,
        contract_catalog: CapabilityContractCatalog,
        context_ref: Callable[[ExecutionContext], str],
        audit: CapabilityAuditSink | None = None,
        activity: CapabilityActivityReporter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._router = router
        self._manifest_provider = manifest_provider
        self._contract_catalog = contract_catalog
        self._context_ref = context_ref
        self._audit: CapabilityAuditSink = audit or MemoryAuditSink()
        self._activity = activity
        self._clock = clock

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------

    async def snapshot(self, context: ExecutionContext) -> CapabilitySnapshot:
        try:
            raw = await self._manifest_provider.entries(context)
        except CapabilityRuntimeError:
            raise
        except Exception as exc:
            raise _map_exception(exc) from exc
        return build_capability_snapshot(
            raw, self._contract_catalog.contracts, context,
        )

    # ------------------------------------------------------------------
    # start
    # ------------------------------------------------------------------

    async def start(
        self,
        context: ExecutionContext,
        capability_ref: CapabilityRef,
        arguments: Mapping[str, Any],
        *,
        tool_call_id: str,
        command_id: str | None = None,
        now: float | None = None,
        deadline: float | None = None,
    ) -> CapabilityInvocationHandle:
        cid = capability_ref.capability_id
        cver = capability_ref.contract_version

        # 1. Obtain the current snapshot
        snap = await self.snapshot(context)

        # 2. Resolve static capability id — absent → UNAVAILABLE
        if not self._contract_catalog.has_capability(cid):
            raise CapabilityRuntimeError(
                CapabilityErrorCode.UNAVAILABLE,
                f"capability not in static catalog: {cid}",
                False,
            )

        # 3. Requested version gate — wrong version → VERSION_UNSUPPORTED
        static_contract = self._contract_catalog.find_exact(cid, "2.4.5")
        if cver != "2.4.5" or static_contract is None:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.VERSION_UNSUPPORTED,
                f"version not supported: {cver}",
                False,
            )

        # 4. Authorize against snapshot.known_contracts
        snap_known_ids = {c.capability_id for c in snap.known_contracts}
        if cid not in snap_known_ids:
            # Distinguish: same id at different version in raw vs completely absent
            raw_versions: dict[str, str] = {}
            for e in snap.raw_entries:
                if e.source_surface == "protocol-2.4.5":
                    raw_versions[e.capability_id] = e.capability_contract_version
            if cid in raw_versions:
                raise CapabilityRuntimeError(
                    CapabilityErrorCode.VERSION_UNSUPPORTED,
                    f"capability {cid} advertised at version {raw_versions[cid]}",
                    False,
                )
            raise CapabilityRuntimeError(
                CapabilityErrorCode.UNAVAILABLE,
                f"capability {cid} not in current snapshot",
                False,
            )

        # 5. Internal-only gate — precedes scope (tp always returns this)
        if static_contract.internal_only or static_contract.adapter is None:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INTERNAL_CONTROL_UNAVAILABLE,
                "internal-only capability cannot be executed through this runtime",
                False,
            )

        # 6. Scope check
        if static_contract.required_scope not in context.scopes:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.SCOPE_DENIED,
                f"required scope {static_contract.required_scope} not in context",
                False,
            )

        # 7. Protected-authority gate
        if PROTECTED_ARGUMENTS.intersection(arguments):
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INVALID_ARGUMENTS,
                "arguments must not contain protected authority fields",
                False,
            )

        # 8. context_ref — callback exceptions become sanitized INTERNAL
        try:
            execution_context_ref = self._context_ref(context)
        except Exception:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INTERNAL,
                "context_ref callback failed",
                False,
            )
        if not isinstance(execution_context_ref, str) or not execution_context_ref:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INTERNAL,
                "context_ref callback returned empty or non-string value",
                False,
            )

        # 9. Stable command identity
        stable_command_id = command_id or str(uuid.uuid4())

        # 10. Adapter preflight
        try:
            prepared = static_contract.adapter.prepare(arguments, stable_command_id)
        except CapabilityRuntimeError:
            raise
        except Exception as exc:
            raise _map_exception(exc) from exc

        # 11. Timing
        issued = now if now is not None else self._clock()
        if not math.isfinite(issued):
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INVALID_ARGUMENTS, "issued_at is not finite", False,
            )

        max_deadline = issued + static_contract.timeout_seconds
        if deadline is None:
            effective_deadline = max_deadline
        else:
            if not math.isfinite(deadline) or deadline <= issued:
                raise CapabilityRuntimeError(
                    CapabilityErrorCode.INVALID_ARGUMENTS,
                    "explicit deadline must be finite and greater than issued_at",
                    False,
                )
            effective_deadline = min(deadline, max_deadline)

        # 12. Route resolution — neutral boundary, refuse unavailable
        try:
            route = await self._router.resolve(context)
        except Exception as exc:
            raise _map_exception(exc) from exc
        if not route.available:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.ROUTE_UNAVAILABLE,
                "game route is unavailable",
                True,
            )

        # 13. Build GameCommand
        command = GameCommand(
            contract_version=GAME_COMMAND_VERSION,
            message_id=str(uuid.uuid4()),
            occurred_at=issued,
            idempotency_key=stable_command_id,
            command_id=stable_command_id,
            tool_call_id=tool_call_id,
            capability_id=static_contract.capability_id,
            capability_version=static_contract.contract_version,
            capability_digest=context.capability_digest,
            operation=static_contract.side_effect,
            validated_payload=prepared.validated_arguments,
            deadline_at=effective_deadline,
            execution_context_ref=execution_context_ref,
            game_instance_nonce=context.game_instance_nonce,
            lease_epoch=(
                context.lease_epoch
                if static_contract.side_effect is SideEffect.MUTATING
                else None
            ),
            trace_context=dict(context.trace_context),
        )

        # 14. Dispatch
        dispatch_started = time.monotonic()
        try:
            receipt = await self._router.dispatch(context, command)
        except Exception as exc:
            raise _record_capability_failure(
                context, stage="dispatch", error=exc, tool_call_id=tool_call_id,
                command_id=stable_command_id, capability=static_contract.capability_id,
                duration_ms=(time.monotonic() - dispatch_started) * 1000,
            ) from exc

        if receipt.state is DispatchState.REJECTED:
            emit_metric(
                "capability_execution_total", "counter", 1, outcome="rejected", stage="dispatch",
                result_state="rejected", runtime_id=context.runtime_ref, generation=context.generation,
                tool_call_id=tool_call_id, command_id=stable_command_id,
                capability=static_contract.capability_id,
            )
            raise CapabilityRuntimeError(
                CapabilityErrorCode.DISPATCH_CONFLICT,
                receipt.detail or "command was rejected",
                False,
            )

        # audit
        self._audit.emit(AuditEvent(
            "command.dispatched", tool_call_id, stable_command_id,
            context.runtime_ref, context.binding_ref, context.game_session_ref,
            {"receipt": receipt.state.value},
            log_id=context.trace_context.get("log_id"),
        ))
        log.info(
            "capability command dispatched",
            extra={"event": "capability.execution.dispatched", "log_id": context.trace_context.get("log_id"),
                   "runtime_id": context.runtime_ref, "generation": context.generation,
                   "tool_call_id": tool_call_id, "command_id": stable_command_id,
                   "capability": static_contract.capability_id, "outcome": receipt.state.value.lower()},
        )
        emit_metric(
            "capability_execution_total", "counter", 1, outcome=receipt.state.value.lower(),
            stage="dispatch", runtime_id=context.runtime_ref, generation=context.generation,
            tool_call_id=tool_call_id, command_id=stable_command_id,
            capability=static_contract.capability_id,
        )

        return CapabilityInvocationHandle(
            command_id=stable_command_id,
            tool_call_id=tool_call_id,
            deadline_at=effective_deadline,
            dispatch_receipt=receipt,
            capability_ref=capability_ref,
            side_effect=static_contract.side_effect,
            validated_arguments=prepared.validated_arguments,
        )

    # ------------------------------------------------------------------
    # await_result
    # ------------------------------------------------------------------

    async def await_result(
        self,
        context: ExecutionContext,
        handle: CapabilityInvocationHandle,
        *,
        deadline: float | None = None,
    ) -> GameCommandResult:
        started = time.monotonic()
        # -- resolve contract from handle ------------------------------------
        contract = self._contract_catalog.find_exact(
            handle.capability_ref.capability_id,
            handle.capability_ref.contract_version,
        )
        if contract is None:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.UNAVAILABLE,
                f"contract not found for handle: {handle.capability_ref.capability_id}",
                False,
            )

        # -- compute effective deadline ---------------------------------------
        if deadline is not None and not math.isfinite(deadline):
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INVALID_ARGUMENTS,
                "explicit deadline must be finite", False,
            )
        effective_deadline = (
            min(deadline, handle.deadline_at)
            if deadline is not None
            else handle.deadline_at
        )

        # -- delegate to Router -----------------------------------------------
        result_started = time.monotonic()
        try:
            result = await self._router.await_result(
                context, handle.command_id, effective_deadline,
            )
        except Exception as exc:
            raise _record_capability_failure(
                context, stage="result", error=exc, tool_call_id=handle.tool_call_id,
                command_id=handle.command_id, capability=handle.capability_ref.capability_id,
                duration_ms=(time.monotonic() - result_started) * 1000,
            ) from exc

        # -- result handling (exact per contract) -----------------------------
        if result.state in _PASSTHROUGH_STATES:
            pass  # return unchanged
        elif result.state in _NORMALIZE_STATES:
            try:
                result = contract.adapter.normalize(handle, result)
            except CapabilityRuntimeError:
                raise
            except Exception as exc:
                raise _map_exception(exc) from exc
        elif result.state in _PARTIAL_NORMALIZE_STATES:
            if result.output:
                try:
                    result = contract.adapter.normalize(handle, result)
                except CapabilityRuntimeError:
                    raise
                except Exception as exc:
                    raise _map_exception(exc) from exc
            # else: return unchanged

        # audit
        self._audit.emit(AuditEvent(
            "command.result", handle.tool_call_id, handle.command_id,
            context.runtime_ref, context.binding_ref, context.game_session_ref,
            {"state": result.state.value, "error_code": result.error_code},
            log_id=context.trace_context.get("log_id"),
        ))
        level = logging.WARNING if result.state in {
            CommandState.FAILED, CommandState.TIMED_OUT, CommandState.UNKNOWN,
            CommandState.EXPIRED_UNKNOWN,
        } else logging.INFO
        duration_ms = (time.monotonic() - started) * 1000
        stable_result_code = normalize_error_code(result.error_code) if result.error_code else ""
        log.log(
            level, "capability command completed",
            extra={"event": "capability.execution.result", "log_id": context.trace_context.get("log_id"),
                   "runtime_id": context.runtime_ref, "generation": context.generation,
                   "tool_call_id": handle.tool_call_id, "command_id": handle.command_id,
                   "capability": handle.capability_ref.capability_id,
                   "result_state": result.state.value.lower(), "outcome": "completed",
                   "error_code": stable_result_code, "duration_ms": duration_ms},
        )
        emit_metric(
            "capability_execution_total", "counter", 1, outcome="completed", stage="result",
            result_state=result.state.value.lower(), runtime_id=context.runtime_ref,
            generation=context.generation, tool_call_id=handle.tool_call_id,
            command_id=handle.command_id, capability=handle.capability_ref.capability_id,
            error_code=stable_result_code,
        )
        emit_metric(
            "capability_execution_duration_ms", "timing", duration_ms, outcome="completed",
            stage="result", result_state=result.state.value.lower(), runtime_id=context.runtime_ref,
            generation=context.generation, tool_call_id=handle.tool_call_id,
            command_id=handle.command_id, capability=handle.capability_ref.capability_id,
            error_code=stable_result_code,
        )

        # activity telemetry (best-effort)
        if result.state is CommandState.SUCCEEDED and self._activity is not None:
            try:
                reported = self._activity.report_succeeded(
                    context.runtime_ref, context.generation,
                )
                if inspect.isawaitable(reported):
                    await reported
            except Exception:
                pass

        return result

    # ------------------------------------------------------------------
    # invoke (convenience)
    # ------------------------------------------------------------------

    async def invoke(
        self,
        context: ExecutionContext,
        capability_ref: CapabilityRef,
        arguments: Mapping[str, Any],
        *,
        tool_call_id: str,
        command_id: str | None = None,
        now: float | None = None,
        deadline: float | None = None,
    ) -> GameCommandResult:
        handle = await self.start(
            context, capability_ref, arguments,
            tool_call_id=tool_call_id, command_id=command_id,
            now=now, deadline=deadline,
        )
        return await self.await_result(context, handle, deadline=deadline)

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    async def cancel(
        self,
        context: ExecutionContext,
        handle: CapabilityInvocationHandle,
        *,
        reason: str,
    ) -> CancelResult:
        # -- validate reason --------------------------------------------------
        if not reason or len(reason) > 512:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INVALID_ARGUMENTS,
                "cancel reason must be non-empty and at most 512 characters",
                False,
            )

        # -- delegate to Router -----------------------------------------------
        try:
            result = await self._router.cancel(context, handle.command_id)
        except Exception as exc:
            raise _record_capability_failure(
                context, stage="cancel", error=exc, tool_call_id=handle.tool_call_id,
                command_id=handle.command_id, capability=handle.capability_ref.capability_id,
            ) from exc

        # -- convergence (McpCore rule) ---------------------------------------
        if result.accepted and result.state not in _TERMINAL:
            try:
                state = await self._router.get_status(context, handle.command_id)
            except Exception:
                pass
            else:
                result = CancelResult(handle.command_id, True, state)

        # audit (reason recorded in detail only; Router has no reason field)
        self._audit.emit(AuditEvent(
            "command.cancel", handle.tool_call_id, handle.command_id,
            context.runtime_ref, context.binding_ref, context.game_session_ref,
            {"accepted": result.accepted, "state": result.state.value,
             "reason": reason},
            log_id=context.trace_context.get("log_id"),
        ))
        log.info(
            "capability cancellation settled",
            extra={"event": "capability.execution.cancel", "log_id": context.trace_context.get("log_id"),
                   "runtime_id": context.runtime_ref, "generation": context.generation,
                   "tool_call_id": handle.tool_call_id, "command_id": handle.command_id,
                   "capability": handle.capability_ref.capability_id,
                   "result_state": result.state.value.lower(),
                   "outcome": "accepted" if result.accepted else "rejected"},
        )
        emit_metric(
            "capability_execution_total", "counter", 1,
            outcome="accepted" if result.accepted else "rejected", stage="cancel",
            result_state=result.state.value.lower(), runtime_id=context.runtime_ref,
            generation=context.generation, tool_call_id=handle.tool_call_id,
            command_id=handle.command_id, capability=handle.capability_ref.capability_id,
        )

        return result

    # ------------------------------------------------------------------
    # get_status
    # ------------------------------------------------------------------

    async def get_status(
        self, context: ExecutionContext, command_id: str,
    ) -> CommandState:
        try:
            return await self._router.get_status(context, command_id)
        except Exception as exc:
            raise _map_exception(exc) from exc
