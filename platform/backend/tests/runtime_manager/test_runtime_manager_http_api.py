from __future__ import annotations

import pytest
import base64
import hashlib
import hmac
import json
import time
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.domain import BindingState, RuntimeBinding, RuntimeProductRecord, RuntimeProductSpec, RuntimeState
from modules.runtime_manager.contracts import InputReceipt, TurnResult
from modules.runtime_manager.runtime_status_view import assemble_runtime_status

ALL_TENANTS = {"service_id":"test-control-plane", "all_tenants":True}
TENANT = {"service_id":"test-tenant-service", "user_id":"user-1", "agentee_id":"agentee-1"}
ALLOW_TENANT = RuntimeAdmissionPolicy(allowed_user_ids=frozenset(("user-1",)))


@pytest.mark.asyncio
async def test_runtime_manager_service_claim_contract_and_replay_protection():
    from modules.http_api.runtime_control_claims import (
        RuntimeControlClaimsVerifier, issue_runtime_control_claim,
    )
    class Conn:
        def __init__(self): self.jtis = set()
        def transaction(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, *args): return "DELETE 0"
        async def fetchval(self, sql, jti, service_id, exp):
            if jti in self.jtis: return None
            self.jtis.add(jti); return jti
    class Pool:
        def __init__(self): self.conn = Conn()
        def acquire(self): return self.conn
    now, pool = int(time.time()), Pool()
    services = {"runtime-operator":{"all_tenants":True},
                "tenant-service":{"user_id":"u","agentee_id":"a"}}
    verifier = RuntimeControlClaimsVerifier("claims-secret", services, pool, now=lambda:now)
    token = issue_runtime_control_claim("claims-secret", "runtime-operator", now=now, jti="once")
    assert await verifier.verify(token) == {"service_id":"runtime-operator","all_tenants":True}
    with pytest.raises(ValueError): await verifier.verify(token)
    for service, issued, ttl in (("unknown",now,30), ("runtime-operator",now-70,30),
                                 ("runtime-operator",now+10,30)):
        bad = issue_runtime_control_claim("claims-secret", service, now=issued, ttl_seconds=ttl)
        with pytest.raises(ValueError): await verifier.verify(bad)
    with pytest.raises(ValueError):
        issue_runtime_control_claim("claims-secret", "runtime-operator", now=now, ttl_seconds=61)
    def resign(changes):
        raw = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "==")); raw.update(changes)
        encoded = base64.urlsafe_b64encode(json.dumps(raw,sort_keys=True,separators=(",",":")).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(b"claims-secret",encoded.encode(),hashlib.sha256).digest()).decode().rstrip("=")
        return f"{encoded}.{sig}"
    for changes in ({"aud":"other"},{"iss":"other"},{"all_tenants":True}):
        with pytest.raises(ValueError): await verifier.verify(resign(changes))


class Coordinator:
    def __init__(self):
        self.calls = []; self.repository = self; self.record = None
        self.max_input_bytes = 65536
        self.rejections = []
        self._handles = {}
        self.adapters = {}
    async def list_active(self): return ()
    async def ensure(self, spec):
        self.calls.append(("ensure", spec))
        self.record = RuntimeProductRecord(
            spec, actual_state=RuntimeState.READY_UNBOUND, ready=True,
        )
        return self.record
    async def provision(self, spec):
        self.calls.append(("provision", spec))
        self.record = RuntimeProductRecord(spec)
        return self.record
    async def get(self, runtime_id): return self.record
    async def get_generation(self, runtime_id, generation): return self.record
    async def status_for(self, record): return await assemble_runtime_status(record)
    async def roll_adapter_config(self, runtime_id, reference, operation_id, generation):
        self.calls.append(("roll", runtime_id, reference, operation_id, generation)); return self.record
    async def ensure_binding(self, runtime_id, generation, user_id, agentee_id, operation_id):
        self.calls.append(("ensure_binding", runtime_id, generation, user_id, agentee_id, operation_id))
        return RuntimeBinding("binding-1", runtime_id, generation, user_id, agentee_id)
    def record_input_rejection(self, runtime_id, generation, limit_kind):
        self.rejections.append((runtime_id, generation, limit_kind))
    async def submit_input(self, runtime_id, generation, value):
        self.calls.append(("input", runtime_id, generation, value))
        return TurnResult(value.request_id, runtime_id, generation, 1, "ok")
    async def submit_input_async(self, runtime_id, generation, value):
        self.calls.append(("input_async", runtime_id, generation, value))
        from modules.runtime_manager.contracts import InputReceipt
        return InputReceipt(
            runtime_id=runtime_id, generation=generation, provider="fake",
            session_id="fake-sess", accepted_at="2026-07-18T10:00:00+00:00",
            transcript_cursor="0", operation_id=value.request_id,
        )


