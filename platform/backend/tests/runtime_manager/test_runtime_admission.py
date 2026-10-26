from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.async_application import AsyncRuntimeService
from modules.runtime_manager.domain import (
    Conflict, ResourceExhausted, RuntimeProductRecord, RuntimeProductSpec, RuntimeState,
)
from modules.runtime_manager.persistence import InMemoryRuntimeAdmissionRepository


def spec(**changes) -> RuntimeProductSpec:
    return replace(RuntimeProductSpec(
        "runtime-1", 1, "activation-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/rev-1",
    ), **changes)


@pytest.mark.asyncio
async def test_in_memory_admission_has_replay_conflict_and_atomic_limit_parity():
    policy = RuntimeAdmissionPolicy(
        allowed_user_ids=frozenset(("user-1",)), max_active_global=1,
        max_active_per_user=1,
    )
    repository = InMemoryRuntimeAdmissionRepository(policy)
    service = AsyncRuntimeService(repository)
    first, replay = await asyncio.gather(service.ensure_runtime(spec()), service.ensure_runtime(spec()))
    assert first == replay
    with pytest.raises(Conflict, match="different stable startup intent"):
        await service.ensure_runtime(spec(adapter_config_ref="adapter/other"))
    with pytest.raises(ResourceExhausted, match="capacity"):
        await service.ensure_runtime(spec(
            runtime_id="runtime-2", activation_key="activation-2",
            user_id="user-2", agentee_id="agentee-2",
        ))
    assert len(await repository.list_active()) == 1


class RouteCoordinator:
    def __init__(self, record=None, provision_error=None):
        self.record = record
        self.provision_error = provision_error
        self.calls = []
        self.repository = self

    async def get(self, runtime_id):
        return self.record

    async def provision(self, value):
        self.calls.append(("provision", value))
        if self.provision_error is not None:
            raise self.provision_error
        return RuntimeProductRecord(value)

    async def ensure(self, value):
        self.calls.append(("ensure", value))
        return self.record or RuntimeProductRecord(value)

    async def submit_input(self, *args):
        self.calls.append(("input", args))
        raise AssertionError("denied input reached coordinator")

    async def ensure_binding(self, runtime_id, generation, user_id, agentee_id, operation_id):
        from modules.runtime_manager.domain import RuntimeBinding
        self.calls.append(("ensure_binding", runtime_id))
        return RuntimeBinding("binding-1", runtime_id, generation, user_id, agentee_id)

    async def stop(self, runtime_id, generation, operation_id, reason):
        self.calls.append(("stop", runtime_id))
        return replace(self.record, actual_state=RuntimeState.STOPPED)

    async def status_for(self, record):
        return record

    async def list_active(self):
        return ()


def body(user_id="user-1"):
    return {
        "runtime_id":"runtime-1", "generation":1,
        "operation_id":"activation-1", "activation_key":"activation-1",
        "user_id":user_id, "agentee_id":"agentee-1", "adapter_kind":"fake",
        "adapter_config_ref":"adapter/default", "persona_artifact_ref":"persona/rev-1",
    }


