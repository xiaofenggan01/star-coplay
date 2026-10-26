from __future__ import annotations

import inspect
import ast
import hashlib
import asyncio
import io
import json
import os
import stat
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.mcp.context.hosted_claims import HostedClaimsAuthority
from modules.mcp.context.hosted_credential import (
    HostedCredentialSigner, HostedCredentialVerifier, KeyStore, RevocationStore,
    generate_signing_key,
)
from modules.mcp.contracts.models import SideEffect
from modules.mcp.core.catalog import ToolCatalog, ToolDefinition
from modules.mcp.core.service import McpCore
from modules.mcp.fakes import FakeGameSessionRouter
from modules.mcp.fakes.router import RouteFence
from modules.mcp.transports.hosted_server import (
    HostedMcpJsonRpcSession,
    HostedMcpWebSocketEndpoint,
)
from modules.mcp.transports.remote import (
    FileCredentialSource, RemoteMcpTransport, RemoteTransportError,
    WebSocketRemoteSession, _credential_file_is_private,
)
from modules.mcp.transports.stdio_proxy import StdioRemoteMcpProxy
from modules.runtime_manager.authority import AuthorityMaterial
from modules.runtime_manager.domain import BindingState, RuntimeBinding


class FakeRemoteSession:
    def __init__(self, credential: str) -> None:
        self.credential_fingerprint = hashlib.sha256(credential.encode()).hexdigest()[:8]
        self.frames = []
        self.closed = False
        self.close_error = False
        self.close_delay = 0.0

    async def request(self, frame):
        self.frames.append(dict(frame))
        if "id" not in frame:
            return None
        if frame.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": frame["id"], "result": {
                "protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": True}},
            }}
        return {"jsonrpc": "2.0", "id": frame["id"], "result": {"tools": []}}

    async def close(self):
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed = True
        if self.close_error:
            raise OSError("simulated close failure")


class FakeConnector:
    def __init__(self) -> None:
        self.credentials = []
        self.sessions = []

    async def connect(self, credential):
        self.credentials.append(credential)
        session = FakeRemoteSession(credential)
        self.sessions.append(session)
        return session


def write_credential(path, value, mode=0o600):
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


@pytest.mark.asyncio
async def test_credential_file_must_be_0600(tmp_path):
    path = tmp_path / "credential"
    write_credential(path, "secret", 0o644)
    source = FileCredentialSource(path)
    if os.name == "nt":
        assert await source.read() == "secret"
    else:
        with pytest.raises(PermissionError, match="0600"):
            await source.read()
        with pytest.raises(PermissionError, match="0600"):
            await source.fingerprint()


def test_credential_privacy_check_preserves_posix_mode_and_windows_file_rules():
    public = SimpleNamespace(st_mode=stat.S_IFREG | 0o644)
    private = SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o600)

    assert _credential_file_is_private(public, platform_name="posix") is False
    assert _credential_file_is_private(private, platform_name="posix") is True
    assert _credential_file_is_private(public, platform_name="nt") is True
    assert _credential_file_is_private(directory, platform_name="nt") is False


@pytest.mark.asyncio
async def test_ready_unbound_proxy_stays_alive_then_activates_surface(tmp_path):
    path = tmp_path / "not-issued-yet"
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    writer = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=writer)
    await transport.start()
    initialized = await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    }))
    assert initialized["result"]["serverInfo"]["name"] == "starcoplay-hosted-proxy"
    listed = await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }))
    assert listed["result"]["tools"] == []
    assert not connector.sessions
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    write_credential(path, "new-active-binding-token")
    await transport.refresh_if_changed()
    assert len(connector.sessions) == 1
    assert [frame["method"] for frame in connector.sessions[0].frames] == [
        "initialize", "notifications/initialized",
    ]
    assert json.loads(writer.getvalue())["method"] == "notifications/tools/list_changed"
    await transport.stop()


@pytest.mark.asyncio
async def test_ready_unbound_notifies_client_with_empty_capabilities_when_binding_activates(tmp_path):
    path = tmp_path / "not-issued-yet"
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    writer = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=writer)
    await transport.start()
    initialized = await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {}},
    }))
    assert initialized["result"]["capabilities"]["tools"]["listChanged"] is True
    listed = await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }))
    assert listed["result"]["tools"] == []
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    write_credential(path, "new-active-binding-token")
    await transport.refresh_if_changed()
    notification = json.loads(writer.getvalue())
    assert notification == {
        "jsonrpc": "2.0", "method": "notifications/tools/list_changed", "params": {},
    }
    await transport.stop()


@pytest.mark.asyncio
async def test_tools_list_request_synchronously_connects_when_credential_appears(tmp_path):
    path = tmp_path / "issued-after-start"
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    proxy = StdioRemoteMcpProxy(transport, writer=io.StringIO())
    await transport.start()
    write_credential(path, "active-token")
    response = await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {},
    }))
    assert "result" in response
    assert len(connector.sessions) == 1
    assert connector.sessions[0].frames == [{
        "jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {},
    }]
    await transport.stop()