@pytest.mark.asyncio
async def test_runtime_input_http_boundary_uses_utf8_bytes_and_rejects_before_submit():
    coordinator = Coordinator()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda _request: TENANT, ALLOW_TENANT)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        exact = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/input",
            json={"operation_id": "exact", "generation": 1, "content": "a" * 65536},
        )
        assert exact.status == 202
        calls_before = len(coordinator.calls)
        oversized = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/input",
            json={"operation_id": "large", "generation": 1, "content": "界" * 21846},
        )
        assert oversized.status == 429
        assert (await oversized.json())["error"]["code"] == "resource_exhausted"
        assert len(coordinator.calls) == calls_before
        assert coordinator.rejections == [
            ("runtime-1", 1, "runtime_input_bytes")
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_runtime_manager_routes_are_internal_token_protected():
    coordinator = Coordinator()
    app = web.Application()
    def auth(request):
        if request.headers.get("Authorization") != "Bearer internal-secret":
            return web.json_response({"error": "unauthorized"}, status=401)
        return ALL_TENANTS
    setup_runtime_manager_routes(app, coordinator, auth)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        denied = await client.get("/internal/runtime-manager/v1/health")
        assert denied.status == 401
        allowed = await client.get(
            "/internal/runtime-manager/v1/health",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert allowed.status == 200
        body = await allowed.json()
        assert body["schema_version"] == 1
        assert body["ok"] is True and body["component"] == "runtime_manager"
        assert body["runtime"] == {
            "active_count": 0, "owned_handle_count": 0,
            "orphan_candidate_count": 0, "quarantined_count": 0,
        }
        assert set(body) == {
            "schema_version", "observed_at", "ok", "component", "runtime",
            "workers", "outboxes", "provider", "sinks",
        }
        forbidden = {
            "user_id", "agentee_id", "runtime_id", "binding_id", "intent_id",
            "session_id", "path", "credential", "provider_body", "exception_message",
        }
        def keys(value):
            if isinstance(value, dict):
                return set(value).union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()
        assert not (keys(body) & forbidden)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["ready_timeout", "process_exited", "provider_rejected", "readiness_failed"]
)
async def test_runtime_manager_readiness_errors_are_safe_allowlisted_503(code):
    coordinator = Coordinator()

    async def fail_list_active():
        raise RuntimeError(code)

    coordinator.list_active = fail_list_active
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda request: ALL_TENANTS)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/internal/runtime-manager/v1/health")
        assert response.status == 503
        assert await response.json() == {"error": {"code": code, "message": code}}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_runtime_owner_context_is_required_and_cross_owner_is_hidden():
    coordinator = Coordinator()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda request: TENANT, ALLOW_TENANT)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        missing = await client.get("/internal/runtime-manager/v1/runtimes/runtime-1")
        assert missing.status == 200
        payload = await missing.json()
        assert set(payload) == {
            "contract_version", "runtime_id", "generation", "status_version",
            "desired_state", "actual_state", "ready", "binding_projection",
            "last_effective_activity_at", "started_at", "exit_reason", "process_health",
        }
        assert payload["binding_projection"] is None
        assert "spec" not in payload and "user_id" not in payload
        foreign = await client.get(
            "/internal/runtime-manager/v1/runtimes/runtime-1",
            headers={"x-runtime-user-id": "other", "x-runtime-agentee-id": "agentee-1"},
        )
        assert foreign.status == 200  # unsigned identity headers are ignored
        own = await client.get(
            "/internal/runtime-manager/v1/runtimes/runtime-1",
            headers={"x-runtime-user-id": "user-1", "x-runtime-agentee-id": "agentee-1"},
        )
        assert own.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ensure_existing_runtime_checks_persisted_owner_before_calling_ensure():
    coordinator = Coordinator()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "owner-user", "owner-agentee", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda request: {
        "service_id":"foreign-service", "user_id":"foreign-user", "agentee_id":"foreign-agentee"})
    client = TestClient(TestServer(app))
    await client.start_server()
    body = {
        "runtime_id": "runtime-1", "generation": 1,
        "operation_id": "activate-foreign", "activation_key": "activate-foreign",
        "user_id": "foreign-user", "agentee_id": "foreign-agentee",
        "adapter_kind": "fake", "adapter_config_ref": "adapter/default",
        "persona_artifact_ref": "persona/ref",
    }
    try:
        response = await client.post(
            "/internal/runtime-manager/v1/runtimes:ensure", json=body,
            headers={
                "x-runtime-user-id": "foreign-user",
                "x-runtime-agentee-id": "foreign-agentee",
            },
        )
        assert response.status == 404
        assert (await response.json())["error"]["code"] == "not_found"
        assert not any(call[0] == "ensure" for call in coordinator.calls)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_provision_is_an_explicit_endpoint_and_ensure_never_accepts_a_defer_flag():
    coordinator = Coordinator()
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda request: TENANT, ALLOW_TENANT)
    client = TestClient(TestServer(app))
    await client.start_server()
    body = {
        "runtime_id": "runtime-deferred", "generation": 1,
        "operation_id": "activation-deferred", "activation_key": "activation-deferred",
        "user_id": "user-1", "agentee_id": "agentee-1",
        "adapter_kind": "claude", "adapter_config_ref": "adapter/default",
        "persona_artifact_ref": "persona/agentee-1/rev-1",
        "allowed_capabilities": ["query_runtime_snapshot", "say"],
    }
    try:
        result = await client.post("/internal/runtime-manager/v1/runtimes:provision", json=body)
        assert result.status == 200
        payload = await result.json()
        assert payload["actual_state"] == "provisioning" and payload["ready"] is False
        assert [call[0] for call in coordinator.calls] == ["provision"]
        started = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-deferred/desired-state",
            json={"operation_id": "start-after-binding", "desired_state": "running"},
        )
        assert started.status == 200
        started_payload = await started.json()
        assert started_payload["actual_state"] == "ready_unbound" and started_payload["ready"] is True
        assert [call[0] for call in coordinator.calls] == ["provision", "ensure"]
        legacy_flag = await client.post(
            "/internal/runtime-manager/v1/runtimes:ensure",
            json={**body, "defer_start_until_bound": True},
        )
        assert legacy_flag.status == 400
        assert [call[0] for call in coordinator.calls] == ["provision", "ensure"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_config_roll_api_is_authenticated_owner_scoped_and_generation_bound():
    coordinator = Coordinator()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 3, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    def auth(request):
        return ALL_TENANTS if request.headers.get("Authorization") == "Bearer secret" else web.Response(status=401)
    setup_runtime_manager_routes(app, coordinator, auth)
    client = TestClient(TestServer(app)); await client.start_server()
    path = "/internal/runtime-manager/v1/runtimes/runtime-1/adapter-config:roll"
    body = {"operation_id":"roll-1","generation":3,"adapter_config_ref":"runtime-config/c/2"}
    try:
        assert (await client.post(path, json=body)).status == 401
        foreign = await client.post(path, json=body, headers={"Authorization":"Bearer secret",
            "x-runtime-user-id":"other","x-runtime-agentee-id":"agentee-1"})
        assert foreign.status == 200  # all-tenant claim, not spoofable headers, authorizes lookup
        own = await client.post(path, json=body, headers={"Authorization":"Bearer secret",
            "x-runtime-user-id":"user-1","x-runtime-agentee-id":"agentee-1"})
        assert own.status == 200
        assert coordinator.calls == [
            ("roll","runtime-1","runtime-config/c/2","roll-1",3),
            ("roll","runtime-1","runtime-config/c/2","roll-1",3),
        ]
        injected = await client.post(path, json={**body,"user_id":"user-1"},
            headers={"Authorization":"Bearer secret"})
        assert injected.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ensure_binding_api_is_owner_scoped_and_rejects_gateway_authority_fields():
    coordinator = Coordinator()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda request: TENANT)
    client = TestClient(TestServer(app)); await client.start_server()
    path = "/internal/runtime-manager/v1/runtimes/runtime-1/bindings:ensure"
    try:
        response = await client.post(path, json={"operation_id":"binding-op","generation":1})
        assert response.status == 200
        payload = await response.json()
        assert payload["binding_id"] == "binding-1"
        assert payload["status"] == BindingState.PENDING.value
        assert coordinator.calls == [
            ("ensure_binding", "runtime-1", 1, "user-1", "agentee-1", "binding-op")
        ]
        for field in ("game_session_id", "binding_id", "user_id", "capability_digest"):
            denied = await client.post(
                path, json={"operation_id":"binding-op-2","generation":1,field:"forged"}
            )
            assert denied.status == 400
        assert len(coordinator.calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ensure_binding_api_hides_cross_owner_runtime():
    coordinator = Coordinator()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "owner-user", "owner-agentee", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda request: TENANT)
    client = TestClient(TestServer(app)); await client.start_server()
    try:
        response = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/bindings:ensure",
            json={"operation_id":"binding-op","generation":1},
        )
        assert response.status == 404
        assert not coordinator.calls
    finally:
        await client.close()
