from __future__ import annotations

import pytest

from common.relay import NewAPIClientError
from modules.ai_relay_account import (
    AIRelayAccountError,
    AIRelayAccountService,
    EnsureRelayAccountRequest,
    RelayAccount,
)
from modules.ai_relay_account.config import AIRelayConfigProvider


class FakeRelayAccountRepository:
    def __init__(self, initial: RelayAccount | None = None) -> None:
        self.accounts: dict[str, RelayAccount] = {}
        if initial is not None:
            self.accounts[initial.user_id] = initial
        self.upserts: list[RelayAccount] = []
        self.sync_errors: list[tuple[str, int | None, str, str]] = []

    async def get_by_user_id(self, user_id: str) -> RelayAccount | None:
        return self.accounts.get(user_id)

    async def get_account(self, user_id: str) -> RelayAccount | None:
        return await self.get_by_user_id(user_id)

    async def get_by_newapi_token_id(self, token_id: int) -> RelayAccount | None:
        for account in self.accounts.values():
            if account.newapi_token_id == token_id:
                return account
        return None

    async def upsert(self, account: RelayAccount) -> RelayAccount:
        self.accounts[account.user_id] = account
        self.upserts.append(account)
        return account

    async def mark_sync_error(
        self,
        user_id: str,
        *,
        newapi_user_id: int | None,
        group: str,
        error: str,
    ) -> RelayAccount:
        self.sync_errors.append((user_id, newapi_user_id, group, error))
        account = RelayAccount(
            user_id=user_id,
            newapi_user_id=newapi_user_id or 0,
            group=group,
            status="sync_failed",
            last_sync_error=error,
        )
        self.accounts[user_id] = account
        return account


