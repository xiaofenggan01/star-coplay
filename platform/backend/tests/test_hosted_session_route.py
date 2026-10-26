from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close as CloseFrame

from agent.generated.protocol import capability_pb2, common_pb2, transport_pb2
from common.auth.token_authenticator import AuthResult
from modules.gateway.hosted_session import (
    ClosePublishDeferred, ControlSelectionConflict, HostedControlSelectionStore, HostedHelloFacts,
    HostedSessionCoordinator, HostedSessionRegistry,
    WebSocketHostedSessionRoute, compute_capability_digest, parse_hosted_hello,
)
from modules.gateway.runtime_contracts import InMemoryGameSessionFacts
from modules.gateway.server import Gateway, GatewayConfig, Session
from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION, CommandState, DispatchState, GameCommand, GameCommandResult, SideEffect,
)
from modules.mcp.core.errors import McpError
from modules.mcp.manifest import build_proto_capability_entries
from modules.runtime.instance_registry import InstanceState


RESULT_VECTORS = json.loads(
    (Path(__file__).resolve().parents[3]
     / "agent/tests/fixtures/canonical-result-vectors.json").read_text()
)["vectors"]


class Authenticator:
    async def authenticate(self, token):
        return AuthResult(True, token)

    async def close(self):
        return None


def command(command_id: str = "cmd-1", *, capability: str = "say",
            payload=None, digest: str | None = None) -> GameCommand:
    now = time.time()
    digest = digest or compute_capability_digest((("protocol-2.4.5", "say", "2.4.5"),))
    return GameCommand(
        GAME_COMMAND_VERSION, f"msg-{command_id}", now, f"idem-{command_id}", command_id,
        f"tool-{command_id}", capability, "canonical.v1", digest, SideEffect.MUTATING,
        payload or {"message": "hello"}, now + 10, "ctx-1", "nonce-1", 1,
    )


async def start_gateway(resume_ttl=5):
    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=resume_ttl),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=coordinator, hosted_session_registry=registry,
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return gateway, registry, events, server, f"ws://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_full_subscriber_queue_cannot_block_suspend_lifecycle():
    class BoundedSession:
        def __init__(self):
            self.connection_id = "conn-full"
            self.instance = SimpleNamespace(user_id="user-1")
            self.hosted_facts = parse_hosted_hello(hello())
            self.hosted_game_session_id = "gs-full"
            self.hosted_resume_identity = "gri-full"
            self.ws = object()
            self.subscribers = []

        def subscribe(self, *, frame_types=None):
            queue = asyncio.Queue(maxsize=1)
            self.subscribers.append(queue)
            return queue

        def unsubscribe(self, queue):
            if queue in self.subscribers:
                self.subscribers.remove(queue)

    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    session = BoundedSession()
    await coordinator.on_authenticated(session, resumed=False)
    route = await registry.resolve_authenticated("gs-full")
    route._subscriber.put_nowait({"type": "stale-frame"})

    await coordinator.on_suspended(session)

    snapshot = coordinator.get_snapshot("gs-full")
    assert snapshot.available is False and snapshot.connection_id is None
    assert events.events[-1].event_type.value == "game_session.suspended"
    assert await registry.resolve_authenticated("gs-full") is None
    assert route._subscriber.get_nowait() == {"type": "hosted_session_detached"}


def hello(**extra):
    entries = ({"source_surface": "protocol-2.4.5", "capability_id": "say",
                "capability_contract_version": "2.4.5"},)
    digest = compute_capability_digest(tuple((e["source_surface"], e["capability_id"],
                                              e["capability_contract_version"]) for e in entries))
    return {
        "type": "hello", "platform_token": "user-1",
        "route_contract_version": "starcoplay.hosted-route.v1",
        "game_instance_nonce": "nonce-1", "capability_digest": digest,
        "capability_entries": list(entries),
        "player_id": "1", "agent_control_enabled": True,
        "agent_control_target_player_id": "1", "agent_control_revision": 1,
        **extra,
    }


@pytest.mark.asyncio
async def test_control_selection_revision_arbitration_is_owner_scoped_and_fail_closed():
    store = HostedControlSelectionStore()
    accepted = await store.accept("owner-1", enabled=True, target_player_id="2", revision=4)
    assert accepted.revision == 4 and accepted.target_player_id == "2"
    assert await store.accept("owner-1", enabled=True, target_player_id="2", revision=4) == accepted
    assert await store.accept("owner-1", enabled=True, target_player_id="1", revision=3) == accepted
    with pytest.raises(ControlSelectionConflict):
        await store.accept("owner-1", enabled=False, target_player_id=None, revision=4)
    other = await store.accept("owner-2", enabled=False, target_player_id=None, revision=1)
    assert other.revision == 1 and other.enabled is False


@pytest.mark.asyncio
async def test_higher_revision_closes_old_before_promoting_online_target():
    order = []
    never = asyncio.Event()
    revocation_converged = asyncio.Event()
    class Activator:
        async def prepare(self, session):
            if session.hosted_player_id == "2" and not revocation_converged.is_set():
                order.append(("prepare_deferred", "2"))
                raise RuntimeError("replacement_binding_not_revoked")
            order.append(("prepare", session.hosted_player_id))
        async def activate(self, session): await never.wait()
        async def close(self, user_id, *, operation_id): order.append(("stop", user_id, operation_id))
    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    class TrackingCoordinator(HostedSessionCoordinator):
        async def on_authenticated(self, session, *, resumed):
            order.append(("available", session.hosted_player_id))
            await super().on_authenticated(session, resumed=resumed)
        async def on_closed(self, session):
            order.append(("closed", session.hosted_player_id))
            await super().on_closed(session)
    coordinator = TrackingCoordinator(registry, events)
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=5),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=coordinator,
        hosted_session_registry=registry, hosted_runtime_activator=Activator(),
        hosted_control_store=HostedControlSelectionStore(),
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        ws1, ws2, submitter = await asyncio.gather(*(websockets.connect(uri) for _ in range(3)))
        await ws1.send(json.dumps(hello(player_id="1")))
        assert json.loads(await ws1.recv())["connection_role"] == "controller"
        await ws2.send(json.dumps(hello(player_id="2")))
        assert json.loads(await ws2.recv())["connection_role"] == "observer"
        await submitter.send(json.dumps(hello(
            player_id="3", agent_control_target_player_id="2", agent_control_revision=2,
        )))
        assert json.loads(await ws1.recv())["connection_role"] == "observer"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws2.recv(), timeout=.02)
        revocation_converged.set()
        assert json.loads(await submitter.recv())["connection_role"] == "observer"
        promoted = json.loads(await ws2.recv())
        assert promoted["connection_role"] == "controller"
        assert order[:4] == [
            ("available", "1"), ("prepare", "1"),
            ("closed", "1"), ("available", "2"),
        ]
        assert ("prepare_deferred", "2") in order
        assert order[-1] == ("prepare", "2")
        assert [event.event_type.value for event in events.events] == [
            "game_session.available", "game_session.closed", "game_session.available",
        ]
        closer_ws = await websockets.connect(uri)
        await closer_ws.send(json.dumps(hello(
            player_id="3", agent_control_enabled=False,
            agent_control_target_player_id=None, agent_control_revision=3,
        )))
        assert json.loads(await closer_ws.recv())["connection_role"] == "observer"
        assert json.loads(await ws2.recv())["connection_role"] == "observer"
        assert order[-2:] == [
            ("closed", "2"), ("stop", "user-1", "control-close:user-1:3"),
        ]
        await asyncio.gather(ws1.close(), ws2.close(), submitter.close(), closer_ws.close())
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_controller_disconnect_then_close_snapshot_closes_retained_gamesession_and_runtime():
    never = asyncio.Event()
    closed = []
    class Activator:
        async def prepare(self, session): return None
        async def activate(self, session): await never.wait()
        async def close(self, user_id, *, operation_id): closed.append((user_id, operation_id))
    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=5),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=HostedSessionCoordinator(registry, events),
        hosted_session_registry=registry, hosted_runtime_activator=Activator(),
        hosted_control_store=HostedControlSelectionStore(),
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        ws = await websockets.connect(uri)
        await ws.send(json.dumps(hello()))
        ack = json.loads(await ws.recv())
        await ws.close()
        for _ in range(50):
            if await registry.resolve_authenticated(ack["game_session_id"]) is None:
                break
            await asyncio.sleep(.01)
        async with websockets.connect(uri) as closer:
            await closer.send(json.dumps(hello(
                player_id="2", agent_control_enabled=False,
                agent_control_target_player_id=None, agent_control_revision=2,
            )))
            assert json.loads(await closer.recv())["connection_role"] == "observer"
        assert events.events[-1].event_type.value == "game_session.closed"
        assert closed == [("user-1", "control-close:user-1:2")]
        assert await registry.resolve_recoverable("user-1", parse_hosted_hello(hello())) is None
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_closed_route_is_fenced_even_when_publisher_fails_then_retries():
    class FailsClosedOnce(InMemoryGameSessionFacts):
        def __init__(self):
            super().__init__(); self.failed = False; self.closed_message_ids = []
        async def publish(self, event):
            if event.event_type.value == "game_session.closed":
                self.closed_message_ids.append(event.message_id)
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("eager_apply_failed")
            await super().publish(event)
    publisher = FailsClosedOnce()
    registry = HostedSessionRegistry()
    coordinator = HostedSessionCoordinator(registry, publisher)
    session = SimpleNamespace(
        connection_id="conn-1", instance=SimpleNamespace(user_id="user-1", meta={}),
        hosted_facts=parse_hosted_hello(hello()), hosted_game_session_id="gs-1",
        hosted_resume_identity="gri-1", subscribers=[], ws=SimpleNamespace(),
        subscribe=lambda **_: asyncio.Queue(), unsubscribe=lambda _: None,
    )
    await coordinator.on_authenticated(session, resumed=False)
    with pytest.raises(ClosePublishDeferred, match="CLOSED publish deferred"):
        await coordinator.on_closed(session)
    assert await registry.resolve_authenticated("gs-1") is None
    assert await registry.resolve_recoverable("user-1", session.hosted_facts) is None
    await coordinator.on_closed(session)
    assert publisher.events[-1].event_type.value == "game_session.closed"
    assert len(set(publisher.closed_message_ids)) == 1


