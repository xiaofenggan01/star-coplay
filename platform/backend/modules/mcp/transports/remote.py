"""Credential-aware client transport for a remote Hosted MCP endpoint.

This module forwards complete JSON-RPC frames and never owns or imports a
Catalog, ContextAuthority, McpCore, or GameSessionRouter.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol


class RemoteTransportError(Exception):
    """Safe, transport-local failure; never contains credential material."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class RemoteMcpSession(Protocol):
    async def request(self, frame: Mapping[str, Any]) -> Mapping[str, Any] | None: ...
    async def close(self) -> None: ...


class RemoteMcpConnector(Protocol):
    async def connect(self, credential: str) -> RemoteMcpSession: ...


def _credential_file_is_private(info: Any, *, platform_name: str = os.name) -> bool:
    return stat.S_ISREG(info.st_mode) and (
        platform_name == "nt" or stat.S_IMODE(info.st_mode) == 0o600
    )


class FileCredentialSource:
    """Read a short-lived credential only while establishing a session."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def _read_bytes(self) -> bytes:
        info = self.path.stat()
        if not _credential_file_is_private(info):
            raise PermissionError("credential file must be a regular 0600 file")
        value = self.path.read_bytes().strip()
        if not value:
            raise ValueError("credential file is empty")
        return value

    async def read(self) -> str:
        return self._read_bytes().decode("utf-8")

    async def fingerprint(self) -> str:
        return hashlib.sha256(self._read_bytes()).hexdigest()

    async def snapshot(self) -> tuple[str, str]:
        value = self._read_bytes()
        return value.decode("utf-8"), hashlib.sha256(value).hexdigest()


class RemoteMcpTransport:
    """Forward JSON-RPC through one credential-bound remote session.

    It is the sole credential watcher.  Rotation connects the replacement,
    verifies its handshake, atomically swaps sessions, retires the old session
    (draining in-flight requests), then emits list_changed.
    """

    def __init__(self, connector: RemoteMcpConnector, credential_source: FileCredentialSource,
                 *, poll_interval: float = 5.0) -> None:
        self._connector = connector
        self._source = credential_source
        self._poll_interval = poll_interval
        self._session: RemoteMcpSession | None = None
        self._initialized_session: RemoteMcpSession | None = None
        self._initialize_frame: Mapping[str, Any] | None = None
        self._probe_initialize_result: Mapping[str, Any] | None = None
        self._fingerprint: str | None = None
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._rotation_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._watch_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._list_changed: list[Callable[[], Awaitable[None] | None]] = []
        self._pending_list_changed: set[Callable[[], Awaitable[None] | None]] = set()
        self._notification_failures = 0
        self._next_notification_retry_at = 0.0
        self._started = False
        # Graceful rotation drain: track in-flight requests per session so
        # a retired (post-rotation) session is only closed once all of its
        # active requests have settled.
        self._active_requests: dict[int, int] = {}
        self._retired_sessions: dict[int, RemoteMcpSession] = {}
        self._drain_lock = asyncio.Lock()

    @property
    def session_active(self) -> bool:
        return self._session is not None

    def on_list_changed(self, callback: Callable[[], Awaitable[None] | None]) -> None:
        self._list_changed.append(callback)

    # ── graceful rotation drain ────────────────────────────────────────────

    def _enter_request(self, session: RemoteMcpSession) -> None:
        self._active_requests[id(session)] = self._active_requests.get(id(session), 0) + 1

    async def _exit_request(self, session: RemoteMcpSession) -> None:
        sid = id(session)
        async with self._drain_lock:
            count = self._active_requests.get(sid, 0) - 1
            if count <= 0:
                self._active_requests.pop(sid, None)
                retired = self._retired_sessions.pop(sid, None)
                should_close = retired is not None
            else:
                self._active_requests[sid] = count
                should_close = False
        if should_close:
            try:
                await session.close()
            except BaseException:
                pass

    async def _retire_session(self, session: RemoteMcpSession) -> None:
        """Retire a session after rotation swap; close once in-flight requests
        drain.  Called only for normal credential rotation — explicit teardown
        (stop / unbind / invalidate) closes immediately without drain."""
        sid = id(session)
        async with self._drain_lock:
            if self._active_requests.get(sid, 0) == 0:
                should_close = True
            else:
                self._retired_sessions[sid] = session
                should_close = False
        if should_close:
            try:
                await session.close()
            except BaseException:
                pass

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            try:
                await self._reconnect(mark_list_changed=False)
            except RemoteTransportError as error:
                # READY_UNBOUND intentionally has no credential/surface. Keep
                # the proxy alive so a later ACTIVE issuance can be observed.
                if error.code != "credential_unavailable":
                    raise
            self._stopping.clear()
            self._started = True
            self._watch_task = asyncio.create_task(
                self._watch(), name="hosted-mcp-credential-watch"
            )

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._started and self._session is None:
                return
            self._started = False
            self._stopping.set()
            task, self._watch_task = self._watch_task, None
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            async with self._lock:
                old, self._session = self._session, None
                self._initialized_session = None
                self._probe_initialize_result = None
                self._fingerprint = None
                self._initialize_frame = None
            self._pending_list_changed.clear()
            # Immediate teardown: close current + all retired sessions
            # without waiting for normal request drain.
            async with self._drain_lock:
                retired = list(self._retired_sessions.values())
                self._retired_sessions.clear()
                self._active_requests.clear()
            all_closing: list[RemoteMcpSession] = []
            if old is not None:
                all_closing.append(old)
            for s in retired:
                if s is not old:
                    all_closing.append(s)
            for s in all_closing:
                try:
                    await s.close()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

    async def handle_line(self, line: str) -> Mapping[str, Any] | None:
        try:
            frame = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error(None, -32700, "Parse error")
        if not isinstance(frame, Mapping) or frame.get("jsonrpc") != "2.0":
            request_id = frame.get("id") if isinstance(frame, Mapping) else None
            return self._error(request_id, -32600, "Invalid Request")
        await self.refresh_if_changed(force_notifications=True)
        return await self.forward_current(frame)

    async def forward_current(self, frame: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Forward to the already-established session without lifecycle recursion."""
        async with self._lock:
            session = self._session
        if session is None:
            raise RemoteTransportError("route_unavailable", "hosted MCP session is unavailable")
        self._enter_request(session)
        try:
            if frame.get("method") == "initialize":
                async with self._init_lock:
                    # Only intercept when a rotation probe actually
                    # pre-initialized this session.  First-connect sessions
                    # (_probe_initialize_result is None) fall through to
                    # the normal forward path.
                    if self._initialized_session is session and self._probe_initialize_result is not None:
                        result = dict(self._probe_initialize_result.get("result", {}))
                        return {"jsonrpc": "2.0", "id": frame.get("id"), "result": result}
                    self._initialize_frame = dict(frame)
            elif "id" in frame and self._initialize_frame is not None and self._initialized_session is not session:
                async with self._init_lock:
                    if self._initialize_frame is not None and self._initialized_session is not session:
                        replay = await session.request(dict(self._initialize_frame))
                        if not self._valid_initialize(self._initialize_frame, replay):
                            raise RemoteTransportError("route_unavailable", "hosted MCP initialization failed")
                        self._initialized_session = session
            response = await session.request(frame)
            if "id" in frame and not self._valid_response(frame, response):
                raise RemoteTransportError("route_unavailable", "hosted MCP returned an invalid response")
            if frame.get("method") == "initialize":
                async with self._init_lock:
                    if not self._valid_initialize(frame, response):
                        raise RemoteTransportError("route_unavailable", "hosted MCP initialization failed")
                    self._initialized_session = session
            return response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._invalidate_session(session)
            if isinstance(exc, RemoteTransportError):
                raise RemoteTransportError("route_unavailable", "hosted MCP connection failed") from exc
            raise RemoteTransportError("route_unavailable", "hosted MCP connection failed") from exc
        finally:
            await self._exit_request(session)

    @staticmethod
    def _valid_initialize(frame: Mapping[str, Any], response: object) -> bool:
        if not RemoteMcpTransport._valid_response(frame, response):
            return False
        result = response.get("result")
        return isinstance(result, Mapping) and isinstance(result.get("protocolVersion"), str)

    @staticmethod
    def _valid_response(frame: Mapping[str, Any], response: object) -> bool:
        return (
            isinstance(response, Mapping)
            and response.get("jsonrpc") == "2.0"
            and response.get("id") == frame.get("id")
            # A JSON-RPC application error is still a valid transport
            # response.  Forward it unchanged so the stdio client sees the
            # real safe MCP error; only malformed/ambiguous frames invalidate
            # the WebSocket session.
            and (("result" in response) ^ ("error" in response))
        )

    async def _invalidate_session(self, session: RemoteMcpSession) -> None:
        async with self._lock:
            if self._session is session:
                self._session = None
                self._initialized_session = None
                self._probe_initialize_result = None
                self._fingerprint = None
        try:
            await session.close()
        except BaseException:
            pass

    async def refresh_if_changed(self, *, force_notifications: bool = True) -> bool:
        try:
            current = await self._source.fingerprint()
        except FileNotFoundError as exc:
            await self._drop_session_for_unbound()
            raise RemoteTransportError("credential_unavailable", "credential is unavailable") from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise RemoteTransportError("route_unavailable", "credential cannot be read safely") from exc
        if self._fingerprint is None or current != self._fingerprint:
            await self._reconnect(mark_list_changed=self._started)
            changed = True
        else:
            changed = False
        await self._deliver_pending_list_changed(force=force_notifications)
        return changed

    async def _drop_session_for_unbound(self) -> None:
        async with self._lock:
            old, self._session = self._session, None
            self._initialized_session = None
            self._probe_initialize_result = None
            had_surface = self._fingerprint is not None
            self._fingerprint = None
            if had_surface:
                self._pending_list_changed.update(self._list_changed)
        async with self._drain_lock:
            retired = list(self._retired_sessions.values())
            self._retired_sessions.clear()
            self._active_requests.clear()
        if old is not None:
            try:
                await old.close()
            except BaseException:
                pass
        for s in retired:
            if s is not old:
                try:
                    await s.close()
                except BaseException:
                    pass
        await self._deliver_pending_list_changed(force=True)

    async def _reconnect(self, *, mark_list_changed: bool) -> None:
        async with self._rotation_lock:
            replacement: RemoteMcpSession | None = None
            ownership_transferred = False
            try:
                credential, fingerprint = await self._source.snapshot()
            except FileNotFoundError as exc:
                raise RemoteTransportError(
                    "credential_unavailable", "credential is unavailable"
                ) from exc
            except (OSError, UnicodeError, ValueError) as exc:
                raise RemoteTransportError(
                    "route_unavailable", "credential cannot be read safely"
                ) from exc
            try:
                if self._session is not None and fingerprint == self._fingerprint:
                    return
                replacement = await self._connector.connect(credential)
                # Only eagerly verify the handshake when rotating away from
                # a healthy existing session with a captured client initialize
                # frame.  First-time connect and READY_UNBOUND activation keep
                # the lazy initialize path.  If we have an old session but no
                # captured initialize frame, we cannot safely verify the
                # replacement — keep the old session and retry on the next
                # poll cycle.
                probe_result: Mapping[str, Any] | None = None
                has_old = self._session is not None
                if has_old:
                    if self._initialize_frame is None:
                        return
                    probe_result = await self._verify_handshake(replacement)
                async with self._lock:
                    old = self._session
                    self._session = replacement
                    if has_old and probe_result is not None:
                        self._initialized_session = replacement
                        self._probe_initialize_result = probe_result
                    else:
                        self._initialized_session = None
                        self._probe_initialize_result = None
                    self._fingerprint = fingerprint
                    ownership_transferred = True
                    if mark_list_changed:
                        self._pending_list_changed.update(self._list_changed)
                        self._notification_failures = 0
                        self._next_notification_retry_at = 0.0
                if old is not None:
                    await self._retire_session(old)
            except RemoteTransportError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RemoteTransportError("route_unavailable", "hosted MCP connection failed") from exc
            finally:
                # Until the atomic swap succeeds, this coroutine exclusively
                # owns replacement. Cancellation and every exceptional exit
                # must therefore reclaim it. After transfer, stop() owns it.
                if replacement is not None and not ownership_transferred:
                    await self._reclaim_untransferred(replacement)

    @staticmethod
    async def _reclaim_untransferred(replacement: RemoteMcpSession) -> None:
        """Finish closing even when the owning reconnect task is cancelled."""
        close_task = asyncio.create_task(replacement.close())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            # shield protects close_task, but wait for ownership reclamation
            # before allowing the original cancellation to unwind.
            try:
                await close_task
            except BaseException:
                pass
        except BaseException:
            pass

    async def _verify_handshake(self, session: RemoteMcpSession) -> Mapping[str, Any]:
        """Replay the captured client initialize frame to verify the new session.

        The caller guarantees that _initialize_frame is not None — rotation
        without a captured client initialize frame is deferred.
        """
        # _initialize_frame is guaranteed non-None by the caller.
        init_frame = dict(self._initialize_frame)  # type: ignore[arg-type]
        response = await session.request(init_frame)
        if not self._valid_initialize(init_frame, response):
            raise RemoteTransportError(
                "route_unavailable", "hosted MCP handshake failed"
            )
        return response

    async def _deliver_pending_list_changed(self, *, force: bool) -> None:
        """Best-effort delivery; failures remain pending for bounded polling/request retry."""
        if not force and time.monotonic() < self._next_notification_retry_at:
            return
        failed = False
        for callback in tuple(self._pending_list_changed):
            try:
                result = callback()
                if result is not None:
                    await result
            except asyncio.CancelledError:
                if self._stopping.is_set():
                    raise
                failed = True
                continue
            except Exception:
                failed = True
                continue
            self._pending_list_changed.discard(callback)
        if failed and self._pending_list_changed:
            self._notification_failures = min(self._notification_failures + 1, 6)
            delay = min(5.0, self._poll_interval * (2 ** (self._notification_failures - 1)))
            self._next_notification_retry_at = time.monotonic() + delay
        elif not self._pending_list_changed:
            self._notification_failures = 0
            self._next_notification_retry_at = 0.0

    async def _watch(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), self._poll_interval)
            except asyncio.TimeoutError:
                try:
                    await self.refresh_if_changed(force_notifications=False)
                except RemoteTransportError:
                    continue
                except Exception:
                    continue

    @staticmethod
    def _error(request_id: object, code: int, message: str) -> Mapping[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class WebSocketRemoteSession:
    def __init__(self, client_session: Any, websocket: Any) -> None:
        self._client_session = client_session
        self._websocket = websocket
        self._send_lock = asyncio.Lock()
        self._pending: dict[object, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None

    async def _start_reader(self) -> None:
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            while True:
                response = await self._websocket.receive_json()
                if isinstance(response, Mapping):
                    rid = response.get("id")
                    if rid is not None:
                        future = self._pending.pop(rid, None)
                        if future is not None and not future.done():
                            future.set_result(response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in self._pending.values():
                if not future.done():
                    error = RemoteTransportError(
                        "route_unavailable", "WebSocket connection closed"
                    )
                    error.__cause__ = exc
                    future.set_exception(error)
            self._pending.clear()

    async def request(self, frame: Mapping[str, Any]) -> Mapping[str, Any] | None:
        async with self._send_lock:
            if "id" not in frame:
                await self._websocket.send_json(dict(frame))
                return None
            rid = frame["id"]
            if rid in self._pending:
                raise RemoteTransportError(
                    "route_unavailable",
                    f"duplicate JSON-RPC request id: {rid}",
                )
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[rid] = future
            try:
                await self._websocket.send_json(dict(frame))
            except BaseException:
                self._pending.pop(rid, None)
                raise
            await self._start_reader()
        # Await without holding any lock so concurrent sends — including
        # notifications — proceed while this request is in-flight.
        try:
            return await future
        finally:
            # Cancelled MCP requests intentionally receive no response.  The
            # caller cancellation must still release the per-request entry.
            if self._pending.get(rid) is future:
                self._pending.pop(rid, None)

    async def close(self) -> None:
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        await self._websocket.close()
        await self._client_session.close()


class WebSocketRemoteConnector:
    """Production connector; bearer credential exists only per connection."""

    def __init__(self, endpoint: str) -> None:
        if not endpoint.startswith(("ws://", "wss://")):
            raise ValueError("hosted MCP endpoint must use ws or wss")
        self._endpoint = endpoint

    async def connect(self, credential: str) -> RemoteMcpSession:
        import aiohttp
        client = aiohttp.ClientSession()
        try:
            websocket = await client.ws_connect(
                self._endpoint, headers={"Authorization": f"Bearer {credential}"}, autoping=True,
            )
        except Exception:
            await client.close()
            raise
        return WebSocketRemoteSession(client, websocket)