@pytest.mark.asyncio
async def test_present_credential_connection_failure_is_error_not_empty_surface(tmp_path):
    class BrokenConnector:
        async def connect(self, credential):
            raise OSError("private endpoint detail")
    path = tmp_path / "credential"
    transport = RemoteMcpTransport(BrokenConnector(), FileCredentialSource(path), poll_interval=3600)
    proxy = StdioRemoteMcpProxy(transport, writer=io.StringIO())
    await transport.start()  # credential absent is the only READY_UNBOUND case
    write_credential(path, "present-but-cannot-connect")
    response = await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {},
    }))
    assert response["error"]["data"]["code"] == "route_unavailable"
    assert "private endpoint detail" not in json.dumps(response)
    await transport.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mode", "empty", "directory", "utf8"])
async def test_only_missing_credential_is_ready_unbound(tmp_path, kind):
    path = tmp_path / "credential"
    if kind == "mode":
        write_credential(path, "token", 0o644)
    elif kind == "empty":
        write_credential(path, "", 0o600)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff\xfe"); path.chmod(0o600)
    transport = RemoteMcpTransport(FakeConnector(), FileCredentialSource(path))
    if kind == "mode" and os.name == "nt":
        await transport.start()
        assert transport.session_active is True
        await transport.stop()
        return
    with pytest.raises(RemoteTransportError) as raised:
        await transport.start()
    assert raised.value.code == "route_unavailable"
    assert not transport.session_active


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "malformed", "eof"])
async def test_bad_remote_initialize_is_closed_safely_and_next_request_reconnects(tmp_path, failure):
    class BadSession(FakeRemoteSession):
        async def request(self, frame):
            self.frames.append(dict(frame))
            if failure == "eof":
                raise EOFError("secret upstream eof detail")
            if failure == "malformed":
                return {"jsonrpc": "2.0", "id": frame["id"], "result": {}}
            return {"jsonrpc": "2.0", "id": frame["id"], "error": {
                "code": -32000, "message": "secret upstream message", "data": {"token": "secret"},
            }}
    class RecoveringConnector(FakeConnector):
        async def connect(self, credential):
            self.credentials.append(credential)
            session = BadSession(credential) if not self.sessions else FakeRemoteSession(credential)
            self.sessions.append(session)
            return session
    path = tmp_path / "credential"; write_credential(path, "credential-secret")
    connector = RecoveringConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    proxy = StdioRemoteMcpProxy(transport, writer=io.StringIO())
    await transport.start()
    denied = await proxy.handle_line(json.dumps({
        "jsonrpc":"2.0", "id":31, "method":"initialize", "params":{},
    }))
    rendered = json.dumps(denied)
    assert denied["error"]["data"]["code"] == "route_unavailable"
    assert "secret" not in rendered
    assert connector.sessions[0].closed and not transport.session_active
    recovered = await proxy.handle_line(json.dumps({
        "jsonrpc":"2.0", "id":32, "method":"tools/list", "params":{},
    }))
    assert "result" in recovered and len(connector.sessions) == 2
    assert [frame["method"] for frame in connector.sessions[1].frames] == ["initialize", "tools/list"]
    await transport.stop()


@pytest.mark.asyncio
async def test_initialize_replay_failure_is_sanitized_closed_and_can_recover_again(tmp_path):
    class ReplayFailSession(FakeRemoteSession):
        async def request(self, frame):
            self.frames.append(dict(frame))
            return {"jsonrpc":"2.0", "id":frame["id"], "error": {
                "code": -32000, "message": "upstream-secret", "data": {"credential": "leak"},
            }}
    class ThreeStageConnector(FakeConnector):
        async def connect(self, credential):
            self.credentials.append(credential)
            if not self.sessions:
                session = ReplayFailSession(credential)
            elif len(self.sessions) == 1:
                session = ReplayFailSession(credential)
            else:
                session = FakeRemoteSession(credential)
            self.sessions.append(session)
            return session
    path = tmp_path / "credential"; write_credential(path, "credential-secret")
    connector = ThreeStageConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    proxy = StdioRemoteMcpProxy(transport, writer=io.StringIO())
    await transport.start()
    first = await proxy.handle_line(json.dumps({
        "jsonrpc":"2.0", "id":41, "method":"initialize", "params":{},
    }))
    assert first["error"]["data"]["code"] == "route_unavailable"
    second = await proxy.handle_line(json.dumps({
        "jsonrpc":"2.0", "id":42, "method":"tools/list", "params":{},
    }))
    assert second["error"]["data"]["code"] == "route_unavailable"
    assert "upstream-secret" not in json.dumps(second)
    assert connector.sessions[1].closed and not transport.session_active
    third = await proxy.handle_line(json.dumps({
        "jsonrpc":"2.0", "id":43, "method":"tools/list", "params":{},
    }))
    assert "result" in third
    assert [frame["method"] for frame in connector.sessions[2].frames] == ["initialize", "tools/list"]
    await transport.stop()