@pytest.mark.asyncio
async def test_resume_same_player_and_nonce_allows_digest_change_on_original_gamesession():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        await ws1.close()
        entries = [{"source_surface": "protocol-2.4.5", "capability_id": "face",
                    "capability_contract_version": "2.4.5"}]
        digest = compute_capability_digest((("protocol-2.4.5", "face", "2.4.5"),))
        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(hello(
                resume_token=ack1["resume_token"], capability_entries=entries,
                capability_digest=digest,
            )))
            ack2 = json.loads(await ws2.recv())
            assert ack2["resumed"] is True
            assert ack2["game_session_id"] == ack1["game_session_id"]
            assert ack2["capability_digest"] == digest
        assert [event.event_type.value for event in events.events][-2:] == [
            "capabilities.changed", "game_session.resumed",
        ]
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_gateway_next_hello_retries_failed_closed_before_runtime_close():
    order, closed_ids = [], []
    binding_revoked = False
    never = asyncio.Event()
    class Publisher(InMemoryGameSessionFacts):
        async def publish(self, event):
            nonlocal binding_revoked
            if event.event_type.value == "game_session.closed":
                closed_ids.append(event.message_id)
                if len(closed_ids) == 1:
                    raise RuntimeError("eager_apply_failed")
                await super().publish(event)
                binding_revoked = True
                order.append("binding_revoked")
                return
            await super().publish(event)
    class Activator:
        async def prepare(self, session): return None
        async def activate(self, session): await never.wait()
        async def close(self, user_id, *, operation_id):
            assert binding_revoked is True
            order.append("runtime_closed")
    publisher = Publisher()
    registry = HostedSessionRegistry()
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=5),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=HostedSessionCoordinator(registry, publisher),
        hosted_session_registry=registry, hosted_runtime_activator=Activator(),
        hosted_control_store=HostedControlSelectionStore(),
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    close_hello = hello(
        player_id="2", agent_control_enabled=False,
        agent_control_target_player_id=None, agent_control_revision=2,
    )
    try:
        controller = await websockets.connect(uri)
        await controller.send(json.dumps(hello()))
        ack = json.loads(await controller.recv())
        first = await websockets.connect(uri)
        await first.send(json.dumps(close_hello))
        with pytest.raises(websockets.ConnectionClosed):
            await first.recv()
        assert await registry.resolve_authenticated(ack["game_session_id"]) is None

        async with websockets.connect(uri) as retry:
            await retry.send(json.dumps(close_hello))
            assert json.loads(await retry.recv())["connection_role"] == "observer"
        assert len(closed_ids) == 2 and len(set(closed_ids)) == 1
        assert order == ["binding_revoked", "runtime_closed"]
        await controller.close()
    finally:
        server.close(); await server.wait_closed()


async def _start_grace_failure_gateway(*, closed_failures: int):
    class Publisher(InMemoryGameSessionFacts):
        def __init__(self):
            super().__init__(); self.closed_ids = []
        async def publish(self, event):
            if event.event_type.value == "game_session.closed":
                self.closed_ids.append(event.message_id)
                if len(self.closed_ids) <= closed_failures:
                    raise RuntimeError("closed_publish_failed")
            await super().publish(event)
    publisher = Publisher()
    registry = HostedSessionRegistry()
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=.02),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=HostedSessionCoordinator(registry, publisher),
        hosted_session_registry=registry,
        hosted_control_store=HostedControlSelectionStore(),
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    return gateway, registry, publisher, server, f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


@pytest.mark.asyncio
async def test_grace_expiry_publish_failure_destroys_old_then_next_hello_replays_and_replaces():
    gateway, registry, publisher, server, uri = await _start_grace_failure_gateway(
        closed_failures=1
    )
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        await ws1.close()
        for _ in range(50):
            if await gateway.registry.get_by_user("user-1") is None:
                break
            await asyncio.sleep(.01)
        assert await gateway.registry.get_by_user("user-1") is None
        assert await registry.resolve_authenticated(ack1["game_session_id"]) is None

        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(hello()))
            ack2 = json.loads(await ws2.recv())
            assert ack2["resumed"] is False
            assert ack2["game_session_id"] != ack1["game_session_id"]
        assert len(publisher.closed_ids) == 2
        assert len(set(publisher.closed_ids)) == 1
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_next_hello_fails_closed_when_pending_expiry_close_retry_still_fails():
    gateway, registry, publisher, server, uri = await _start_grace_failure_gateway(
        closed_failures=2
    )
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        await ws1.close()
        for _ in range(50):
            if await gateway.registry.get_by_user("user-1") is None:
                break
            await asyncio.sleep(.01)
        assert await gateway.registry.get_by_user("user-1") is None

        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(hello()))
            with pytest.raises(websockets.ConnectionClosed):
                await ws2.recv()
        assert await registry.resolve_authenticated(ack1["game_session_id"]) is None
        assert len(publisher.closed_ids) == 2
        assert len(set(publisher.closed_ids)) == 1
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_grace_expiry_prefence_failure_retains_owner_then_auto_retries_to_closed():
    class FailsTombstoneOnce(HostedSessionRegistry):
        def __init__(self):
            super().__init__(); self.attempts = 0
            self.first_failed = asyncio.Event(); self.allow_retry = asyncio.Event()
        async def tombstone(self, game_session_id, connection_id):
            self.attempts += 1
            if self.attempts == 1:
                self.first_failed.set()
                raise RuntimeError("prefence_failed")
            await self.allow_retry.wait()
            await super().tombstone(game_session_id, connection_id)
    registry = FailsTombstoneOnce()
    publisher = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, publisher)
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=.01),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=coordinator, hosted_session_registry=registry,
        hosted_control_store=HostedControlSelectionStore(),
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        await ws1.close()
        await asyncio.wait_for(registry.first_failed.wait(), timeout=.3)
        assert await gateway.registry.get_by_user("user-1") is not None
        assert coordinator.get_snapshot(ack1["game_session_id"]).resumable is True

        registry.allow_retry.set()
        for _ in range(50):
            old = await gateway.registry.get_by_user("user-1")
            snapshot = coordinator.get_snapshot(ack1["game_session_id"])
            if old is None and snapshot is not None and snapshot.resumable is False:
                break
            await asyncio.sleep(.01)
        assert old is None and snapshot.resumable is False

        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(hello()))
            ack2 = json.loads(await ws2.recv())
            assert ack2["resumed"] is False
            assert ack2["game_session_id"] != ack1["game_session_id"]
        assert registry.attempts == 2
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_real_websocket_handshake_dispatch_result_and_no_client_owned_session_identity():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello(game_session_id="forged", resume_identity="forged")))
            ack = json.loads(await ws.recv())
            assert ack["game_session_id"].startswith("gs_") and ack["game_session_id"] != "forged"
            assert ack["resume_identity"].startswith("gri_") and ack["resume_identity"] != "forged"
            route = await registry.resolve_authenticated(ack["game_session_id"])
            assert route is not None
            task = asyncio.create_task(route.dispatch(command()))
            frame = json.loads(await ws.recv())
            receipt = await task
            assert receipt.state is DispatchState.ACCEPTED
            assert frame["type"] == "protocol_envelope"
            request = transport_pb2.Envelope()
            request.ParseFromString(base64.b64decode(frame["payload"]))
            assert request.command_request.command_id == "cmd-1"
            assert frame["hosted_fence"]["game_instance_nonce"] == "nonce-1"

            result = transport_pb2.Envelope(
                protocol_version="2.4.5", message_id="result-1", request_id=request.request_id,
                session_id=ack["game_session_id"], timestamp_ms=int(time.time() * 1000),
            )
            result.command_result.command_id = "cmd-1"
            result.command_result.capability = "say"
            result.command_result.success = True
            result.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
            await ws.send(json.dumps({"type": "protocol_envelope", "encoding": "base64",
                "payload": base64.b64encode(result.SerializeToString()).decode("ascii")}))
            resolved = await route.await_result("cmd-1", time.time() + 2)
            assert resolved.command_id == "cmd-1" and resolved.state is CommandState.SUCCEEDED
            assert events.get_snapshot(ack["game_session_id"]).owner_user_id == "user-1"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_protocol_failure_preserves_named_error_and_debug_detail():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            await route.dispatch(command())
            dispatched = json.loads(await ws.recv())
            request = transport_pb2.Envelope()
            request.ParseFromString(base64.b64decode(dispatched["payload"]))

            envelope = transport_pb2.Envelope(
                protocol_version="2.4.5", message_id="failed-result",
                request_id=request.request_id, session_id=ack["game_session_id"],
                timestamp_ms=int(time.time() * 1000),
            )
            result = envelope.command_result
            result.command_id = "cmd-1"
            result.capability = "say"
            result.success = False
            result.status = capability_pb2.COMMAND_STATUS_FAILED
            result.error.code = common_pb2.EXECUTION_FAILED
            result.error.message = "path is blocked"
            result.error.detail = "target=(3,12)"
            await ws.send(json.dumps({
                "type": "protocol_envelope", "encoding": "base64",
                "payload": base64.b64encode(envelope.SerializeToString()).decode("ascii"),
            }))

            resolved = await route.await_result("cmd-1", time.time() + 2)
            assert resolved.state is CommandState.FAILED
            assert resolved.error_code == "EXECUTION_FAILED"
            assert resolved.output["error"] == {
                "code": "EXECUTION_FAILED",
                "message": "path is blocked",
                "detail": "target=(3,12)",
            }
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_resume_preserves_result_domain_and_stale_socket_is_fenced():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        route = await registry.resolve_authenticated(ack1["game_session_id"])
        await ws1.close()
        for _ in range(50):
            if await registry.resolve_authenticated(ack1["game_session_id"]) is None:
                break
            await asyncio.sleep(0.01)
        assert (await route.health()).healthy is False

        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(hello(resume_token=ack1["resume_token"])))
            ack2 = json.loads(await ws2.recv())
            assert ack2["resumed"] is True
            assert ack2["game_session_id"] == ack1["game_session_id"]
            assert await registry.resolve_authenticated(ack1["game_session_id"]) is route
            assert (await route.health()).healthy is True
    finally:
        server.close()
        await server.wait_closed()