class FakeNewAPIAccountClient:
    def __init__(self) -> None:
        self.users: dict[int, dict] = {}
        self.tokens: dict[int, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.next_user_id = 100
        self.next_token_id = 900
        self.fail_create_token: NewAPIClientError | None = None
        self.log_items_by_type: dict[int, list[dict]] = {}

    async def search_users(self, *, keyword, group=None, page=1, page_size=20):
        self.calls.append(("search_users", {"keyword": keyword, "group": group}))
        items = [user for user in self.users.values() if user.get("username") == keyword]
        return {"success": True, "data": {"items": items}}

    async def create_user(self, payload):
        self.calls.append(("create_user", dict(payload)))
        user_id = self.next_user_id
        self.next_user_id += 1
        self.users[user_id] = {"id": user_id, "status": 1, **dict(payload)}
        return {"success": True}

    async def get_user(self, newapi_user_id, *, access_token=None):
        self.calls.append(("get_user", {"id": newapi_user_id, "access_token": access_token}))
        return {"success": True, "data": dict(self.users[newapi_user_id])}

    async def get_self_user(self, *, user_id, access_token=None):
        self.calls.append(("get_self_user", {"user_id": user_id, "access_token": access_token}))
        return {"success": True, "data": dict(self.users[user_id])}

    async def update_user(self, payload):
        self.calls.append(("update_user", dict(payload)))
        user_id = int(payload["id"])
        self.users.setdefault(user_id, {"id": user_id}).update(dict(payload))
        return {"success": True}

    async def manage_user(self, payload):
        self.calls.append(("manage_user", dict(payload)))
        user_id = int(payload["id"])
        if payload["action"] == "disable":
            self.users.setdefault(user_id, {"id": user_id})["status"] = 2
        if payload["action"] == "add_quota":
            user = self.users.setdefault(user_id, {"id": user_id, "quota": 0, "used_quota": 0})
            value = int(payload.get("value") or 0)
            mode = payload.get("mode") or "add"
            if mode == "add":
                user["quota"] = int(user.get("quota") or 0) + value
            elif mode == "subtract":
                user["quota"] = max(0, int(user.get("quota") or 0) - value)
            elif mode == "override":
                user["quota"] = value
        return {"success": True}

    async def list_tokens(self, *, user_id, access_token=None, page=1, page_size=20):
        self.calls.append(("list_tokens", {"user_id": user_id, "access_token": access_token}))
        return {"success": True, "data": {"items": list(self.tokens.get(user_id, []))}}

    async def create_token(self, payload, *, user_id, access_token=None):
        self.calls.append(("create_token", {"user_id": user_id, "access_token": access_token, **dict(payload)}))
        if self.fail_create_token is not None:
            raise self.fail_create_token
        token_id = self.next_token_id
        self.next_token_id += 1
        token = {"id": token_id, "key": "sk-real-token-key", "status": 1, **dict(payload)}
        self.tokens.setdefault(user_id, []).append(token)
        return {"success": True}

    async def update_token(self, payload, *, user_id, access_token=None):
        self.calls.append(("update_token", {"user_id": user_id, "access_token": access_token, **dict(payload)}))
        token_id = int(payload["id"])
        for token in self.tokens.setdefault(user_id, []):
            if token["id"] == token_id:
                token.update(dict(payload))
                break
        return {"success": True}

    async def delete_token(self, token_id, *, user_id, access_token=None):
        self.calls.append(("delete_token", {"user_id": user_id, "token_id": token_id, "access_token": access_token}))
        self.tokens[user_id] = [token for token in self.tokens.get(user_id, []) if token["id"] != token_id]
        return {"success": True}

    async def get_token_key(self, token_id, *, user_id, access_token=None):
        self.calls.append(("get_token_key", {"user_id": user_id, "token_id": token_id, "access_token": access_token}))
        return {"success": True, "data": {"key": "sk-real-token-key"}}

    async def issue_user_access_token(self, *, username, password, user_id):
        self.calls.append(
            (
                "issue_user_access_token",
                {
                    "username": username,
                    "password_len": len(password),
                    "user_id": user_id,
                },
            )
        )
        return {"success": True, "data": f"user-access-{user_id}"}

    async def get_status(self):
        self.calls.append(("get_status", {}))
        return {"success": True, "data": {"version": "test", "quota_per_unit": 500000}}

    async def get_test_status(self):
        self.calls.append(("get_test_status", {}))
        return {"success": True, "message": "Server is running", "http_stats": {"requests": 3}}

    async def list_groups(self):
        self.calls.append(("list_groups", {}))
        return {"success": True, "data": ["default", "premium"]}

    async def list_dashboard_models(self, *, user_id, access_token=None):
        self.calls.append(("list_dashboard_models", {"user_id": user_id}))
        return {"success": True, "data": {"1": ["gpt-4o-mini"]}}

    async def list_channel_models(self):
        self.calls.append(("list_channel_models", {}))
        return {"success": True, "data": [{"id": "gpt-4o-mini", "owned_by": "openai"}]}

    async def list_enabled_models(self):
        self.calls.append(("list_enabled_models", {}))
        return {"success": True, "data": ["gpt-4o-mini"]}

    async def list_channels(
        self,
        *,
        page=1,
        page_size=100,
        group=None,
        status=None,
        channel_type=None,
        sort_by=None,
        sort_order=None,
    ):
        self.calls.append(("list_channels", {"group": group, "status": status}))
        return {
            "success": True,
            "data": {
                "page": page,
                "page_size": page_size,
                "total": 1,
                "items": [
                    {
                        "id": 1,
                        "name": "primary",
                        "key": "provider-secret",
                        "status": 1,
                        "group": "default",
                        "models": "gpt-4o-mini",
                        "channel_info": {"is_multi_key": False, "multi_key_size": 0},
                    }
                ],
            },
        }

    async def get_token_usage(self, token_key):
        self.calls.append(("get_token_usage", {"token_key": token_key}))
        return {
            "code": True,
            "data": {
                "name": "starcoplay-agent-runtime",
                "total_granted": 1000,
                "total_used": 300,
                "total_available": 700,
                "unlimited_quota": True,
                "model_limits_enabled": False,
                "model_limits": {},
                "expires_at": 0,
            },
        }

    async def list_logs(self, params=None):
        self.calls.append(("list_logs", dict(params or {})))
        log_type = int((params or {}).get("type") or 0)
        items = self.log_items_by_type.get(log_type)
        if items is None:
            items = [
                {
                    "id": 11,
                    "user_id": 100,
                    "username": "sc_8e0f",
                    "type": log_type,
                    "content": "ledger event",
                    "quota": 50,
                    "created_at": 1710000000 + log_type,
                    "token_name": "starcoplay-agent-runtime",
                    "token_id": 900,
                    "key": "must-not-return",
                }
            ]
        return {
            "success": True,
            "data": {
                "page": (params or {}).get("p", 1),
                "page_size": (params or {}).get("page_size", 20),
                "total": len(items),
                "items": items,
            },
        }

    async def get_logs_stat(self, params=None):
        self.calls.append(("get_logs_stat", dict(params or {})))
        return {"success": True, "data": {"quota": 123, "rpm": 2, "tpm": 456}}

    async def get_user_logs(self, *, user_id, params=None, access_token=None):
        self.calls.append(("get_user_logs", {"user_id": user_id, "access_token": access_token, **dict(params or {})}))
        return await self.list_logs(params)

    async def get_user_logs_stat(self, *, user_id, params=None, access_token=None):
        self.calls.append(("get_user_logs_stat", {"user_id": user_id, "access_token": access_token, **dict(params or {})}))
        return await self.get_logs_stat(params)

    async def list_quota_data(self, params=None):
        self.calls.append(("list_quota_data", dict(params or {})))
        return {
            "success": True,
            "data": [
                {
                    "created_at": 1710000000,
                    "model_name": "gpt-4o-mini",
                    "count": 2,
                    "quota": 123,
                    "token_used": 456,
                    "unexpected": "ignored",
                },
                {
                    "created_at": 1710003600,
                    "model_name": "gpt-4o-mini",
                    "count": 1,
                    "quota": 77,
                    "token_used": 100,
                },
                {
                    "created_at": 1710003600,
                    "model_name": "gpt-4o",
                    "count": 1,
                    "quota": 300,
                    "token_used": 200,
                },
            ],
        }

    async def list_user_quota_data(self, params=None):
        self.calls.append(("list_user_quota_data", dict(params or {})))
        return {
            "success": True,
            "data": [
                {"username": "sc_a", "created_at": 1710000000, "model_name": "gpt-4o-mini", "count": 2, "quota": 100, "token_used": 20},
                {"username": "sc_a", "created_at": 1710003600, "model_name": "gpt-4o-mini", "count": 1, "quota": 50, "token_used": 10},
                {"username": "sc_b", "created_at": 1710003600, "model_name": "gpt-4o", "count": 1, "quota": 200, "token_used": 30},
            ],
        }

    async def list_self_quota_data(self, *, user_id, params=None, access_token=None):
        self.calls.append(("list_self_quota_data", {"user_id": user_id, "access_token": access_token, **dict(params or {})}))
        return await self.list_quota_data(params)


class FakeQuotaAdjustmentRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def get_by_idempotency_key(self, idempotency_key: str):
        return self.rows.get(idempotency_key)

    async def list_succeeded_for_newapi_user(
        self,
        *,
        newapi_user_id,
        start_timestamp,
        end_timestamp,
        limit,
    ):
        rows = [
            row
            for row in self.rows.values()
            if row.get("newapi_user_id") == newapi_user_id and row.get("status") == "succeeded"
        ]
        return rows[:limit]

    async def insert_pending(
        self,
        *,
        idempotency_key,
        user_id,
        mode,
        quota,
        reason,
        biz_ref_type,
        biz_ref_id,
    ):
        row = {
            "idempotency_key": idempotency_key,
            "user_id": user_id,
            "newapi_user_id": None,
            "mode": mode,
            "quota": quota,
            "reason": reason,
            "biz_ref_type": biz_ref_type,
            "biz_ref_id": biz_ref_id,
            "status": "pending",
            "newapi_response": {},
            "quota_summary": {},
            "error": None,
            "created_at": None,
            "updated_at": None,
        }
        self.rows[idempotency_key] = row
        return row

    async def mark_succeeded(self, *, idempotency_key, newapi_user_id, newapi_response, quota_summary):
        row = self.rows[idempotency_key]
        row.update(
            {
                "newapi_user_id": newapi_user_id,
                "status": "succeeded",
                "newapi_response": dict(newapi_response),
                "quota_summary": dict(quota_summary),
                "error": None,
            }
        )
        return row

    async def mark_failed(self, *, idempotency_key, error):
        row = self.rows[idempotency_key]
        row.update({"status": "failed", "error": error})
        return row


def _service(
    repo: FakeRelayAccountRepository,
    newapi: FakeNewAPIAccountClient,
    *,
    groups: tuple[str, ...] = ("default",),
    default_preferences: dict[str, str] | None = None,
    quota_adjustments: FakeQuotaAdjustmentRepository | None = None,
) -> AIRelayAccountService:
    return AIRelayAccountService(
        repo,
        newapi,
        config_provider=AIRelayConfigProvider(
            config_client=StaticAIRelayConfigClient(groups, default_preferences or {}),
            environment="test",
        ),
        quota_adjustment_repository=quota_adjustments,
    )


class StaticAIRelayConfigClient:
    def __init__(self, groups: tuple[str, ...], default_preferences: dict[str, str]) -> None:
        self.groups = list(groups)
        self.default_preferences = dict(default_preferences)

    async def get_string(self, domain, config_key, environment=None):
        if domain == "ai_relay" and config_key == "default_group":
            return self.groups[0]
        raise AssertionError(f"unexpected string config: {domain}/{config_key}/{environment}")

    async def get_json(self, domain, config_key, environment=None):
        if domain != "ai_relay":
            raise AssertionError(f"unexpected config domain: {domain}")
        if config_key == "allowed_groups":
            return list(self.groups)
        if config_key == "default_preferences":
            return dict(self.default_preferences)
        if config_key == "model_tier_mapping":
            return {}
        raise AssertionError(f"unexpected json config: {domain}/{config_key}/{environment}")

    async def get_decimal(self, domain, config_key, environment=None):
        if domain == "ai_relay" and config_key == "initial_user_quota_usd":
            return 100.0
        raise AssertionError(f"unexpected decimal config: {domain}/{config_key}/{environment}")


@pytest.mark.asyncio
async def test_ensure_returns_ready_existing_account_without_newapi_calls():
    existing = RelayAccount(
        user_id="u1",
        newapi_user_id=42,
        newapi_token_id=7,
        newapi_token_key="sk-existing",
        newapi_user_access_token="user-access-42",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi)

    result = await service.ensure_account(EnsureRelayAccountRequest(user_id="u1"))

    assert result == existing
    assert newapi.calls == []
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_ensure_creates_newapi_user_token_and_binding():
    repo = FakeRelayAccountRepository()
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi, groups=("default", "pro"))

    result = await service.ensure_account(
        EnsureRelayAccountRequest(
            user_id="platform-user-1",
            email="user@example.com",
            display_name="Player One",
            default_group="pro",
        )
    )

    assert result.is_ready
    assert result.newapi_user_id == 100
    assert result.newapi_token_id == 900
    assert result.newapi_token_key == "sk-real-token-key"
    assert result.has_newapi_user_access_token is True
    assert result.newapi_user_access_token == "user-access-100"
    assert result.group == "pro"
    assert repo.accounts["platform-user-1"].is_ready
    assert any(name == "create_user" for name, _payload in newapi.calls)
    create_user_payload = next(payload for name, payload in newapi.calls if name == "create_user")
    assert create_user_payload["email"] == "user@example.com"
    assert create_user_payload["display_name"] == "Player One"
    assert create_user_payload["quota"] == 50_000_000
    assert create_user_payload["remark"] == "starcoplay_user_id=platform-user-1"
    assert any(name == "issue_user_access_token" for name, _payload in newapi.calls)
    assert any(name == "create_token" for name, _payload in newapi.calls)
    create_token_payload = next(payload for name, payload in newapi.calls if name == "create_token")
    assert create_token_payload["access_token"] == "user-access-100"


