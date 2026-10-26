"""Thin stdio-to-remote-MCP proxy for stdio-only Agent clients."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Protocol, TextIO

from modules.mcp.transports.remote import (
    FileCredentialSource, RemoteMcpTransport, RemoteTransportError,
    WebSocketRemoteConnector,
)


class LineReader(Protocol):
    def __aiter__(self) -> AsyncIterator[str]: ...


class StdioRemoteMcpProxy:
    """Owns only protocol I/O; authority, catalog and routing stay remote."""

    def __init__(self, transport: RemoteMcpTransport, *, writer: TextIO) -> None:
        self._transport = transport
        self._writer = writer
        self._write_lock = asyncio.Lock()
        self._initialized = False
        self._initialize_frame: Mapping[str, Any] | None = None
        self._initialized_frame: Mapping[str, Any] | None = None
        transport.on_list_changed(self._notify_list_changed)

    async def serve(self, reader: LineReader) -> None:
        await self._transport.start()
        pending: set[asyncio.Task] = set()
        failures: list[BaseException] = []

        def _completed(task: asyncio.Task) -> None:
            pending.discard(task)
            if not task.cancelled() and task.exception() is not None:
                failures.append(task.exception())

        try:
            async for line in reader:
                if failures:
                    raise failures[0]
                if not line.strip():
                    continue
                task = asyncio.create_task(self._process_line(line))
                pending.add(task)
                task.add_done_callback(_completed)
            # Clean EOF: deterministically wait for the exact in-flight set.
            # Process shutdown/cancellation remains the outer owner's policy;
            # this transport must not silently discard a response after an
            # arbitrary grace period.
            if pending:
                await asyncio.gather(*tuple(pending), return_exceptions=True)
            if failures:
                raise failures[0]
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._transport.stop()

    async def _process_line(self, line: str) -> None:
        response = await self.handle_line(line)
        if response is not None:
            await self._write(response)

    async def handle_line(self, line: str) -> Mapping[str, Any] | None:
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        method = request.get("method") if isinstance(request, Mapping) else None
        if method == "initialize":
            # A second initialize starts a new local protocol epoch.  Never
            # replay the previous epoch's initialized notification with it.
            self._initialize_frame = dict(request)
            self._initialized_frame = None
            self._initialized = False
        elif method == "notifications/initialized":
            # initialized is only meaningful after this proxy has completed
            # initialize for the same local protocol epoch.
            if not self._initialized or self._initialize_frame is None:
                return None
            self._initialized_frame = dict(request)
            try:
                changed = await self._transport.refresh_if_changed(force_notifications=True)
            except RemoteTransportError as error:
                if error.code == "credential_unavailable":
                    # READY_UNBOUND: retain the complete handshake so a later
                    # remote session can be brought to protocol-ready state.
                    return None
                return {
                    "jsonrpc": "2.0", "id": request.get("id"),
                    "error": {"code": -32000, "message": error.safe_message,
                              "data": {"code": error.code}},
                }
            if changed:
                # The reconnect callback already replayed initialize and this
                # initialized frame before publishing list_changed.
                return None
            try:
                return await self._transport.forward_current(request)
            except RemoteTransportError as error:
                return {
                    "jsonrpc": "2.0", "id": request.get("id"),
                    "error": {"code": -32000, "message": error.safe_message,
                              "data": {"code": error.code}},
                }
        try:
            response = await self._transport.handle_line(line)
        except RemoteTransportError as error:
            if error.code == "credential_unavailable" and isinstance(request, Mapping) and request.get("method") == "initialize":
                self._initialized = True
                return {"jsonrpc": "2.0", "id": request.get("id"), "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "starcoplay-hosted-proxy", "version": "0.1.0"},
                }}
            if error.code == "credential_unavailable" and isinstance(request, Mapping) and request.get("method") == "tools/list":
                return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"tools": []}}
            return {
                "jsonrpc": "2.0", "id": request.get("id") if isinstance(request, Mapping) else None,
                "error": {"code": -32000, "message": error.safe_message,
                          "data": {"code": error.code}},
            }
        if isinstance(request, Mapping) and request.get("method") == "initialize" and response and "result" in response:
            self._initialized = True
        return response

    async def _notify_list_changed(self) -> None:
        if not self._initialized or self._initialize_frame is None or self._initialized_frame is None:
            # Keep the transport notification pending until the client has
            # completed the full MCP handshake.  list_changed must never be
            # published for a remote session that has only seen initialize.
            raise RuntimeError("local MCP handshake is not complete")
        if self._transport.session_active:
            response = await self._transport.forward_current(dict(self._initialize_frame))
            if response is None or "result" not in response:
                raise RuntimeError("remote MCP session initialization failed")
            initialized_response = await self._transport.forward_current(dict(self._initialized_frame))
            if initialized_response is not None:
                raise RuntimeError("remote MCP initialized notification was rejected")
        # tools.listChanged is a server capability, not a client opt-in.  A
        # READY_UNBOUND Claude runtime commonly initializes with empty client
        # capabilities; suppressing this notification leaves its initially
        # empty tool surface permanently stale after Binding activation.
        await self._write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {}})

    async def _write(self, frame: Mapping[str, Any]) -> None:
        rendered = json.dumps(frame, separators=(",", ":"), ensure_ascii=False) + "\n"
        async with self._write_lock:
            self._writer.write(rendered)
            self._writer.flush()


class AsyncStdinLines:
    async def __aiter__(self):
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return
            yield line


async def _main() -> None:
    # MCP stdio is UTF-8 regardless of the host console code page.  Windows
    # Python otherwise defaults redirected pipes to the active ANSI code page.
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    endpoint = os.environ.get("STARCOPLAY_HOSTED_MCP_ENDPOINT", "")
    credential_path = os.environ.get("STARCOPLAY_MCP_CREDENTIAL_PATH", "")
    if not endpoint or not credential_path:
        raise SystemExit("hosted MCP endpoint and credential path are required")
    transport = RemoteMcpTransport(
        WebSocketRemoteConnector(endpoint), FileCredentialSource(Path(credential_path)),
    )
    await StdioRemoteMcpProxy(transport, writer=sys.stdout).serve(AsyncStdinLines())


if __name__ == "__main__":
    asyncio.run(_main())