async def _assert_expired_without_token_starts_fresh(nonce: str):
    gateway, registry, events, server, uri = await start_gateway(resume_ttl=.02)
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        await ws1.close()
        for _ in range(50):
            snapshot = events.get_snapshot(ack1["game_session_id"])
            if snapshot is not None and snapshot.resumable is False:
                break
            await asyncio.sleep(.01)
        assert snapshot.resumable is False
        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(hello(game_instance_nonce=nonce)))
            ack2 = json.loads(await ws2.recv())
            assert ack2["resumed"] is False
            assert ack2["game_session_id"] != ack1["game_session_id"]
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_grace_expiry_without_token_same_nonce_starts_fresh_gamesession():
    await _assert_expired_without_token_starts_fresh("nonce-1")


@pytest.mark.asyncio
async def test_grace_expiry_without_token_new_nonce_starts_fresh_replacement():
    await _assert_expired_without_token_starts_fresh("nonce-2")


@pytest.mark.asyncio
async def test_hosted_mode_fails_closed_for_missing_facts_and_dependencies():
    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    with pytest.raises(ValueError, match="requires session registry"):
        Gateway(GatewayConfig(hosted_route_enabled=True), lambda *_: asyncio.sleep(0),
                authenticator=Authenticator(), game_session_event_hook=coordinator)
    gateway, _, _, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "platform_token": "user-1"}))
            with pytest.raises(websockets.ConnectionClosed):
                await ws.recv()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_digest_mismatch_and_cross_user_resume_fail_closed():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as bad:
            await bad.send(json.dumps(hello(capability_digest="sha256:" + "0" * 64)))
            with pytest.raises(websockets.ConnectionClosed):
                await bad.recv()

        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack = json.loads(await ws1.recv())
        await ws1.close()
        forged = hello(resume_token=ack["resume_token"], platform_token="user-2")
        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps(forged))
            with pytest.raises(websockets.ConnectionClosed):
                await ws2.recv()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_duplicate_collision_status_cancel_unknown_and_capability_event():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            first = await route.dispatch(command())
            await ws.recv()
            assert first.state is DispatchState.ACCEPTED
            assert (await route.dispatch(command())).state is DispatchState.DUPLICATE
            changed = command()
            object.__setattr__(changed, "validated_payload", {"message": "different"})
            assert (await route.dispatch(changed)).state is DispatchState.REJECTED
            assert await route.get_status("missing") is CommandState.UNKNOWN
            assert (await route.cancel("missing")).state is CommandState.UNKNOWN
            assert (await route.await_result("missing", time.time() + .01)).state is CommandState.UNKNOWN

            entries = [{"source_surface": "protocol-2.4.5", "capability_id": "face",
                        "capability_contract_version": "2.4.5"}]
            digest = compute_capability_digest((("protocol-2.4.5", "face", "2.4.5"),))
            await ws.send(json.dumps({"type": "capabilities_changed",
                                      "capability_entries": entries, "capability_digest": digest}))
            for _ in range(50):
                if events.get_snapshot(ack["game_session_id"]).capability_digest == digest:
                    break
                await asyncio.sleep(.01)
            assert events.get_snapshot(ack["game_session_id"]).capability_digest == digest
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_real_websocket_v2_json_framing_and_result():
    gateway, registry, events, server, uri = await start_gateway()
    entries = [{"source_surface": "v2", "capability_id": "menu_interact",
                "capability_contract_version": "2.0"}]
    digest = compute_capability_digest((("v2", "menu_interact", "2.0"),))
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello(capability_entries=entries, capability_digest=digest)))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            assert (await route.dispatch(command(
                "v2-1", capability="menu_interact",
                payload={"menu_type": "Shop", "operations": [{"action": "buy"}]},
                digest=digest,
            ))).state is DispatchState.ACCEPTED
            frame = json.loads(await ws.recv())
            assert frame["type"] == "command" and frame["action_id"] == "v2-1"
            assert frame["capability"] == "menu_interact" and frame["hosted_fence"]["deadline_at"] > time.time()
            assert await route.get_status("v2-1") is CommandState.ACCEPTED
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), .02)
            cancelled = await route.cancel("v2-1")
            cancel_frame = json.loads(await ws.recv())
            assert cancel_frame == {"type": "command", "action_id": "v2-1", "cancel": True}
            assert cancelled.accepted is True and cancelled.state is CommandState.ACCEPTED
            raw = RESULT_VECTORS["v2_menu_interact_completed"]["result"]["output"]
            raw = {**raw, "action_id": "v2-1"}
            await ws.send(json.dumps({"type": "command_result", **raw}))
            result = await route.await_result("v2-1", time.time() + 2)
            assert result.state is CommandState.SUCCEEDED
            assert result.output == raw

            assert (await route.dispatch(command(
                "v2-timeout", capability="menu_interact",
                payload={"menu_type": "Shop", "operations": [{"action": "buy"}]},
                digest=digest,
            ))).state is DispatchState.ACCEPTED
            await ws.recv()
            timeout_raw = {
                "type": "command_result", "action_id": "v2-timeout",
                "protocol_version": "2.0", "success": False, "status": "failed",
                "error": {"code": "NAVIGATION_TIMEOUT", "message": "path timed out"},
            }
            await ws.send(json.dumps(timeout_raw))
            timed_out = await route.await_result("v2-timeout", time.time() + 2)
            assert timed_out.state is CommandState.TIMED_OUT
            assert timed_out.output == {key: value for key, value in timeout_raw.items() if key != "type"}
            assert timed_out.error_code == "NAVIGATION_TIMEOUT"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_same_revision_second_socket_becomes_observer_without_displacing_controller():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        old_route = await registry.resolve_authenticated(ack1["game_session_id"])
        ws2 = await websockets.connect(uri)
        await ws2.send(json.dumps(hello(game_instance_nonce="nonce-2")))
        ack2 = json.loads(await ws2.recv())
        assert ack2["connection_role"] == "observer"
        assert "game_session_id" not in ack2
        assert await registry.resolve_authenticated(ack1["game_session_id"]) is old_route
        assert (await old_route.health()).healthy is True
        await ws2.close()
        await ws1.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_dispatch_rechecks_deadline_after_operation_lock_and_sends_nothing():
    class Session:
        connection_id = "conn-1"
        outbound = asyncio.Queue()
        def __init__(self): self.ws = object()
        def subscribe(self, *, frame_types=None): return asyncio.Queue()
        def unsubscribe(self, queue): pass
    ticks = iter((4.0, 6.0))
    route = WebSocketHostedSessionRoute(
        "gs-1", "resume-1",
        HostedHelloFacts("starcoplay.hosted-route.v1", "nonce-1",
                         compute_capability_digest((("protocol-2.4.5", "say", "2.4.5"),)),
                         (("protocol-2.4.5", "say", "2.4.5"),)),
        Session(), clock=lambda: next(ticks),
    )
    value = command()
    object.__setattr__(value, "deadline_at", 5.0)
    receipt = await route.dispatch(value)
    assert receipt.state is DispatchState.REJECTED
    assert route._session.outbound.empty()


