from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from common.auth.token_authenticator import AuthResult
from modules.gateway.hosted_session import (
    HostedSessionCoordinator, HostedSessionRegistry, compute_capability_digest,
)
from modules.gateway.runtime_contracts import InMemoryGameSessionFacts
from modules.gateway.server import Gateway, GatewayConfig


def connection_is_open(connection) -> bool:
    closed = getattr(connection, "closed", None)
    if closed is not None:
        return not closed
    return getattr(getattr(connection, "state", None), "name", "") == "OPEN"


class Authenticator:
    async def authenticate(self, token): return AuthResult(True, token)
    async def close(self): return None


class Activator:
    def __init__(self, events, fail=False, gate=None):
        self.events, self.fail, self.gate = events, fail, gate
    async def prepare(self, session):
        self.events.append("prepare")
    async def activate(self, session):
        self.events.append("activate")
        if self.fail: raise RuntimeError("activation_failed")
        if self.gate is not None:
            await session.outbound.put({"type": "activation_probe"})
            await self.gate.wait()


class Hook(HostedSessionCoordinator):
    def __init__(self, registry, publisher, events):
        super().__init__(registry, publisher); self.events = events
    async def on_authenticated(self, session, *, resumed):
        self.events.append("projection")
        await super().on_authenticated(session, resumed=resumed)


def hosted_hello(token, *, nonce="nonce-1", player_id="1", revision=1):
    entries = [{"source_surface": "protocol-2.4.5", "capability_id": "say",
                "capability_contract_version": "2.4.5"}]
    digest = compute_capability_digest(tuple(
        (x["source_surface"], x["capability_id"], x["capability_contract_version"])
        for x in entries
    ))
    return {"type": "hello", "platform_token": token,
            "route_contract_version": "starcoplay.hosted-route.v1",
            "game_instance_nonce": nonce, "capability_digest": digest,
            "capability_entries": entries,
            "player_id": player_id,
            "agent_control_enabled": True,
            "agent_control_target_player_id": player_id,
            "agent_control_revision": revision}


def hosted_resume_hello(token, resume_token):
    hello = hosted_hello(token)
    hello["resume_token"] = resume_token
    return hello


