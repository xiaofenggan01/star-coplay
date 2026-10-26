"""Newline-delimited JSON-RPC 2.0 transport for local MCP clients.

Only JSON-RPC frames are written to the provided protocol writer.  Operational
logging, if added by a composition root, must use stderr.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, AsyncIterator, Mapping, Protocol, TextIO

from modules.mcp.contracts.models import ContextHandle
from modules.mcp.core.errors import McpError
from modules.mcp.core.service import McpCore
from common.observability import bind_log_context, new_log_id, reset_log_context


class LineReader(Protocol):
    def __aiter__(self) -> AsyncIterator[str]: ...


class _MethodNotFound(Exception):
    pass


def _jsonable(value: Any) -> Any:
    """Serialize frozen runtime results without ``dataclasses.asdict`` deepcopy."""
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if hasattr(value, "value"):
        return value.value
    return value


@dataclass
class _RequestState:
    """One JSON-RPC request registration owned by the serve loop.

    Registration happens before the loop can consume another input line.  A
    cancellation notification therefore either finds this state or is for a
    genuinely unknown/already-finished request.
    """

    request_id: object
    task: asyncio.Task | None = None
    tool_name: str | None = None
    command_id: str | None = None
    tool_call_id: str | None = None
    correlation: Mapping[str, str] | None = None
    cancel_requested: bool = False


class StdioMcpTransport:
    PROTOCOL_VERSION = "2025-03-26"

    def __init__(
        self,
        core: McpCore,
        authority,
        *,
        writer: TextIO,
        connection_metadata: Mapping[str, str] | None = None,
        exposure_port: Any = None,
        tool_call_gate: Any = None,
        tool_call_observer: Any = None,
    ):
        self._core = core
        self._authority = authority
        self._exposure_port = exposure_port
        self._tool_call_gate = tool_call_gate
        self._tool_call_observer = tool_call_observer
        self._writer = writer
        self._connection_metadata = dict(connection_metadata or {})
        self._handle: ContextHandle | None = None
        self._write_lock = asyncio.Lock()
        self._requests: dict[object, _RequestState] = {}
        self._control_tasks: set[asyncio.Task] = set()

    async def serve(self, reader: LineReader) -> None:
        pending: set[asyncio.Task] = set()
        failures: list[BaseException] = []

        def completed(task: asyncio.Task, state: _RequestState | None = None) -> None:
            pending.discard(task)
            if (state is not None
                    and self._requests.get(state.request_id) is state
                    and state.task is task):
                self._requests.pop(state.request_id, None)
            if not task.cancelled() and task.exception() is not None:
                failures.append(task.exception())

        try:
            async for line in reader:
                if failures:
                    raise failures[0]
                if not line.strip():
                    continue
                # Cancellation is a notification and must be observed while a
                # tools/call request is still awaiting its downstream result.
                # Other notifications remain ordered and response-free.
                try:
                    envelope = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    envelope = None
                if isinstance(envelope, Mapping) and "id" not in envelope:
                    await self.handle_line(line)
                    continue
                if isinstance(envelope, Mapping) and envelope.get("method") == "initialize":
                    response = await self.handle_line(line)
                    if response is not None:
                        await self._write(response)
                    continue
                state: _RequestState | None = None
                if (isinstance(envelope, Mapping)
                        and envelope.get("method") == "tools/call"
                        and "id" in envelope):
                    request_id = envelope.get("id")
                    if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                        # This registration is deliberately synchronous: no
                        # await may occur before the task reference is stored.
                        if request_id not in self._requests:
                            state = _RequestState(request_id)
                            self._requests[request_id] = state
                task = asyncio.create_task(self._process_line(line, state))
                if state is not None:
                    state.task = task
                pending.add(task)
                task.add_done_callback(lambda done, registered=state: completed(done, registered))
            if pending:
                # EOF means the client has stopped waiting.  Allow already
                # completed requests to flush, but never let an in-flight
                # game command hold process teardown until its tool deadline.
                await asyncio.wait(tuple(pending), timeout=0.05)
            if failures:
                raise failures[0]
        finally:
            owned_tasks = set(pending) | {
                state.task for state in self._requests.values() if state.task is not None
            } | set(self._control_tasks)
            for task in owned_tasks:
                task.cancel()
            if owned_tasks:
                await asyncio.gather(*owned_tasks, return_exceptions=True)
            await self.close()

    async def close(self) -> None:
        # The credential is needed only until a context is successfully
        # established.  Failed pre-auth/metadata validation may be retried,
        # but transport shutdown must always erase the retained material.
        self._connection_metadata.pop("credential", None)
        if self._handle is not None:
            handle, self._handle = self._handle, None
            await self._authority.close(handle)

    async def _process_line(self, line: str, state: _RequestState | None = None) -> None:
        request_id: object | None = None
        try:
            envelope = json.loads(line)
            if isinstance(envelope, Mapping):
                request_id = envelope.get("id")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        try:
            if state is not None and state.cancel_requested:
                return
            response = await self.handle_line(line)
            if response is not None and not (state is not None and state.cancel_requested):
                await self._write(response)
        except asyncio.CancelledError:
            # A successfully-routed MCP cancellation intentionally suppresses
            # the original request response.
            if state is None or not state.cancel_requested:
                raise

    async def handle_line(self, line: str) -> Mapping[str, Any] | None:
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error(None, -32700, "Parse error")
        if not isinstance(request, Mapping) or request.get("jsonrpc") != "2.0":
            return self._error(request.get("id") if isinstance(request, Mapping) else None, -32600, "Invalid Request")
        # Notifications never receive responses, including initialized.
        if "id" not in request:
            if request.get("method") == "notifications/cancelled":
                params = request.get("params", {})
                if isinstance(params, Mapping):
                    await self._cancel_request(params)
            return None
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(method, str) or not isinstance(params, Mapping):
            return self._error(request_id, -32600, "Invalid Request")
        try:
            result = await self._dispatch(method, params, request_id=request_id)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except _MethodNotFound:
            return self._error(request_id, -32601, "Method not found")
        except McpError as exc:
            code = -32602 if exc.code == "invalid_arguments" else -32000
            return self._error(request_id, code, exc.safe_message, exc.as_dict())
        except Exception:
            return self._error(request_id, -32603, "Internal error")

    async def _dispatch(
        self, method: str, params: Mapping[str, Any], *, request_id: object
    ) -> Mapping[str, Any]:
        if method == "initialize":
            if self._handle is not None:
                raise McpError("conflict", "transport is already initialized")
            raw_metadata = params.get("metadata", {})
            if not isinstance(raw_metadata, Mapping):
                raise McpError("invalid_arguments", "initialize metadata must be an object")
            metadata = dict(raw_metadata)
            # The standard MCP initialize shape contains protocolVersion,
            # capabilities and clientInfo.  Local launch material is normally
            # injected out-of-band by the composition root, never by the model.
            metadata.update(self._connection_metadata)
            credential = metadata.get("credential", params.get("credential"))
            if not isinstance(credential, str):
                raise McpError("invalid_arguments", "local launch credential is unavailable")
            metadata["credential"] = credential
            try:
                self._handle = await self._authority.establish(metadata)
            finally:
                metadata.pop("credential", None)
            # Erase the retained copy only after establishment succeeds.  A
            # rejected client-supplied metadata attempt must not consume the
            # out-of-band boot credential and prevent a corrected initialize.
            self._connection_metadata.pop("credential", None)
            return {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "starcoplay-local-direct", "version": "0.1.0"},
            }
        if self._handle is None:
            raise McpError("context_expired", "transport is not initialized")
        if method == "tools/list":
            tools = await self._core.list_tools(self._handle)
            projected = self._core._projection is not None
            return {"tools": [{
                "name": tool.name,
                "description": tool.description if projected else f"StarCoPlay capability {tool.capability_id}",
                # Projected schemas are recursively frozen with
                # ``MappingProxyType``.  A shallow ``dict`` conversion leaves
                # nested proxies behind and makes json.dumps fail in the
                # background writer task, which previously surfaced to the
                # client as a silent tools/list timeout.
                "inputSchema": _jsonable(tool.input_schema),
                **({"outputSchema": _jsonable(tool.output_schema), "annotations": _jsonable(tool.annotations)}
                   if projected and tool.output_schema is not None else
                   ({"annotations": _jsonable(tool.annotations)} if projected else {})),
            } for tool in tools], "_meta": {"catalogDigest": (tools[0].catalog_digest if projected and tools else self._core.catalog_digest)}}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            meta = params.get("_meta", {})
            if not isinstance(name, str) or not isinstance(arguments, Mapping) or not isinstance(meta, Mapping):
                raise McpError("invalid_arguments", "tools/call parameters are invalid")
            tool_call_id = meta.get("toolCallId", f"jsonrpc:{request_id}")
            command_id = meta.get("commandId", f"jsonrpc:{request_id}")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise McpError("invalid_arguments", "tools/call requires _meta.toolCallId")
            if not isinstance(command_id, str) or not command_id:
                raise McpError("invalid_arguments", "_meta.commandId must be a string")
            gate_acquired = False
            if self._tool_call_gate is not None:
                gate_acquired = self._tool_call_gate.try_acquire()
                if not gate_acquired:
                    raise self._tool_call_gate.rejection()
            try:
                return await self._dispatch_tool_call(
                    name, arguments, request_id, tool_call_id, command_id
                )
            finally:
                if gate_acquired:
                    self._tool_call_gate.release()
        raise _MethodNotFound

    async def _dispatch_tool_call(
        self, name: str, arguments: Mapping[str, Any], request_id: object,
        tool_call_id: str, command_id: str,
    ) -> Mapping[str, Any]:
        task = asyncio.current_task()
        state = self._requests.get(request_id)
        if task is None or (state is not None and state.task is not task):
            raise McpError("internal_error", "tool request has no task owner")
        if state is not None:
            state.tool_name = name
            state.command_id = command_id
            state.tool_call_id = tool_call_id
            if state.cancel_requested:
                raise asyncio.CancelledError
        correlation = None
        context_token = None
        begin = getattr(self._authority, "begin_tool_call", None)
        if begin is not None:
            correlation, context_token = await begin(self._handle)
        if correlation is None:
            # Local profiles own an independent transport-local diagnostic
            # correlation and never consult the Hosted active-turn registry.
            correlation = {"log_id": new_log_id()}
        if state is not None:
            state.correlation = dict(correlation)
        log_token = bind_log_context(
            **dict(correlation or {}), tool_call_id=tool_call_id,
            command_id=command_id,
        )
        observation = None
        if self._tool_call_observer is not None:
            observation = self._tool_call_observer.started(
                correlation=dict(correlation or {}), tool_call_id=tool_call_id,
                command_id=command_id, tool_name=name, arguments=arguments,
            )
        try:
            try:
                result = await self._core.call_tool(
                    self._handle, name, arguments,
                    tool_call_id=tool_call_id, command_id=command_id,
                    request_id=str(request_id) if request_id is not None else None,
                )
            except BaseException as error:
                if observation is not None:
                    self._tool_call_observer.finished(observation, result_state="failed", error=error)
                raise
            if observation is not None:
                self._tool_call_observer.finished(
                    observation, result_state=str(result.state.value).lower(), error=None,
                    error_code=result.error_code,
                )
        finally:
            reset_log_context(log_token)
            end = getattr(self._authority, "end_tool_call", None)
            if end is not None and context_token is not None:
                end(context_token)
        structured = _jsonable(result)
        structured["state"] = result.state.value
        return {
            "content": [{"type": "text", "text": json.dumps(structured, separators=(",", ":"))}],
            "structuredContent": structured,
            "isError": result.state.value in {"FAILED", "CANCELLED", "TIMED_OUT", "EXPIRED_UNKNOWN", "UNKNOWN"},
        }

    async def _cancel_request(self, params: Mapping[str, Any]) -> None:
        request_id = params.get("requestId")
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            return
        state = self._requests.get(request_id)
        if state is None or state.cancel_requested:
            return
        # Closing the response gate is the semantics of the MCP notification,
        # independent of whether downstream cancellation can still be accepted.
        state.cancel_requested = True
        task = state.task
        if task is None or task.done():
            return
        if (self._handle is None or state.tool_name is None
                or state.command_id is None or state.tool_call_id is None):
            task.cancel()
            return
        control = asyncio.create_task(self._settle_cancellation(state, task))
        self._control_tasks.add(control)
        control.add_done_callback(self._control_tasks.discard)

    async def _settle_cancellation(
        self, state: _RequestState, task: asyncio.Task,
    ) -> None:
        """Best-effort downstream cancellation after the response gate is closed."""
        context_token = None
        resume = getattr(self._authority, "resume_tool_call", None)
        if resume is not None and state.correlation is not None:
            context_token = resume(state.correlation)
        log_token = bind_log_context(
            **dict(state.correlation or {}),
            tool_call_id=state.tool_call_id,
            command_id=state.command_id,
        )
        try:
            try:
                result = await self._core.cancel_command(
                    self._handle, state.tool_name, state.command_id,
                    tool_call_id=state.tool_call_id,
                )
            except McpError:
                return
        finally:
            reset_log_context(log_token)
            end = getattr(self._authority, "end_tool_call", None)
            if end is not None and context_token is not None:
                end(context_token)
        if result.accepted:
            task.cancel()

    async def _write(self, frame: Mapping[str, Any]) -> None:
        rendered = json.dumps(frame, separators=(",", ":"), ensure_ascii=False) + "\n"
        async with self._write_lock:
            self._writer.write(rendered)
            self._writer.flush()

    @staticmethod
    def _error(request_id: object, code: int, message: str, data: Mapping[str, Any] | None = None):
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = dict(data)
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
