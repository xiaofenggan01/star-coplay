from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.test_utils import make_mocked_request

from modules.mcp.context.hosted_claims import HostedClaimsAuthority
from modules.mcp.contracts.models import (
    CancelResult, CommandState, DispatchReceipt, DispatchState, GameCommandResult,
    RouteHealth, SideEffect,
)
from modules.mcp.transports.hosted_server import HostedMcpJsonRpcSession
from modules.mcp.core.audit import DurableJsonlAuditSink
from modules.mcp.core.catalog import ToolCatalog, ToolDefinition
from modules.mcp.core.service import McpCore
from modules.mcp.core.errors import McpError
from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayRouter, InMemoryGameSessionFacts
from modules.runtime_manager.authority import AuthorityMaterial
from modules.runtime_manager.correlation import ActiveTurnCorrelationRegistry
from common.observability import new_log_id
from modules.runtime_manager.domain import BindingState, RuntimeBinding
from modules.http_api.server import HttpApiServer
import modules.http_api.server as http_server_module


def _binding(runtime_id: str, binding_id: str) -> RuntimeBinding:
    return RuntimeBinding(
        binding_id, runtime_id, 1, f"user-{runtime_id}", "agentee-1", 1, 1,
        BindingState.ACTIVE, f"game-{runtime_id}", f"nonce-{runtime_id}",
        f"digest-{runtime_id}",
    )


