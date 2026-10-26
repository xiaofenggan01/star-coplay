from __future__ import annotations

import json

import httpx
import pytest

from common.relay import NewAPIClient, NewAPIClientConfig, NewAPIClientError


def _client(handler, **config_overrides) -> NewAPIClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://newapi.test", transport=transport)
    return NewAPIClient(
        NewAPIClientConfig(
            base_url="http://newapi.test/",
            access_token="admin-token",
            user_id=1,
            **config_overrides,
        ),
        client=http_client,
    )


@pytest.mark.asyncio
async def test_request_adds_auth_and_user_headers():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["new_api_user"] = request.headers["new-api-user"]
        return httpx.Response(200, json={"success": True, "data": {"ok": True}})

    client = _client(handler)
    payload = await client.list_groups()

    assert captured == {
        "url": "http://newapi.test/api/group/",
        "authorization": "admin-token",
        "new_api_user": "1",
    }
    assert payload["data"]["ok"] is True


@pytest.mark.asyncio
async def test_request_can_override_user_and_access_token():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["new_api_user"] = request.headers["new-api-user"]
        body = json.loads(request.content)
        assert body["name"] == "default"
        return httpx.Response(200, json={"success": True})

    client = _client(handler)
    await client.create_token(
        {"name": "default"},
        user_id=42,
        access_token="user-token",
    )

    assert captured == {
        "authorization": "user-token",
        "new_api_user": "42",
    }


@pytest.mark.asyncio
async def test_issue_user_access_token_logs_in_and_generates_token_with_session_cookie():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/user/login":
            body = json.loads(request.content)
            assert body == {"username": "relay-user", "password": "secret"}
            return httpx.Response(
                200,
                json={"success": True, "data": {"id": 42}},
                headers={"set-cookie": "session=abc; Path=/"},
            )
        if request.url.path == "/api/user/token":
            seen["new_api_user"] = request.headers["new-api-user"]
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200, json={"success": True, "data": "user-access-token"})
        return httpx.Response(404, json={"success": False, "message": "not found"})

    client = _client(handler)
    payload = await client.issue_user_access_token(
        username="relay-user",
        password="secret",
        user_id=42,
    )

    assert payload["data"] == "user-access-token"
    assert seen == {"new_api_user": "42", "cookie": "session=abc"}


@pytest.mark.asyncio
async def test_params_drop_none_values():
    captured_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_url
        captured_url = str(request.url)
        return httpx.Response(200, json={"success": True, "data": []})

    client = _client(handler)
    await client.list_logs({"type": 2, "model_name": None, "p": 1})

    assert captured_url == "http://newapi.test/api/log/?type=2&p=1"


@pytest.mark.asyncio
async def test_get_token_usage_uses_bound_token_as_bearer():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"code": True, "data": {"total_available": 10}})

    client = _client(handler)
    await client.get_token_usage("sk-bound")

    assert captured == {
        "url": "http://newapi.test/api/usage/token/",
        "authorization": "Bearer sk-bound",
    }


@pytest.mark.asyncio
async def test_list_channels_uses_admin_channel_endpoint():
    captured_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_url
        captured_url = str(request.url)
        return httpx.Response(200, json={"success": True, "data": {"items": []}})

    client = _client(handler)
    await client.list_channels(page=2, page_size=50, group="premium", status="enabled")

    assert captured_url == (
        "http://newapi.test/api/channel/?p=2&page_size=50&group=premium&status=enabled"
    )


@pytest.mark.asyncio
async def test_list_user_quota_data_uses_admin_usage_endpoint():
    captured_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_url
        captured_url = str(request.url)
        return httpx.Response(200, json={"success": True, "data": []})

    client = _client(handler)
    await client.list_user_quota_data({"start_timestamp": 1, "end_timestamp": 2})

    assert captured_url == "http://newapi.test/api/data/users?start_timestamp=1&end_timestamp=2"


