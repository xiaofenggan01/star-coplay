from __future__ import annotations

import asyncio
import io
import json

import pytest

from modules.mcp.context import LocalBootAuthority, LocalBootMaterial
from modules.mcp.contracts.models import CancelResult, CommandState, ContextHandle, GameCommandResult
from modules.mcp.core.service import McpCore
from modules.mcp.fakes.router import FakeGameSessionRouter, RouteFence
from modules.mcp.transports import StdioMcpTransport


def transport(catalog, *, inject_credential=False):
    boot = LocalBootMaterial.create(
        capability_digest="digest-a",
        tool_capabilities={"observe": "observe", "say": "say"},
        scopes=frozenset({"game:read", "game:write"}), lifetime_seconds=60,
    )
    authority = LocalBootAuthority(boot, negotiated_capability_digest=lambda: "digest-a")
    router = FakeGameSessionRouter(RouteFence(
        boot.game_session_ref, boot.game_instance_nonce, boot.generation,
        boot.binding_version, boot.lease_epoch, boot.capability_digest,
    ))
    writer = io.StringIO()
    metadata = {"credential": boot.secret.reveal()} if inject_credential else None
    return boot, writer, StdioMcpTransport(
        McpCore(authority, router, catalog), authority, writer=writer,
        connection_metadata=metadata,
    )


def request(request_id, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})


@pytest.mark.asyncio
async def test_initialize_list_and_call_use_newline_json_rpc(catalog):
    boot, writer, server = transport(catalog)
    initialized = await server.handle_line(request(1, "initialize", {"credential": boot.secret.reveal()}))
    assert initialized["result"]["protocolVersion"] == StdioMcpTransport.PROTOCOL_VERSION
    listed = await server.handle_line(request(2, "tools/list"))
    assert [item["name"] for item in listed["result"]["tools"]] == ["observe", "say"]
    assert listed["result"]["_meta"]["catalogDigest"] == server._core.catalog_digest
    called = await server.handle_line(request(3, "tools/call", {
        "name": "say", "arguments": {"message": "hello"},
        "_meta": {"toolCallId": "tc-1", "commandId": "cmd-1"},
    }))
    assert called["result"]["structuredContent"]["state"] == "SUCCEEDED"
    assert writer.getvalue() == ""  # handle_line is pure; serve owns stdout writes.


@pytest.mark.asyncio
async def test_transport_rejects_parse_errors_pre_initialize_and_protected_metadata(catalog):
    boot, _, server = transport(catalog)
    assert (await server.handle_line("{"))["error"]["code"] == -32700
    assert (await server.handle_line(request(1, "tools/list")))["error"]["data"]["code"] == "context_expired"
    response = await server.handle_line(request(2, "initialize", {
        "credential": boot.secret.reveal(), "metadata": {"game_session_ref": "attacker"},
    }))
    assert response["error"]["data"]["code"] == "invalid_arguments"
    assert boot.secret.reveal() not in json.dumps(response)


@pytest.mark.asyncio
async def test_unknown_method_uses_standard_json_rpc_error(catalog):
    boot, _, server = transport(catalog)
    await server.handle_line(request(1, "initialize", {"credential": boot.secret.reveal()}))
    response = await server.handle_line(request(2, "unknown/method"))
    assert response["error"] == {"code": -32601, "message": "Method not found"}


@pytest.mark.asyncio
async def test_serve_writes_only_one_json_frame_per_request_and_closes_context(catalog):
    boot, writer, server = transport(catalog)

    async def lines():
        secret = boot.secret.reveal()
        yield request(1, "initialize", {"credential": secret}) + "\n"
        yield json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        yield request(2, "tools/list") + "\n"

    await server.serve(lines())
    frames = writer.getvalue().splitlines()
    assert len(frames) == 2
    assert [json.loads(line)["id"] for line in frames] == [1, 2]
    assert all(json.loads(line)["jsonrpc"] == "2.0" for line in frames)
    assert boot.secret.reveal() not in writer.getvalue()


@pytest.mark.asyncio
async def test_invalid_tools_call_does_not_leak_argument_content(catalog):
    boot, _, server = transport(catalog)
    await server.handle_line(request(1, "initialize", {"credential": boot.secret.reveal()}))
    secret = "tool-argument-secret"
    response = await server.handle_line(request(2, "tools/call", {
        "name": "say", "arguments": {"message": secret, "lease_epoch": 99},
        "_meta": {"toolCallId": "tc"},
    }))
    assert response["error"]["data"]["code"] == "invalid_arguments"
    assert secret not in json.dumps(response)