@pytest.mark.asyncio
async def test_application_error_is_forwarded_without_invalidating_session(tmp_path):
    class ErrorSession(FakeRemoteSession):
        async def request(self, frame):
            self.frames.append(dict(frame))
            if frame.get("method") == "tools/call":
                return {"jsonrpc": "2.0", "id": frame["id"], "error": {
                    "code": -32000, "message": "binding is unavailable",
                    "data": {"code": "binding_unavailable"},
                }}
            return await super().request(frame)

    class ErrorConnector(FakeConnector):
        async def connect(self, credential):
            self.credentials.append(credential)
            session = ErrorSession(credential)
            self.sessions.append(session)
            return session

    path = tmp_path / "credential"
    write_credential(path, "credential-secret")
    connector = ErrorConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    await transport.start()
    response = await transport.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 44, "method": "tools/call",
        "params": {"name": "read", "arguments": {}},
    }))
    assert response["error"]["data"]["code"] == "binding_unavailable"
    assert transport.session_active
    assert not connector.sessions[0].closed
    await transport.stop()


@pytest.mark.asyncio
async def test_rotation_reconnects_closes_old_session_and_emits_list_changed(tmp_path):
    path = tmp_path / "credential"
    write_credential(path, "short-lived-one")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    output = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=output)
    await transport.start()
    initialize = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    })
    assert "result" in await proxy.handle_line(initialize)
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    write_credential(path, "short-lived-two")
    assert await transport.refresh_if_changed()
    assert connector.credentials == ["short-lived-one", "short-lived-two"]
    assert connector.sessions[0].closed
    assert not connector.sessions[1].closed
    assert [frame["method"] for frame in connector.sessions[1].frames] == [
        "initialize", "notifications/initialized",
    ]
    notification = json.loads(output.getvalue())
    assert notification["method"] == "notifications/tools/list_changed"
    await transport.stop()
    assert connector.sessions[1].closed


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_restart_is_safe(tmp_path):
    path = tmp_path / "credential"
    write_credential(path, "short-lived-one")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)

    await transport.start()
    first_watcher = transport._watch_task
    await transport.start()
    assert transport._watch_task is first_watcher
    assert connector.credentials == ["short-lived-one"]

    connector.sessions[0].close_error = True
    await transport.stop()
    await transport.stop()
    assert connector.sessions[0].closed
    assert not transport.session_active

    await transport.start()
    assert transport._watch_task is not first_watcher
    assert connector.credentials == ["short-lived-one", "short-lived-one"]
    response = await transport.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {},
    }))
    assert response["id"] == 9
    await transport.stop()


class FlakyWriter(io.StringIO):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures
        self.attempts = 0

    def write(self, value):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("simulated writer failure")
        return super().write(value)


@pytest.mark.asyncio
async def test_failed_list_changed_delivery_stays_pending_until_writer_recovers(tmp_path):
    path = tmp_path / "credential"
    write_credential(path, "short-lived-one")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=0.01)
    writer = FlakyWriter(failures=2)
    proxy = StdioRemoteMcpProxy(transport, writer=writer)
    await transport.start()
    await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    }))
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    write_credential(path, "short-lived-two")
    for _ in range(100):
        if writer.getvalue():
            break
        await asyncio.sleep(0.01)
    assert len(connector.sessions) == 2
    assert connector.sessions[0].closed
    assert not connector.sessions[1].closed
    assert transport._watch_task is not None and not transport._watch_task.done()
    assert writer.attempts == 3
    notification = json.loads(writer.getvalue())
    assert notification["method"] == "notifications/tools/list_changed"

    # Successful delivery is removed from pending and is never duplicated.
    await asyncio.sleep(0.04)
    assert writer.attempts == 3
    response = await transport.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }))
    assert response["id"] == 2
    await transport.stop()


@pytest.mark.asyncio
async def test_initialized_before_successful_initialize_is_not_cached_or_forwarded(tmp_path):
    path = tmp_path / "credential"
    write_credential(path, "active-token")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    proxy = StdioRemoteMcpProxy(transport, writer=io.StringIO())
    await transport.start()

    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None
    assert connector.sessions[0].frames == []

    assert "result" in await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    }))
    write_credential(path, "rotated-token")
    await transport.refresh_if_changed()

    # The transport eagerly replays initialize during rotation so the new
    # session is pre-initialized.  list_changed is still not published
    # because the proxy layer has not captured a complete client handshake
    # (no initialized notification was sent by the client).
    assert [frame["method"] for frame in connector.sessions[1].frames] == [
        "initialize",
    ]
    await transport.stop()


