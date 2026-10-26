"""Concrete loopback WebSocket connection for the Local Direct Mod route."""
from __future__ import annotations

import asyncio
import json
import time
import threading
import secrets
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

import websockets
from websockets.exceptions import ConnectionClosed

from agent.protocol.canonical_surface import map_canonical_command
from modules.mcp.contracts.models import (
    CancelResult,
    CommandState,
    GameCommand,
    GameCommandResult,
    RouteHealth,
)
from modules.mcp.core.errors import McpError
from modules.mcp.routers.local_mod import LocalRouteFence, ModConnection


_TERMINAL = frozenset({
    CommandState.SUCCEEDED,
    CommandState.FAILED,
    CommandState.CANCELLED,
    CommandState.TIMED_OUT,
})
@dataclass(frozen=True)
class _PendingResponse:
    future: asyncio.Future[dict[str, Any]]
    response_type: str
    control_id: str
    command_id: str
    operation: str


def capability_snapshot_digest(entries: list[dict[str, str]]) -> str:
    from modules.mcp.capabilities.snapshot import compute_capability_snapshot_digest
    return compute_capability_snapshot_digest(entries)


class LocalModRouteServer(ModConnection):
    """One-owner, one-boot Local Direct WebSocket server.

    The server binds only to 127.0.0.1.  Authentication and all immutable
    fencing fields are checked before the socket becomes the active Mod route.
    """

    def __init__(
        self,
        fence: LocalRouteFence,
        *,
        boot_secret: str,
        clock: Callable[[], float] = time.time,
    ):
        if not boot_secret:
            raise ValueError("boot_secret must not be empty")
        self._fence = fence
        self._boot_secret = boot_secret
        self._clock = clock
        self._server: Any = None
        self._socket: Any = None
        self._owner_claimed = False
        self._reconnect_secret = secrets.token_urlsafe(48)
        self._pending: dict[str, _PendingResponse] = {}
        self._terminal: dict[str, GameCommandResult] = {}
        self._dispatched: set[str] = set()
        self._connection_dispatched: set[str] = set()
        self._terminal_waiters: dict[str, asyncio.Future[GameCommandResult]] = {}
        self._send_lock = asyncio.Lock()
        self._endpoint: str | None = None
        self._negotiated_capability_entries: tuple[dict[str, str], ...] = ()

    @property
    def negotiated_capability_entries(self) -> tuple[dict[str, str], ...]:
        """Exact entries accepted during the authenticated Proto handshake."""
        return self._negotiated_capability_entries

    @property
    def endpoint(self) -> str:
        if self._endpoint is None:
            raise RuntimeError("LocalModRouteServer has not started")
        return self._endpoint

    @property
    def connected(self) -> bool:
        # The modern websockets asyncio connection no longer exposes the
        # legacy ``closed`` boolean.  Ownership of this reference is scoped to
        # the handler and cleared in its finally block.
        return self._socket is not None

    async def start(self) -> str:
        if self._server is not None:
            raise McpError("conflict", "local Mod route server is already running")
        self._server = await websockets.serve(self._accept, "127.0.0.1", 0, max_size=2**20)
        port = self._server.sockets[0].getsockname()[1]
        self._endpoint = f"ws://127.0.0.1:{port}"
        return self._endpoint

    async def stop(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close(code=1001, reason="local route shutdown")
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._mark_transport_unknown()
        self._boot_secret = ""

    def _expected_handshake(self) -> dict[str, Any]:
        f = self._fence
        return {
            "profile": "local_direct",
            "generation": f.generation,
            "binding_ref": f.binding_ref,
            "binding_version": f.binding_version,
            "game_session_ref": f.game_session_ref,
            "game_instance_nonce": f.game_instance_nonce,
            "lease_epoch": f.lease_epoch,
            "capability_digest": f.capability_digest,
        }

    async def _accept(self, websocket: Any) -> None:
        if websocket.remote_address is None or websocket.remote_address[0] not in {"127.0.0.1", "::1"}:
            await websocket.close(code=1008, reason="loopback required")
            return
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5)
            hello = json.loads(raw)
            if hello.get("type") != "local_direct_hello":
                raise ValueError("missing local_direct_hello")
            expected = self._expected_handshake()
            entries = hello.get("capability_entries")
            try:
                recomputed_digest = capability_snapshot_digest(entries)
            except (TypeError, KeyError, ValueError):
                recomputed_digest = ""
            first_claim = not self._owner_claimed
            credential_valid = (
                hello.get("proof") == self._boot_secret if first_claim
                else hello.get("reconnect_proof") == self._reconnect_secret
            )
            if (self._socket is not None
                    or not credential_valid
                    or hello.get("capability_digest") != recomputed_digest
                    or any(hello.get(key) != value for key, value in expected.items())):
                await websocket.close(code=1008, reason="local direct fence rejected")
                return
            self._owner_claimed = True
            self._socket = websocket
            self._negotiated_capability_entries = tuple(
                {str(k): str(v) for k, v in entry.items()}
                for entry in entries
                if isinstance(entry, Mapping)
            )
            self._boot_secret = ""  # proof is one-time and never reusable
            await websocket.send(json.dumps({
                "type": "local_direct_ready",
                "reconnect_proof": self._reconnect_secret,
            }, separators=(",", ":")))
            async for message in websocket:
                self._handle_message(json.loads(message))
        except (asyncio.TimeoutError, ValueError, json.JSONDecodeError):
            await websocket.close(code=1008, reason="invalid local direct handshake")
        except ConnectionClosed:
            pass
        finally:
            if self._socket is websocket:
                self._socket = None
                self._mark_transport_unknown()

    def _handle_message(self, frame: dict[str, Any]) -> None:
        command_id = frame.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            return
        control_id = frame.get("control_id")
        if isinstance(control_id, str):
            pending = self._pending.get(control_id)
            if pending is None or not self._valid_response(frame, pending):
                return
            self._record_terminal(frame)
            self._pending.pop(control_id, None)
            if not pending.future.done():
                pending.future.set_result(frame)
            return
        # Async terminal push is allowed only for an identity this exact
        # connection dispatched. Unknown/nonterminal/terminal-regression
        # frames are intentionally ignored.
        if command_id not in self._dispatched or frame.get("type") != "local_command_result":
            return
        try:
            state = CommandState(frame.get("state"))
        except (ValueError, TypeError):
            return
        if state not in _TERMINAL or command_id in self._terminal:
            return
        if (not self._valid_common_shape(frame)
                or ("accepted" in frame and frame.get("accepted") is not None)):
            return
        self._record_terminal(frame)

    @staticmethod
    def _valid_common_shape(frame: Mapping[str, Any]) -> bool:
        output = frame.get("output")
        error = frame.get("error_code")
        return (output is None or isinstance(output, Mapping)) and (error is None or isinstance(error, str))

    def _valid_response(self, frame: Mapping[str, Any], pending: _PendingResponse) -> bool:
        if (frame.get("type") != pending.response_type
                or frame.get("control_id") != pending.control_id
                or frame.get("command_id") != pending.command_id
                or not self._valid_common_shape(frame)):
            return False
        try:
            CommandState(frame.get("state"))
        except (ValueError, TypeError):
            return False
        if pending.operation == "cancel":
            return type(frame.get("accepted")) is bool
        if pending.operation in {"status", "result"}:
            return "accepted" not in frame or frame.get("accepted") is None
        return False

    def _record_terminal(self, frame: Mapping[str, Any]) -> None:
        command_id = str(frame["command_id"])
        state = CommandState(frame["state"])
        if (command_id not in self._dispatched or state not in _TERMINAL
                or command_id in self._terminal):
            return
        result = GameCommandResult(
            command_id, state, dict(frame.get("output") or {}), frame.get("error_code")
        )
        self._terminal[command_id] = result
        waiter = self._terminal_waiters.pop(command_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(result)

    def _mark_transport_unknown(self) -> None:
        for control_id, pending in tuple(self._pending.items()):
            if not pending.future.done():
                response = {
                    "type": "local_command_result",
                    "control_id": control_id,
                    "command_id": pending.command_id,
                    "state": CommandState.UNKNOWN.value,
                    "output": {},
                    "error_code": "route_unavailable",
                }
                if pending.operation == "cancel":
                    response["accepted"] = False
                pending.future.set_result(response)
        self._pending.clear()
        # Every command written on this connection becomes UNKNOWN when the
        # transport disappears, including the narrow window before
        # ``result()`` has installed its waiter.  Persist that terminal
        # uncertainty so a later convergence read cannot wait until the full
        # capability deadline.
        for command_id in tuple(self._connection_dispatched):
            self._terminal.setdefault(
                command_id,
                GameCommandResult(
                    command_id, CommandState.UNKNOWN,
                    error_code="route_unavailable",
                ),
            )
        for command_id, waiter in tuple(self._terminal_waiters.items()):
            if not waiter.done():
                waiter.set_result(self._terminal.get(
                    command_id,
                    GameCommandResult(
                        command_id, CommandState.UNKNOWN,
                        error_code="route_unavailable",
                    ),
                ))
        self._terminal_waiters.clear()
        self._connection_dispatched.clear()

    def _fence_payload(self) -> dict[str, Any]:
        result = asdict(self._fence)
        result["profile"] = "local_direct"
        return result

    async def _send(self, frame: dict[str, Any]) -> None:
        if not self.connected:
            raise McpError("route_unavailable", "local Mod is not connected")
        async with self._send_lock:
            try:
                await self._socket.send(json.dumps(frame, separators=(",", ":"), sort_keys=True))
            except ConnectionClosed as exc:
                self._socket = None
                self._mark_transport_unknown()
                raise McpError("route_unavailable", "local Mod transport was lost") from exc

    async def dispatch(self, command: GameCommand) -> None:
        mapped = map_canonical_command(
            command.capability_id,
            command.validated_payload,
            command_id=command.command_id,
        )
        # Register ownership before the write.  A loopback peer can receive
        # and close while ``send`` is still unwinding; registering afterwards
        # loses the disconnect signal and leaves result convergence blocked.
        self._dispatched.add(command.command_id)
        self._connection_dispatched.add(command.command_id)
        await self._send({
            "type": "local_command",
            "fence": self._fence_payload(),
            "deadline_at": command.deadline_at,
            "command_id": command.command_id,
            "idempotency_key": command.idempotency_key,
            "capability_digest": command.capability_digest,
            "operation": command.operation.value,
            "wire_format": mapped.wire_format,
            "payload": mapped.payload,
        })

    async def _request(self, request_type: str, command_id: str, *, deadline: float) -> dict[str, Any]:
        if command_id in self._terminal:
            result = self._terminal[command_id]
            return {
                "command_id": command_id,
                "state": result.state.value,
                "output": dict(result.output),
                "error_code": result.error_code,
            }
        control_id = f"{request_type}:{command_id}"
        if control_id in self._pending:
            raise McpError("conflict", "a query for this command is already pending")
        future = asyncio.get_running_loop().create_future()
        operation = request_type.removeprefix("local_")
        self._pending[control_id] = _PendingResponse(
            future, "local_command_result", control_id, command_id, operation
        )
        try:
            await self._send({
                "type": request_type,
                "control_id": control_id,
                "command_id": command_id,
                "fence": self._fence_payload(),
                "deadline_at": deadline,
            })
            return await asyncio.wait_for(future, max(0, deadline - self._clock()))
        finally:
            self._pending.pop(control_id, None)

    async def result(self, command_id: str, deadline: float) -> GameCommandResult:
        # Terminal cache hit — return immediately (survives reconnect).
        if command_id in self._terminal:
            return self._terminal[command_id]
        # Commands dispatched on this connection: wait for the async
        # terminal push instead of sending a local_result query that races
        # ahead of the Mod's main-thread drain.
        if command_id in self._connection_dispatched:
            if command_id in self._terminal_waiters:
                raise McpError("conflict", "a result waiter for this command is already pending")
            future = asyncio.get_running_loop().create_future()
            self._terminal_waiters[command_id] = future
            try:
                # Re-check terminal cache after registering the waiter to
                # close the race where the terminal push lands between the
                # initial cache check and waiter registration.
                if command_id in self._terminal:
                    return self._terminal[command_id]
                return await asyncio.wait_for(future, max(0, deadline - self._clock()))
            except (asyncio.TimeoutError, TimeoutError):
                return GameCommandResult(command_id, CommandState.UNKNOWN, error_code="command_timeout")
            finally:
                self._terminal_waiters.pop(command_id, None)
        # Commands not dispatched by this connection (recovery / explicit
        # query) fall back to the local_result protocol.
        frame = await self._request("local_result", command_id, deadline=deadline)
        return GameCommandResult(
            command_id,
            CommandState(frame.get("state", "UNKNOWN")),
            frame.get("output") or {},
            frame.get("error_code"),
        )

    async def status(self, command_id: str) -> CommandState:
        frame = await self._request("local_status", command_id, deadline=self._clock() + 5)
        return CommandState(frame.get("state", "UNKNOWN"))

    async def cancel(self, command_id: str) -> CancelResult:
        frame = await self._request("local_cancel", command_id, deadline=self._clock() + 5)
        return CancelResult(command_id, frame["accepted"], CommandState(frame["state"]))

    async def health(self) -> RouteHealth:
        return RouteHealth(self.connected, "" if self.connected else "local Mod is not connected")


class ThreadedLocalModRouteServer(ModConnection):
    """Synchronous lifecycle wrapper used by the process-oriented launcher."""

    def __init__(self, fence: LocalRouteFence, *, boot_secret: str):
        self._server = LocalModRouteServer(fence, boot_secret=boot_secret)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    @property
    def connected(self) -> bool:
        return self._server.connected

    @property
    def endpoint(self) -> str:
        return self._server.endpoint

    def start(self) -> str:
        return self._submit(self._server.start()).result(timeout=5)

    def stop(self) -> None:
        try:
            self._submit(self._server.stop()).result(timeout=5)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

    def _submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    async def dispatch(self, command: GameCommand) -> None:
        await asyncio.wrap_future(self._submit(self._server.dispatch(command)))

    async def result(self, command_id: str, deadline: float) -> GameCommandResult:
        return await asyncio.wrap_future(self._submit(self._server.result(command_id, deadline)))

    async def status(self, command_id: str) -> CommandState:
        return await asyncio.wrap_future(self._submit(self._server.status(command_id)))

    async def cancel(self, command_id: str) -> CancelResult:
        return await asyncio.wrap_future(self._submit(self._server.cancel(command_id)))

    async def health(self) -> RouteHealth:
        return await asyncio.wrap_future(self._submit(self._server.health()))