def _material(binding: RuntimeBinding) -> AuthorityMaterial:
    return AuthorityMaterial(
        credential_ref="test", iss="starcoplay-runtime-manager", aud="starcoplay-mcp",
        sub=binding.runtime_id, jti=f"jti-{binding.runtime_id}",
        runtime_id=binding.runtime_id, generation=binding.generation,
        binding_id=binding.binding_id, binding_version=binding.binding_version,
        lease_epoch=binding.lease_epoch, game_session_id=binding.game_session_id or "",
        game_instance_nonce=binding.game_instance_nonce or "",
        capability_digest=binding.capability_digest or "", user_id=binding.user_id,
        agentee_id=binding.agentee_id, scopes=("game:read",),
        allowed_tools=("observe",),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


class _Verifier:
    def __init__(self, materials):
        self.materials = materials

    async def verify(self, credential):
        return self.materials[credential]

    async def is_revoked(self, material):
        return False


class _Bindings:
    def __init__(self, bindings):
        self.bindings = {binding.binding_id: binding for binding in bindings}

    async def get(self, binding_id):
        return self.bindings.get(binding_id)


def test_server_log_id_format_and_registry_compare_and_clear():
    registry = ActiveTurnCorrelationRegistry()
    log_id = new_log_id()
    assert len(log_id) == 36 and log_id.startswith("log_")
    assert all(ch in "0123456789abcdef" for ch in log_id[4:])

    registry.register("runtime-1", 3, log_id, "operation-1")
    assert registry.lookup("runtime-1", 3).log_id == log_id
    assert not registry.clear_if_matches("runtime-1", 3, "stale-operation")
    assert registry.lookup("runtime-1", 3).operation_id == "operation-1"
    assert registry.clear_if_matches("runtime-1", 3, "operation-1")
    assert registry.lookup("runtime-1", 3) is None


@pytest.mark.asyncio
async def test_http_ingress_ignores_client_log_id_and_isolates_concurrent_requests():
    server = object.__new__(HttpApiServer)
    app = web.Application(middlewares=[server._request_logging_middleware])

    async def echo(request):
        await asyncio.sleep(0)
        return web.json_response({"log_id": request["log_id"]})

    app.router.add_get("/echo", echo)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        responses = await asyncio.gather(
            client.get("/echo", headers={"x-log-id": "log_" + "f" * 32}),
            client.get("/echo", headers={"x-correlation-id": "client-parent"}),
        )
        payloads = [await response.json() for response in responses]
        log_ids = [response.headers["x-log-id"] for response in responses]
        assert len(set(log_ids)) == 2
        assert [payload["log_id"] for payload in payloads] == log_ids
        assert "log_" + "f" * 32 not in log_ids
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_http_context_resets_once_when_success_postprocessing_raises(monkeypatch):
    server = object.__new__(HttpApiServer)
    request = make_mocked_request("GET", "/postprocess-failure")
    reset_tokens = []
    real_reset = http_server_module.reset_log_context

    def counted_reset(token):
        reset_tokens.append(token)
        real_reset(token)

    def fail_completed_log(*args, **kwargs):
        raise RuntimeError("completion log failed")

    monkeypatch.setattr(http_server_module, "reset_log_context", counted_reset)
    monkeypatch.setattr(http_server_module.log, "log", fail_completed_log)

    async def handler(_request):
        return web.Response(text="ok")

    with pytest.raises(RuntimeError, match="completion log failed"):
        await server._request_logging_middleware(request, handler)
    assert len(reset_tokens) == 1

    # A subsequent record in the same task must not inherit the failed
    # request's correlation context.
    from common.observability import JsonLogFormatter
    rendered = JsonLogFormatter(component="test").format(logging.LogRecord(
        "test", logging.INFO, __file__, 1, "after", (), None,
    ))
    assert json.loads(rendered).get("log_id") is None


@pytest.mark.asyncio
async def test_hosted_authority_inherits_active_turn_and_direct_calls_are_fresh():
    binding = _binding("runtime-1", "binding-1")
    registry = ActiveTurnCorrelationRegistry()
    registry.register("runtime-1", 1, "log_" + "a" * 32, "operation-1")
    authority = HostedClaimsAuthority(
        _Verifier({"credential": _material(binding)}), _Bindings([binding]),
        turn_correlation_registry=registry,
    )
    handle = await authority.establish({"credential": "credential"})

    trace, token = await authority.begin_tool_call(handle)
    try:
        context = await authority.authorize(handle, "observe", "observe")
        assert trace == {"log_id": "log_" + "a" * 32, "operation_id": "operation-1"}
        assert context.trace_context == trace
    finally:
        authority.end_tool_call(token)

    registry.clear_if_matches("runtime-1", 1, "operation-1")
    first, first_token = await authority.begin_tool_call(handle)
    authority.end_tool_call(first_token)
    resumed_token = authority.resume_tool_call(first)
    try:
        resumed = await authority.authorize(handle, "observe", "observe")
        assert resumed.trace_context == first
    finally:
        authority.end_tool_call(resumed_token)
    second, second_token = await authority.begin_tool_call(handle)
    authority.end_tool_call(second_token)
    assert first["log_id"] != second["log_id"]
    assert first["log_id"].startswith("log_")


@pytest.mark.asyncio
async def test_interrupted_hosted_turn_cannot_start_another_tool_call():
    binding = _binding("runtime-1", "binding-1")
    registry = ActiveTurnCorrelationRegistry()
    registry.register("runtime-1", 1, "log_" + "a" * 32, "operation-1")
    registry.mark_interrupted("runtime-1", 1, "player_superseded")
    authority = HostedClaimsAuthority(
        _Verifier({"credential": _material(binding)}), _Bindings([binding]),
        turn_correlation_registry=registry,
    )
    handle = await authority.establish({"credential": "credential"})

    with pytest.raises(McpError) as error:
        await authority.begin_tool_call(handle)

    assert error.value.code == "player_interrupted"


@pytest.mark.asyncio
async def test_hosted_tool_context_is_isolated_across_concurrent_authoritative_runtimes():
    bindings = [_binding("runtime-a", "binding-a"), _binding("runtime-b", "binding-b")]
    registry = ActiveTurnCorrelationRegistry()
    registry.register("runtime-a", 1, "log_" + "a" * 32, "operation-a")
    registry.register("runtime-b", 1, "log_" + "b" * 32, "operation-b")
    authority = HostedClaimsAuthority(
        _Verifier({"a": _material(bindings[0]), "b": _material(bindings[1])}),
        _Bindings(bindings), turn_correlation_registry=registry,
    )
    handles = [
        await authority.establish({"credential": "a"}),
        await authority.establish({"credential": "b"}),
    ]

    async def read(handle, expected):
        _trace, token = await authority.begin_tool_call(handle)
        try:
            await asyncio.sleep(0)
            context = await authority.authorize(handle, "observe", "observe")
            assert context.trace_context["log_id"] == expected
            await asyncio.sleep(0)
            return context.trace_context["operation_id"]
        finally:
            authority.end_tool_call(token)

    assert await asyncio.gather(
        read(handles[0], "log_" + "a" * 32),
        read(handles[1], "log_" + "b" * 32),
    ) == ["operation-a", "operation-b"]


@pytest.mark.asyncio
async def test_client_mcp_meta_cannot_override_server_correlation_or_identity():
    binding = _binding("runtime-1", "binding-1")
    registry = ActiveTurnCorrelationRegistry()
    registry.register("runtime-1", 1, "log_" + "c" * 32, "operation-server")
    authority = HostedClaimsAuthority(
        _Verifier({"credential": _material(binding)}), _Bindings([binding]),
        turn_correlation_registry=registry,
    )

    class _Core:
        captured = None

        async def call_tool(self, handle, name, arguments, **kwargs):
            self.captured = await authority.authorize(handle, name, name)
            return GameCommandResult(kwargs["command_id"], CommandState.SUCCEEDED, {})

    core = _Core()
    session = HostedMcpJsonRpcSession(core, authority, "credential")
    assert "result" in await session.request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    })
    response = await session.request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "observe", "arguments": {},
            "_meta": {
                "toolCallId": "tool-1", "commandId": "command-1",
                "log_id": "log_" + "f" * 32,
                "runtime_id": "attacker-runtime", "generation": 999,
            },
        },
    })
    await session.close()
    assert "result" in response
    assert core.captured.runtime_ref == "runtime-1"
    assert core.captured.generation == 1
    assert core.captured.trace_context == {
        "log_id": "log_" + "c" * 32, "operation_id": "operation-server",
    }