@pytest.mark.asyncio
async def test_reconnect_replays_complete_handshake_before_list_changed(tmp_path):
    path = tmp_path / "credential"
    write_credential(path, "active-token")
    connector = FakeConnector()
    output = io.StringIO()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    proxy = StdioRemoteMcpProxy(transport, writer=output)
    await transport.start()

    assert "result" in await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 9, "method": "initialize", "params": {"capabilities": {}},
    }))
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None
    write_credential(path, "rotated-token")
    await transport.refresh_if_changed()

    assert [frame["method"] for frame in connector.sessions[1].frames] == [
        "initialize", "notifications/initialized",
    ]
    assert json.loads(output.getvalue())["method"] == "notifications/tools/list_changed"
    await transport.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_close_raises,close_delay", [
    (False, 0.0), (True, 0.0), (False, 0.02),
])
async def test_stop_cancellation_reclaims_connected_replacement_before_swap(
    tmp_path, replacement_close_raises, close_delay,
):
    path = tmp_path / "credential"
    write_credential(path, "short-lived-one")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=0.001)
    await transport.start()

    # Freeze the atomic swap. The watcher can connect replacement, but cannot
    # transfer its ownership into transport._session before stop cancels it.
    await transport._lock.acquire()
    stop_task = None
    try:
        write_credential(path, "short-lived-two")
        for _ in range(200):
            if len(connector.sessions) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(connector.sessions) == 2
        replacement = connector.sessions[1]
        replacement.close_error = replacement_close_raises
        replacement.close_delay = close_delay

        stop_task = asyncio.create_task(transport.stop())
        for _ in range(200):
            if replacement.closed:
                break
            await asyncio.sleep(0.001)
        assert replacement.closed
    finally:
        transport._lock.release()
    assert stop_task is not None
    await stop_task

    assert connector.sessions[0].closed
    assert connector.sessions[1].closed
    assert transport._watch_task is None
    assert not transport.session_active
    assert not transport._started


def test_remote_and_proxy_do_not_own_mcp_semantics():
    from modules.mcp.transports import remote, stdio_proxy
    trees = [ast.parse(inspect.getsource(module)) for module in (remote, stdio_proxy)]
    imported = set()
    imported_modules = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.add(node.module or "")
                imported.update(alias.name for alias in node.names)
    assert not any(module.startswith("modules.mcp.core") for module in imported_modules)
    for forbidden in ("McpCore", "ToolCatalog", "ContextAuthority", "GameSessionRouter"):
        assert forbidden not in imported


