from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
import websockets

from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION,
    CommandState,
    DispatchState,
    ExecutionContext,
    GameCommand,
    SideEffect,
)
from modules.mcp.context import EphemeralSecret
from modules.mcp.core.errors import McpError
from modules.mcp.routers import LocalModRouteServer, LocalModRouter, LocalRouteFence
from modules.mcp.routers.local_mod_connection import capability_snapshot_digest


RESULT_VECTORS = json.loads(
    (Path(__file__).resolve().parents[4]
     / "agent/tests/fixtures/canonical-result-vectors.json").read_text()
)["vectors"]


def fence() -> LocalRouteFence:
    digest = capability_snapshot_digest([CAPABILITY_ENTRY])
    return LocalRouteFence(
        "ctx", "principal", "runtime", "game", "nonce", 2,
        "binding", 3, 4, digest,
    )


CAPABILITY_ENTRY = {
    "source_surface": "protocol-2.4.5",
    "capability_id": "say",
    "capability_contract_version": "2.4.5",
}


def hello(**changes):
    entries = [CAPABILITY_ENTRY]
    payload = {
        "type": "local_direct_hello",
        "profile": "local_direct",
        "proof": "one-time-proof",
        "generation": 2,
        "binding_ref": "binding",
        "binding_version": 3,
        "game_session_ref": "game",
        "game_instance_nonce": "nonce",
        "lease_epoch": 4,
        "capability_entries": entries,
        "capability_digest": capability_snapshot_digest(entries),
    }
    payload.update(changes)
    return payload


def command(capability: str = "say") -> GameCommand:
    digest = capability_snapshot_digest([CAPABILITY_ENTRY])
    payload = {"message": "hello"} if capability == "say" else {"max_count": 1}
    return GameCommand(
        GAME_COMMAND_VERSION, "message", 100, "idem", "caller-command",
        "tool-call", capability, "canonical.v1", digest, SideEffect.MUTATING,
        payload, 200, "ctx", "nonce", 4,
    )