@pytest.mark.asyncio
async def test_composed_hosted_core_gateway_and_durable_audit_share_server_log_id(tmp_path, caplog):
    catalog = ToolCatalog([ToolDefinition(
        "observe", "observe", "canonical.v1", "game:read", SideEffect.READ_ONLY,
        {"type": "object", "properties": {}, "additionalProperties": False},
    )])
    binding = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-runtime-1", "agentee-1", 1, 1,
        BindingState.ACTIVE, "game-runtime-1", "nonce-runtime-1", catalog.digest,
    )
    material = _material(binding)
    registry = ActiveTurnCorrelationRegistry()
    expected_log_id = "log_" + "d" * 32
    registry.register("runtime-1", 1, expected_log_id, "operation-1")
    authority = HostedClaimsAuthority(
        _Verifier({"credential": material}), _Bindings([binding]),
        turn_correlation_registry=registry,
    )

    class _SessionRoute:
        def __init__(self):
            self.commands = {}

        async def dispatch(self, command):
            self.commands[command.command_id] = command
            return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)

        async def await_result(self, command_id, deadline):
            return GameCommandResult(command_id, CommandState.SUCCEEDED, {"ok": True})

        async def cancel(self, command_id):
            return CancelResult(command_id, True, CommandState.CANCELLED)

        async def get_status(self, command_id):
            return CommandState.SUCCEEDED

        async def health(self):
            return RouteHealth(True)

    route = _SessionRoute()

    class _AuthenticatedRoutes:
        async def resolve_authenticated(self, game_session_ref):
            return route if game_session_ref == binding.game_session_id else None

    class _Validator:
        async def validate(self, context, snapshot):
            assert context.binding_ref == binding.binding_id

    facts = InMemoryGameSessionFacts()
    facts.snapshots[binding.game_session_id] = GameSessionSnapshot(
        binding.game_session_id, binding.game_instance_nonce, binding.user_id,
        "connection-1", 1, binding.capability_digest, True,
    )
    router = GatewayRouter(_AuthenticatedRoutes(), facts, _Validator())
    audit_path = (tmp_path / "audit" / "hosted.jsonl").resolve()
    audit = DurableJsonlAuditSink(audit_path)
    core = McpCore(authority, router, catalog, audit=audit)
    session = HostedMcpJsonRpcSession(core, authority, "credential")

    with caplog.at_level("INFO", logger="platform_ws.gateway.hosted_router"):
        assert "result" in await session.request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        response = await session.request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "observe", "arguments": {},
                "_meta": {
                    "toolCallId": "tool-real", "commandId": "command-real",
                    "log_id": "log_" + "f" * 32,
                    "runtime_id": "spoofed", "generation": 999,
                },
            },
        })
    await session.close()
    assert response["result"]["isError"] is False

    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert {record["event"] for record in records} == {
        "command.dispatched", "command.result",
    }
    assert all(record["log_id"] == expected_log_id for record in records)
    assert all(record["tool_call_id"] == "tool-real" for record in records)
    assert all(record["command_id"] == "command-real" for record in records)

    gateway_records = [
        record for record in caplog.records
        if getattr(record, "event", "") in {
            "gateway.command.dispatch", "gateway.command.result",
        }
    ]
    assert len(gateway_records) == 2
    assert all(record.log_id == expected_log_id for record in gateway_records)
    assert gateway_records[0].tool_call_id == "tool-real"
    assert all(record.command_id == "command-real" for record in gateway_records)