def protocol_response(session_id: str, command_id: str, body: str,
                      status=capability_pb2.COMMAND_STATUS_RUNNING,
                      request_id: str | None = None, message_id: str | None = None,
                      protocol_version: str = "2.4.5"):
    envelope = transport_pb2.Envelope(protocol_version=protocol_version,
        message_id=message_id or f"m-{uuid.uuid4().hex}",
        request_id=request_id or command_id, session_id=session_id, timestamp_ms=int(time.time() * 1000))
    target = getattr(envelope, body)
    target.command_id = command_id
    if body in {"command_status_response", "command_progress", "command_result"}:
        target.status = status
    if body == "command_result":
        target.capability = "say"
        target.success = False
    return {"type": "protocol_envelope", "encoding": "base64",
            "payload": base64.b64encode(envelope.SerializeToString()).decode("ascii")}


@pytest.mark.asyncio
async def test_hosted_result_survives_snapshot_volume_beyond_subscriber_capacity():
    session = Session(
        connection_id="conn-long-lived",
        instance=SimpleNamespace(user_id="user-1"),
        ws=object(),
    )
    facts = parse_hosted_hello(hello())
    route = WebSocketHostedSessionRoute("gs-long-lived", "resume-1", facts, session)

    for sequence in range(201):
        session.fanout({"type": "state_snapshot", "sequence": sequence})

    receipt = await route.dispatch(command())
    assert receipt.state is DispatchState.ACCEPTED
    await session.outbound.get()
    session.fanout(protocol_response(
        "gs-long-lived", "cmd-1", "command_result",
        status=capability_pb2.COMMAND_STATUS_COMPLETED,
        request_id="tool-cmd-1",
    ))

    result = await route.await_result("cmd-1", time.time() + 0.05)
    assert result.state is CommandState.SUCCEEDED


def test_unfiltered_gateway_subscription_still_receives_state_snapshots():
    session = Session(
        connection_id="conn-dashboard",
        instance=SimpleNamespace(user_id="user-1"),
        ws=object(),
    )
    subscriber = session.subscribe()

    snapshot = {"type": "state_snapshot", "sequence": 1}
    session.fanout(snapshot)

    assert subscriber.get_nowait() == snapshot


@pytest.mark.asyncio
async def test_status_and_cancel_wait_for_exact_current_session_and_command_response():
    registry = HostedSessionRegistry(control_timeout_s=.5)
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    gateway = Gateway(GatewayConfig(hosted_route_enabled=True),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=coordinator, hosted_session_registry=registry)
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            await route.dispatch(command())
            await ws.recv()

            status_task = asyncio.create_task(route.get_status("cmd-1"))
            status_frame = json.loads(await ws.recv())
            status_request = transport_pb2.Envelope()
            status_request.ParseFromString(base64.b64decode(status_frame["payload"]))
            assert status_request.WhichOneof("body") == "command_status_request"
            assert status_request.command_status_request.command_id == "cmd-1"
            status_request_id = status_request.request_id
            await ws.send(json.dumps(protocol_response(ack["game_session_id"], "wrong", "command_status_response")))
            await ws.send(json.dumps(protocol_response("wrong-session", "cmd-1", "command_status_response")))
            await ws.send(json.dumps(protocol_response(ack["game_session_id"], "cmd-1",
                                                       "command_status_response", request_id="wrong-request")))
            await ws.send(json.dumps(protocol_response(ack["game_session_id"], "cmd-1",
                                                       "command_status_response", request_id=status_request_id)))
            assert await status_task is CommandState.RUNNING

            cancel_task = asyncio.create_task(route.cancel("cmd-1"))
            cancel_frame = json.loads(await ws.recv())
            cancel_request = transport_pb2.Envelope()
            cancel_request.ParseFromString(base64.b64decode(cancel_frame["payload"]))
            assert cancel_request.WhichOneof("body") == "command_cancel"
            assert cancel_request.command_cancel.command_id == "cmd-1"
            cancel_request_id = cancel_request.request_id
            await ws.send(json.dumps(protocol_response(ack["game_session_id"], "wrong", "command_accepted")))
            await ws.send(json.dumps(protocol_response(ack["game_session_id"], "cmd-1",
                                                       "command_accepted", request_id=cancel_request_id)))
            cancelled = await cancel_task
            assert cancelled.accepted is True and cancelled.state is CommandState.RUNNING

            cancel_task = asyncio.create_task(route.cancel("cmd-1"))
            cancel_frame = json.loads(await ws.recv())
            cancel_request = transport_pb2.Envelope()
            cancel_request.ParseFromString(base64.b64decode(cancel_frame["payload"]))
            cancelled_result = transport_pb2.Envelope(
                protocol_version="2.4.5", message_id="mod-cancelled-result",
                request_id=cancel_request.request_id, session_id=ack["game_session_id"],
                timestamp_ms=int(time.time() * 1000),
            )
            cancelled_result.command_result.command_id = "cmd-1"
            cancelled_result.command_result.status = capability_pb2.COMMAND_STATUS_CANCELLED
            cancelled_result.command_result.success = False
            await ws.send(json.dumps({"type": "protocol_envelope", "encoding": "base64",
                "payload": base64.b64encode(cancelled_result.SerializeToString()).decode()}))
            cancelled = await cancel_task
            assert cancelled.accepted is True and cancelled.state is CommandState.CANCELLED
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("terminal,proto_status", [
    (CommandState.SUCCEEDED, capability_pb2.COMMAND_STATUS_COMPLETED),
    (CommandState.FAILED, capability_pb2.COMMAND_STATUS_FAILED),
    (CommandState.CANCELLED, capability_pb2.COMMAND_STATUS_CANCELLED),
    (CommandState.TIMED_OUT, capability_pb2.COMMAND_STATUS_TIMEOUT),
    (CommandState.EXPIRED_UNKNOWN, None),
])
@pytest.mark.parametrize("stale_body", [
    "command_accepted", "command_progress", "command_status_response", "command_result",
])
@pytest.mark.asyncio
async def test_terminal_state_is_monotonic_across_late_real_websocket_frames(
    terminal, proto_status, stale_body,
):
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            await route.dispatch(command())
            await ws.recv()
            if proto_status is None:
                route._records["cmd-1"].state = terminal
                route._records["cmd-1"].result = GameCommandResult("cmd-1", terminal)
            else:
                result = transport_pb2.Envelope(protocol_version="2.4.5", message_id="terminal",
                    request_id="tool-cmd-1", session_id=ack["game_session_id"],
                    timestamp_ms=int(time.time() * 1000))
                result.command_result.command_id = "cmd-1"
                result.command_result.capability = "say"
                result.command_result.status = proto_status
                result.command_result.success = terminal is CommandState.SUCCEEDED
                await ws.send(json.dumps({"type": "protocol_envelope", "encoding": "base64",
                    "payload": base64.b64encode(result.SerializeToString()).decode("ascii")}))
                assert (await route.await_result("cmd-1", time.time() + 1)).state is terminal

            stale = protocol_response(
                ack["game_session_id"], "cmd-1", stale_body,
                capability_pb2.COMMAND_STATUS_RUNNING,
                request_id="cmd-1" if stale_body == "command_status_response" else "tool-cmd-1",
            )
            await ws.send(json.dumps(stale))
            frame = await asyncio.wait_for(route._subscriber.get(), 1)
            route._ingest(frame)
            assert (await route.await_result("cmd-1", time.time() + .01)).state is terminal
            assert await route.get_status("cmd-1") is terminal
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), .02)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_wrong_request_id_progress_and_result_are_purely_ignored():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            await route.dispatch(command())
            await ws.recv()
            wait = asyncio.create_task(route.await_result("cmd-1", time.time() + 1))
            await ws.send(json.dumps(protocol_response(
                ack["game_session_id"], "cmd-1", "command_progress",
                request_id="wrong-request",
            )))
            await ws.send(json.dumps(protocol_response(
                ack["game_session_id"], "cmd-1", "command_result",
                capability_pb2.COMMAND_STATUS_FAILED, request_id="wrong-request",
            )))
            await asyncio.sleep(.02)
            assert route._records["cmd-1"].state is CommandState.ACCEPTED
            result = transport_pb2.Envelope(protocol_version="2.4.5", message_id="right-result",
                request_id="tool-cmd-1", session_id=ack["game_session_id"],
                timestamp_ms=int(time.time() * 1000))
            result.command_result.command_id = "cmd-1"
            result.command_result.capability = "say"
            result.command_result.status = capability_pb2.COMMAND_STATUS_COMPLETED
            result.command_result.success = True
            await ws.send(json.dumps({"type": "protocol_envelope", "encoding": "base64",
                "payload": base64.b64encode(result.SerializeToString()).decode("ascii")}))
            assert (await wait).state is CommandState.SUCCEEDED
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_detached_terminal_status_and_result_need_no_socket_or_outbound():
    gateway, registry, events, server, uri = await start_gateway()
    try:
        ws = await websockets.connect(uri)
        await ws.send(json.dumps(hello()))
        ack = json.loads(await ws.recv())
        route = await registry.resolve_authenticated(ack["game_session_id"])
        await route.dispatch(command())
        await ws.recv()
        route._records["cmd-1"].state = CommandState.SUCCEEDED
        route._records["cmd-1"].result = GameCommandResult("cmd-1", CommandState.SUCCEEDED)
        await ws.close()
        for _ in range(50):
            if not (await route.health()).healthy:
                break
            await asyncio.sleep(.01)
        assert await route.get_status("cmd-1") is CommandState.SUCCEEDED
        assert (await route.await_result("cmd-1", time.time() + .01)).state is CommandState.SUCCEEDED
        assert route._session.outbound.empty()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_live_socket_resume_closes_old_and_old_grace_cannot_destroy_new_owner():
    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    gateway = Gateway(GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=.03),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=coordinator, hosted_session_registry=registry)
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        ws1 = await websockets.connect(uri)
        await ws1.send(json.dumps(hello()))
        ack1 = json.loads(await ws1.recv())
        instance_id = ack1["agent_instance_id"]
        ws2 = await websockets.connect(uri)
        await ws2.send(json.dumps(hello(resume_token=ack1["resume_token"])))
        ack2 = json.loads(await ws2.recv())
        assert ack2["resumed"] is True and ack2["game_session_id"] == ack1["game_session_id"]
        role_change = json.loads(await ws1.recv())
        assert role_change["connection_role"] == "observer"
        await asyncio.sleep(.08)
        snapshot = coordinator.get_snapshot(ack1["game_session_id"])
        assert snapshot.available is True and snapshot.connection_id == gateway.get_session(instance_id).connection_id
        assert gateway.get_session(instance_id).ws is ws2 or gateway.get_session(instance_id).connection_id
        assert gateway.get_session(instance_id).instance.state.value == "running"
        assert await registry.resolve_authenticated(ack1["game_session_id"]) is not None
        await ws2.close()
    finally:
        server.close()
        await server.wait_closed()