async def client_for(coordinator, policy, principal=None, authenticate=None):
    @web.middleware
    async def correlation(request, handler):
        request["log_id"] = "test-log-id"
        return await handler(request)

    app = web.Application(middlewares=[correlation])
    setup_runtime_manager_routes(
        app, coordinator,
        authenticate or (lambda request: principal if principal is not None else {
            "service_id":"tenant-service", "user_id":"user-1", "agentee_id":"agentee-1",
        }),
        policy,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_empty_and_non_exact_allowlist_deny_before_runtime_side_effects():
    for allowed in (frozenset(), frozenset(("user-10",)), frozenset(("USER-1",))):
        coordinator = RouteCoordinator()
        client = await client_for(coordinator, RuntimeAdmissionPolicy(allowed_user_ids=allowed))
        try:
            for endpoint in ("runtimes:provision", "runtimes:ensure"):
                response = await client.post(
                    f"/internal/runtime-manager/v1/{endpoint}", json=body()
                )
                assert response.status == 403
                error = (await response.json())["error"]
                assert error["code"] == "hosted_runtime_not_allowed"
                assert error["log_id"] == "test-log-id"
            assert coordinator.calls == []
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_input_denial_follows_owner_check_and_precedes_operation_or_provider():
    record = RuntimeProductRecord(spec(), actual_state=RuntimeState.READY_UNBOUND)
    coordinator = RouteCoordinator(record)
    client = await client_for(coordinator, RuntimeAdmissionPolicy())
    try:
        response = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/input",
            json={"operation_id":"input-1", "content":"hello"},
        )
        assert response.status == 403
        payload = await response.json()
        assert payload["error"]["code"] == "hosted_runtime_not_allowed"
        assert "allowlist" not in payload["error"]["message"]
        assert coordinator.calls == []
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_desired_running_is_denied_before_ensure_but_existing_stop_is_not():
    record = RuntimeProductRecord(spec(), actual_state=RuntimeState.READY_UNBOUND)
    coordinator = RouteCoordinator(record)
    client = await client_for(coordinator, RuntimeAdmissionPolicy())
    try:
        denied = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/desired-state",
            json={"operation_id":"start-1", "desired_state":"running"},
        )
        assert denied.status == 403
        assert coordinator.calls == []
        stopped = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/desired-state",
            json={"operation_id":"stop-1", "desired_state":"stopped", "generation":1},
        )
        assert stopped.status == 200
        assert coordinator.calls == [("stop", "runtime-1")]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_exact_allowlisted_user_succeeds_and_quota_error_is_stable_non_disclosing_429():
    allowed = RuntimeAdmissionPolicy(allowed_user_ids=frozenset(("user-1",)))
    coordinator = RouteCoordinator()
    client = await client_for(coordinator, allowed)
    try:
        response = await client.post("/internal/runtime-manager/v1/runtimes:provision", json=body())
        assert response.status == 200
        assert [call[0] for call in coordinator.calls] == ["provision"]
    finally:
        await client.close()

    coordinator = RouteCoordinator(
        provision_error=ResourceExhausted("runtime capacity is unavailable")
    )
    client = await client_for(coordinator, allowed)
    try:
        response = await client.post("/internal/runtime-manager/v1/runtimes:provision", json=body())
        assert response.status == 429
        error = (await response.json())["error"]
        assert error == {
            "code":"resource_exhausted", "message":"runtime capacity is unavailable",
            "log_id":"test-log-id",
        }
        assert "count" not in error["message"] and "limit" not in error["message"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_all_authenticated_policy_accepts_multiple_tenants_without_allowlist():
    policy = RuntimeAdmissionPolicy(allow_all_authenticated=True)
    for user_id in ("tenant-a", "tenant-b"):
        policy.require_allowed(user_id)


@pytest.mark.asyncio
async def test_cross_owner_404_precedes_allowlist_and_stop_remains_available():
    record = RuntimeProductRecord(spec(user_id="owner", agentee_id="owner-agent"))
    coordinator = RouteCoordinator(record)
    client = await client_for(coordinator, RuntimeAdmissionPolicy())
    try:
        hidden = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/input",
            json={"operation_id":"input-1", "content":"hello"},
        )
        assert hidden.status == 404
    finally:
        await client.close()

    own = {"service_id":"tenant", "user_id":"owner", "agentee_id":"owner-agent"}
    client = await client_for(coordinator, RuntimeAdmissionPolicy(), own)
    try:
        stopped = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/desired-state",
            json={"operation_id":"stop-1", "desired_state":"stopped", "generation":1},
        )
        assert stopped.status == 200
        assert coordinator.calls == [("stop", "runtime-1")]
        assert (await client.get("/internal/runtime-manager/v1/runtimes/runtime-1")).status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authentication_and_owner_precede_allowlist_for_every_protected_ingress():
    endpoints = (
        ("/internal/runtime-manager/v1/runtimes:provision", body()),
        ("/internal/runtime-manager/v1/runtimes:ensure", body()),
        ("/internal/runtime-manager/v1/runtimes/runtime-1/desired-state",
         {"operation_id":"start-1", "desired_state":"running"}),
        ("/internal/runtime-manager/v1/runtimes/runtime-1/input",
         {"operation_id":"input-1", "content":"hello"}),
    )
    coordinator = RouteCoordinator(RuntimeProductRecord(spec()))
    client = await client_for(
        coordinator, RuntimeAdmissionPolicy(),
        authenticate=lambda request: web.Response(status=401),
    )
    try:
        for endpoint, payload in endpoints:
            assert (await client.post(endpoint, json=payload)).status == 401
        assert coordinator.calls == []
    finally:
        await client.close()

    foreign_record = RuntimeProductRecord(spec(user_id="owner", agentee_id="owner-agent"))
    coordinator = RouteCoordinator(foreign_record)
    client = await client_for(coordinator, RuntimeAdmissionPolicy())
    try:
        for endpoint, payload in endpoints:
            response = await client.post(endpoint, json=payload)
            assert response.status == 404
            assert (await response.json())["error"]["code"] == "not_found"
        assert coordinator.calls == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ensure_binding_is_available_after_allowlist_removal():
    coordinator = RouteCoordinator(RuntimeProductRecord(spec()))
    client = await client_for(coordinator, RuntimeAdmissionPolicy())
    try:
        response = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/bindings:ensure",
            json={"operation_id":"binding-1", "generation":1},
        )
        assert response.status == 200
        assert coordinator.calls == [("ensure_binding", "runtime-1")]
    finally:
        await client.close()