async def start(allowed, *, all_authenticated=False, fail=False, gate=None, expose_gateway=False):
    events, worker_calls = [], []
    async def legacy_worker(session, message):
        worker_calls.append(message)
    registry, facts = HostedSessionRegistry(), InMemoryGameSessionFacts()
    hook = Hook(registry, facts, events)
    gateway = Gateway(
        GatewayConfig(hosted_route_enabled=True, resume_token_ttl_s=.01),
        legacy_worker,
        authenticator=Authenticator(), game_session_event_hook=hook,
        hosted_session_registry=registry, hosted_user_ids=frozenset(allowed),
        hosted_all_authenticated=all_authenticated,
        hosted_runtime_activator=Activator(events, fail=fail, gate=gate),
    )
    server = await websockets.serve(gateway._handle_connection, "127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    result = (server, uri, events, worker_calls)
    return (*result, gateway) if expose_gateway else result


@pytest.mark.asyncio
async def test_allowlisted_route_activates_and_never_calls_legacy_worker():
    server, uri, events, worker_calls = await start({"cc-user"})
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hosted_hello("cc-user")))
            ack = json.loads(await ws.recv())
            assert ack["route_contract_version"] == "starcoplay.hosted-route.v1"
            await ws.send(json.dumps({"type": "game_event", "name": "tick"}))
            await asyncio.sleep(.02)
        assert events[:3] == ["projection", "prepare", "activate"]
        assert worker_calls == []
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_all_authenticated_route_activates_multiple_tenants():
    server, uri, events, worker_calls = await start(set(), all_authenticated=True)
    try:
        for tenant in ("tenant-a", "tenant-b"):
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps(hosted_hello(tenant, nonce=f"nonce-{tenant}")))
                ack = json.loads(await ws.recv())
                assert ack["route_contract_version"] == "starcoplay.hosted-route.v1"
        assert events == ["projection", "prepare", "activate"] * 2
        assert worker_calls == []
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_non_allowlisted_route_accepts_legacy_hello_and_calls_worker():
    server, uri, events, worker_calls = await start({"someone-else"})
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "platform_token": "legacy-user"}))
            ack = json.loads(await ws.recv())
            assert "route_contract_version" not in ack
            await ws.send(json.dumps({"type": "game_event", "name": "tick"}))
            await asyncio.sleep(.02)
        assert events == []
        assert worker_calls == [{"type": "game_event", "name": "tick"}]
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_allowlisted_activation_failure_is_fail_closed():
    server, uri, events, worker_calls, gateway = await start(
        {"cc-user"}, fail=True, expose_gateway=True
    )
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hosted_hello("cc-user")))
            ack = json.loads(await ws.recv())
            assert ack["route_contract_version"] == "starcoplay.hosted-route.v1"
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
        assert events == ["projection", "prepare", "activate"]
        assert worker_calls == []
        for _ in range(20):
            snapshot = next(iter(gateway._game_session_event_hook._snapshots.values()))
            if not snapshot.available:
                break
            await asyncio.sleep(.01)
        assert snapshot.available is False
        assert snapshot.resumable is True
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_higher_revision_explicitly_switches_controller_without_closing_old_socket():
    server, uri, events, worker_calls, gateway = await start(
        {"cc-user"}, expose_gateway=True
    )
    first = second = None
    try:
        first = await websockets.connect(uri)
        await first.send(json.dumps(hosted_hello("cc-user")))
        first_ack = json.loads(await first.recv())

        second = await websockets.connect(uri)
        await second.send(json.dumps(hosted_hello(
            "cc-user", nonce="nonce-2", player_id="2", revision=2
        )))
        second_ack = json.loads(await second.recv())

        assert second_ack["connection_role"] == "controller"
        assert second_ack["accepted_agent_control_revision"] == 2
        assert second_ack["game_session_id"] != first_ack["game_session_id"]
        role_changed = json.loads(await asyncio.wait_for(first.recv(), timeout=.2))
        assert role_changed == {
            "type": "connection_role_changed",
            "connection_role": "observer",
            "accepted_agent_control_revision": 2,
        }
        assert connection_is_open(first)

        snapshot = gateway._game_session_event_hook.get_snapshot(first_ack["game_session_id"])
        assert snapshot.available is False
        assert worker_calls == []
    finally:
        if second is not None:
            await second.close()
        if first is not None:
            await first.close()
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_fresh_socket_with_different_writer_facts_fails_closed():
    server, uri, events, worker_calls, gateway = await start(
        {"cc-user"}, expose_gateway=True
    )
    first = second = None
    try:
        first = await websockets.connect(uri)
        await first.send(json.dumps(hosted_hello("cc-user")))
        first_ack = json.loads(await first.recv())

        second = await websockets.connect(uri)
        await second.send(json.dumps(hosted_hello(
            "cc-user", nonce="nonce-2", player_id="2"
        )))
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await second.recv()

        snapshot = gateway._game_session_event_hook.get_snapshot(first_ack["game_session_id"])
        assert snapshot.available is True
        assert await gateway._hosted_session_registry.resolve_authenticated(
            first_ack["game_session_id"]
        ) is not None
        assert worker_calls == []
    finally:
        if second is not None:
            await second.close()
        if first is not None:
            await first.close()
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_fresh_socket_after_grace_does_not_inherit_detached_identity():
    server, uri, events, worker_calls, gateway = await start(
        {"cc-user"}, expose_gateway=True
    )
    try:
        first = await websockets.connect(uri)
        await first.send(json.dumps(hosted_hello("cc-user")))
        first_ack = json.loads(await first.recv())
        await first.close()
        for _ in range(30):
            if not gateway._sessions_by_instance:
                break
            await asyncio.sleep(.01)
        assert gateway._sessions_by_instance == {}
        detached = gateway._game_session_event_hook.get_snapshot(
            first_ack["game_session_id"]
        )
        assert detached.available is False

        async with websockets.connect(uri) as second:
            await second.send(json.dumps(hosted_hello("cc-user")))
            second_ack = json.loads(await second.recv())
            assert second_ack["resumed"] is False
            assert second_ack["game_session_id"] != first_ack["game_session_id"]
            assert second_ack["resume_identity"] != first_ack["resume_identity"]
            recovered = gateway._game_session_event_hook.get_snapshot(
                second_ack["game_session_id"]
            )
            assert recovered.available is True
        assert worker_calls == []
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_destroyed_route_with_different_nonce_is_not_recovered():
    server, uri, events, worker_calls, gateway = await start(
        {"cc-user"}, expose_gateway=True
    )
    try:
        first = await websockets.connect(uri)
        await first.send(json.dumps(hosted_hello("cc-user")))
        first_ack = json.loads(await first.recv())
        await first.close()
        for _ in range(30):
            if not gateway._sessions_by_instance:
                break
            await asyncio.sleep(.01)
        assert gateway._sessions_by_instance == {}

        async with websockets.connect(uri) as second:
            await second.send(json.dumps(hosted_hello(
                "cc-user", nonce="nonce-2", player_id="2"
            )))
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await second.recv()
        snapshot = gateway._game_session_event_hook.get_snapshot(
            first_ack["game_session_id"]
        )
        assert snapshot.available is False
        assert snapshot.game_instance_nonce == "nonce-1"
        assert worker_calls == []
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_allowlisted_unknown_resume_token_starts_fresh_after_gateway_restart():
    server, uri, events, worker_calls = await start({"cc-user"})
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hosted_resume_hello("cc-user", "stale-process-token")))
            ack = json.loads(await ws.recv())
            assert ack["resumed"] is False
            assert ack["game_session_id"].startswith("gs_")
        assert events[:3] == ["projection", "prepare", "activate"]
        assert worker_calls == []
    finally:
        server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_slow_activation_sends_hello_and_runs_outbound_pump_first():
    gate = asyncio.Event()
    server, uri, events, worker_calls = await start({"cc-user"}, gate=gate)
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(hosted_hello("cc-user")))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=.1))
            assert ack["route_contract_version"] == "starcoplay.hosted-route.v1"
            probe = json.loads(await asyncio.wait_for(ws.recv(), timeout=.1))
            assert probe == {"type": "activation_probe"}
            gate.set()
        assert worker_calls == []
    finally:
        gate.set()
        server.close(); await server.wait_closed()