def decode_control(raw: str):
    frame = json.loads(raw)
    envelope = transport_pb2.Envelope()
    envelope.ParseFromString(base64.b64decode(frame["payload"]))
    return envelope


@pytest.mark.asyncio
async def test_consecutive_controls_handle_out_of_order_duplicates_and_wrong_protocol():
    registry = HostedSessionRegistry(control_timeout_s=1)
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    gateway = Gateway(GatewayConfig(hosted_route_enabled=True),
        worker_handle=lambda *_: asyncio.sleep(0), authenticator=Authenticator(),
        game_session_event_hook=coordinator, hosted_session_registry=registry)
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            route = await registry.resolve_authenticated(ack["game_session_id"])
            await route.dispatch(command())
            await ws.recv()

            s1 = asyncio.create_task(route.get_status("cmd-1"))
            s2 = asyncio.create_task(route.get_status("cmd-1"))
            sr1, sr2 = decode_control(await ws.recv()), decode_control(await ws.recv())
            assert sr1.request_id != sr2.request_id
            duplicate_id = "duplicate-status"
            second = protocol_response(ack["game_session_id"], "cmd-1", "command_status_response",
                                       request_id=sr2.request_id, message_id=duplicate_id)
            await ws.send(json.dumps(second))
            await ws.send(json.dumps(second))
            await ws.send(json.dumps(protocol_response(
                ack["game_session_id"], "cmd-1", "command_status_response",
                request_id=sr1.request_id, protocol_version="9.9.9")))
            await ws.send(json.dumps(protocol_response(
                ack["game_session_id"], "cmd-1", "command_status_response",
                request_id=sr1.request_id)))
            assert await s1 is CommandState.RUNNING
            assert await s2 is CommandState.RUNNING

            c1 = asyncio.create_task(route.cancel("cmd-1"))
            c2 = asyncio.create_task(route.cancel("cmd-1"))
            cr1, cr2 = decode_control(await ws.recv()), decode_control(await ws.recv())
            assert cr1.request_id != cr2.request_id
            await ws.send(json.dumps(protocol_response(
                ack["game_session_id"], "cmd-1", "command_accepted", request_id=cr2.request_id)))
            await ws.send(json.dumps(protocol_response(
                ack["game_session_id"], "cmd-1", "command_accepted", request_id=cr1.request_id)))
            assert (await c1).accepted is True
            assert (await c2).accepted is True
    finally:
        server.close()
        await server.wait_closed()


class QueueSession:
    def __init__(self, connection_id):
        self.connection_id = connection_id
        self.outbound = asyncio.Queue()
        self.subscribers = []
        self.ws = object()
    def subscribe(self, *, frame_types=None):
        queue = asyncio.Queue()
        self.subscribers.append(queue)
        return queue
    def unsubscribe(self, queue):
        if queue in self.subscribers:
            self.subscribers.remove(queue)


def queue_route(control_timeout_s=.2):
    digest = compute_capability_digest((("protocol-2.4.5", "say", "2.4.5"),))
    facts = HostedHelloFacts("starcoplay.hosted-route.v1", "nonce-1", digest,
                             (("protocol-2.4.5", "say", "2.4.5"),))
    old = QueueSession("c1")
    route = WebSocketHostedSessionRoute("gs-1", "resume-1", facts, old,
                                        control_timeout_s=control_timeout_s)
    return route, facts, old


@pytest.mark.parametrize("operation", ["cancel", "status"])
@pytest.mark.asyncio
async def test_control_rebind_race_sends_only_to_new_current_session(operation):
    route, facts, old = queue_route()
    await route.dispatch(command())
    await old.outbound.get()
    await route._operation_lock.acquire()
    task = asyncio.create_task(route.cancel("cmd-1") if operation == "cancel"
                               else route.get_status("cmd-1"))
    await asyncio.sleep(0)
    new = QueueSession("c2")
    route.rebind(facts, new)
    route._operation_lock.release()
    frame = await asyncio.wait_for(new.outbound.get(), 1)
    assert old.outbound.empty()
    request = transport_pb2.Envelope()
    request.ParseFromString(base64.b64decode(frame["payload"]))
    assert request.request_id.startswith("cancel:" if operation == "cancel" else "status:")
    body = "command_accepted" if operation == "cancel" else "command_status_response"
    await route._subscriber.put(protocol_response(
        "gs-1", "cmd-1", body, request_id=request.request_id,
    ))
    result = await task
    if operation == "cancel":
        assert result.accepted is True
    else:
        assert result is CommandState.RUNNING


@pytest.mark.parametrize("operation", ["cancel", "status"])
@pytest.mark.asyncio
async def test_control_lock_race_with_disconnect_fails_closed_and_sends_nothing(operation):
    route, facts, old = queue_route()
    await route.dispatch(command())
    await old.outbound.get()
    await route._operation_lock.acquire()
    task = asyncio.create_task(route.cancel("cmd-1") if operation == "cancel"
                               else route.get_status("cmd-1"))
    await asyncio.sleep(0)
    route.detach("c1")
    route._operation_lock.release()
    with pytest.raises(McpError, match="stale or disconnected"):
        await task
    assert old.outbound.empty()