@pytest.mark.asyncio
async def test_ensure_omits_newapi_email_when_platform_email_exceeds_newapi_limit():
    repo = FakeRelayAccountRepository()
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi)
    long_email = f"{'a' * 45}@example.test"

    result = await service.ensure_account(
        EnsureRelayAccountRequest(
            user_id="platform-user-long-email",
            email=long_email,
            display_name="Player With Long Email",
        )
    )

    assert result.is_ready
    create_user_payload = next(payload for name, payload in newapi.calls if name == "create_user")
    update_user_payload = next(
        payload
        for name, payload in newapi.calls
        if name == "update_user" and payload.get("password")
    )
    assert len(long_email) > 50
    assert create_user_payload["email"] == ""
    assert update_user_payload["email"] == ""
    assert create_user_payload["remark"] == "starcoplay_user_id=platform-user-long-email"


@pytest.mark.asyncio
async def test_ensure_marks_sync_failed_when_newapi_token_route_cannot_create_for_user():
    repo = FakeRelayAccountRepository()
    newapi = FakeNewAPIAccountClient()
    newapi.fail_create_token = NewAPIClientError(
        "NEWAPI_API_ERROR",
        "auth user id mismatch",
        status_code=200,
        payload={"success": False},
    )
    service = _service(repo, newapi)

    with pytest.raises(AIRelayAccountError) as exc:
        await service.ensure_account(EnsureRelayAccountRequest(user_id="u2"))

    assert exc.value.code == "NEWAPI_TOKEN_CREATE_FAILED"
    assert repo.sync_errors
    user_id, newapi_user_id, group, error = repo.sync_errors[0]
    assert user_id == "u2"
    assert newapi_user_id == 100
    assert group == "default"
    assert "NEWAPI_TOKEN_CREATE_FAILED" in error
    assert repo.accounts["u2"].status == "sync_failed"