@pytest.mark.asyncio
async def test_self_usage_endpoints_use_bound_user_access_token():
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "url": str(request.url),
            "authorization": request.headers["authorization"],
            "new_api_user": request.headers["new-api-user"],
        })
        return httpx.Response(200, json={"success": True, "data": []})

    client = _client(handler)
    await client.get_user_logs(
        user_id=42,
        access_token="user-access-token",
        params={"type": 2, "p": 1},
    )
    await client.get_user_logs_stat(
        user_id=42,
        access_token="user-access-token",
        params={"type": 2},
    )
    await client.list_self_quota_data(
        user_id=42,
        access_token="user-access-token",
        params={"start_timestamp": 1, "end_timestamp": 2},
    )

    assert captured == [
        {
            "url": "http://newapi.test/api/log/self?type=2&p=1",
            "authorization": "user-access-token",
            "new_api_user": "42",
        },
        {
            "url": "http://newapi.test/api/log/self/stat?type=2",
            "authorization": "user-access-token",
            "new_api_user": "42",
        },
        {
            "url": "http://newapi.test/api/data/self?start_timestamp=1&end_timestamp=2",
            "authorization": "user-access-token",
            "new_api_user": "42",
        },
    ]


@pytest.mark.asyncio
async def test_get_self_user_uses_bound_user_access_token():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["new_api_user"] = request.headers["new-api-user"]
        return httpx.Response(200, json={"success": True, "data": {"id": 42}})

    client = _client(handler)
    await client.get_self_user(user_id=42, access_token="user-access-token")

    assert captured == {
        "url": "http://newapi.test/api/user/self",
        "authorization": "user-access-token",
        "new_api_user": "42",
    }


@pytest.mark.asyncio
async def test_search_users_uses_admin_search_endpoint():
    captured_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_url
        captured_url = str(request.url)
        return httpx.Response(200, json={"success": True, "data": {"items": []}})

    client = _client(handler)
    await client.search_users(keyword="relay_user_abc", group=None, page=2, page_size=10)

    assert captured_url == "http://newapi.test/api/user/search?keyword=relay_user_abc&p=2&size=10"


@pytest.mark.asyncio
async def test_success_false_raises_api_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "bad request"})

    client = _client(handler)

    with pytest.raises(NewAPIClientError) as exc:
        await client.get_user(123)

    assert exc.value.code == "NEWAPI_API_ERROR"
    assert exc.value.status_code == 200
    assert exc.value.message == "bad request"


@pytest.mark.asyncio
async def test_http_error_raises_with_payload_message():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    client = _client(handler)

    with pytest.raises(NewAPIClientError) as exc:
        await client.get_status()

    assert exc.value.code == "NEWAPI_HTTP_ERROR"
    assert exc.value.status_code == 401
    assert exc.value.message == "unauthorized"


@pytest.mark.asyncio
async def test_http_429_retries_then_succeeds():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"message": "too many requests"})
        return httpx.Response(200, json={"success": True, "data": {"ok": True}})

    client = _client(handler, max_429_retries=1, retry_base_delay_seconds=0)

    payload = await client.get_status()

    assert calls == 2
    assert payload["data"]["ok"] is True


@pytest.mark.asyncio
async def test_transport_timeout_is_normalized():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    client = _client(handler)

    with pytest.raises(NewAPIClientError) as exc:
        await client.get_status()

    assert exc.value.code == "NEWAPI_TIMEOUT"


@pytest.mark.asyncio
async def test_non_json_error_body_is_preserved():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = _client(handler)

    with pytest.raises(NewAPIClientError) as exc:
        await client.get_status()

    assert exc.value.code == "NEWAPI_HTTP_ERROR"
    assert exc.value.message == "bad gateway"
    assert exc.value.payload == "bad gateway"


def test_config_requires_base_url_and_access_token():
    with pytest.raises(ValueError):
        NewAPIClient(NewAPIClientConfig(base_url="", access_token="token"))
    with pytest.raises(ValueError):
        NewAPIClient(NewAPIClientConfig(base_url="http://newapi.test", access_token=""))