@pytest.mark.parametrize("proto_status,expected_state", [
    (capability_pb2.COMMAND_STATUS_REJECTED, CommandState.FAILED),
    (capability_pb2.COMMAND_STATUS_UNSPECIFIED, CommandState.UNKNOWN),
])
@pytest.mark.asyncio
async def test_mod_style_cancel_result_rejected_or_unknown_is_not_accepted(proto_status, expected_state):
    route, _, session = queue_route(control_timeout_s=1)
    await route.dispatch(command())
    await session.outbound.get()
    task = asyncio.create_task(route.cancel("cmd-1"))
    frame = await session.outbound.get()
    request = transport_pb2.Envelope()
    request.ParseFromString(base64.b64decode(frame["payload"]))
    response = transport_pb2.Envelope(
        protocol_version="2.4.5", message_id=f"mod-result-{proto_status}",
        request_id=request.request_id, session_id="gs-1",
        timestamp_ms=int(time.time() * 1000),
    )
    response.command_result.command_id = "cmd-1"
    response.command_result.status = proto_status
    response.command_result.success = False
    await route._subscriber.put({
        "type": "protocol_envelope", "encoding": "base64",
        "payload": base64.b64encode(response.SerializeToString()).decode(),
    })
    result = await task
    assert result.accepted is False
    assert result.state is expected_state


# ═══════════════════════════════════════════════════════════════════════
# M3.1 Capability Manifest Snapshot Baseline Tests
# ═══════════════════════════════════════════════════════════════════════


def test_cross_language_digest_agreement_proto_only():
    """Python compute_capability_digest produces well-formed digests.

    The C# side uses the identical algorithm in
    HostedRouteHello.ComputeCapabilityDigest.
    Cross-language agreement is verified by running both test suites
    against the same canonical input and comparing outputs.
    """
    entries = (
        ("protocol-2.4.5", "face", "2.4.5"),
        ("protocol-2.4.5", "say", "2.4.5"),
    )
    assert compute_capability_digest(entries) == (
        "sha256:f9daca8a6fd4072c95bcd36cec6c8c0c67cab546c3fc1ceaba0df0987db29778"
    )


def test_empty_or_missing_facts_fail_closed():
    """Missing required hello fields must fail with upstream_protocol_error."""
    valid_entries = [
        {"source_surface": "protocol-2.4.5", "capability_id": "say",
         "capability_contract_version": "2.4.5"},
    ]
    valid_digest = compute_capability_digest(
        (("protocol-2.4.5", "say", "2.4.5"),)
    )
    base = {
        "route_contract_version": "starcoplay.hosted-route.v1",
        "game_instance_nonce": "nonce-1",
        "capability_digest": valid_digest,
        "capability_entries": valid_entries,
    }

    for missing_key in ("route_contract_version", "game_instance_nonce", "capability_digest"):
        bad = {**base}
        del bad[missing_key]
        with pytest.raises(McpError, match="missing or invalid"):
            parse_hosted_hello(bad)

    with pytest.raises(McpError, match="missing or invalid"):
        parse_hosted_hello({**base, "capability_entries": []})


def test_unknown_surface_accepted():
    """Unknown source surfaces are accepted (forward compat) without rejection."""
    entries = [
        {"source_surface": "future-proto-v3", "capability_id": "beam",
         "capability_contract_version": "3.0.0"},
    ]
    digest = compute_capability_digest(
        (("future-proto-v3", "beam", "3.0.0"),)
    )
    facts = parse_hosted_hello({
        "route_contract_version": "starcoplay.hosted-route.v1",
        "game_instance_nonce": "nonce-1",
        "capability_digest": digest,
        "capability_entries": entries,
        "player_id": "1", "agent_control_enabled": True,
        "agent_control_target_player_id": "1", "agent_control_revision": 1,
    })
    assert facts.capability_entries == (("future-proto-v3", "beam", "3.0.0"),)


def test_single_proto_entry_produces_valid_snapshot():
    """Minimal valid snapshot: one Proto entry with correct version."""
    entries = [{"source_surface": "protocol-2.4.5", "capability_id": "say",
                "capability_contract_version": "2.4.5"}]
    digest = compute_capability_digest((("protocol-2.4.5", "say", "2.4.5"),))
    facts = parse_hosted_hello({
        "route_contract_version": "starcoplay.hosted-route.v1",
        "game_instance_nonce": "nonce-1",
        "capability_digest": digest,
        "capability_entries": entries,
        "player_id": "1", "agent_control_enabled": True,
        "agent_control_target_player_id": "1", "agent_control_revision": 1,
    })
    assert facts.capability_entries == (("protocol-2.4.5", "say", "2.4.5"),)
    assert facts.capability_digest == digest
    assert len(facts.capability_entries) == 1


# ═══════════════════════════════════════════════════════════════════════
# M3.1 Local Expected Snapshot and Parity Tests
# ═══════════════════════════════════════════════════════════════════════


def test_local_entries_no_v2_compat():
    """Production Local entries contain no v2-compat and match Proto registrations."""
    repo_root = Path(__file__).resolve().parents[3]
    entries = build_proto_capability_entries(repo_root)
    expected_ids = {
        "emote", "equip", "face", "go_to", "go_to_bed", "interact",
        "menu_click", "menu_close", "move_to", "open_menu",
        "query_inspect_refs", "query_inventory_snapshot", "query_menu",
        "query_runtime_snapshot", "query_ui_snapshot", "query_world_region",
        "say", "tp", "use_tool",
    }
    assert {entry["capability_id"] for entry in entries} == expected_ids
    for entry in entries:
        assert entry["source_surface"].startswith("protocol-"), \
            f"unexpected source_surface: {entry['source_surface']}"
        assert not entry["source_surface"].startswith("v2"), \
            f"v2 entry leaked into Proto snapshot: {entry}"
        assert entry["capability_id"], "empty capability_id"
        assert entry["capability_contract_version"], "empty contract version"
    # Verify canonical ordering: each entry is unique and sorted by the
    # tab-separated line representation.
    for i in range(len(entries) - 1):
        a = f"{entries[i]['source_surface']}\t{entries[i]['capability_id']}\t{entries[i]['capability_contract_version']}"
        b = f"{entries[i+1]['source_surface']}\t{entries[i+1]['capability_id']}\t{entries[i+1]['capability_contract_version']}"
        assert a < b, f"entries not canonically sorted: {a!r} >= {b!r}"


def test_local_hosted_parity():
    """The production Local Proto set survives the Hosted raw snapshot parser unchanged."""
    repo_root = Path(__file__).resolve().parents[3]
    entries = build_proto_capability_entries(repo_root)
    triple = tuple((e["source_surface"], e["capability_id"], e["capability_contract_version"]) for e in entries)
    # Hosted path: parse_hosted_hello + compute_capability_digest
    hosted_digest = compute_capability_digest(triple)
    facts = parse_hosted_hello({
        "route_contract_version": "starcoplay.hosted-route.v1",
        "game_instance_nonce": "nonce-1",
        "capability_digest": hosted_digest,
        "capability_entries": entries,
        "player_id": "1", "agent_control_enabled": True,
        "agent_control_target_player_id": "1", "agent_control_revision": 1,
    })
    # Local path: capability_snapshot_digest
    from modules.mcp.routers.local_mod_connection import capability_snapshot_digest
    local_digest = capability_snapshot_digest(entries)
    assert hosted_digest == local_digest
    assert facts.capability_digest == local_digest
    assert facts.capability_entries == tuple(sorted(set(triple)))


@pytest.mark.asyncio
async def test_capabilities_changed_noop_with_same_digest():
    """Duplicate and reordered entries canonicalize before no-op comparison."""
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            initial_entries = [
                {"source_surface": "protocol-2.4.5", "capability_id": "say",
                 "capability_contract_version": "2.4.5"},
                {"source_surface": "protocol-2.4.5", "capability_id": "face",
                 "capability_contract_version": "2.4.5"},
            ]
            original_digest = compute_capability_digest(tuple(
                (e["source_surface"], e["capability_id"], e["capability_contract_version"])
                for e in initial_entries
            ))
            await ws.send(json.dumps(hello(
                capability_entries=initial_entries, capability_digest=original_digest)))
            ack = json.loads(await ws.recv())
            original = events.get_snapshot(ack["game_session_id"])
            original_event_count = len(events.events)
            await ws.send(json.dumps({
                "type": "capabilities_changed",
                "capability_entries": [initial_entries[1], initial_entries[0], initial_entries[0]],
                "capability_digest": original_digest,
            }))
            await asyncio.sleep(0.05)
            preserved = events.get_snapshot(ack["game_session_id"])
            assert preserved.capability_digest == original_digest
            assert preserved.session_version == original.session_version
            assert len(events.events) == original_event_count
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_capabilities_changed_invalid_digest_leaves_old_unchanged():
    """capabilities_changed with invalid digest rejects and leaves old snapshot."""
    gateway, registry, events, server, uri = await start_gateway()
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hello()))
            ack = json.loads(await ws.recv())
            original = events.get_snapshot(ack["game_session_id"])
            route = await registry.resolve_authenticated(ack["game_session_id"])
            original_facts = route.facts
            original_meta_digest = route._session.instance.meta["hosted_capability_digest"]
            # Send capabilities_changed with a WRONG digest
            await ws.send(json.dumps({
                "type": "capabilities_changed",
                "capability_entries": [{"source_surface": "protocol-2.4.5",
                                        "capability_id": "face",
                                        "capability_contract_version": "2.4.5"}],
                "capability_digest": "sha256:" + "0" * 64,
            }))
            with pytest.raises(websockets.ConnectionClosed):
                await ws.recv()
            # Old snapshot must be preserved
            preserved = events.get_snapshot(ack["game_session_id"])
            assert preserved.capability_digest == original.capability_digest
            # The invalid frame closes the socket, so normal suspension advances
            # session lifecycle state without accepting any capability mutation.
            assert preserved.session_version == original.session_version + 1
            assert preserved.available is False
            assert route.facts == original_facts
            assert route._session.hosted_facts == original_facts
            assert route._session.instance.meta["hosted_capability_digest"] == original_meta_digest
    finally:
        server.close()
        await server.wait_closed()