def material(binding: RuntimeBinding, **changes) -> AuthorityMaterial:
    values = dict(
        credential_ref="not-persisted", iss="starcoplay-runtime-manager", aud="starcoplay-mcp",
        sub=binding.runtime_id, jti="jti-a", runtime_id=binding.runtime_id,
        generation=binding.generation, binding_id=binding.binding_id,
        binding_version=binding.binding_version, lease_epoch=binding.lease_epoch,
        game_session_id=binding.game_session_id or "", game_instance_nonce=binding.game_instance_nonce or "",
        capability_digest=binding.capability_digest or "", user_id=binding.user_id,
        agentee_id=binding.agentee_id, scopes=("game:read",), allowed_tools=("observe",),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    values.update(changes)
    return AuthorityMaterial(**values)


class BindingReader:
    def __init__(self, binding): self.binding = binding
    async def get(self, binding_id):
        return self.binding if binding_id == self.binding.binding_id else None


def hosted_stack(binding):
    keys = KeyStore(generate_signing_key(kid="hosted-key", lifetime_seconds=3600))
    revocations = RevocationStore()
    signer = HostedCredentialSigner(keys)
    verifier = HostedCredentialVerifier(keys, revocations)
    authority = HostedClaimsAuthority(verifier, BindingReader(binding))
    catalog = ToolCatalog([ToolDefinition(
        "observe", "observe", "canonical.v1", "game:read", SideEffect.READ_ONLY,
        {"type": "object", "properties": {}, "additionalProperties": False},
    )])
    router = FakeGameSessionRouter(RouteFence(
        binding.game_session_id or "game-a", binding.game_instance_nonce or "nonce-a",
        binding.generation, binding.binding_version, binding.lease_epoch,
        binding.capability_digest or "digest-a", binding.user_id, binding.runtime_id,
        binding.binding_id,
    ))
    return signer, revocations, authority, McpCore(authority, router, catalog)


@pytest.mark.asyncio
async def test_ready_unbound_has_no_callable_surface_and_active_binding_does(tmp_path):
    pending = RuntimeBinding(
        "binding-a", "runtime-a", 1, "user-a", "agentee-a", 1, 1,
        BindingState.PENDING, "game-a", "nonce-a", "digest-a",
    )
    signer, _, authority, core = hosted_stack(pending)
    token = signer.sign(material(pending))
    session = HostedMcpJsonRpcSession(core, authority, token)
    response = await session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response["error"]["data"]["code"] == "binding_unavailable"
    await session.close()

    active = replace(pending, status=BindingState.ACTIVE)
    signer, _, authority, core = hosted_stack(active)
    session = HostedMcpJsonRpcSession(core, authority, signer.sign(material(active)))
    assert "result" in await session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    listed = await session.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["observe"]
    await session.close()


@pytest.mark.asyncio
async def test_cross_tenant_and_revoked_token_cannot_initialize():
    active = RuntimeBinding(
        "binding-a", "runtime-a", 1, "user-a", "agentee-a", 1, 1,
        BindingState.ACTIVE, "game-a", "nonce-a", "digest-a",
    )
    signer, revocations, authority, core = hosted_stack(active)
    foreign = material(active, user_id="user-b", jti="jti-foreign")
    foreign_session = HostedMcpJsonRpcSession(core, authority, signer.sign(foreign))
    denied = await foreign_session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert denied["error"]["data"]["code"] == "binding_unavailable"
    revocations.revoke_jti("jti-revoked")
    revoked = material(active, jti="jti-revoked")
    revoked_session = HostedMcpJsonRpcSession(core, authority, signer.sign(revoked))
    denied = await revoked_session.request({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    assert denied["error"]["data"]["code"] == "capability_denied"


@pytest.mark.asyncio
async def test_old_binding_token_and_wrong_scope_have_no_authority():
    current = RuntimeBinding(
        "binding-a", "runtime-a", 2, "user-a", "agentee-a", 2, 2,
        BindingState.ACTIVE, "game-a", "nonce-new", "digest-new",
    )
    signer, _, authority, core = hosted_stack(current)
    old = material(
        current, generation=1, binding_version=1, lease_epoch=1,
        game_instance_nonce="nonce-old", capability_digest="digest-old", jti="jti-old",
    )
    old_session = HostedMcpJsonRpcSession(core, authority, signer.sign(old))
    denied = await old_session.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert denied["error"]["data"]["code"] == "binding_unavailable"

    wrong_scope = material(current, scopes=("game:write",), jti="jti-wrong-scope")
    scoped_session = HostedMcpJsonRpcSession(core, authority, signer.sign(wrong_scope))
    assert "result" in await scoped_session.request({
        "jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {},
    })
    listed = await scoped_session.request({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    assert listed["result"]["tools"] == []
    called = await scoped_session.request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "observe", "arguments": {}, "_meta": {"toolCallId": "call-1"}},
    })
    assert called["error"]["data"]["code"] == "capability_denied"


# ── WebSocketRemoteSession concurrency tests ──────────────────────────

class _MockWebSocket:
    """Minimal fake for WebSocketRemoteSession unit tests."""

    def __init__(self, *, receive_items=(), send_raises_after=None):
        self.sent: list[dict] = []
        self._receive = list(receive_items)
        self._send_count = 0
        self._send_raises_after = send_raises_after

    async def send_json(self, data):
        self._send_count += 1
        if (self._send_raises_after is not None
                and self._send_count > self._send_raises_after):
            raise ConnectionResetError("simulated send failure")
        self.sent.append(dict(data))

    async def receive_json(self):
        if self._receive:
            return self._receive.pop(0)
        # Block until cancelled — simulates a pending request.
        import asyncio
        await asyncio.Event().wait()
        return {}  # unreachable

    async def close(self):
        pass


class _MockClientSession:
    async def close(self):
        pass


def _make_session(**kw):
    return WebSocketRemoteSession(_MockClientSession(), _MockWebSocket(**kw))


@pytest.mark.asyncio
async def test_duplicate_request_id_is_rejected_and_first_request_stays_pending():
    """Gap 1: concurrent duplicate JSON-RPC id must not overwrite the old future."""
    session = _make_session()
    frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}

    # First request registers the future.
    first_task = asyncio.create_task(session.request(dict(frame)))
    # Let the first request reach the await-future stage.
    await asyncio.sleep(0)

    # Second request with the same id must be rejected immediately.
    with pytest.raises(RemoteTransportError, match="duplicate JSON-RPC request id"):
        await session.request(dict(frame))

    # The first future must still be pending (not overwritten / not resolved).
    assert 1 in session._pending
    assert not session._pending[1].done()

    # Cleanup: cancel first_task and close.
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    await session.close()


@pytest.mark.asyncio
async def test_send_json_failure_removes_just_registered_pending():
    """Gap 1: send_json failure must remove the freshly registered pending future."""
    session = _make_session(send_raises_after=0)
    frame = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {}}

    with pytest.raises(ConnectionResetError, match="simulated send failure"):
        await session.request(dict(frame))

    # Pending must be clean — the failed request was removed.
    assert 2 not in session._pending


