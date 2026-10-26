from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from common.auth.token_authenticator import AuthResult
from modules.http_api.server import HttpApiConfig, HttpApiServer
from modules.runtime_manager.contracts import InputReceipt
from modules.runtime_manager.contracts import OutputPage, TranscriptEntry


class Auth:
    async def authenticate(self, token):
        return AuthResult(True, "tenant-1") if token == "valid" else AuthResult(False, None)


class RuntimeManager:
    def __init__(self):
        self.calls = []
        self.record = SimpleNamespace(
            generation=4,
            spec=SimpleNamespace(user_id="tenant-1", agentee_id="agentee-1"),
        )

    async def get(self, runtime_id):
        return self.record if runtime_id == "runtime-1" else None

    async def submit_input_async(self, runtime_id, generation, value):
        self.calls.append((runtime_id, generation, value))
        return InputReceipt(
            runtime_id=runtime_id,
            generation=generation,
            provider="claude",
            session_id="session-1",
            accepted_at="2026-07-21T00:00:00+00:00",
            transcript_cursor="cursor",
            operation_id=value.request_id,
        )

    async def read_output(self, runtime_id, generation, query):
        assert (runtime_id, generation, query.limit) == ("runtime-1", 4, 25)
        return OutputPage(
            entries=[TranscriptEntry(role="assistant", content="visible reply")],
            runtime_id=runtime_id,
            generation=generation,
        )


class Admission:
    def require_allowed(self, user_id):
        assert user_id == "tenant-1"


async def client(runtime_manager):
    server = HttpApiServer(
        gateway=SimpleNamespace(),
        authenticator=Auth(),
        config=HttpApiConfig(),
        runtime_manager=runtime_manager,
        runtime_manager_enabled=True,
        runtime_admission_policy=Admission(),
    )
    app = web.Application()
    app.router.add_post(
        "/api/v1/hosted-runtimes/{runtime_id}/inputs",
        server._hosted_runtime_input,
    )
    app.router.add_get(
        "/api/v1/hosted-runtimes/{runtime_id}/output",
        server._hosted_runtime_output,
    )
    result = TestClient(TestServer(app))
    await result.start_server()
    return result


@pytest.mark.asyncio
async def test_platform_input_uses_authenticated_runtime_owner_and_current_generation():
    manager = RuntimeManager()
    http = await client(manager)
    try:
        response = await http.post(
            "/api/v1/hosted-runtimes/runtime-1/inputs",
            headers={"Authorization": "Bearer valid"},
            json={"input_id": "ui-1", "text": "same words as a prior say"},
        )
        assert response.status == 202
        body = await response.json()
        assert body["input_id"] == "ui-1"
        assert body["operation_id"] == "platform:ui-1"
        runtime_id, generation, value = manager.calls[0]
        assert (runtime_id, generation) == ("runtime-1", 4)
        assert value.content == "same words as a prior say"
        assert value.trace_context["source"] == "platform"
    finally:
        await http.close()


@pytest.mark.asyncio
async def test_platform_input_cannot_assert_owner_or_generation():
    manager = RuntimeManager()
    http = await client(manager)
    try:
        response = await http.post(
            "/api/v1/hosted-runtimes/runtime-1/inputs",
            headers={"Authorization": "Bearer valid"},
            json={"input_id": "ui-1", "text": "hello", "generation": 99},
        )
        assert response.status == 400
        assert manager.calls == []
    finally:
        await http.close()


@pytest.mark.asyncio
async def test_platform_input_hides_other_tenant_runtime_as_not_found():
    manager = RuntimeManager()
    manager.record.spec.user_id = "tenant-2"
    http = await client(manager)
    try:
        response = await http.post(
            "/api/v1/hosted-runtimes/runtime-1/inputs",
            headers={"Authorization": "Bearer valid"},
            json={"input_id": "ui-1", "text": "hello"},
        )
        assert response.status == 404
        assert manager.calls == []
    finally:
        await http.close()


@pytest.mark.asyncio
async def test_platform_can_read_only_sanitised_runtime_output():
    manager = RuntimeManager()
    http = await client(manager)
    try:
        response = await http.get(
            "/api/v1/hosted-runtimes/runtime-1/output?limit=25",
            headers={"Authorization": "Bearer valid"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["entries"] == [{
            "role": "assistant", "content": "visible reply", "uuid": None,
            "parent_uuid": None, "timestamp": None, "model": None,
        }]
    finally:
        await http.close()