# ═══════════════════════════════════════════════════════════════════════
# Hosted transport recovery: 1006 / no-close-frame / heartbeat silence
# ═══════════════════════════════════════════════════════════════════════


class _RecoveryAuth:
    def __init__(self, user_id="user-1"):
        self._user = user_id

    async def authenticate(self, token):
        return AuthResult(True, token if token and token.strip() else self._user)

    async def close(self):
        pass


class _RecoveryActivator:
    """Minimal activator that records calls without side effects."""

    def __init__(self):
        self.prepare_calls = []
        self.activate_calls = []
        self.close_calls = []

    async def prepare(self, session):
        self.prepare_calls.append(session)

    async def activate(self, session):
        self.activate_calls.append(session)

    async def close(self, user_id, *, operation_id):
        self.close_calls.append((user_id, operation_id))


class _RecoveryWS:
    """Mock WebSocket that sends a valid hosted hello and captures responses."""

    def __init__(self, hello_payload: dict):
        self.sent: list[str] = []
        self._hello = json.dumps(hello_payload)
        self.subscribers: list[asyncio.Queue] = []

    async def recv(self):
        return self._hello

    async def send(self, payload: str):
        self.sent.append(payload)

    async def close(self):
        pass

    def subscribe(self, *, frame_types=None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass


class _ExplicitEndWS(_RecoveryWS):
    """Handshake once, then explicitly end the authenticated GameSession."""

    def __init__(self, hello_payload: dict):
        super().__init__(hello_payload)
        self._frames = iter((json.dumps({
            "type": "session.end",
            "reason": "returned_to_title",
        }),))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._frames)
        except StopIteration:
            raise StopAsyncIteration


def _recovery_hello(*, token="user-1", player_id="1", revision=1,
                    resume_token: str | None = None):
    entries = [{"source_surface": "protocol-2.4.5", "capability_id": "say",
                "capability_contract_version": "2.4.5"}]
    digest = compute_capability_digest((("protocol-2.4.5", "say", "2.4.5"),))
    msg = {
        "type": "hello", "platform_token": token,
        "route_contract_version": "starcoplay.hosted-route.v1",
        "game_instance_nonce": "nonce-1", "capability_digest": digest,
        "capability_entries": list(entries),
        "player_id": player_id,
        "agent_control_enabled": True,
        "agent_control_target_player_id": player_id,
        "agent_control_revision": revision,
    }
    if resume_token is not None:
        msg["resume_token"] = resume_token
    return msg


def _make_recovery_gateway(*, resume_ttl_s: float = 0.5):
    """Create a Gateway wired for hosted route with a mock activator."""
    registry = HostedSessionRegistry()
    events = InMemoryGameSessionFacts()
    coordinator = HostedSessionCoordinator(registry, events)
    activator = _RecoveryActivator()
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=resume_ttl_s),
        AsyncMock(),
        authenticator=_RecoveryAuth(),
        game_session_event_hook=coordinator,
        hosted_session_registry=registry,
        hosted_runtime_activator=activator,
        hosted_control_store=HostedControlSelectionStore(),
    )
    return gateway, registry, events, activator


@pytest.mark.asyncio
async def test_1006_disconnect_drains_not_fails_and_resume_preserves_gamesession():
    """A ws_close_1006 on a hosted controller must DRAIN (not FAIL) and
    preserve the GameSession identity so a resume within TTL recovers the
    same session, generation, and runtime identity."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    # --- first connection: normal handshake, then simulated 1006 ---
    ws1 = _RecoveryWS(_recovery_hello())
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)

    # Instance must be DRAINING, not FAILED
    inst = await gateway.registry.get_by_user("user-1")
    assert inst is not None
    assert inst.state == InstanceState.DRAINING
    assert inst.resume_token is not None
    resume_tok = inst.resume_token
    gs_id = inst.meta.get("hosted_game_session_id")
    resume_id = inst.meta.get("hosted_resume_identity")
    assert isinstance(gs_id, str) and gs_id.startswith("gs_")
    assert isinstance(resume_id, str)

    # hello_ok must carry the resume token and game_session_id
    hello_ok = json.loads(ws1.sent[-1])
    assert hello_ok["type"] == "hello_ok"
    assert hello_ok["resume_token"] == resume_tok
    assert hello_ok["resumed"] is False
    assert hello_ok["game_session_id"] == gs_id

    # --- second connection: resume with same token, keep online for assertions ---
    ws2 = _RecoveryWS(_recovery_hello(resume_token=resume_tok))
    online = asyncio.Event()
    gateway._run_session = lambda _: online.wait()
    task = asyncio.create_task(gateway._handle_connection(ws2))
    for _ in range(20):
        current = await gateway.registry.get_by_user("user-1")
        if current is not None and current.state == InstanceState.RUNNING:
            break
        await asyncio.sleep(0)

    try:
        # Must be the SAME instance, now RUNNING (still online)
        inst2 = await gateway.registry.get_by_user("user-1")
        assert inst2 is not None
        assert inst2.agent_instance_id == inst.agent_instance_id
        assert inst2.state == InstanceState.RUNNING
        assert inst2.meta.get("hosted_game_session_id") == gs_id
        assert inst2.meta.get("hosted_resume_identity") == resume_id

        hello_ok2 = json.loads(ws2.sent[-1])
        assert hello_ok2["type"] == "hello_ok"
        assert hello_ok2["resumed"] is True
        assert hello_ok2["game_session_id"] == gs_id
    finally:
        online.set()
        await task


@pytest.mark.asyncio
async def test_1006_ttl_expiry_destroys_instance():
    """After 1006 disconnect, the resume token must expire after TTL and the
    instance must be destroyed; a late resume attempt must start fresh."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=0.02)

    ws1 = _RecoveryWS(_recovery_hello())
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)

    inst = await gateway.registry.get_by_user("user-1")
    assert inst is not None and inst.state == InstanceState.DRAINING
    resume_tok = inst.resume_token
    gs_id = inst.meta.get("hosted_game_session_id")

    # Wait for TTL to expire
    for _ in range(50):
        if await gateway.registry.get_by_user("user-1") is None:
            break
        await asyncio.sleep(0.01)
    assert await gateway.registry.get_by_user("user-1") is None

    # Resume after TTL must start fresh
    ws2 = _RecoveryWS(_recovery_hello(resume_token=resume_tok))
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(ws2)

    inst2 = await gateway.registry.get_by_user("user-1")
    assert inst2 is not None
    # After TTL expiry, the old instance is gone; the token is unknown so
    # a fresh GameSession is minted.
    hello_ok2 = json.loads(ws2.sent[-1])
    assert hello_ok2["resumed"] is False
    assert hello_ok2["game_session_id"] != gs_id


