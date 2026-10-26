"""Server-side Hosted MCP websocket boundary.

Unlike the remote client/proxy modules, this endpoint is the legitimate owner
of the Hosted authority + MCP Core composition for one authenticated session.

Concurrent WebSocket request/notification/cancellation is achieved by bridging
the WS read loop into StdioMcpTransport.serve, which already owns the correct
registration-before-await and cancel-response-gate semantics.  The bridge uses
two async queues: in_queue feeds WS text frames into serve's LineReader; serve's
writer pushes JSON-RPC lines into out_queue, which a single WS output pump
serialises back to the client.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import time
from typing import Any, Mapping

from common.observability import emit_metric
import common.observability as observability
from modules.mcp.core.errors import McpError
from modules.mcp.transports.stdio import StdioMcpTransport

log = logging.getLogger("mcp.hosted.transport")


class _HostedToolCallObserver:
    """Hosted-only lifecycle projection; never installed by Local profiles."""

    def __init__(self) -> None:
        self._active = 0
        self._failed_fingerprints: dict[tuple[str, int, str, str], int] = {}
        self._repeated_failure_total = 0
        self._last_repeated_failure: dict[str, str] | None = None

    def started(self, *, correlation, tool_call_id: str, command_id: str,
                tool_name: str = "", arguments: Mapping[str, Any] | None = None):
        self._active += 1
        fields = {
            "log_id": correlation.get("log_id"), "runtime_id": correlation.get("runtime_id"),
            "generation": correlation.get("generation"), "tool_call_id": tool_call_id,
            "command_id": command_id, "operation_id": correlation.get("operation_id"),
            "capability": tool_name,
        }
        fingerprint = hashlib.sha256(json.dumps(
            {"capability": tool_name, "arguments": arguments or {}},
            sort_keys=True, separators=(",", ":"), default=str,
        ).encode()).hexdigest()
        log.info("hosted MCP ToolCall started", extra={"event": "mcp.hosted.call.started", **fields})
        observability.emit_metric("hosted_mcp_call_total", "counter", 1, outcome="started", **fields)
        observability.emit_metric("hosted_mcp_call_inflight", "gauge", self._active, outcome="current")
        return time.monotonic(), fields, fingerprint

    def finished(self, observation, *, result_state: str, error,
                 error_code: str | None = None) -> None:
        started, fields, fingerprint = observation
        self._active = max(0, self._active - 1)
        duration_ms = (time.monotonic() - started) * 1000
        failed = bool(error) or result_state in {
            "failed", "cancelled", "timed_out", "expired_unknown", "unknown"
        }
        stable_error_code = observability.normalize_error_code(
            getattr(error, "code", None) if error else error_code,
            fallback="internal_error" if error else "unknown",
        ) if failed else ""
        level = logging.WARNING if failed else logging.INFO
        self._record_repeated_failure(
            fields, fingerprint, failed=failed, error_code=stable_error_code,
        )
        log.log(
            level, "hosted MCP ToolCall finished",
            extra={"event": "mcp.hosted.call.finished", **fields,
                   "result_state": result_state, "outcome": "failed" if failed else "completed",
                   "error_code": stable_error_code, "duration_ms": duration_ms,
                   "exception_type": type(error).__name__ if error is not None else ""},
        )
        observability.emit_metric(
            "hosted_mcp_call_total", "counter", 1,
            outcome="failed" if failed else "completed", result_state=result_state,
            error_code=stable_error_code, **fields,
        )
        observability.emit_metric(
            "hosted_mcp_call_duration_ms", "timing", duration_ms,
            outcome="failed" if failed else "completed", result_state=result_state,
            error_code=stable_error_code, **fields,
        )
        observability.emit_metric("hosted_mcp_call_inflight", "gauge", self._active, outcome="current")

    def _record_repeated_failure(self, fields: Mapping[str, Any], fingerprint: str,
                                 *, failed: bool, error_code: str) -> None:
        operation_id = str(fields.get("operation_id") or "")
        if not operation_id:
            return
        runtime_id = str(fields.get("runtime_id") or "")
        generation = int(fields.get("generation") or 0)
        key = (runtime_id, generation, operation_id, fingerprint)
        if not failed:
            self._failed_fingerprints.pop(key, None)
            return
        count = self._failed_fingerprints.get(key, 0) + 1
        self._failed_fingerprints[key] = count
        if len(self._failed_fingerprints) > 256:
            self._failed_fingerprints.pop(next(iter(self._failed_fingerprints)))
        if count < 2:
            return
        capability = str(fields.get("capability") or "")
        self._repeated_failure_total += 1
        self._last_repeated_failure = {
            "capability": capability,
            "error_code": error_code,
        }
        observability.emit_metric(
            "hosted_mcp_repeated_failure_total", "counter", 1,
            outcome="repeated", error_code=error_code,
            operation_id=operation_id, capability=capability,
            runtime_id=fields.get("runtime_id"), generation=fields.get("generation"),
        )

    def diagnostics_snapshot(self) -> dict[str, Any]:
        return {
            "repeated_failure_total": self._repeated_failure_total,
            "last_repeated_failure": (
                dict(self._last_repeated_failure)
                if self._last_repeated_failure is not None else None
            ),
        }


class _HostedResourceExhausted(McpError):
    """Transport-owned overload error; MCP Core stays policy-free."""

    def __init__(self, message: str = "hosted MCP capacity is exhausted") -> None:
        Exception.__init__(self, message)
        self.code = "resource_exhausted"
        self.retryable = True
        self.safe_message = message


class _HostedToolCallGate:
    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("Hosted ToolCall limit must be positive")
        self._limit = limit
        self._active = 0

    def try_acquire(self) -> bool:
        # No await occurs in this critical section; event-loop scheduling makes
        # the counter update atomic for one Hosted session.
        if self._active >= self._limit:
            log.warning(
                "hosted MCP ToolCall rejected by bounded ingress",
                extra={"event": "mcp.hosted.tool_call.rejected",
                       "limit_kind": "hosted_mcp_inflight_calls",
                       "error_code": "resource_exhausted"},
            )
            emit_metric(
                "hosted_mcp_tool_call_rejected_total", "counter", 1,
                outcome="rejected", limit_kind="hosted_mcp_inflight_calls",
                error_code="resource_exhausted",
            )
            return False
        self._active += 1
        return True

    def release(self) -> None:
        if self._active <= 0:
            raise RuntimeError("Hosted ToolCall gate released without ownership")
        self._active -= 1

    @staticmethod
    def rejection() -> McpError:
        return _HostedResourceExhausted()


class _HostedQueueOverflow(RuntimeError):
    pass


class HostedMcpJsonRpcSession:
    """Single-request session for test fixtures.

    Calls StdioMcpTransport.handle_line directly — no serve loop, no concurrent
    cancellation.  Production concurrency goes through
    HostedMcpWebSocketEndpoint.handle, which feeds frames through
    StdioMcpTransport.serve.
    """

    def __init__(self, core: Any, authority: Any, credential: str,
                 *, max_inflight_calls: int = 1) -> None:
        self._transport = StdioMcpTransport(
            core, authority, writer=io.StringIO(),
            connection_metadata={"credential": credential},
            tool_call_gate=_HostedToolCallGate(max_inflight_calls),
            tool_call_observer=_HostedToolCallObserver(),
        )

    async def request(self, frame: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return await self._transport.handle_line(json.dumps(dict(frame), separators=(",", ":")))

    async def close(self) -> None:
        await self._transport.close()


class HostedMcpWebSocketEndpoint:
    """aiohttp-compatible websocket handler with bearer auth out of band."""

    def __init__(self, core: Any, authority: Any, exposure_port: Any = None,
                 *, max_inflight_calls: int = 1, queue_size: int = 8) -> None:
        if queue_size <= 0:
            raise ValueError("Hosted MCP queue size must be positive")
        self._core = core
        self._authority = authority
        self._exposure_port = exposure_port
        self._max_inflight_calls = max_inflight_calls
        self._queue_size = queue_size
        self._tool_call_observer = _HostedToolCallObserver()

    def diagnostics_snapshot(self) -> dict[str, Any]:
        return self._tool_call_observer.diagnostics_snapshot()

    async def handle(self, request: Any) -> Any:
        from aiohttp import WSMsgType, web

        authorization = request.headers.get("Authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not credential:
            raise web.HTTPUnauthorized(text="hosted MCP credential is required")
        response_headers = {}
        if request.get("request_id"):
            response_headers["x-request-id"] = str(request["request_id"])
        if request.get("log_id"):
            response_headers["x-log-id"] = str(request["log_id"])
        websocket = web.WebSocketResponse(autoping=True)
        websocket.headers.update(response_headers)
        await websocket.prepare(request)
        log.info(
            "hosted MCP session opened",
            extra={"event": "mcp.hosted.session.opened", "outcome": "success",
                   "request_id": request.get("request_id"), "log_id": request.get("log_id")},
        )
        observability.emit_metric("hosted_mcp_session_total", "counter", 1, outcome="opened")

        # ---- bridge: WS ↔ async queues ↔ StdioMcpTransport.serve ----------
        in_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        out_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        overloaded = False
        overflow_event = asyncio.Event()

        def _signal_overload(limit_kind: str) -> None:
            nonlocal overloaded
            if overloaded:
                return
            overloaded = True
            overflow_event.set()
            log.warning(
                "hosted MCP WebSocket queue overflow",
                extra={"event": "mcp.hosted.websocket.overflow",
                       "limit_kind": limit_kind,
                       "error_code": "resource_exhausted"},
            )
            emit_metric(
                "hosted_mcp_websocket_overflow_total", "counter", 1,
                outcome="rejected", limit_kind=limit_kind,
                error_code="resource_exhausted",
            )

        class _QueueLineReader:
            def __init__(self, q: asyncio.Queue[str]) -> None:
                self._q = q

            async def __aiter__(self):
                while True:
                    line = await self._q.get()
                    yield line

        class _QueueWriter:
            def __init__(self, q: asyncio.Queue[str]) -> None:
                self._q = q

            def write(self, s: str) -> int:
                try:
                    self._q.put_nowait(s)
                except asyncio.QueueFull as exc:
                    _signal_overload("hosted_mcp_output_frames")
                    raise _HostedQueueOverflow("Hosted MCP output queue is full") from exc
                return len(s)

            def flush(self) -> None:
                pass

        transport = StdioMcpTransport(
            self._core, self._authority,
            writer=_QueueWriter(out_queue),
            connection_metadata={"credential": credential},
            exposure_port=self._exposure_port,
            tool_call_gate=_HostedToolCallGate(self._max_inflight_calls),
            tool_call_observer=self._tool_call_observer,
        )

        # ---- WS I/O tasks --------------------------------------------------
        async def _ws_reader() -> None:
            async for message in websocket:
                if message.type is WSMsgType.TEXT:
                    try:
                        in_queue.put_nowait(message.data)
                    except asyncio.QueueFull:
                        _signal_overload("hosted_mcp_input_frames")
                        return
                elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break

        async def _ws_writer() -> None:
            try:
                while True:
                    item = await out_queue.get()
                    text: str = item.rstrip("\n")
                    if not text:
                        continue
                    try:
                        frame = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    await websocket.send_json(dict(frame))
            except asyncio.CancelledError:
                raise
            except Exception:
                raise

        reader_task = asyncio.create_task(_ws_reader(), name="hosted-ws-reader")
        writer_task = asyncio.create_task(_ws_writer(), name="hosted-ws-writer")
        serve_task = asyncio.create_task(
            transport.serve(_QueueLineReader(in_queue)), name="hosted-mcp-serve"
        )
        overflow_task = asyncio.create_task(
            overflow_event.wait(), name="hosted-ws-overflow-signal"
        )

        # Any boundary task ending means the bridge can no longer safely
        # accept work.  Teardown cancels all siblings directly; it never needs
        # a sentinel insertion into a potentially full queue.
        try:
            done, _ = await asyncio.wait(
                (reader_task, writer_task, serve_task, overflow_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task is overflow_task:
                    continue
                if not task.cancelled() and task.exception() is not None:
                    if isinstance(task.exception(), _HostedQueueOverflow):
                        overloaded = True
                    else:
                        log.warning(
                            "hosted MCP WebSocket bridge task failed",
                            extra={
                                "event": "mcp.hosted.websocket.bridge_failed",
                                "exception_type": type(task.exception()).__name__,
                            },
                        )
        finally:
            reader_task.cancel()
            writer_task.cancel()
            serve_task.cancel()
            overflow_task.cancel()
            await asyncio.gather(
                reader_task, writer_task, serve_task, overflow_task,
                return_exceptions=True,
            )
            if overloaded and not websocket.closed:
                await websocket.close(code=1013, message=b"resource_exhausted")
            log.info(
                "hosted MCP session closed",
                extra={"event": "mcp.hosted.session.closed",
                       "outcome": "overloaded" if overloaded else "closed",
                       "request_id": request.get("request_id"), "log_id": request.get("log_id")},
            )
            observability.emit_metric(
                "hosted_mcp_session_total", "counter", 1,
                outcome="overloaded" if overloaded else "closed",
            )

        return websocket
