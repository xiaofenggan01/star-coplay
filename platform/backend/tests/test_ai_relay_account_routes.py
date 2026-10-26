from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from modules.ai_relay_account import RelayAccount
from modules.gateway.server import Gateway, GatewayConfig
from modules.http_api.server import HttpApiConfig, HttpApiServer


class FakeAIRelayAccountService:
    def __init__(self) -> None:
        self.ensure_calls = []
        self.update_calls = []
        self.disabled_users = []
        self.token_updates = []
        self.deleted_tokens = []
        self.quota_adjustments = []
        self.accounts = {
            "u-existing": RelayAccount(
                user_id="u-existing",
                newapi_user_id=22,
                newapi_token_id=33,
                newapi_token_key="sk-secret-key",
                has_newapi_user_access_token=True,
                group="default",
            )
        }

    async def ensure_account(self, request):
        self.ensure_calls.append(request)
        account = RelayAccount(
            user_id=request.user_id,
            newapi_user_id=100,
            newapi_token_id=900,
            newapi_token_key="sk-created-secret",
            has_newapi_user_access_token=True,
            group=request.default_group,
            preferred_models=dict(request.preferred_models),
        )
        self.accounts[request.user_id] = account
        return account

    async def get_account(self, user_id):
        return self.accounts.get(user_id)

    async def update_account(self, user_id, *, group=None, preferred_models=None, status=None):
        self.update_calls.append(
            {
                "user_id": user_id,
                "group": group,
                "preferred_models": preferred_models,
                "status": status,
            }
        )
        existing = self.accounts[user_id]
        updated = RelayAccount(
            user_id=existing.user_id,
            newapi_user_id=existing.newapi_user_id,
            newapi_token_id=existing.newapi_token_id,
            newapi_token_key=existing.newapi_token_key,
            has_newapi_user_access_token=existing.has_newapi_user_access_token,
            group=group or existing.group,
            preferred_models=dict(preferred_models if preferred_models is not None else existing.preferred_models),
            credential_mode=existing.credential_mode,
            status=status or existing.status,
        )
        self.accounts[user_id] = updated
        return updated

    async def disable_account(self, user_id):
        self.disabled_users.append(user_id)
        existing = self.accounts[user_id]
        disabled = RelayAccount(
            user_id=existing.user_id,
            newapi_user_id=existing.newapi_user_id,
            newapi_token_id=existing.newapi_token_id,
            newapi_token_key=existing.newapi_token_key,
            has_newapi_user_access_token=existing.has_newapi_user_access_token,
            group=existing.group,
            preferred_models=dict(existing.preferred_models),
            credential_mode=existing.credential_mode,
            status="disabled",
        )
        self.accounts[user_id] = disabled
        return disabled

    async def get_quota(self, user_id):
        return {
            "user_id": user_id,
            "newapi_user_id": 22,
            "quota": 700,
            "used_quota": 300,
            "token": {"id": 33, "total_available": 700},
        }

    async def list_groups(self, *, include_disabled=False):
        return {
            "default_group": "default",
            "allowed_groups": ["default", "premium"],
            "newapi_groups": ["default", "premium"],
            "groups": ["default", "premium"] if include_disabled else ["default"],
        }

    async def list_tokens(self, user_id, *, page=1, page_size=20):
        return {
            "user_id": user_id,
            "page": page,
            "page_size": page_size,
            "total": 1,
            "items": [{"id": 33, "name": "runtime", "key": "sk-s***cret"}],
        }

    async def update_token(self, token_id, *, status=None, group=None, quota_policy=None, model_limits=None):
        self.token_updates.append(
            {
                "token_id": token_id,
                "status": status,
                "group": group,
                "quota_policy": quota_policy,
                "model_limits": model_limits,
            }
        )
        return {"token": {"id": token_id, "status": status, "group": group, "model_limits": "gpt-4o-mini"}}

    async def delete_token(self, token_id, *, reason=""):
        self.deleted_tokens.append({"token_id": token_id, "reason": reason})
        return {"token": {"id": token_id}, "deleted": True}

    async def adjust_quota(
        self,
        *,
        user_id,
        mode,
        reason,
        idempotency_key,
        quota=None,
        quota_usd=None,
        biz_ref_type=None,
        biz_ref_id=None,
    ):
        call = {
            "user_id": user_id,
            "mode": mode,
            "quota": quota,
            "quota_usd": quota_usd,
            "reason": reason,
            "idempotency_key": idempotency_key,
            "biz_ref_type": biz_ref_type,
            "biz_ref_id": biz_ref_id,
        }
        self.quota_adjustments.append(call)
        return {**call, "status": "succeeded", "duplicate": False}

    async def list_quota_ledger(
        self,
        user_id,
        *,
        page=1,
        page_size=20,
        start_timestamp=None,
        end_timestamp=None,
        log_types=None,
    ):
        return {
            "user_id": user_id,
            "page": page,
            "page_size": page_size,
            "total": 1,
            "items": [{"id": 1, "type": 1, "quota": 50}],
            "included_log_types": sorted(log_types or {1, 3, 4, 6}),
            "excluded_log_types": [2],
        }

    async def list_usage_logs(
        self,
        user_id,
        *,
        page=1,
        page_size=20,
        start_timestamp=None,
        end_timestamp=None,
        model_name=None,
        token_name=None,
        group=None,
        request_id=None,
        include_errors=True,
    ):
        return {
            "user_id": user_id,
            "page": page,
            "page_size": page_size,
            "total": 1,
            "items": [{"id": 2, "type": 2, "model_name": model_name or "gpt-4o-mini"}],
            "included_log_types": [2, 5] if include_errors else [2],
        }

    async def get_usage_summary(
        self,
        user_id,
        *,
        start_timestamp=None,
        end_timestamp=None,
        model_name=None,
        token_name=None,
        group=None,
        granularity=None,
        page=1,
        page_size=20,
    ):
        return {
            "user_id": user_id,
            "quota": 123,
            "rpm": 1,
            "tpm": 456,
            "granularity": granularity or "hour",
            "model_summaries": {"page": page, "page_size": page_size, "total": 0, "items": []},
            "buckets": [],
        }

    async def get_health(self, *, group=None):
        return {
            "newapi_status": {"version": "test"},
            "groups": ["default"],
            "models": {"enabled": ["gpt-4o-mini"]},
            "channels": {"total": 0, "items": []},
        }

    async def get_health_models(self, *, group=None):
        return {"group": group, "models": {"enabled": ["gpt-4o-mini"]}}

    async def get_health_channels(self, *, group=None, model=None, include_balance=False):
        return {"group": group, "model": model, "items": [{"id": 1, "name": "primary"}]}

    async def get_health_users(
        self,
        *,
        start_timestamp=None,
        end_timestamp=None,
        granularity=None,
        page=1,
        page_size=20,
    ):
        return {"page": page, "page_size": page_size, "total": 1, "items": [{"username": "sc_test", "quota": 10}]}