@pytest.mark.asyncio
async def test_1006_wrong_resume_token_is_fenced():
    """A wrong resume token after 1006 must NOT receive a GameSession
    identity.  The handler rejects before hello_ok so the bogus connection
    receives nothing; the original DRAINING controller remains intact."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    ws1 = _RecoveryWS(_recovery_hello())
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)

    inst = await gateway.registry.get_by_user("user-1")
    assert inst is not None and inst.state == InstanceState.DRAINING
    gs_id = inst.meta.get("hosted_game_session_id")
    old_instance_id = inst.agent_instance_id

    # Second connection with a bogus token — handler rejects before hello_ok
    ws2 = _RecoveryWS(_recovery_hello(resume_token="bogus-token"))
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(ws2)

    # Bogus connection receives no response (fenced before hello_ok)
    assert ws2.sent == []

    # The original DRAINING controller is intact, not displaced
    inst1 = await gateway.registry.get_by_user("user-1")
    assert inst1 is not None
    assert inst1.agent_instance_id == old_instance_id
    assert inst1.state == InstanceState.DRAINING
    assert inst1.meta.get("hosted_game_session_id") == gs_id


@pytest.mark.asyncio
async def test_1006_without_token_offers_same_target_revision_recovery_then_takes_over():
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    ws1 = _RecoveryWS(_recovery_hello(revision=4))
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)
    first_ack = json.loads(ws1.sent[-1])

    probe = _RecoveryWS(_recovery_hello(revision=4))
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(probe)
    frames = [json.loads(value) for value in probe.sent]
    assert frames[0]["type"] == "hello_ok"
    assert frames[0]["connection_role"] == "observer"
    assert frames[1] == {
        "type": "hosted_status",
        "code": "controller_recovery_required",
        "retryable": True,
        "accepted_agent_control_revision": 4,
    }
    diagnostics = gateway.hosted_diagnostics_snapshot()
    assert diagnostics["summary"]["reconnecting"] == 1
    assert diagnostics["last_takeover"]["outcome"] == "recovery_offered"
    assert diagnostics["last_takeover"]["target_player_id"] == "1"
    assert "user-1" not in str(diagnostics)

    replacement = _RecoveryWS(_recovery_hello(revision=5))
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(replacement)
    ack = json.loads(replacement.sent[-1])
    assert ack["type"] == "hello_ok"
    assert ack["connection_role"] == "controller"
    assert ack["accepted_agent_control_revision"] == 5
    assert ack["resumed"] is True
    assert ack["game_session_id"] == first_ack["game_session_id"]
    event_types = [event.event_type.value for event in events.events]
    assert event_types[:3] == [
        "game_session.available", "game_session.suspended", "game_session.resumed",
    ]


@pytest.mark.asyncio
async def test_live_same_target_controller_never_offers_revision_recovery():
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)
    online = asyncio.Event()
    gateway._run_session = lambda _: online.wait()
    controller = _RecoveryWS(_recovery_hello(revision=4))
    controller_task = asyncio.create_task(gateway._handle_connection(controller))
    await asyncio.sleep(0)

    try:
        duplicate = _RecoveryWS(_recovery_hello(revision=4))
        gateway._run_session = AsyncMock(return_value=None)
        await gateway._handle_connection(duplicate)
        frames = [json.loads(value) for value in duplicate.sent]
        assert frames[0]["connection_role"] == "observer"
        assert all(frame.get("code") != "controller_recovery_required" for frame in frames)
    finally:
        online.set()
        await controller_task


@pytest.mark.asyncio
async def test_1006_cross_user_resume_fails():
    """A resume token is owner-bound; a different user presenting the same
    token after 1006 must be rejected (the handler swallows the protocol
    error and fails the fresh instance without harming the original)."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    ws1 = _RecoveryWS(_recovery_hello(token="user-1"))
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)

    inst = await gateway.registry.get_by_user("user-1")
    assert inst is not None and inst.state == InstanceState.DRAINING
    resume_tok = inst.resume_token

    # Different user tries to use user-1's resume token.
    # hosted_resume_owner_mismatch is caught by except Exception and the
    # fresh instance is FAILED; the handler does not re-raise.
    ws2 = _RecoveryWS(_recovery_hello(token="user-2", resume_token=resume_tok))
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(ws2)

    # user-2's fresh instance must be gone (FAILED → drained → destroyed)
    assert await gateway.registry.get_by_user("user-2") is None
    # user-1's original instance must still be intact in DRAINING
    inst1 = await gateway.registry.get_by_user("user-1")
    assert inst1 is not None and inst1.state == InstanceState.DRAINING


@pytest.mark.asyncio
async def test_1006_old_connection_finally_does_not_destroy_new_owner():
    """When a 1006-disconnected session is resumed by a new connection, the
    old connection's scheduled destroy timer must be fenced and leave the
    resumed session untouched, even after the old TTL has passed."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=0.02)

    # --- first connection: 1006 disconnect ---
    ws1 = _RecoveryWS(_recovery_hello())
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)

    inst = await gateway.registry.get_by_user("user-1")
    assert inst.state == InstanceState.DRAINING
    resume_tok = inst.resume_token
    gs_id = inst.meta.get("hosted_game_session_id")

    # --- second connection: resume with token, keep online ---
    ws2 = _RecoveryWS(_recovery_hello(resume_token=resume_tok))
    online = asyncio.Event()
    gateway._run_session = lambda _: online.wait()
    task = asyncio.create_task(gateway._handle_connection(ws2))
    for _ in range(20):
        current = await gateway.registry.get_by_user("user-1")
        if current is not None and current.state == InstanceState.RUNNING:
            break
        await asyncio.sleep(0)

    try:
        inst2 = await gateway.registry.get_by_user("user-1")
        assert inst2 is not None
        assert inst2.state == InstanceState.RUNNING
        assert inst2.agent_instance_id == inst.agent_instance_id
        assert inst2.meta.get("hosted_game_session_id") == gs_id

        # Wait past the old short TTL to prove old timer was cancelled
        await asyncio.sleep(0.08)

        inst3 = await gateway.registry.get_by_user("user-1")
        assert inst3 is not None
        assert inst3.state == InstanceState.RUNNING
        assert inst3.agent_instance_id == inst.agent_instance_id
    finally:
        online.set()
        await task


@pytest.mark.asyncio
async def test_heartbeat_silence_on_hosted_controller_is_recoverable():
    """Heartbeat silence on a hosted controller must go through DRAINING
    with resume TTL, not immediate FAILED.  The _heartbeat_watchdog calls
    session.ws.close() which triggers ConnectionClosed."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    ws1 = _RecoveryWS(_recovery_hello())
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(None, None))
    await gateway._handle_connection(ws1)

    inst = await gateway.registry.get_by_user("user-1")
    assert inst is not None
    assert inst.state == InstanceState.DRAINING
    assert inst.resume_token is not None

    resume_tok = inst.resume_token
    gs_id = inst.meta.get("hosted_game_session_id")

    # Resume with token, keep online for assertions
    ws2 = _RecoveryWS(_recovery_hello(resume_token=resume_tok))
    online = asyncio.Event()
    gateway._run_session = lambda _: online.wait()
    task = asyncio.create_task(gateway._handle_connection(ws2))
    for _ in range(20):
        current = await gateway.registry.get_by_user("user-1")
        if current is not None and current.state == InstanceState.RUNNING:
            break
        await asyncio.sleep(0)

    try:
        inst2 = await gateway.registry.get_by_user("user-1")
        assert inst2.state == InstanceState.RUNNING
        hello_ok2 = json.loads(ws2.sent[-1])
        assert hello_ok2["resumed"] is True
        assert hello_ok2["game_session_id"] == gs_id
    finally:
        online.set()
        await task


@pytest.mark.asyncio
async def test_explicit_close_1001_destroys_immediately_no_resume_window():
    """A normal ws close (1001 — save-quit, /agent_control close, explicit
    game exit) on a hosted controller must destroy the instance immediately
    without a resume window.  The resume token must not recover a destroyed
    instance."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    ws1 = _RecoveryWS(_recovery_hello())
    # Simulate explicit close (1001 — "going away")
    gateway._run_session = AsyncMock(side_effect=ConnectionClosed(
        rcvd=CloseFrame(code=1001, reason="going away"), sent=None,
    ))
    await gateway._handle_connection(ws1)

    # After explicit close, the instance must be DESTROYED immediately
    inst = await gateway.registry.get_by_user("user-1")
    assert inst is None  # destroyed, not DRAINING

    # The hello_ok must still carry a resume_token (issued during handshake),
    # but the instance is already gone so a resume attempt must start fresh.
    hello_ok1 = json.loads(ws1.sent[-1])
    resume_tok = hello_ok1.get("resume_token")
    gs_id = hello_ok1.get("game_session_id")
    assert isinstance(resume_tok, str)

    # Attempt resume after explicit close: instance is already destroyed
    ws2 = _RecoveryWS(_recovery_hello(resume_token=resume_tok))
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(ws2)

    inst2 = await gateway.registry.get_by_user("user-1")
    assert inst2 is not None
    hello_ok2 = json.loads(ws2.sent[-1])
    assert hello_ok2["resumed"] is False
    assert hello_ok2["game_session_id"] != gs_id


@pytest.mark.asyncio
async def test_explicit_session_end_closes_binding_and_runtime_without_resume_window():
    """Returning to title is a GameSession end, not a transport outage."""
    gateway, registry, events, activator = _make_recovery_gateway(resume_ttl_s=5.0)

    ws = _ExplicitEndWS(_recovery_hello())
    await gateway._handle_connection(ws)

    assert await gateway.registry.get_by_user("user-1") is None
    assert [event.event_type.value for event in events.events] == [
        "game_session.available",
        "game_session.closed",
    ]
    assert len(activator.close_calls) == 1
    owner, operation_id = activator.close_calls[0]
    assert owner == "user-1"
    assert operation_id == "control-close:user-1:1"