@pytest.mark.asyncio
async def test_ensure_recovers_partial_binding_by_creating_missing_token():
    repo = FakeRelayAccountRepository(
        RelayAccount(
            user_id="u3",
            newapi_user_id=77,
            group="default",
            status="sync_failed",
            last_sync_error="previous failure",
        )
    )
    newapi = FakeNewAPIAccountClient()
    newapi.users[77] = {
        "id": 77,
        "username": "sc_existing",
        "display_name": "SC Existing",
        "role": 1,
        "group": "default",
        "remark": "",
    }
    service = _service(repo, newapi)

    result = await service.ensure_account(EnsureRelayAccountRequest(user_id="u3"))

    assert result.is_ready
    assert result.newapi_user_id == 77
    assert result.newapi_token_id == 900
    assert result.newapi_user_access_token == "user-access-77"
    assert any(name == "create_token" for name, _payload in newapi.calls)


@pytest.mark.asyncio
async def test_update_account_group_syncs_user_and_token_group():
    existing = RelayAccount(
        user_id="u4",
        newapi_user_id=55,
        newapi_token_id=12,
        newapi_token_key="sk-bound",
        newapi_user_access_token="user-access-55",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.users[55] = {
        "id": 55,
        "username": "sc_u4",
        "display_name": "SC u4",
        "role": 1,
        "group": "default",
        "remark": "",
    }
    newapi.tokens[55] = [
        {
            "id": 12,
            "name": "starcoplay-agent-runtime",
            "status": 1,
            "expired_time": -1,
            "remain_quota": 0,
            "unlimited_quota": True,
            "model_limits_enabled": False,
            "model_limits": "",
            "allow_ips": "",
            "group": "default",
            "cross_group_retry": False,
        }
    ]
    service = _service(repo, newapi, groups=("default", "premium"))

    result = await service.update_account("u4", group="premium", preferred_models={"balanced": "gpt-4o"})

    assert result.group == "premium"
    assert result.preferred_models == {"balanced": "gpt-4o"}
    assert newapi.users[55]["group"] == "premium"
    assert newapi.tokens[55][0]["group"] == "premium"
    assert any(name == "update_user" for name, _payload in newapi.calls)
    assert any(name == "update_token" for name, _payload in newapi.calls)
    update_token_payload = next(payload for name, payload in newapi.calls if name == "update_token")
    assert update_token_payload["access_token"] == "user-access-55"


@pytest.mark.asyncio
async def test_disable_account_disables_newapi_user_and_local_binding():
    existing = RelayAccount(
        user_id="u5",
        newapi_user_id=56,
        newapi_token_id=13,
        newapi_token_key="sk-bound",
        newapi_user_access_token="user-access-56",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.users[56] = {"id": 56, "username": "sc_u5", "status": 1}
    service = _service(repo, newapi)

    result = await service.disable_account("u5")

    assert result.status == "disabled"
    assert newapi.users[56]["status"] == 2
    assert ("manage_user", {"id": 56, "action": "disable"}) in newapi.calls


@pytest.mark.asyncio
async def test_ensure_applies_default_group_and_preferences_from_business_config():
    repo = FakeRelayAccountRepository()
    newapi = FakeNewAPIAccountClient()
    service = _service(
        repo,
        newapi,
        groups=("premium", "default"),
        default_preferences={"balanced": "gpt-4o"},
    )

    result = await service.ensure_account(
        EnsureRelayAccountRequest(
            user_id="configured-user",
            default_group="",
            preferred_models={"fast": "gpt-4o-mini"},
        )
    )

    assert result.group == "premium"
    assert result.preferred_models == {
        "balanced": "gpt-4o",
        "fast": "gpt-4o-mini",
    }


@pytest.mark.asyncio
async def test_update_rejects_group_not_allowed_by_business_config():
    repo = FakeRelayAccountRepository(
        RelayAccount(
            user_id="u6",
            newapi_user_id=60,
            newapi_token_id=14,
            newapi_token_key="sk-bound",
            newapi_user_access_token="user-access-60",
            has_newapi_user_access_token=True,
            group="default",
        )
    )
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi, groups=("default",))

    with pytest.raises(AIRelayAccountError) as exc:
        await service.update_account("u6", group="premium")

    assert exc.value.code == "INVALID_GROUP"


@pytest.mark.asyncio
async def test_get_quota_reads_newapi_user_and_bound_token_without_exposing_key():
    existing = RelayAccount(
        user_id="u7",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.users[100] = {
        "id": 100,
        "username": "sc_u7",
        "quota": 50_000_000,
        "used_quota": 1_000_000,
        "request_count": 5,
        "group": "default",
    }
    service = _service(repo, newapi)

    result = await service.get_quota("u7")

    assert result["quota"] == 50_000_000
    assert result["token"]["total_available"] == 700
    assert result["display"]["balance_credits"] == 100
    assert result["display"]["used_credits"] == 2
    assert result["display"]["token_available_credits"] == 0.0014
    assert "sk-secret" not in str(result)
    assert ("get_token_usage", {"token_key": "sk-secret"}) in newapi.calls
    assert ("get_self_user", {"user_id": 100, "access_token": "user-access-100"}) in newapi.calls


@pytest.mark.asyncio
async def test_list_groups_filters_to_platform_allowed_groups():
    repo = FakeRelayAccountRepository()
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi, groups=("default", "premium", "disabled-only"))

    result = await service.list_groups()
    all_result = await service.list_groups(include_disabled=True)

    assert result["default_group"] == "default"
    assert result["groups"] == ["default", "premium"]
    assert all_result["groups"] == ["default", "disabled-only", "premium"]


@pytest.mark.asyncio
async def test_token_list_update_and_delete_hide_secret_key():
    existing = RelayAccount(
        user_id="u-token",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-real-token-key",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.tokens[100] = [
        {
            "id": 900,
            "name": "starcoplay-agent-runtime",
            "key": "sk-real-token-key",
            "status": 1,
            "expired_time": -1,
            "remain_quota": 0,
            "unlimited_quota": True,
            "model_limits_enabled": False,
            "model_limits": "",
            "allow_ips": "",
            "group": "default",
            "cross_group_retry": False,
        }
    ]
    service = _service(repo, newapi, groups=("default", "premium"))

    listed = await service.list_tokens("u-token")
    updated = await service.update_token(900, group="premium", model_limits=["gpt-4o-mini"])
    deleted = await service.delete_token(900, reason="test cleanup")

    assert listed["items"][0]["key"] == "sk-r***-key"
    assert updated["token"]["group"] == "premium"
    assert updated["token"]["model_limits"] == "gpt-4o-mini"
    assert repo.accounts["u-token"].group == "premium"
    assert deleted["deleted"] is True
    assert repo.accounts["u-token"].status == "sync_failed"
    assert "sk-real-token-key" not in str(listed)
    assert ("delete_token", {"user_id": 100, "token_id": 900, "access_token": "user-access-100"}) in newapi.calls


@pytest.mark.asyncio
async def test_token_management_requires_relay_user_access_token():
    repo = FakeRelayAccountRepository(
        RelayAccount(
            user_id="u-token-missing-access",
            newapi_user_id=100,
            newapi_token_id=900,
            newapi_token_key="sk-real-token-key",
            group="default",
        )
    )
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi)

    with pytest.raises(AIRelayAccountError) as exc:
        await service.list_tokens("u-token-missing-access")

    assert exc.value.code == "NEWAPI_USER_ACCESS_TOKEN_MISSING"
    assert not any(name == "list_tokens" for name, _payload in newapi.calls)