@pytest.mark.asyncio
async def test_standard_initialize_and_tools_call_use_out_of_band_boot_material(catalog):
    _, _, server = transport(catalog, inject_credential=True)
    response = await server.handle_line(request(41, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "standard-client", "version": "1"},
    }))
    assert response["result"]["protocolVersion"] == "2025-03-26"
    called = await server.handle_line(request(42, "tools/call", {
        "name": "say", "arguments": {"message": "standard-shape"},
    }))
    assert called["result"]["structuredContent"]["command_id"]
    assert called["result"]["structuredContent"]["state"] == "SUCCEEDED"


class _ImmediateReader:
    def __init__(self, lines):
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


class _QueueReader:
    def __init__(self):
        self._queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self._queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def put(self, value):
        await self._queue.put(value)


class _NoopAuthority:
    async def close(self, _handle):
        return None


class _CancellationCore:
    def __init__(self, *, accepted=False):
        self.accepted = accepted
        self.call_started = asyncio.Event()
        self.result_ready = asyncio.Event()
        self.cancel_started = asyncio.Event()
        self.cancel_release = asyncio.Event()
        self.cancel_calls = 0
        self.call_count = 0

    async def call_tool(self, *_args, **_kwargs):
        self.call_count += 1
        self.call_started.set()
        await self.result_ready.wait()
        return GameCommandResult("command", CommandState.SUCCEEDED, {"ok": True})

    async def cancel_command(self, *_args, **_kwargs):
        self.cancel_calls += 1
        self.cancel_started.set()
        await self.cancel_release.wait()
        return CancelResult("command", self.accepted, CommandState.RUNNING)


def _call_line(request_id="request"):
    return request(request_id, "tools/call", {
        "name": "say", "arguments": {"message": "hello"},
        "_meta": {"toolCallId": "tool", "commandId": "command"},
    }) + "\n"


def _cancel_line(request_id="request"):
    return json.dumps({
        "jsonrpc": "2.0", "method": "notifications/cancelled",
        "params": {"requestId": request_id, "reason": "client stopped waiting"},
    }) + "\n"


@pytest.mark.asyncio
async def test_default_stdio_local_direct_has_no_hosted_tool_call_gate():
    core = _CancellationCore()
    server = StdioMcpTransport(core, _NoopAuthority(), writer=io.StringIO())
    server._handle = ContextHandle("local-direct")
    first = asyncio.create_task(server.handle_line(_call_line("first")))
    second = asyncio.create_task(server.handle_line(_call_line("second")))
    for _ in range(20):
        if core.call_count == 2:
            break
        await asyncio.sleep(0)
    assert core.call_count == 2
    core.result_ready.set()
    assert "result" in await first
    assert "result" in await second
    await server.close()


@pytest.mark.asyncio
async def test_buffered_call_then_cancel_registers_before_next_line_and_leaks_no_state():
    core = _CancellationCore()
    writer = io.StringIO()
    server = StdioMcpTransport(core, _NoopAuthority(), writer=writer)
    server._handle = ContextHandle("handle")

    await server.serve(_ImmediateReader([
        _call_line(), _cancel_line(), _cancel_line("genuinely-unknown"),
    ]))

    assert writer.getvalue() == ""
    assert core.cancel_calls == 0  # cancelled before any downstream dispatch
    assert not server._requests and not server._control_tasks


@pytest.mark.asyncio
async def test_cancel_notification_closes_response_gate_even_when_result_wins_and_cancel_rejects():
    core = _CancellationCore(accepted=False)
    writer = io.StringIO()
    server = StdioMcpTransport(core, _NoopAuthority(), writer=writer)
    server._handle = ContextHandle("handle")
    reader = _QueueReader()
    serving = asyncio.create_task(server.serve(reader))

    await reader.put(_call_line())
    await core.call_started.wait()
    await reader.put(_cancel_line())
    await core.cancel_started.wait()
    core.result_ready.set()  # original result wins before downstream cancel acknowledgement
    await asyncio.sleep(0)
    core.cancel_release.set()  # rejected cancel still cannot reopen the client response gate
    await asyncio.sleep(0)
    await reader.put(None)
    await serving

    assert core.cancel_calls == 1
    assert writer.getvalue() == ""
    assert not server._requests and not server._control_tasks