@pytest.mark.asyncio
async def test_close_cancels_and_awaits_reader_then_resolves_pending():
    """Gap 2: close() must cancel + await the reader and drain pending futures."""
    session = _make_session()
    frame = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {}}

    # Issue a request so the reader is started and a future is pending.
    task = asyncio.create_task(session.request(dict(frame)))
    await asyncio.sleep(0)  # let registration happen
    assert session._reader is not None
    assert 3 in session._pending

    # Close must cancel + await reader and cancel all pending.
    await session.close()

    # Reader must be done after close.
    assert session._reader is None or session._reader.done()
    # Pending must be drained.
    assert 3 not in session._pending
    # The awaiting task observes the cancelled future without leaking.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()


# ── StdioRemoteMcpProxy.serve concurrency tests ───────────────────────

class _SlowRemoteTransport:
    """RemoteMcpTransport stub: start/stop + handle_line that can block."""

    def __init__(self, *, fail_on=None):
        self.started = False
        self.stopped = False
        self.fail_on = fail_on or set()
        self._list_changed_cb = None
        self.session_active = True
        self.handled: list = []

    def on_list_changed(self, cb):
        self._list_changed_cb = cb

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def handle_line(self, line):
        self.handled.append(line)
        request = json.loads(line)
        rid = request.get("id")
        if rid in self.fail_on:
            raise RuntimeError(f"simulated unexpected failure for {rid}")
        if "id" not in request:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}


@pytest.mark.asyncio
async def test_serve_collects_and_propagates_background_task_exception():
    """Gap 3: unexpected background task exceptions are collected and propagated."""
    transport = _SlowRemoteTransport(fail_on={2})
    writer = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=writer)

    async def _lines():
        yield json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        yield json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"

    with pytest.raises(RuntimeError, match="simulated unexpected failure for 2"):
        await proxy.serve(_lines())

    assert transport.stopped


@pytest.mark.asyncio
async def test_serve_clean_eof_waits_for_in_flight_then_stops():
    """Gap 3: clean EOF waits for in-flight requests to settle."""
    # Use a queue-based reader so we can control EOF timing.
    transport = _SlowRemoteTransport()
    writer = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=writer)

    class _QueueReader:
        def __init__(self):
            self._q: asyncio.Queue = asyncio.Queue()

        def __aiter__(self):
            return self

        async def __anext__(self):
            v = await self._q.get()
            if v is None:
                raise StopAsyncIteration
            return v

    reader = _QueueReader()
    serve_task = asyncio.create_task(proxy.serve(reader))

    await reader._q.put(
        json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/list"}) + "\n"
    )
    await asyncio.sleep(0)  # let serve pick it up

    # Signal EOF — serve should wait for in-flight, then stop.
    await reader._q.put(None)
    await serve_task

    assert transport.stopped
    assert len(transport.handled) == 1


@pytest.mark.asyncio
async def test_serve_cancel_notification_still_penetrates():
    """Gap 3: cancel notifications are not broken by the new serve machinery."""
    transport = _SlowRemoteTransport()
    writer = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=writer)

    async def _lines():
        yield json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "say", "arguments": {}}}) + "\n"
        yield json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                          "params": {"requestId": 1}}) + "\n"

    await proxy.serve(_lines())
    # Both the call and the cancel notification must have been forwarded.
    assert len(transport.handled) == 2
    assert transport.stopped


# ── HostedMcpWebSocketEndpoint writer-failure teardown test ──────────

class _SlowListToolsCore:
    """McpCore stub whose list_tools blocks until released, giving the test
    time to disconnect the client before the server writes a response."""

    def __init__(self):
        self.catalog_digest = "test-digest"
        self._release = asyncio.Event()
        self.list_tools_called = False

    async def list_tools(self, _handle):
        self.list_tools_called = True
        await self._release.wait()
        from modules.mcp.core.catalog import ToolDefinition
        return [ToolDefinition(
            "test", "test", "canonical.v1", "game:read",
            SideEffect.READ_ONLY, {"type": "object", "properties": {}},
        )]

    async def call_tool(self, *_args, **_kwargs):
        raise NotImplementedError


class _NoopCloseAuthority:
    async def establish(self, metadata):
        from modules.mcp.contracts.models import ContextHandle
        return ContextHandle("test-handle")

    async def close(self, _handle):
        pass