@pytest.mark.asyncio
async def test_loopback_handshake_dispatch_preserves_identity_and_correlates_result():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    assert endpoint.startswith("ws://127.0.0.1:")
    try:
        async with websockets.connect(endpoint) as mod:
            await mod.send(json.dumps(hello()))
            ready = json.loads(await mod.recv())
            assert ready["type"] == "local_direct_ready"
            assert ready["reconnect_proof"]
            assert server.connected

            await server.dispatch(command())
            dispatched = json.loads(await mod.recv())
            assert dispatched["type"] == "local_command"
            assert dispatched["command_id"] == "caller-command"
            assert dispatched["payload"]["command_id"] == "caller-command"
            assert dispatched["fence"]["game_instance_nonce"] == "nonce"

            waiting = asyncio.create_task(server.result("caller-command", 200))
            await mod.send(json.dumps({
                "type": "local_command_result",
                "command_id": "caller-command",
                "state": "SUCCEEDED",
                "output": {"ok": True},
            }))
            result = await waiting
            assert result.state is CommandState.SUCCEEDED
            assert result.output == {"ok": True}
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_fence_mismatch_and_second_owner_fail_closed():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof")
    endpoint = await server.start()
    try:
        async with websockets.connect(endpoint) as attacker:
            await attacker.send(json.dumps(hello(generation=99)))
            with pytest.raises(websockets.ConnectionClosed):
                await attacker.recv()

        async with websockets.connect(endpoint) as owner:
            await owner.send(json.dumps(hello()))
            await owner.recv()
            async with websockets.connect(endpoint) as second:
                await second.send(json.dumps(hello()))
                with pytest.raises(websockets.ConnectionClosed):
                    await second.recv()
            assert server.connected
    finally:
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,bad", [
    ("proof", "wrong-proof"), ("profile", "hosted"), ("generation", 99),
    ("binding_ref", "stale"),
    ("binding_version", 99), ("game_session_ref", "stale"),
    ("game_instance_nonce", "stale"), ("lease_epoch", 99),
    ("capability_digest", "sha256:" + "0" * 64),
])
async def test_every_handshake_fencing_dimension_fails_closed(field, bad):
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof")
    endpoint = await server.start()
    try:
        async with websockets.connect(endpoint) as ws:
            await ws.send(json.dumps(hello(**{field: bad})))
            with pytest.raises(websockets.ConnectionClosed):
                await ws.recv()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_consumed_boot_secret_cannot_be_replayed_after_disconnect():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof")
    endpoint = await server.start()
    try:
        first = await websockets.connect(endpoint)
        await first.send(json.dumps(hello()))
        await first.recv()
        await first.close()
        await asyncio.sleep(0)
        async with websockets.connect(endpoint) as replay:
            await replay.send(json.dumps(hello()))
            with pytest.raises(websockets.ConnectionClosed):
                await replay.recv()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_transport_loss_returns_unknown_and_terminal_cache_avoids_blind_replay():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    try:
        mod = await websockets.connect(endpoint)
        await mod.send(json.dumps(hello()))
        await mod.recv()
        waiting = asyncio.create_task(server.result("uncertain", 200))
        assert json.loads(await mod.recv())["type"] == "local_result"
        await mod.close()
        result = await waiting
        assert result.state is CommandState.UNKNOWN
        assert result.error_code == "route_unavailable"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_reconnect_uses_server_issued_proof_and_queries_terminal_cache_without_replay():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    try:
        first = await websockets.connect(endpoint)
        await first.send(json.dumps(hello()))
        reconnect_proof = json.loads(await first.recv())["reconnect_proof"]
        await server.dispatch(command())
        assert json.loads(await first.recv())["command_id"] == "caller-command"
        await first.send(json.dumps({
            "type": "local_command_result", "command_id": "caller-command",
            "state": "SUCCEEDED", "output": {"cached": True},
        }))
        await asyncio.sleep(0)
        await first.close()
        await asyncio.sleep(0)

        reconnect = hello()
        reconnect.pop("proof")
        reconnect["reconnect_proof"] = reconnect_proof
        async with websockets.connect(endpoint) as second:
            await second.send(json.dumps(reconnect))
            assert json.loads(await second.recv())["type"] == "local_direct_ready"
            result = await server.result("caller-command", 200)
            assert result.state is CommandState.SUCCEEDED
            assert result.output == {"cached": True}
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(second.recv(), 0.02)
    finally:
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "cancel"])
@pytest.mark.parametrize("capability", ["say", "water_crops"])
async def test_control_response_requires_exact_type_control_command_state_and_shape(operation, capability):
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    try:
        async with websockets.connect(endpoint) as mod:
            await mod.send(json.dumps(hello()))
            await mod.recv()
            await server.dispatch(command(capability))
            dispatched = json.loads(await mod.recv())
            assert dispatched["wire_format"] == (
                "protocol-2.4.5" if capability == "say" else "v2-json"
            )
            if operation == "status":
                task = asyncio.create_task(server.status("caller-command"))
            else:
                task = asyncio.create_task(server.cancel("caller-command"))
            query = json.loads(await mod.recv())
            base = {
                "type": "local_command_result", "control_id": query["control_id"],
                "command_id": "caller-command", "state": "RUNNING", "output": {},
            }
            invalid = [
                {**base, "type": "wrong_type"},
                {**base, "control_id": "wrong-control"},
                {**base, "command_id": "wrong-command"},
                {**base, "state": "NOT_A_STATE"},
                {**base, "output": []},
            ]
            if operation == "cancel":
                invalid.append({**base, "accepted": 1})
                correct = {**base, "accepted": True}
            else:
                invalid.append({**base, "accepted": False})
                correct = {**base}
            for frame in invalid:
                await mod.send(json.dumps(frame))
                await asyncio.sleep(0)
                assert not task.done()
            await mod.send(json.dumps(correct))
            value = await asyncio.wait_for(task, 1)
            if operation == "status":
                assert value is CommandState.RUNNING
            elif operation == "cancel":
                assert value.accepted is True and value.state is CommandState.RUNNING
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unsolicited_terminal_only_for_dispatched_identity_and_never_regresses():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    try:
        async with websockets.connect(endpoint) as mod:
            await mod.send(json.dumps(hello()))
            await mod.recv()
            await mod.send(json.dumps({"type": "local_command_result", "command_id": "unknown",
                                       "state": "SUCCEEDED", "output": {"bad": True}}))
            await server.dispatch(command())
            await mod.recv()
            await mod.send(json.dumps({"type": "local_command_result", "command_id": "caller-command",
                                       "state": "RUNNING", "output": {}}))
            await mod.send(json.dumps({"type": "local_command_result", "command_id": "caller-command",
                                       "state": "SUCCEEDED", "output": {"first": True}}))
            for _ in range(20):
                if "caller-command" in server._terminal:
                    break
                await asyncio.sleep(0.01)
            await mod.send(json.dumps({"type": "local_command_result", "command_id": "caller-command",
                                       "state": "FAILED", "output": {"regressed": True}}))
            await asyncio.sleep(0)
            assert "unknown" not in server._terminal
            assert server._terminal["caller-command"].state is CommandState.SUCCEEDED
            assert server._terminal["caller-command"].output == {"first": True}
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_invalid_control_frames_leave_pending_until_real_loopback_timeout():
    now = time.time()
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: now)
    endpoint = await server.start()
    try:
        async with websockets.connect(endpoint) as mod:
            await mod.send(json.dumps(hello()))
            await mod.recv()
            await server.dispatch(command())
            await mod.recv()
            waiting = asyncio.create_task(server.status("caller-command"))
            query = json.loads(await mod.recv())
            await mod.send(json.dumps({
                "type": "wrong", "control_id": query["control_id"],
                "command_id": "caller-command", "state": "SUCCEEDED", "output": {},
            }))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(waiting, .03)
    finally:
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["result", "status", "cancel"])
@pytest.mark.parametrize("capability", ["say", "water_crops"])
async def test_real_loopback_disconnect_returns_operation_shaped_unknown(operation, capability):
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    try:
        mod = await websockets.connect(endpoint)
        await mod.send(json.dumps(hello()))
        await mod.recv()
        await server.dispatch(command(capability))
        dispatched = json.loads(await mod.recv())
        assert dispatched["wire_format"] == (
            "protocol-2.4.5" if capability == "say" else "v2-json"
        )
        if operation == "result":
            task = asyncio.create_task(server.result("caller-command", 200))
        elif operation == "status":
            task = asyncio.create_task(server.status("caller-command"))
        else:
            task = asyncio.create_task(server.cancel("caller-command"))
        if operation != "result":
            await mod.recv()
        await mod.close()
        value = await asyncio.wait_for(task, 1)
        if operation == "result":
            assert value.state is CommandState.UNKNOWN and value.output == {}
        elif operation == "status":
            assert value is CommandState.UNKNOWN
        else:
            assert value.command_id == "caller-command"
            assert value.accepted is False and value.state is CommandState.UNKNOWN
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_csharp_equivalent_structured_terminal_frame_is_accepted_and_cached():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    try:
        async with websockets.connect(endpoint) as mod:
            await mod.send(json.dumps(hello()))
            await mod.recv()
            await server.dispatch(command())
            await mod.recv()
            waiting = asyncio.create_task(server.result("caller-command", 200))
            # Exact shape emitted by LocalDirectBridge.QueueResult after its
            # C# JsonDocument conversion: output is an object, never JSON text.
            raw = RESULT_VECTORS["proto_say_completed"]["result"]["output"]
            raw = {**raw, "commandId": "caller-command"}
            await mod.send(json.dumps({
                "type": "local_command_result",
                "command_id": "caller-command", "state": "SUCCEEDED",
                "output": raw,
                "error_code": None, "accepted": None,
            }))
            result = await waiting
            assert result.output == raw
            assert server._terminal["caller-command"].output == raw
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_real_loopback_router_duplicate_collision_and_expired_are_zero_extra_execution():
    server = LocalModRouteServer(fence(), boot_secret="one-time-proof", clock=lambda: 100)
    endpoint = await server.start()
    router = LocalModRouter(
        fence(), server, boot_secret=EphemeralSecret("router-proof"),
        presented_secret="router-proof", clock=lambda: 100,
    )
    context = ExecutionContext(
        profile="local_direct", principal_id="principal", runtime_ref="runtime",
        generation=2, binding_ref="binding", binding_version=3,
        game_session_ref="game", game_instance_nonce="nonce", lease_epoch=4,
        scopes=frozenset({"game:read", "game:write"}),
        allowed_tools=frozenset({"say"}), capability_digest=fence().capability_digest,
        issued_at=100,
    )
    try:
        async with websockets.connect(endpoint) as mod:
            await mod.send(json.dumps(hello()))
            await mod.recv()
            original = command()
            assert (await router.dispatch(context, original)).state is DispatchState.ACCEPTED
            assert json.loads(await mod.recv())["command_id"] == original.command_id

            assert (await router.dispatch(context, original)).state is DispatchState.DUPLICATE
            collision = replace(original, validated_payload={"message": "changed"})
            assert (await router.dispatch(context, collision)).state is DispatchState.REJECTED
            expired = replace(
                original, message_id="expired-message", command_id="expired-command",
                idempotency_key="expired-command", tool_call_id="expired-tool", deadline_at=100,
            )
            with pytest.raises(McpError) as caught:
                await router.dispatch(context, expired)
            assert caught.value.code == "conflict"
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(mod.recv(), 0.03)
    finally:
        await server.stop()