@pytest.mark.asyncio
async def test_adjust_quota_is_idempotent_and_updates_newapi_quota():
    existing = RelayAccount(
        user_id="u-quota",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.users[100] = {"id": 100, "username": "sc_quota", "quota": 100, "used_quota": 0}
    quota_repo = FakeQuotaAdjustmentRepository()
    service = _service(repo, newapi, quota_adjustments=quota_repo)

    result = await service.adjust_quota(
        user_id="u-quota",
        mode="add",
        quota=50,
        reason="admin grant",
        biz_ref_type="admin_adjustment",
        biz_ref_id="grant-1",
        idempotency_key="idem-1",
    )
    duplicate = await service.adjust_quota(
        user_id="u-quota",
        mode="add",
        quota=50,
        reason="admin grant",
        biz_ref_type="admin_adjustment",
        biz_ref_id="grant-1",
        idempotency_key="idem-1",
    )

    assert result["status"] == "succeeded"
    assert result["duplicate"] is False
    assert result["quota_summary"]["quota"] == 150
    assert duplicate["duplicate"] is True
    assert newapi.users[100]["quota"] == 150
    assert [name for name, _payload in newapi.calls].count("manage_user") == 1
    manage_payload = next(payload for name, payload in newapi.calls if name == "manage_user")
    assert manage_payload["reason"] == "admin grant"
    assert manage_payload["biz_ref_type"] == "admin_adjustment"
    assert manage_payload["biz_ref_id"] == "grant-1"


@pytest.mark.asyncio
async def test_adjust_quota_usd_uses_newapi_quota_per_unit():
    existing = RelayAccount(
        user_id="u-quota-usd",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.users[100] = {"id": 100, "username": "sc_quota_usd", "quota": 0, "used_quota": 0}
    service = _service(repo, newapi)

    result = await service.adjust_quota(
        user_id="u-quota-usd",
        mode="override",
        quota_usd=2,
        reason="admin override",
        idempotency_key="idem-usd-1",
    )

    manage_payload = next(payload for name, payload in newapi.calls if name == "manage_user")
    assert manage_payload["value"] == 1_000_000
    assert result["quota"] == 1_000_000
    assert result["quota_usd"] == 2
    assert result["quota_per_usd"] == 500_000
    assert ("get_status", {}) in newapi.calls


@pytest.mark.asyncio
async def test_quota_ledger_uses_non_consume_log_types():
    existing = RelayAccount(
        user_id="u8",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi)

    result = await service.list_quota_ledger("u8", page=1, page_size=20)

    queried_types = [payload["type"] for name, payload in newapi.calls if name == "get_user_logs"]
    assert 2 not in queried_types
    assert set(queried_types) == {1, 3, 4, 6}
    assert all(payload["access_token"] == "user-access-100" for name, payload in newapi.calls if name == "get_user_logs")
    assert result["excluded_log_types"] == [2]
    assert "must-not-return" not in str(result)


@pytest.mark.asyncio
async def test_quota_ledger_parses_legacy_manage_log_quota_amounts():
    existing = RelayAccount(
        user_id="u8",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.log_items_by_type[3] = [
        {
            "id": 21,
            "user_id": 100,
            "type": 3,
            "content": "管理员增加用户额度 ＄100.000000 额度",
            "quota": 0,
            "created_at": 1710000003,
        }
    ]
    service = _service(repo, newapi)

    result = await service.list_quota_ledger("u8", page=1, page_size=20, log_types={3})

    assert result["items"][0]["quota"] == 50_000_000
    assert result["items"][0]["content"] == "管理员增加用户额度"
    assert result["items"][0]["display"]["quota_credits"] == 100.0


@pytest.mark.asyncio
async def test_quota_ledger_enriches_legacy_manage_logs_from_adjustments():
    existing = RelayAccount(
        user_id="u8",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.log_items_by_type[3] = [
        {
            "id": 21,
            "user_id": 100,
            "type": 3,
            "content": "管理员增加用户额度 ＄20.000000 额度",
            "quota": 0,
            "created_at": 1710000003,
            "other": '{"admin_info":{"admin_id":1,"admin_username":"root"}}',
        }
    ]
    quota_repo = FakeQuotaAdjustmentRepository()
    quota_repo.rows["invitee_bonus:u8"] = {
        "idempotency_key": "invitee_bonus:u8",
        "user_id": "u8",
        "newapi_user_id": 100,
        "mode": "add",
        "quota": 10_000_000,
        "reason": "邀请奖励：被邀请人开通后发放 20 积分",
        "biz_ref_type": "invite_reward",
        "biz_ref_id": "invitee_bonus:u8",
        "status": "succeeded",
        "newapi_response": {},
        "quota_summary": {},
        "error": None,
        "created_at": 1710000003,
        "updated_at": 1710000003,
    }
    service = _service(repo, newapi, quota_adjustments=quota_repo)

    result = await service.list_quota_ledger("u8", page=1, page_size=20)
    manage_log = next(item for item in result["items"] if item["type"] == 3)

    assert manage_log["content"] == "邀请奖励：被邀请人开通"
    assert manage_log["quota"] == 10_000_000
    assert manage_log["display"]["quota_credits"] == 20.0
    assert '"reason": "邀请奖励：被邀请人开通"' in manage_log["other"]
    assert '"biz_ref_type": "invite_reward"' in manage_log["other"]


@pytest.mark.asyncio
async def test_quota_ledger_does_not_enrich_mismatched_manage_logs():
    existing = RelayAccount(
        user_id="u8",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    newapi.log_items_by_type[3] = [
        {
            "id": 21,
            "user_id": 100,
            "type": 3,
            "content": "管理员增加用户额度 ＄1.000000 额度",
            "quota": 500_000,
            "created_at": 1710000003,
            "other": '{"admin_info":{"admin_id":1,"admin_username":"root"}}',
        }
    ]
    quota_repo = FakeQuotaAdjustmentRepository()
    quota_repo.rows["invitee_bonus:u8"] = {
        "idempotency_key": "invitee_bonus:u8",
        "user_id": "u8",
        "newapi_user_id": 100,
        "mode": "add",
        "quota": 10_000_000,
        "reason": "邀请奖励：被邀请人开通后发放 20 积分",
        "biz_ref_type": "invite_reward",
        "biz_ref_id": "invitee_bonus:u8",
        "status": "succeeded",
        "newapi_response": {},
        "quota_summary": {},
        "error": None,
        "created_at": 1710000003,
        "updated_at": 1710000003,
    }
    service = _service(repo, newapi, quota_adjustments=quota_repo)

    result = await service.list_quota_ledger("u8", page=1, page_size=20, log_types={3})

    assert result["items"][0]["content"] == "管理员增加用户额度"
    assert result["items"][0]["quota"] == 500_000


@pytest.mark.asyncio
async def test_usage_logs_and_summary_use_newapi_usage_sources():
    existing = RelayAccount(
        user_id="u9",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    repo = FakeRelayAccountRepository(existing)
    newapi = FakeNewAPIAccountClient()
    service = _service(repo, newapi)

    logs = await service.list_usage_logs("u9", include_errors=False)
    summary = await service.get_usage_summary(
        "u9",
        start_timestamp=1710000000,
        end_timestamp=1710003600,
        granularity="all",
        page=1,
        page_size=1,
    )

    assert logs["included_log_types"] == [2]
    assert summary["quota"] == 123
    mini_bucket = next(item for item in summary["buckets"] if item["model_name"] == "gpt-4o-mini")
    assert mini_bucket["quota"] == 200
    assert summary["model_summaries"]["total"] == 2
    assert summary["model_summaries"]["total_quota"] == 500
    assert summary["model_summaries"]["items"][0]["model_name"] == "gpt-4o"
    assert summary["model_summaries"]["items"][0]["quota"] == 300
    assert summary["model_summaries"]["items"][0]["quota_percent"] == 60.0
    assert any(name == "get_user_logs_stat" and payload["access_token"] == "user-access-100" for name, payload in newapi.calls)
    assert any(name == "list_self_quota_data" and payload["access_token"] == "user-access-100" for name, payload in newapi.calls)


@pytest.mark.asyncio
async def test_usage_summary_granularity_groups_model_buckets():
    existing = RelayAccount(
        user_id="u9",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    service = _service(FakeRelayAccountRepository(existing), FakeNewAPIAccountClient())

    summary = await service.get_usage_summary(
        "u9",
        start_timestamp=1710000000,
        end_timestamp=1710007200,
        granularity="day",
        page=1,
        page_size=20,
    )

    assert summary["granularity"] == "day"
    mini_buckets = [item for item in summary["buckets"] if item["model_name"] == "gpt-4o-mini"]
    assert len(mini_buckets) == 1
    assert mini_buckets[0]["count"] == 3
    assert mini_buckets[0]["quota"] == 200


@pytest.mark.asyncio
async def test_usage_summary_splits_long_quota_ranges_for_newapi():
    existing = RelayAccount(
        user_id="u9",
        newapi_user_id=100,
        newapi_token_id=900,
        newapi_token_key="sk-secret",
        newapi_user_access_token="user-access-100",
        has_newapi_user_access_token=True,
        group="default",
    )
    newapi = FakeNewAPIAccountClient()
    service = _service(FakeRelayAccountRepository(existing), newapi)

    await service.get_usage_summary(
        "u9",
        start_timestamp=1710000000,
        end_timestamp=1710000000 + 90 * 24 * 60 * 60,
        granularity="all",
    )

    quota_calls = [payload for name, payload in newapi.calls if name == "list_self_quota_data"]
    assert len(quota_calls) == 3
    assert all(
        payload["end_timestamp"] - payload["start_timestamp"] <= 30 * 24 * 60 * 60
        for payload in quota_calls
    )


@pytest.mark.asyncio
async def test_health_sanitizes_channel_keys():
    service = _service(FakeRelayAccountRepository(), FakeNewAPIAccountClient())

    result = await service.get_health()

    assert result["server_test"]["success"] is True
    assert result["groups"] == ["default", "premium"]
    assert result["channels"]["items"][0]["name"] == "primary"
    assert "provider-secret" not in str(result)


@pytest.mark.asyncio
async def test_health_model_channel_and_user_views():
    service = _service(FakeRelayAccountRepository(), FakeNewAPIAccountClient())

    models = await service.get_health_models()
    channels = await service.get_health_channels(model="gpt-4o-mini")
    users = await service.get_health_users(page=1, page_size=1)

    assert models["models"]["enabled"] == ["gpt-4o-mini"]
    assert channels["items"][0]["name"] == "primary"
    assert "balance" not in channels["items"][0]
    assert users["total"] == 2
    assert users["items"][0]["username"] == "sc_b"