@pytest.mark.asyncio
async def test_hosted_writer_failure_triggers_bridge_teardown_not_silent_pileup():
    """Gap 4: writer send_json failure must signal EOF and tear down the bridge.

    When the WebSocket client disconnects mid-request and the server attempts
    send_json on a dead connection, the writer exception must propagate through
    in_queue (None sentinel) so serve stops and all tasks are consumed by gather."""
    import aiohttp
    from aiohttp import web

    core = _SlowListToolsCore()
    authority = _NoopCloseAuthority()
    endpoint = HostedMcpWebSocketEndpoint(core, authority)
    app = web.Application()
    app.router.add_get("/mcp/hosted/ws", endpoint.handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        client = aiohttp.ClientSession()
        try:
            ws = await client.ws_connect(
                f"http://127.0.0.1:{port}/mcp/hosted/ws",
                headers={"Authorization": "Bearer test-token"},
            )
            # Send initialize to establish the context handle.
            await ws.send_json({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1"}},
            })
            init_resp = await ws.receive_json()
            assert "result" in init_resp and init_resp["id"] == 1

            # Send a tools/list that will block inside list_tools.
            await ws.send_json({
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            })
            # Wait until the server has received the request and is blocked.
            for _ in range(100):
                if core.list_tools_called:
                    break
                await asyncio.sleep(0.01)
            assert core.list_tools_called

            # Abruptly terminate the client session so the server-side
            # writer hits a send failure (ConnectionResetError) rather than
            # a graceful CLOSE frame that the reader processes first.
        finally:
            await client.close()

        # Release list_tools — the server will now try to send_json on a dead
        # connection, the writer will fail, signal EOF via in_queue, and the
        # bridge must tear down without hanging.
        core._release.set()

        # Give the server a moment to process and tear down.
        await asyncio.sleep(0.3)

        # The endpoint's handle coroutine should have completed by now.
        # If the bridge had leaked, the AppRunner shutdown would hang.
    finally:
        await runner.cleanup()


# ── Non-disruptive credential rotation tests ────────────────────────────


class _ControllableFutureSession(FakeRemoteSession):
    """Session whose non-initialize requests block on a controllable future."""

    def __init__(self, credential: str) -> None:
        super().__init__(credential)
        self._pending_future: asyncio.Future | None = None

    async def request(self, frame):
        self.frames.append(dict(frame))
        if "id" not in frame:
            return None
        if frame.get("method") == "initialize":
            return {"jsonrpc": "2.0", "id": frame["id"], "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": True}},
            }}
        self._pending_future = asyncio.get_running_loop().create_future()
        return await self._pending_future


class _ControllableConnector:
    def __init__(self) -> None:
        self.sessions: list[_ControllableFutureSession] = []
        self.credentials: list[str] = []

    async def connect(self, credential: str):
        self.credentials.append(credential)
        session = _ControllableFutureSession(credential)
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_rotation_preserves_inflight_requests(tmp_path):
    """In-flight requests on the old session must survive credential rotation."""
    path = tmp_path / "credential"
    write_credential(path, "original-token")
    connector = _ControllableConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    output = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=output)
    await transport.start()

    # Complete client handshake.
    assert "result" in await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    }))
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    old_session = connector.sessions[0]

    # Start a request that will stay pending.
    pending_task = asyncio.create_task(proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "say", "arguments": {}},
    })))
    # Wait for the request to reach the pending future inside the session.
    for _ in range(200):
        if old_session._pending_future is not None:
            break
        await asyncio.sleep(0.001)
    assert old_session._pending_future is not None
    assert not old_session._pending_future.done()

    # Rotate the credential.
    write_credential(path, "rotated-token")

    # Rotation must complete without waiting for the old in-flight request.
    rotation_task = asyncio.create_task(transport.refresh_if_changed())
    for _ in range(200):
        if len(connector.sessions) == 2:
            break
        await asyncio.sleep(0.001)
    assert len(connector.sessions) == 2

    # Old session must NOT be closed while the request is pending.
    assert not old_session.closed

    # New session must be active and pre-initialized.
    assert transport.session_active

    # Resolve the old session's in-flight request.
    old_session._pending_future.set_result({
        "jsonrpc": "2.0", "id": 99, "result": {"content": [{"type": "text", "text": "ok"}]},
    })
    result = await asyncio.wait_for(pending_task, timeout=2)
    assert result["result"]["content"][0]["text"] == "ok"

    # Now that the in-flight request is drained, the old session should close.
    for _ in range(200):
        if old_session.closed:
            break
        await asyncio.sleep(0.001)
    assert old_session.closed

    await rotation_task
    await transport.stop()


class _HandshakeFailSession(FakeRemoteSession):
    """Session that fails initialize handshake."""

    def __init__(self, credential: str, fail_mode: str = "error"):
        super().__init__(credential)
        self.fail_mode = fail_mode

    async def request(self, frame):
        self.frames.append(dict(frame))
        if frame.get("method") == "initialize":
            if self.fail_mode == "error":
                return {"jsonrpc": "2.0", "id": frame["id"], "error": {
                    "code": -32000, "message": "internal error",
                }}
            if self.fail_mode == "malformed":
                return {"jsonrpc": "2.0", "id": frame["id"], "result": {}}
        return await super().request(frame)


