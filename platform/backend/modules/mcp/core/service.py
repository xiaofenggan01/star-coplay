from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import Any, Mapping, Protocol

from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION,
    CancelResult,
    CommandState,
    ContextAuthority,
    ContextHandle,
    DispatchState,
    GameCommand,
    GameCommandResult,
    GameSessionRouter,
    SideEffect,
)
from modules.mcp.core.audit import AuditEvent, MemoryAuditSink
from modules.mcp.core.catalog import ToolCatalog, validate_arguments
from modules.mcp.core.errors import McpError
from agent.protocol.result_adapter import normalize_canonical_result
from agent.protocol.tool_mapper import ProtocolMappingError

_PROTECTED_ARGUMENTS = frozenset({
    "profile", "principal_id", "runtime_ref", "runtime_id", "generation",
    "binding_ref", "binding_id", "binding_version", "game_session_ref",
    "game_session_id", "game_instance_nonce", "lease_epoch", "capability_digest",
    "execution_context_ref", "user_id", "agentee_id",
})


class ToolActivityReporter(Protocol):
    """Optional deployment port notified only after a successful tool terminal."""

    async def report_succeeded(self, runtime_ref: str, generation: int) -> None: ...


class McpCore:
    def __init__(self, authority: ContextAuthority, router: GameSessionRouter,
                 catalog: ToolCatalog, audit=None,
                 activity: ToolActivityReporter | None = None,
                 *, adapt_results: bool = False, projection=None):
        self._authority = authority
        self._router = router
        self._catalog = catalog
        self._audit = audit or MemoryAuditSink()
        self._activity = activity
        self._adapt_results = adapt_results
        self._projection = projection
        self._command_by_tool_call: dict[tuple[str, str], str] = {}
        self._surface_snapshot_by_handle: dict[str, tuple[str, str, frozenset[str]]] = {}
        self._surface_pin_lock = asyncio.Lock()

    @property
    def catalog_digest(self) -> str:
        return self._catalog.digest

    @staticmethod
    async def _adapter_call(awaitable):
        try:
            return await awaitable
        except McpError:
            raise
        except Exception as exc:
            # Provider/transport exception text may contain endpoint or secret
            # material. Keep it on the private exception chain only.
            raise McpError("internal_error", "adapter operation failed") from exc

    async def _pin_surface(self, handle: ContextHandle) -> tuple[str, str, frozenset[str]]:
        existing = self._surface_snapshot_by_handle.get(handle.value)
        if existing is not None:
            return existing
        async with self._surface_pin_lock:
            existing = self._surface_snapshot_by_handle.get(handle.value)
            if existing is not None:
                return existing
            status = await self._adapter_call(self._authority.revalidate(handle))
            if not status.valid:
                raise McpError("context_expired", "context is no longer valid")
            visible_names: set[str] = set()
            capability_digests: set[str] = set()
            for tool in self._catalog._tools.values():
                try:
                    context = await self._adapter_call(
                        self._authority.authorize(handle, tool.name, tool.capability_id)
                    )
                except McpError as exc:
                    if exc.code == "capability_denied":
                        continue
                    raise
                if tool.name not in context.allowed_tools:
                    continue
                if tool.required_scope not in context.scopes:
                    continue
                capability_digests.add(context.capability_digest)
                visible_names.add(tool.name)
            if len(capability_digests) > 1:
                raise McpError("conflict", "authority returned an inconsistent capability snapshot")
            snapshot = (
                self._catalog.digest,
                next(iter(capability_digests), "empty-surface"),
                frozenset(visible_names),
            )
            self._surface_snapshot_by_handle[handle.value] = snapshot
            return snapshot

    async def list_tools(self, handle: ContextHandle):
        if self._projection is not None:
            try:
                return await self._projection.list_tools(handle)
            except Exception as exc:
                if isinstance(exc, McpError):
                    raise
                code = getattr(exc, "code", "internal_error")
                if code not in {"context_expired", "capability_denied", "conflict", "internal_error"}:
                    code = "internal_error"
                raise McpError(code, code) from exc
        _, _, visible_names = await self._pin_surface(handle)
        return tuple(
            tool for tool in self._catalog._tools.values() if tool.name in visible_names
        )

    async def call_tool(
        self,
        handle: ContextHandle,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        tool_call_id: str,
        command_id: str | None = None,
        request_id: str | None = None,
        now: float | None = None,
    ) -> GameCommandResult:
        if self._projection is not None:
            if not isinstance(arguments, Mapping):
                raise McpError("invalid_arguments", "tool arguments must be an object")
            try:
                from modules.mcp.core.projection import McpCallMetadata
                return await self._projection.call_tool(
                    handle, tool_name, arguments,
                    metadata=McpCallMetadata(tool_call_id=tool_call_id, request_id=request_id),
                )
            except McpError:
                raise
            except Exception as exc:
                code = getattr(exc, "code", "internal_error")
                if hasattr(code, "value"):
                    code = code.value
                if code not in {"context_expired", "capability_denied", "conflict", "invalid_arguments", "timeout", "cancelled", "unknown", "internal_error"}:
                    code = "internal_error"
                raise McpError(code, code) from exc
        if _PROTECTED_ARGUMENTS.intersection(arguments):
            raise McpError("invalid_arguments", "authorization context cannot be supplied as tool arguments")
        tool = self._catalog.get(tool_name)
        payload = validate_arguments(tool.input_schema, arguments)
        snapshot = await self._pin_surface(handle)
        context = await self._adapter_call(self._authority.authorize(handle, tool.name, tool.capability_id))
        catalog_digest, capability_digest, visible_names = snapshot
        if tool.name not in visible_names:
            raise McpError("capability_denied", "tool is not part of the listed capability snapshot")
        if catalog_digest != self._catalog.digest or context.capability_digest != capability_digest:
            raise McpError("conflict", "capability snapshot changed; establish a new MCP session")
        if tool.name not in context.allowed_tools or tool.required_scope not in context.scopes:
            raise McpError("capability_denied", "tool is not authorized")
        route = await self._adapter_call(self._router.resolve(context))
        if not route.available:
            raise McpError("route_unavailable", "game route is unavailable")
        issued = time.time() if now is None else now
        retry_key = (handle.value, tool_call_id)
        prior_command_id = self._command_by_tool_call.get(retry_key)
        if command_id is not None and prior_command_id is not None and command_id != prior_command_id:
            raise McpError("conflict", "tool call retry changed command id")
        stable_command_id = command_id or prior_command_id or str(uuid.uuid4())
        self._command_by_tool_call[retry_key] = stable_command_id
        command = GameCommand(
            contract_version=GAME_COMMAND_VERSION,
            message_id=str(uuid.uuid4()),
            occurred_at=issued,
            idempotency_key=stable_command_id,
            command_id=stable_command_id,
            tool_call_id=tool_call_id,
            capability_id=tool.capability_id,
            capability_version=tool.capability_version,
            capability_digest=context.capability_digest,
            operation=tool.side_effect,
            validated_payload=payload,
            deadline_at=issued + tool.timeout_seconds,
            execution_context_ref=handle.value,
            game_instance_nonce=context.game_instance_nonce,
            lease_epoch=context.lease_epoch if tool.side_effect is SideEffect.MUTATING else None,
            trace_context=context.trace_context,
        )
        receipt = await self._adapter_call(self._router.dispatch(context, command))
        self._audit.emit(AuditEvent("command.dispatched", tool_call_id, stable_command_id,
                                    context.runtime_ref, context.binding_ref, context.game_session_ref,
                                    {"receipt": receipt.state.value},
                                    log_id=context.trace_context.get("log_id")))
        if receipt.state is DispatchState.REJECTED:
            raise McpError("conflict", "command was rejected")
        result = await self._adapter_call(
            self._router.await_result(context, stable_command_id, command.deadline_at)
        )
        if self._adapt_results:
            try:
                normalized = normalize_canonical_result(
                    tool.name,
                    payload,
                    expected_command_id=stable_command_id,
                    lifecycle_state=result.state.value,
                    raw_output=result.output,
                    error_code=result.error_code,
                )
                if normalized is not None:
                    if tool.output_schema is None:
                        raise ProtocolMappingError("canonical tool has no output schema")
                    normalized = validate_arguments(tool.output_schema, normalized)
                    result = GameCommandResult(
                        result.command_id, result.state, normalized, result.error_code,
                    )
            except ProtocolMappingError as exc:
                raise McpError(
                    "upstream_protocol_error", "Mod result does not match the canonical contract"
                ) from exc
            except McpError as exc:
                if exc.code != "invalid_arguments":
                    raise
                raise McpError(
                    "upstream_protocol_error", "Mod result does not match the canonical contract"
                ) from exc
        self._audit.emit(AuditEvent("command.result", tool_call_id, stable_command_id,
                                    context.runtime_ref, context.binding_ref, context.game_session_ref,
                                    {"state": result.state.value, "error_code": result.error_code},
                                    log_id=context.trace_context.get("log_id")))
        if result.state is CommandState.TIMED_OUT:
            raise McpError("command_timeout", "command outcome is uncertain; query status before retrying")
        if result.state is CommandState.CANCELLED:
            raise McpError("command_cancelled", "command was cancelled")
        if result.state is CommandState.SUCCEEDED and self._activity is not None:
            try:
                reported = self._activity.report_succeeded(
                    context.runtime_ref, context.generation
                )
                if inspect.isawaitable(reported):
                    await reported
            except Exception:
                # Activity telemetry is best-effort and cannot turn an already
                # successful game command into a failed MCP response.
                pass
        return result

    async def cancel_command(
        self, handle: ContextHandle, tool_name: str, command_id: str, *, tool_call_id: str
    ):
        if self._projection is not None:
            try:
                from modules.mcp.core.projection import McpCallMetadata
                return await self._projection.cancel(
                    handle, McpCallMetadata(tool_call_id=tool_call_id), reason="cancelled"
                )
            except McpError:
                raise
            except Exception as exc:
                raise McpError("internal_error", "projection cancellation failed") from exc
        tool = self._catalog.get(tool_name)
        context = await self._adapter_call(
            self._authority.authorize(handle, tool.name, tool.capability_id)
        )
        result = await self._adapter_call(self._router.cancel(context, command_id))
        if result.accepted and result.state not in {
            CommandState.SUCCEEDED, CommandState.FAILED, CommandState.CANCELLED,
            CommandState.TIMED_OUT, CommandState.EXPIRED_UNKNOWN,
        }:
            # Cancellation acceptance is not a terminal outcome.  Converge
            # through the same Router status contract before releasing the
            # transport request correlation.
            try:
                state = await self._adapter_call(self._router.get_status(context, command_id))
            except McpError:
                # The accepted cancel remains authoritative even if the
                # follow-up status is temporarily UNKNOWN/unavailable.
                pass
            else:
                result = CancelResult(command_id, True, state)
        self._audit.emit(AuditEvent("command.cancel", tool_call_id, command_id,
                                    context.runtime_ref, context.binding_ref, context.game_session_ref,
                                    {"accepted": result.accepted, "state": result.state.value},
                                    log_id=context.trace_context.get("log_id")))
        return result