async def _client(service=None, *, internal_api_token="internal-token"):
    gateway = Gateway(GatewayConfig(admin_secret="admin-secret"), AsyncMock())
    server = HttpApiServer(
        gateway,
        gateway.authenticator,
        HttpApiConfig(),
        ai_relay_account_service=service,
        internal_api_token=internal_api_token,
    )
    test_server = TestServer(server.build_app())
    client = TestClient(test_server)
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_ai_relay_account_routes_require_internal_token():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        resp = await client.get("/internal/ai/relay/users/u-existing")
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "CONFIG_UNAUTHORIZED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_relay_account_returns_public_binding_without_token_key():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        resp = await client.get(
            "/internal/ai/relay/users/u-existing",
            headers={"Authorization": "Bearer internal-token"},
        )
        assert resp.status == 200
        body = await resp.json()
        data = body["data"]
        assert data["user_id"] == "u-existing"
        assert data["newapi_token_id"] == 33
        assert data["has_token_key"] is True
        assert data["has_user_access_token"] is True
        assert "newapi_token_key" not in data
        assert "newapi_user_access_token" not in data
        assert "sk-secret-key" not in json.dumps(body)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ensure_relay_account_passes_request_to_service():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        resp = await client.put(
            "/internal/ai/relay/users/u-new/ensure",
            headers={"x-internal-api-token": "internal-token"},
            json={
                "email": "u@example.com",
                "display_name": "User New",
                "default_group": "premium",
                "preferred_models": {"balanced": "gpt-4o"},
            },
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["user_id"] == "u-new"
        assert body["data"]["group"] == "premium"
        assert body["data"]["preferred_models"] == {"balanced": "gpt-4o"}
        assert len(service.ensure_calls) == 1
        call = service.ensure_calls[0]
        assert call.user_id == "u-new"
        assert call.email == "u@example.com"
        assert call.display_name == "User New"
        assert call.default_group == "premium"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ai_relay_routes_support_api_internal_prefix_for_gateway_proxy():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    headers = {"Authorization": "Bearer internal-token"}
    try:
        get_resp = await client.get(
            "/api/internal/ai/relay/users/u-existing",
            headers=headers,
        )
        assert get_resp.status == 200
        get_body = await get_resp.json()
        assert get_body["data"]["user_id"] == "u-existing"

        ensure_resp = await client.put(
            "/api/internal/ai/relay/users/u-prefixed/ensure",
            headers=headers,
            json={"email": "prefixed@example.com"},
        )
        assert ensure_resp.status == 200
        ensure_body = await ensure_resp.json()
        assert ensure_body["data"]["user_id"] == "u-prefixed"
        assert len(service.ensure_calls) == 1
        assert service.ensure_calls[0].email == "prefixed@example.com"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_patch_relay_account_updates_group_and_preferences():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        resp = await client.patch(
            "/internal/ai/relay/users/u-existing",
            headers={"Authorization": "Bearer internal-token"},
            json={"group": "premium", "preferred_models": {"fast": "gpt-4o-mini"}},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["group"] == "premium"
        assert service.update_calls == [
            {
                "user_id": "u-existing",
                "group": "premium",
                "preferred_models": {"fast": "gpt-4o-mini"},
                "status": None,
            }
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delete_relay_account_only_supports_disable_mode():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        unsupported = await client.delete(
            "/internal/ai/relay/users/u-existing?mode=delete",
            headers={"Authorization": "Bearer internal-token"},
        )
        assert unsupported.status == 400
        body = await unsupported.json()
        assert body["error"]["code"] == "UNSUPPORTED_DELETE_MODE"

        disabled = await client.delete(
            "/internal/ai/relay/users/u-existing",
            headers={"Authorization": "Bearer internal-token"},
        )
        assert disabled.status == 200
        body = await disabled.json()
        assert body["data"]["status"] == "disabled"
        assert service.disabled_users == ["u-existing"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ai_relay_routes_return_503_when_service_not_configured():
    client = await _client(service=None)
    try:
        resp = await client.get(
            "/internal/ai/relay/users/u-existing",
            headers={"Authorization": "Bearer internal-token"},
        )
        assert resp.status == 503
        body = await resp.json()
        assert body["error"]["code"] == "AI_RELAY_NOT_CONFIGURED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ai_relay_quota_usage_and_health_routes():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    headers = {"Authorization": "Bearer internal-token"}
    try:
        quota = await client.get("/internal/ai/relay/users/u-existing/quota", headers=headers)
        assert quota.status == 200
        quota_body = await quota.json()
        assert quota_body["data"]["quota"] == 700

        ledger = await client.get(
            "/internal/ai/relay/users/u-existing/quota-ledger?page=2&page_size=10&types=1,4",
            headers=headers,
        )
        assert ledger.status == 200
        ledger_body = await ledger.json()
        assert ledger_body["data"]["page"] == 2
        assert ledger_body["data"]["included_log_types"] == [1, 4]
        assert ledger_body["data"]["excluded_log_types"] == [2]

        usage = await client.get(
            "/internal/ai/relay/users/u-existing/usage/logs?model_name=gpt-4o&include_errors=false",
            headers=headers,
        )
        assert usage.status == 200
        usage_body = await usage.json()
        assert usage_body["data"]["included_log_types"] == [2]
        assert usage_body["data"]["items"][0]["model_name"] == "gpt-4o"

        summary = await client.get(
            "/internal/ai/relay/users/u-existing/usage/summary?start_timestamp=1&end_timestamp=2&granularity=day&page=2&page_size=10",
            headers=headers,
        )
        assert summary.status == 200
        summary_body = await summary.json()
        assert summary_body["data"]["quota"] == 123
        assert summary_body["data"]["granularity"] == "day"
        assert summary_body["data"]["model_summaries"]["page"] == 2
        assert summary_body["data"]["model_summaries"]["page_size"] == 10

        health = await client.get("/internal/ai/relay/health", headers=headers)
        assert health.status == 200
        health_body = await health.json()
        assert health_body["data"]["models"]["enabled"] == ["gpt-4o-mini"]

        query_quota = await client.get(
            "/internal/ai/relay/quota?user_id=u-existing",
            headers=headers,
        )
        assert query_quota.status == 200
        query_quota_body = await query_quota.json()
        assert query_quota_body["data"]["quota"] == 700
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ai_relay_groups_tokens_quota_adjustment_and_health_views():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    headers = {"Authorization": "Bearer internal-token"}
    try:
        groups = await client.get("/internal/ai/relay/groups?include_disabled=true", headers=headers)
        assert groups.status == 200
        groups_body = await groups.json()
        assert groups_body["data"]["groups"] == ["default", "premium"]

        tokens = await client.get(
            "/internal/ai/relay/users/u-existing/tokens?page=2&page_size=10",
            headers=headers,
        )
        assert tokens.status == 200
        tokens_body = await tokens.json()
        assert tokens_body["data"]["page"] == 2
        assert "sk-secret-key" not in json.dumps(tokens_body)

        updated = await client.patch(
            "/internal/ai/relay/tokens/33",
            headers=headers,
            json={
                "status": 1,
                "group": "premium",
                "quota_policy": {"unlimited_quota": True},
                "model_limits": ["gpt-4o-mini"],
            },
        )
        assert updated.status == 200
        assert service.token_updates == [
            {
                "token_id": 33,
                "status": 1,
                "group": "premium",
                "quota_policy": {"unlimited_quota": True},
                "model_limits": ["gpt-4o-mini"],
            }
        ]

        deleted = await client.delete(
            "/internal/ai/relay/tokens/33?reason=manual",
            headers=headers,
        )
        assert deleted.status == 200
        assert service.deleted_tokens == [{"token_id": 33, "reason": "manual"}]

        adjusted = await client.post(
            "/internal/ai/relay/quota-adjustments",
            headers=headers,
            json={
                "user_id": "u-existing",
                "mode": "add",
                "quota": 10,
                "reason": "admin grant",
                "idempotency_key": "idem-route-1",
                "biz_ref_type": "admin",
                "biz_ref_id": "ticket-1",
            },
        )
        assert adjusted.status == 200
        assert service.quota_adjustments[0]["quota"] == 10

        adjusted_usd = await client.post(
            "/internal/ai/relay/quota-adjustments",
            headers=headers,
            json={
                "user_id": "u-existing",
                "mode": "override",
                "quota_usd": 100,
                "idempotency_key": "idem-route-usd-1",
            },
        )
        assert adjusted_usd.status == 200
        assert service.quota_adjustments[1]["quota"] is None
        assert service.quota_adjustments[1]["quota_usd"] == 100
        assert service.quota_adjustments[1]["reason"] == "后台额度调整：override"

        models = await client.get("/internal/ai/relay/health/models", headers=headers)
        channels = await client.get("/internal/ai/relay/health/channels?model=gpt-4o-mini", headers=headers)
        users = await client.get("/internal/ai/relay/health/users?page_size=10", headers=headers)
        assert models.status == 200
        assert channels.status == 200
        assert users.status == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ai_relay_query_alias_requires_user_id():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        resp = await client.get(
            "/internal/ai/relay/quota",
            headers={"Authorization": "Bearer internal-token"},
        )
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "INVALID_QUERY"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ai_relay_query_routes_validate_pagination():
    service = FakeAIRelayAccountService()
    client = await _client(service)
    try:
        resp = await client.get(
            "/internal/ai/relay/users/u-existing/usage/logs?page_size=101",
            headers={"Authorization": "Bearer internal-token"},
        )
        assert resp.status == 400
        body = await resp.json()
        assert body["error"]["code"] == "INVALID_QUERY"
    finally:
        await client.close()