class _SecondConnectFailsConnector:
    """Returns a healthy session first, then a handshake-failing one."""

    def __init__(self, fail_mode: str = "error"):
        self.credentials: list[str] = []
        self.sessions: list[FakeRemoteSession] = []
        self._attempt = 0
        self._fail_mode = fail_mode

    async def connect(self, credential):
        self._attempt += 1
        self.credentials.append(credential)
        if self._attempt == 1:
            session = FakeRemoteSession(credential)
        else:
            session = _HandshakeFailSession(credential, fail_mode=self._fail_mode)
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_rotation_verifies_handshake_before_swap(tmp_path):
    """Rotation must eagerly verify initialize handshake before swapping."""
    path = tmp_path / "credential"
    write_credential(path, "original-token")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    output = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=output)
    await transport.start()

    assert "result" in await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    }))
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    old_session = connector.sessions[0]
    assert not old_session.closed

    write_credential(path, "rotated-token")
    await transport.refresh_if_changed()

    # Old session retired and drained (no in-flight requests → closed immediately).
    assert old_session.closed
    assert len(connector.sessions) == 2
    assert not connector.sessions[1].closed
    assert [frame["method"] for frame in connector.sessions[1].frames] == [
        "initialize", "notifications/initialized",
    ]
    await transport.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_mode", ["error", "malformed"])
async def test_handshake_failure_preserves_old_session(tmp_path, fail_mode):
    """When new session handshake fails, old session must stay active."""
    path = tmp_path / "credential"
    write_credential(path, "original-token")
    connector = _SecondConnectFailsConnector(fail_mode=fail_mode)
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    output = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=output)
    await transport.start()

    assert "result" in await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    }))
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    old_session = connector.sessions[0]

    write_credential(path, "rotated-token")
    with pytest.raises(RemoteTransportError, match="hosted MCP handshake failed"):
        await transport.refresh_if_changed()

    # Old session must still be active and not closed.
    assert not old_session.closed
    assert transport.session_active
    # The bad replacement must have been reclaimed.
    assert connector.sessions[1].closed
    await transport.stop()


@pytest.mark.asyncio
async def test_stop_closes_retired_session_immediately(tmp_path):
    """stop() must close retired sessions immediately, not wait for drain."""
    path = tmp_path / "credential"
    write_credential(path, "original-token")
    connector = _ControllableConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    output = io.StringIO()
    proxy = StdioRemoteMcpProxy(transport, writer=output)
    await transport.start()

    assert "result" in await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"capabilities": {"tools": {"listChanged": True}}},
    }))
    assert await proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    })) is None

    old_session = connector.sessions[0]

    # Start a pending request on the old session.
    pending_task = asyncio.create_task(proxy.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": "say", "arguments": {}},
    })))
    for _ in range(200):
        if old_session._pending_future is not None:
            break
        await asyncio.sleep(0.001)
    assert old_session._pending_future is not None
    assert not old_session._pending_future.done()

    # Rotate — old session enters retired with an active request.
    write_credential(path, "rotated-token")
    rotation_task = asyncio.create_task(transport.refresh_if_changed())
    for _ in range(200):
        if len(connector.sessions) == 2:
            break
        await asyncio.sleep(0.001)
    assert len(connector.sessions) == 2
    assert not old_session.closed  # still retired, pending request

    # stop() must close old session immediately and clear tracking.
    await transport.stop()

    assert old_session.closed
    assert connector.sessions[1].closed
    assert not transport.session_active
    async with transport._drain_lock:
        assert len(transport._retired_sessions) == 0
        assert len(transport._active_requests) == 0

    # Clean up the pending task.
    if not pending_task.done():
        pending_task.cancel()
        try:
            await pending_task
        except asyncio.CancelledError:
            pass
    await rotation_task


@pytest.mark.asyncio
async def test_no_probe_without_initialize_frame_retries(tmp_path):
    """Rotation without a captured initialize frame returns early; old session
    stays active and the replacement is reclaimed."""
    path = tmp_path / "credential"
    write_credential(path, "original-token")
    connector = FakeConnector()
    transport = RemoteMcpTransport(connector, FileCredentialSource(path), poll_interval=3600)
    await transport.start()

    # Session is active but no client initialize has been sent yet.
    old_session = connector.sessions[0]
    assert transport.session_active
    assert transport._initialize_frame is None

    # Rotation attempt — must return without swapping.
    write_credential(path, "rotated-token")
    await transport.refresh_if_changed()

    # Old session still active (no swap happened).
    assert transport.session_active
    assert transport._session is old_session
    # Replacement was connected then reclaimed.
    assert len(connector.sessions) == 2
    assert connector.sessions[1].closed

    # Now client completes initialize — rotation should succeed on next attempt.
    await transport.forward_current({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}},
    })
    assert transport._initialize_frame is not None

    write_credential(path, "rotated-again")
    await transport.refresh_if_changed()

    # This time rotation succeeds — old session retired, new session active.
    assert transport.session_active
    assert transport._session is not old_session
    assert len(connector.sessions) == 3
    assert old_session.closed  # drained immediately (no in-flight requests)
    await transport.stop()
