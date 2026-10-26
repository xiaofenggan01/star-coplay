from __future__ import annotations

import hashlib
import json
import re
import secrets
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Protocol

from common.relay import NewAPIClientError
from modules.ai_relay_account.config import (
    AIRelayConfigError,
    AIRelayConfigProvider,
)


DEFAULT_CREDENTIAL_MODE = "newapi_bound_token"
DEFAULT_TOKEN_NAME = "starcoplay-agent-runtime"
NEWAPI_EMAIL_MAX_LENGTH = 50
NEWAPI_LOG_TYPE_TOPUP = 1
NEWAPI_LOG_TYPE_CONSUME = 2
NEWAPI_LOG_TYPE_MANAGE = 3
NEWAPI_LOG_TYPE_SYSTEM = 4
NEWAPI_LOG_TYPE_ERROR = 5
NEWAPI_LOG_TYPE_REFUND = 6
NEWAPI_QUOTA_POINTS_PER_CREDIT = Decimal("500000")
QUOTA_LEDGER_ADJUSTMENT_MATCH_WINDOW_SECONDS = 120
NEWAPI_LOG_QUOTA_AMOUNT_RE = re.compile(r"[\uff04$]\s*([0-9]+(?:\.[0-9]+)?)")
GENERIC_MANAGE_LOG_PREFIXES = {
    "add": "\u7ba1\u7406\u5458\u589e\u52a0\u7528\u6237\u989d\u5ea6",
    "subtract": "\u7ba1\u7406\u5458\u51cf\u5c11\u7528\u6237\u989d\u5ea6",
    "override": "\u7ba1\u7406\u5458\u8986\u76d6\u7528\u6237\u989d\u5ea6",
}
DEFAULT_LEDGER_LOG_TYPES = frozenset(
    {
        NEWAPI_LOG_TYPE_TOPUP,
        NEWAPI_LOG_TYPE_MANAGE,
        NEWAPI_LOG_TYPE_SYSTEM,
        NEWAPI_LOG_TYPE_REFUND,
    }
)
USAGE_SUMMARY_GRANULARITIES = frozenset({"all", "hour", "day", "month"})


class AIRelayAccountError(Exception):
    def __init__(self, code: str, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.cause = cause


@dataclass(frozen=True)
class RelayAccount:
    user_id: str
    newapi_user_id: int
    group: str
    status: str = "active"
    credential_mode: str = DEFAULT_CREDENTIAL_MODE
    newapi_token_id: int | None = None
    newapi_token_key: str | None = None
    newapi_user_access_token: str | None = None
    has_newapi_user_access_token: bool = False
    preferred_models: dict[str, str] = field(default_factory=dict)
    last_sync_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_ready(self) -> bool:
        return (
            self.status == "active"
            and self.credential_mode == DEFAULT_CREDENTIAL_MODE
            and self.newapi_token_id is not None
            and bool(self.newapi_token_key)
            and self.has_newapi_user_access_token
        )


@dataclass(frozen=True)
class EnsureRelayAccountRequest:
    user_id: str
    email: str | None = None
    display_name: str | None = None
    default_group: str = "default"
    preferred_models: dict[str, str] = field(default_factory=dict)


class RelayAccountRepository(Protocol):
    async def get_by_user_id(self, user_id: str) -> RelayAccount | None: ...

    async def get_by_newapi_token_id(self, token_id: int) -> RelayAccount | None: ...

    async def upsert(self, account: RelayAccount) -> RelayAccount: ...

    async def mark_sync_error(
        self,
        user_id: str,
        *,
        newapi_user_id: int | None,
        group: str,
        error: str,
    ) -> RelayAccount: ...


class QuotaAdjustmentRepository(Protocol):
    async def get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None: ...

    async def list_succeeded_for_newapi_user(
        self,
        *,
        newapi_user_id: int,
        start_timestamp: int | None,
        end_timestamp: int | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    async def insert_pending(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        mode: str,
        quota: int,
        reason: str,
        biz_ref_type: str | None,
        biz_ref_id: str | None,
    ) -> dict[str, Any]: ...

    async def mark_succeeded(
        self,
        *,
        idempotency_key: str,
        newapi_user_id: int,
        newapi_response: Mapping[str, Any],
        quota_summary: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    async def mark_failed(self, *, idempotency_key: str, error: str) -> dict[str, Any]: ...


class NewAPIAccountClient(Protocol):
    async def get_status(self) -> Any: ...

    async def get_test_status(self) -> Any: ...

    async def list_groups(self) -> Any: ...

    async def list_dashboard_models(
        self,
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any: ...

    async def list_channel_models(self) -> Any: ...

    async def list_enabled_models(self) -> Any: ...

    async def list_channels(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        group: str | None = None,
        status: str | None = None,
        channel_type: int | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> Any: ...

    async def search_users(
        self,
        *,
        keyword: str,
        group: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any: ...

    async def create_user(self, payload: Mapping[str, Any]) -> Any: ...

    async def get_user(self, newapi_user_id: int, *, access_token: str | None = None) -> Any: ...

    async def get_self_user(
        self,
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any: ...

    async def update_user(self, payload: Mapping[str, Any]) -> Any: ...

    async def manage_user(self, payload: Mapping[str, Any]) -> Any: ...

    async def issue_user_access_token(
        self,
        *,
        username: str,
        password: str,
        user_id: int,
    ) -> Any: ...

    async def list_tokens(
        self,
        *,
        user_id: int,
        access_token: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any: ...

    async def create_token(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any: ...

    async def update_token(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any: ...

    async def delete_token(
        self,
        token_id: int,
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any: ...

    async def get_token_key(
        self,
        token_id: int,
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any: ...

    async def get_token_usage(self, token_key: str) -> Any: ...

    async def list_logs(self, params: Mapping[str, Any] | None = None) -> Any: ...

    async def get_logs_stat(self, params: Mapping[str, Any] | None = None) -> Any: ...

    async def get_user_logs(
        self,
        *,
        user_id: int,
        params: Mapping[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any: ...

    async def get_user_logs_stat(
        self,
        *,
        user_id: int,
        params: Mapping[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any: ...

    async def list_quota_data(self, params: Mapping[str, Any] | None = None) -> Any: ...

    async def list_user_quota_data(self, params: Mapping[str, Any] | None = None) -> Any: ...

    async def list_self_quota_data(
        self,
        *,
        user_id: int,
        params: Mapping[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any: ...


@dataclass
class AIRelayAccountService:
    repository: RelayAccountRepository
    newapi_client: NewAPIAccountClient
    config_provider: AIRelayConfigProvider = field(default_factory=AIRelayConfigProvider)
    quota_adjustment_repository: QuotaAdjustmentRepository | None = None
    token_name: str = DEFAULT_TOKEN_NAME

    def _require_user_access_token(self, account: RelayAccount) -> str:
        if account.newapi_user_access_token:
            return account.newapi_user_access_token
        raise AIRelayAccountError(
            "NEWAPI_USER_ACCESS_TOKEN_MISSING",
            "relay account is missing NewAPI user access token; run ensure_account to repair it",
        )

    async def ensure_account(self, request: EnsureRelayAccountRequest) -> RelayAccount:
        user_id = request.user_id.strip()
        if not user_id:
            raise AIRelayAccountError("INVALID_USER_ID", "user_id is required")
        group = await self._resolve_group(request.default_group)
        preferred_models = await self._resolve_preferences(request.preferred_models)
        existing = await self.repository.get_by_user_id(user_id)
        if existing is not None and existing.is_ready:
            return existing

        platform_username = _platform_username(user_id)
        newapi_user_id = existing.newapi_user_id if existing else 0
        if newapi_user_id <= 0:
            newapi_user_id = await self._ensure_newapi_user(
                user_id=user_id,
                username=platform_username,
                group=group,
                email=request.email,
                display_name=request.display_name,
            )
        else:
            await self._sync_newapi_user_group(newapi_user_id, group)

        try:
            user_access_token = existing.newapi_user_access_token if existing else None
            if not user_access_token:
                user_access_token = await self._ensure_newapi_user_access_token(
                    newapi_user_id=newapi_user_id,
                    username=platform_username,
                    group=group,
                    user_id=user_id,
                    email=request.email,
                    display_name=request.display_name,
                )
        except AIRelayAccountError as exc:
            await self.repository.mark_sync_error(
                user_id,
                newapi_user_id=newapi_user_id,
                group=group,
                error=f"{exc.code}: {exc.message}",
            )
            raise

        partial = RelayAccount(
            user_id=user_id,
            newapi_user_id=newapi_user_id,
            newapi_token_id=existing.newapi_token_id if existing else None,
            newapi_token_key=existing.newapi_token_key if existing else None,
            newapi_user_access_token=user_access_token,
            has_newapi_user_access_token=bool(user_access_token),
            group=group,
            preferred_models=preferred_models,
            credential_mode=DEFAULT_CREDENTIAL_MODE,
            status="syncing",
        )
        await self.repository.upsert(partial)

        try:
            token_id, token_key = await self._ensure_default_token(
                newapi_user_id=newapi_user_id,
                access_token=user_access_token,
                existing_token_id=partial.newapi_token_id,
                existing_token_key=partial.newapi_token_key,
                group=group,
            )
        except AIRelayAccountError as exc:
            await self.repository.mark_sync_error(
                user_id,
                newapi_user_id=newapi_user_id,
                group=group,
                error=f"{exc.code}: {exc.message}",
            )
            raise

        ready = RelayAccount(
            user_id=user_id,
            newapi_user_id=newapi_user_id,
            newapi_token_id=token_id,
            newapi_token_key=token_key,
            newapi_user_access_token=user_access_token,
            has_newapi_user_access_token=bool(user_access_token),
            group=group,
            preferred_models=preferred_models,
            credential_mode=DEFAULT_CREDENTIAL_MODE,
            status="active",
        )
        return await self.repository.upsert(ready)

    async def get_account(self, user_id: str) -> RelayAccount | None:
        return await self.repository.get_by_user_id(user_id)

    async def get_quota(self, user_id: str) -> dict[str, Any]:
        account = await self._require_account(user_id)
        return await self._get_quota_for_account(account)

    async def _get_quota_for_account(self, account: RelayAccount) -> dict[str, Any]:
        access_token = self._require_user_access_token(account)
        try:
            user_payload = await self.newapi_client.get_self_user(
                user_id=account.newapi_user_id,
                access_token=access_token,
            )
            token_payload = (
                await self.newapi_client.get_token_usage(account.newapi_token_key)
                if account.newapi_token_key
                else None
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_QUOTA_FETCH_FAILED") from exc

        user = _extract_data_object(user_payload)
        token_usage = _extract_data_object(token_payload)
        user_quota = _coerce_int(user.get("quota")) or 0
        used_quota = _coerce_int(user.get("used_quota")) or 0
        token_available = _coerce_int(token_usage.get("total_available"))
        token_used = _coerce_int(token_usage.get("total_used"))
        token_granted = _coerce_int(token_usage.get("total_granted"))
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "newapi_token_id": account.newapi_token_id,
            "group": user.get("group") or account.group,
            "status": account.status,
            "quota": user_quota,
            "used_quota": used_quota,
            "request_count": _coerce_int(user.get("request_count")) or 0,
            "token": {
                "id": account.newapi_token_id,
                "name": token_usage.get("name"),
                "unlimited_quota": bool(token_usage.get("unlimited_quota")),
                "total_granted": token_granted,
                "total_used": token_used,
                "total_available": token_available,
                "expires_at": token_usage.get("expires_at"),
                "model_limits_enabled": bool(token_usage.get("model_limits_enabled")),
                "model_limits": token_usage.get("model_limits") or {},
            },
            "display": {
                "balance_points": user_quota,
                "used_points": used_quota,
                "token_available_points": token_available,
                "balance_credits": _newapi_quota_to_credits(user_quota),
                "used_credits": _newapi_quota_to_credits(used_quota),
                "token_available_credits": _newapi_quota_to_credits(token_available),
            },
        }

    async def list_groups(self, *, include_disabled: bool = False) -> dict[str, Any]:
        try:
            groups_payload = await self.newapi_client.list_groups()
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_GROUPS_FETCH_FAILED") from exc
        runtime = await self.config_provider.get_runtime_config()
        newapi_groups = [str(item) for item in _extract_data_list(groups_payload) if str(item).strip()]
        allowed = sorted(runtime.allowed_groups)
        visible = [group for group in allowed if include_disabled or group in newapi_groups]
        return {
            "default_group": runtime.default_group,
            "allowed_groups": allowed,
            "newapi_groups": newapi_groups,
            "groups": visible,
        }

    async def list_tokens(
        self,
        user_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        account = await self._require_account(user_id)
        access_token = self._require_user_access_token(account)
        try:
            payload = await self.newapi_client.list_tokens(
                user_id=account.newapi_user_id,
                access_token=access_token,
                page=page,
                page_size=page_size,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_LIST_FAILED") from exc
        page_data = _extract_page(payload)
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "page": page_data["page"],
            "page_size": page_data["page_size"],
            "total": page_data["total"],
            "items": [_sanitize_token(item) for item in page_data["items"]],
        }

    async def update_token(
        self,
        token_id: int,
        *,
        status: int | None = None,
        group: str | None = None,
        quota_policy: Mapping[str, Any] | None = None,
        model_limits: Mapping[str, Any] | list[str] | str | None = None,
    ) -> dict[str, Any]:
        account, token = await self._find_account_and_token(token_id)
        access_token = self._require_user_access_token(account)
        next_group = await self._resolve_group(group or token.get("group") or account.group)
        payload = _build_token_update_payload(
            token,
            token_id=token_id,
            status=status,
            group=next_group,
            quota_policy=quota_policy,
            model_limits=model_limits,
            default_name=self.token_name,
        )
        try:
            updated_payload = await self.newapi_client.update_token(
                payload,
                user_id=account.newapi_user_id,
                access_token=access_token,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_UPDATE_FAILED") from exc

        updated = _extract_data_object(updated_payload) or {**token, **payload}
        if account.newapi_token_id == token_id and next_group != account.group:
            await self.repository.upsert(
                RelayAccount(
                    user_id=account.user_id,
                    newapi_user_id=account.newapi_user_id,
                    newapi_token_id=account.newapi_token_id,
                    newapi_token_key=account.newapi_token_key,
                    newapi_user_access_token=account.newapi_user_access_token,
                    has_newapi_user_access_token=account.has_newapi_user_access_token,
                    group=next_group,
                    preferred_models=dict(account.preferred_models),
                    credential_mode=account.credential_mode,
                    status=account.status,
                )
            )
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "token": _sanitize_token(updated),
        }

    async def delete_token(self, token_id: int, *, reason: str = "") -> dict[str, Any]:
        account, token = await self._find_account_and_token(token_id)
        access_token = self._require_user_access_token(account)
        try:
            await self.newapi_client.delete_token(
                token_id,
                user_id=account.newapi_user_id,
                access_token=access_token,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_DELETE_FAILED") from exc

        if account.newapi_token_id == token_id:
            await self.repository.upsert(
                RelayAccount(
                    user_id=account.user_id,
                    newapi_user_id=account.newapi_user_id,
                    newapi_token_id=None,
                    newapi_token_key=None,
                    newapi_user_access_token=account.newapi_user_access_token,
                    has_newapi_user_access_token=account.has_newapi_user_access_token,
                    group=account.group,
                    preferred_models=dict(account.preferred_models),
                    credential_mode=account.credential_mode,
                    status="sync_failed",
                    last_sync_error=f"default token deleted: {reason}".strip(),
                )
            )
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "token": _sanitize_token(token),
            "deleted": True,
        }

    async def adjust_quota(
        self,
        *,
        user_id: str,
        mode: str,
        reason: str,
        idempotency_key: str,
        quota: int | None = None,
        quota_usd: float | int | str | None = None,
        biz_ref_type: str | None = None,
        biz_ref_id: str | None = None,
    ) -> dict[str, Any]:
        account = await self._require_account(user_id)
        normalized_mode = _normalize_quota_mode(mode)
        quota_per_usd: int | None = None
        if quota_usd is not None:
            quota, quota_per_usd = await self._newapi_quota_from_usd(quota_usd)
        elif quota is None:
            raise AIRelayAccountError("INVALID_QUOTA", "quota or quota_usd is required")
        if quota < 0:
            raise AIRelayAccountError("INVALID_QUOTA", "quota must be >= 0")
        if normalized_mode in {"add", "subtract"} and quota <= 0:
            raise AIRelayAccountError("INVALID_QUOTA", "quota must be > 0 for add/subtract")
        idem = idempotency_key.strip()
        if not idem:
            raise AIRelayAccountError("INVALID_IDEMPOTENCY_KEY", "idempotency_key is required")
        if len(idem) > 128:
            raise AIRelayAccountError("INVALID_IDEMPOTENCY_KEY", "idempotency_key is too long")

        existing_request: dict[str, Any] | None = None
        if self.quota_adjustment_repository is not None:
            existing_request = await self.quota_adjustment_repository.get_by_idempotency_key(idem)
            if existing_request is not None:
                if _quota_adjustment_matches(existing_request, user_id, normalized_mode, quota):
                    return _with_quota_display(
                        _quota_adjustment_public(existing_request, duplicate=True),
                        quota_per_usd=quota_per_usd,
                    )
                raise AIRelayAccountError(
                    "DUPLICATE_REQUEST",
                    "idempotency_key was already used for a different quota adjustment",
                )
            await self.quota_adjustment_repository.insert_pending(
                idempotency_key=idem,
                user_id=user_id,
                mode=normalized_mode,
                quota=quota,
                reason=reason,
                biz_ref_type=biz_ref_type,
                biz_ref_id=biz_ref_id,
            )

        payload = {
            "id": account.newapi_user_id,
            "action": "add_quota",
            "mode": normalized_mode,
            "value": quota,
            "reason": reason,
            "biz_ref_type": biz_ref_type,
            "biz_ref_id": biz_ref_id,
        }
        try:
            newapi_response = await self.newapi_client.manage_user(payload)
            quota_summary = await self._get_quota_for_account(account)
        except NewAPIClientError as exc:
            if self.quota_adjustment_repository is not None:
                await self.quota_adjustment_repository.mark_failed(
                    idempotency_key=idem,
                    error=exc.message,
                )
            raise _to_service_error(exc, "NEWAPI_QUOTA_ADJUST_FAILED") from exc
        except AIRelayAccountError as exc:
            if self.quota_adjustment_repository is not None:
                await self.quota_adjustment_repository.mark_failed(
                    idempotency_key=idem,
                    error=exc.message,
                )
            raise

        if self.quota_adjustment_repository is not None:
            row = await self.quota_adjustment_repository.mark_succeeded(
                idempotency_key=idem,
                newapi_user_id=account.newapi_user_id,
                newapi_response=_dict_payload(newapi_response),
                quota_summary=quota_summary,
            )
            return _with_quota_display(
                _quota_adjustment_public(row, duplicate=False),
                quota_per_usd=quota_per_usd,
            )

        return _with_quota_display({
            "idempotency_key": idem,
            "user_id": user_id,
            "newapi_user_id": account.newapi_user_id,
            "mode": normalized_mode,
            "quota": quota,
            "reason": reason,
            "status": "succeeded",
            "duplicate": False,
            "newapi_response": _dict_payload(newapi_response),
            "quota_summary": quota_summary,
        }, quota_per_usd=quota_per_usd)

    async def list_quota_ledger(
        self,
        user_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        log_types: set[int] | None = None,
    ) -> dict[str, Any]:
        account = await self._require_account(user_id)
        selected_types = log_types or set(DEFAULT_LEDGER_LOG_TYPES)
        results: list[dict[str, Any]] = []
        total = 0
        for log_type in sorted(selected_types):
            payload = await self._fetch_user_logs(
                account,
                params={
                    "type": log_type,
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                    "p": page,
                    "page_size": page_size,
                },
                fallback_code="NEWAPI_LEDGER_FETCH_FAILED",
            )
            page_data = _extract_page(payload)
            total += page_data["total"]
            for item in page_data["items"]:
                results.append(_sanitize_log(item))
        results.sort(key=lambda item: (_coerce_int(item.get("created_at")) or 0, _coerce_int(item.get("id")) or 0), reverse=True)
        if self.quota_adjustment_repository is not None and NEWAPI_LOG_TYPE_MANAGE in selected_types:
            adjustments = await self.quota_adjustment_repository.list_succeeded_for_newapi_user(
                newapi_user_id=account.newapi_user_id,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                limit=max(100, page_size * 8),
            )
            results = _enrich_quota_ledger_with_adjustments(results, adjustments)
        trimmed = results[:page_size]
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": trimmed,
            "included_log_types": sorted(selected_types),
            "excluded_log_types": [NEWAPI_LOG_TYPE_CONSUME],
        }

    async def list_usage_logs(
        self,
        user_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        model_name: str | None = None,
        token_name: str | None = None,
        group: str | None = None,
        request_id: str | None = None,
        include_errors: bool = True,
    ) -> dict[str, Any]:
        account = await self._require_account(user_id)
        log_types = [NEWAPI_LOG_TYPE_CONSUME]
        if include_errors:
            log_types.append(NEWAPI_LOG_TYPE_ERROR)
        items: list[dict[str, Any]] = []
        total = 0
        for log_type in log_types:
            payload = await self._fetch_user_logs(
                account,
                params={
                    "type": log_type,
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                    "model_name": model_name,
                    "token_name": token_name,
                    "group": group,
                    "request_id": request_id,
                    "p": page,
                    "page_size": page_size,
                },
                fallback_code="NEWAPI_USAGE_LOGS_FETCH_FAILED",
            )
            page_data = _extract_page(payload)
            total += page_data["total"]
            items.extend(_sanitize_log(item) for item in page_data["items"])
        items.sort(key=lambda item: (_coerce_int(item.get("created_at")) or 0, _coerce_int(item.get("id")) or 0), reverse=True)
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items[:page_size],
            "included_log_types": log_types,
        }

    async def get_usage_summary(
        self,
        user_id: str,
        *,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        model_name: str | None = None,
        token_name: str | None = None,
        group: str | None = None,
        granularity: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        resolved_granularity = _normalize_usage_granularity(granularity)
        account = await self._require_account(user_id)
        params = {
            "type": NEWAPI_LOG_TYPE_CONSUME,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "model_name": model_name,
            "token_name": token_name,
            "group": group,
        }
        try:
            access_token = self._require_user_access_token(account)
            stat_payload = await self.newapi_client.get_user_logs_stat(
                user_id=account.newapi_user_id,
                access_token=access_token,
                params=params,
            )
            raw_buckets = await self._list_self_quota_data_buckets(
                account,
                access_token=access_token,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_USAGE_SUMMARY_FETCH_FAILED") from exc
        stat = _extract_data_object(stat_payload)
        quota = _coerce_int(stat.get("quota")) or 0
        scoped_buckets = _filter_quota_buckets(
            raw_buckets,
            model_name=model_name,
            token_name=token_name,
            group=group,
        )
        model_summaries = _aggregate_model_usage(scoped_buckets)
        total_model_quota = sum(_coerce_int(item.get("quota")) or 0 for item in model_summaries)
        paged_model_summaries = _paginate_items(model_summaries, page=page, page_size=page_size)
        return {
            "user_id": account.user_id,
            "newapi_user_id": account.newapi_user_id,
            "range": {
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
            "granularity": resolved_granularity,
            "quota": quota,
            "rpm": _coerce_int(stat.get("rpm")) or 0,
            "tpm": _coerce_int(stat.get("tpm")) or 0,
            "buckets": _aggregate_quota_buckets(scoped_buckets, granularity=resolved_granularity),
            "model_summaries": {
                "page": page,
                "page_size": page_size,
                "total": len(model_summaries),
                "total_quota": total_model_quota,
                "items": [
                    _with_quota_share(item, total_model_quota)
                    for item in paged_model_summaries
                ],
            },
            "display": {
                "quota_credits": _newapi_quota_to_credits(quota),
            },
        }

    async def _list_self_quota_data_buckets(
        self,
        account: RelayAccount,
        *,
        access_token: str,
        start_timestamp: int | None,
        end_timestamp: int | None,
    ) -> list[Mapping[str, Any]]:
        ranges = _split_timestamp_range(
            start_timestamp,
            end_timestamp,
            max_seconds=30 * 24 * 60 * 60,
        )
        buckets: list[Mapping[str, Any]] = []
        for start, end in ranges:
            quota_payload = await self.newapi_client.list_self_quota_data(
                user_id=account.newapi_user_id,
                access_token=access_token,
                params={
                    "start_timestamp": start,
                    "end_timestamp": end,
                },
            )
            buckets.extend(
                item for item in _extract_data_list(quota_payload)
                if isinstance(item, Mapping)
            )
        return buckets

    async def get_health(self, *, group: str | None = None) -> dict[str, Any]:
        try:
            status_payload = await self.newapi_client.get_status()
            test_status_payload = await self.newapi_client.get_test_status()
            groups_payload = await self.newapi_client.list_groups()
            enabled_models_payload = await self.newapi_client.list_enabled_models()
            channel_models_payload = await self.newapi_client.list_channel_models()
            channels_payload = await self.newapi_client.list_channels(
                page=1,
                page_size=100,
                group=group,
                sort_by="priority",
                sort_order="desc",
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_HEALTH_FETCH_FAILED") from exc

        channels_page = _extract_page(channels_payload)
        channels = [_sanitize_channel(item) for item in channels_page["items"]]
        channels_by_status: dict[str, int] = {}
        for channel in channels:
            key = str(channel.get("status") or "unknown")
            channels_by_status[key] = channels_by_status.get(key, 0) + 1

        return {
            "checked_at": int(time.time()),
            "newapi_status": _extract_data_object(status_payload),
            "server_test": _extract_health_test(test_status_payload),
            "groups": _extract_data_list(groups_payload),
            "models": {
                "enabled": _extract_data_list(enabled_models_payload),
                "channel_catalog": _extract_data_list(channel_models_payload),
            },
            "channels": {
                "total": channels_page["total"],
                "sample_size": len(channels),
                "by_status": channels_by_status,
                "items": channels,
            },
        }

    async def get_health_models(self, *, group: str | None = None) -> dict[str, Any]:
        health = await self.get_health(group=group)
        return {
            "checked_at": health["checked_at"],
            "group": group,
            "groups": health["groups"],
            "models": health["models"],
        }

    async def get_health_channels(
        self,
        *,
        group: str | None = None,
        model: str | None = None,
        include_balance: bool = False,
    ) -> dict[str, Any]:
        try:
            payload = await self.newapi_client.list_channels(
                page=1,
                page_size=100,
                group=group,
                sort_by="priority",
                sort_order="desc",
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_CHANNELS_FETCH_FAILED") from exc
        page_data = _extract_page(payload)
        channels = []
        for item in page_data["items"]:
            channel = _sanitize_channel(item)
            if model and model not in str(channel.get("models") or ""):
                continue
            if not include_balance:
                channel.pop("balance", None)
                channel.pop("balance_updated_time", None)
            channels.append(channel)
        return {
            "checked_at": int(time.time()),
            "group": group,
            "model": model,
            "total": page_data["total"],
            "items": channels,
        }

    async def get_health_users(
        self,
        *,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        granularity: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        if granularity and granularity not in {"hour", "day", "month"}:
            raise AIRelayAccountError("INVALID_GRANULARITY", "granularity must be hour/day/month")
        try:
            payload = await self.newapi_client.list_user_quota_data(
                {
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                }
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_USER_USAGE_FETCH_FAILED") from exc
        rows = [_sanitize_quota_bucket(item) | {"username": item.get("username")} for item in _extract_data_list(payload) if isinstance(item, dict)]
        aggregate: dict[str, dict[str, Any]] = {}
        for row in rows:
            username = str(row.get("username") or "")
            if not username:
                continue
            current = aggregate.setdefault(
                username,
                {"username": username, "count": 0, "quota": 0, "token_used": 0},
            )
            current["count"] += _coerce_int(row.get("count")) or 0
            current["quota"] += _coerce_int(row.get("quota")) or 0
            current["token_used"] += _coerce_int(row.get("token_used")) or 0
        items = sorted(aggregate.values(), key=lambda item: item["quota"], reverse=True)
        offset = (page - 1) * page_size
        return {
            "checked_at": int(time.time()),
            "range": {"start_timestamp": start_timestamp, "end_timestamp": end_timestamp},
            "granularity": granularity or "hour",
            "page": page,
            "page_size": page_size,
            "total": len(items),
            "items": items[offset : offset + page_size],
        }

    async def update_account(
        self,
        user_id: str,
        *,
        group: str | None = None,
        preferred_models: Mapping[str, str] | None = None,
        status: str | None = None,
    ) -> RelayAccount:
        existing = await self.repository.get_by_user_id(user_id)
        if existing is None:
            raise AIRelayAccountError("ACCOUNT_NOT_FOUND", "relay account not found")

        next_group = await self._resolve_group(group or existing.group)
        if preferred_models is not None:
            next_preferences = await self._resolve_preferences(preferred_models)
        else:
            next_preferences = dict(existing.preferred_models)
        next_status = (status or existing.status).strip()
        if next_status not in {"active", "disabled", "syncing", "sync_failed"}:
            raise AIRelayAccountError("INVALID_STATUS", "invalid relay account status")

        if next_group != existing.group:
            await self._sync_newapi_user_group(existing.newapi_user_id, next_group)
            if existing.newapi_token_id is not None:
                access_token = self._require_user_access_token(existing)
                await self._sync_newapi_token_group(
                    existing.newapi_user_id,
                    existing.newapi_token_id,
                    next_group,
                    access_token=access_token,
                )

        updated = RelayAccount(
            user_id=existing.user_id,
            newapi_user_id=existing.newapi_user_id,
            newapi_token_id=existing.newapi_token_id,
            newapi_token_key=existing.newapi_token_key,
            newapi_user_access_token=existing.newapi_user_access_token,
            has_newapi_user_access_token=existing.has_newapi_user_access_token,
            group=next_group,
            preferred_models=next_preferences,
            credential_mode=existing.credential_mode,
            status=next_status,
        )
        return await self.repository.upsert(updated)

    async def _resolve_group(self, group: str | None) -> str:
        try:
            return await self.config_provider.resolve_group(group)
        except AIRelayConfigError as exc:
            raise AIRelayAccountError(exc.code, exc.message) from exc

    async def _resolve_preferences(self, preferred_models: Mapping[str, str] | None) -> dict[str, str]:
        try:
            return await self.config_provider.resolve_preferences(preferred_models)
        except AIRelayConfigError as exc:
            raise AIRelayAccountError(exc.code, exc.message) from exc

    async def disable_account(self, user_id: str) -> RelayAccount:
        existing = await self.repository.get_by_user_id(user_id)
        if existing is None:
            raise AIRelayAccountError("ACCOUNT_NOT_FOUND", "relay account not found")
        try:
            await self.newapi_client.manage_user(
                {"id": existing.newapi_user_id, "action": "disable"}
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_SYNC_FAILED") from exc
        disabled = RelayAccount(
            user_id=existing.user_id,
            newapi_user_id=existing.newapi_user_id,
            newapi_token_id=existing.newapi_token_id,
            newapi_token_key=existing.newapi_token_key,
            newapi_user_access_token=existing.newapi_user_access_token,
            has_newapi_user_access_token=existing.has_newapi_user_access_token,
            group=existing.group,
            preferred_models=dict(existing.preferred_models),
            credential_mode=existing.credential_mode,
            status="disabled",
        )
        return await self.repository.upsert(disabled)

    async def _require_account(self, user_id: str) -> RelayAccount:
        account = await self.repository.get_by_user_id(user_id)
        if account is None:
            raise AIRelayAccountError("ACCOUNT_NOT_FOUND", "relay account not found")
        return account

    async def _find_account_and_token(self, token_id: int) -> tuple[RelayAccount, dict[str, Any]]:
        if token_id <= 0:
            raise AIRelayAccountError("INVALID_TOKEN", "token_id must be positive")
        finder = getattr(self.repository, "get_by_newapi_token_id", None)
        if finder is None:
            raise AIRelayAccountError("TOKEN_LOOKUP_UNAVAILABLE", "token lookup requires repository token index")
        account = await finder(token_id)
        if account is None:
            raise AIRelayAccountError("INVALID_TOKEN", "relay token not found")
        access_token = self._require_user_access_token(account)
        try:
            payload = await self.newapi_client.list_tokens(
                user_id=account.newapi_user_id,
                access_token=access_token,
                page=1,
                page_size=100,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_LIST_FAILED") from exc
        token = next(
            (item for item in _extract_items(payload) if _coerce_int(item.get("id")) == token_id),
            None,
        )
        if token is None:
            raise AIRelayAccountError("INVALID_TOKEN", "relay token not found in NewAPI")
        return account, token

    async def _fetch_user_logs(
        self,
        account: RelayAccount,
        *,
        params: Mapping[str, Any],
        fallback_code: str,
    ) -> Any:
        access_token = self._require_user_access_token(account)
        try:
            return await self.newapi_client.get_user_logs(
                user_id=account.newapi_user_id,
                access_token=access_token,
                params=params,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, fallback_code) from exc

    async def _ensure_newapi_user(
        self,
        *,
        user_id: str,
        username: str,
        group: str,
        email: str | None,
        display_name: str | None,
    ) -> int:
        found = await self._find_newapi_user(username)
        if found is not None:
            return found

        initial_quota = await self._initial_user_quota()
        payload = {
            "username": username,
            "password": _random_password(),
            "display_name": _display_name(user_id, display_name),
            "role": 1,
            "email": _newapi_email(email),
            "group": group,
            "quota": initial_quota,
            "remark": f"starcoplay_user_id={user_id}",
        }
        try:
            await self.newapi_client.create_user(payload)
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_USER_CREATE_FAILED") from exc

        found = await self._find_newapi_user(username)
        if found is None:
            raise AIRelayAccountError(
                "NEWAPI_USER_ID_UNRESOLVED",
                "NewAPI user was created but id could not be resolved",
            )
        if initial_quota > 0:
            await self._ensure_user_min_quota(found, initial_quota)
        await self._sync_newapi_user_group(found, group)
        return found

    async def _initial_user_quota(self) -> int:
        try:
            quota_usd = await self.config_provider.get_initial_user_quota_usd()
        except AIRelayConfigError:
            raise
        quota, _quota_per_usd = await self._newapi_quota_from_usd(quota_usd)
        return quota

    async def _newapi_quota_from_usd(self, amount: float | int | str) -> tuple[int, int]:
        quota_per_usd = await self._newapi_quota_per_usd()
        return _newapi_quota_from_usd(amount, quota_per_usd), quota_per_usd

    async def _newapi_quota_per_usd(self) -> int:
        try:
            payload = await self.newapi_client.get_status()
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_QUOTA_RATE_FETCH_FAILED") from exc
        status = _extract_data_object(payload)
        quota_per_usd = _coerce_int(status.get("quota_per_unit"))
        if quota_per_usd is None or quota_per_usd <= 0:
            raise AIRelayAccountError(
                "NEWAPI_QUOTA_RATE_MISSING",
                "NewAPI status did not include a positive quota_per_unit",
            )
        return quota_per_usd

    async def _ensure_user_min_quota(self, newapi_user_id: int, target_quota: int) -> None:
        try:
            payload = await self.newapi_client.get_user(newapi_user_id)
            current_quota = _coerce_int(_extract_data_object(payload).get("quota")) or 0
            if current_quota >= target_quota:
                return
            await self.newapi_client.manage_user(
                {
                    "id": newapi_user_id,
                    "action": "add_quota",
                    "mode": "add",
                    "value": target_quota - current_quota,
                }
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_INITIAL_QUOTA_GRANT_FAILED") from exc

    async def _find_newapi_user(self, username: str) -> int | None:
        try:
            payload = await self.newapi_client.search_users(keyword=username, page=1, page_size=10)
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_USER_SEARCH_FAILED") from exc
        for item in _extract_items(payload):
            if str(item.get("username", "")).strip() == username:
                user_id = item.get("id")
                if isinstance(user_id, int):
                    return user_id
                if isinstance(user_id, str) and user_id.isdigit():
                    return int(user_id)
        return None

    async def _ensure_newapi_user_access_token(
        self,
        *,
        newapi_user_id: int,
        username: str,
        group: str,
        user_id: str,
        email: str | None,
        display_name: str | None,
    ) -> str:
        password = _random_password()
        try:
            current = await self.newapi_client.get_user(newapi_user_id)
            data = current.get("data") if isinstance(current, dict) else {}
            if not isinstance(data, dict):
                data = {}
            payload = {
                "id": newapi_user_id,
                "username": data.get("username") or username,
                "password": password,
                "display_name": data.get("display_name") or _display_name(user_id, display_name),
                "role": data.get("role") or 1,
                "status": data.get("status") or 1,
                "email": _newapi_email(data.get("email") or email),
                "group": group,
                "quota": data.get("quota") or 0,
                "remark": data.get("remark") or f"starcoplay_user_id={user_id}",
            }
            await self.newapi_client.update_user(payload)
            token_payload = await self.newapi_client.issue_user_access_token(
                username=str(payload["username"]),
                password=password,
                user_id=newapi_user_id,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_USER_ACCESS_TOKEN_FAILED") from exc
        token = _extract_string_data(token_payload)
        if not token:
            raise AIRelayAccountError(
                "NEWAPI_USER_ACCESS_TOKEN_MISSING",
                "NewAPI user access token response did not include a token",
            )
        return token

    async def _ensure_default_token(
        self,
        *,
        newapi_user_id: int,
        access_token: str,
        existing_token_id: int | None,
        existing_token_key: str | None,
        group: str,
    ) -> tuple[int, str]:
        if existing_token_id is not None and existing_token_key:
            return existing_token_id, existing_token_key

        token = await self._find_default_token(newapi_user_id, access_token=access_token)
        if token is None:
            token = await self._create_default_token(newapi_user_id, access_token, group)
        token_id = _require_int(token.get("id"), "NEWAPI_TOKEN_ID_MISSING")
        token_key = token.get("key")
        if not isinstance(token_key, str) or _looks_masked_token_key(token_key):
            token_key = await self._fetch_token_key(newapi_user_id, access_token, token_id)
        return token_id, token_key

    async def _find_default_token(self, newapi_user_id: int, *, access_token: str) -> dict[str, Any] | None:
        try:
            payload = await self.newapi_client.list_tokens(
                user_id=newapi_user_id,
                access_token=access_token,
                page=1,
                page_size=100,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_LIST_FAILED") from exc
        for item in _extract_items(payload):
            if item.get("name") == self.token_name:
                return item
        return None

    async def _create_default_token(
        self,
        newapi_user_id: int,
        access_token: str,
        group: str,
    ) -> dict[str, Any]:
        payload = {
            "name": self.token_name,
            "expired_time": -1,
            "remain_quota": 0,
            "unlimited_quota": True,
            "model_limits_enabled": False,
            "model_limits": "",
            "group": group,
        }
        try:
            await self.newapi_client.create_token(payload, user_id=newapi_user_id, access_token=access_token)
            listed = await self._find_default_token(newapi_user_id, access_token=access_token)
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_CREATE_FAILED") from exc
        if listed is None:
            raise AIRelayAccountError(
                "NEWAPI_TOKEN_ID_UNRESOLVED",
                "NewAPI token was created but id could not be resolved",
            )
        return listed

    async def _fetch_token_key(self, newapi_user_id: int, access_token: str, token_id: int) -> str:
        try:
            payload = await self.newapi_client.get_token_key(
                token_id,
                user_id=newapi_user_id,
                access_token=access_token,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_KEY_FETCH_FAILED") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        key = data.get("key") if isinstance(data, dict) else None
        if not isinstance(key, str) or not key:
            raise AIRelayAccountError(
                "NEWAPI_TOKEN_KEY_MISSING",
                "NewAPI token key response did not include a usable key",
            )
        return key

    async def _sync_newapi_user_group(self, newapi_user_id: int, group: str) -> None:
        try:
            current = await self.newapi_client.get_user(newapi_user_id)
            data = current.get("data") if isinstance(current, dict) else {}
            if not isinstance(data, dict):
                data = {}
            payload = {
                "id": newapi_user_id,
                "username": data.get("username") or f"relay_user_{newapi_user_id}",
                "password": "",
                "display_name": data.get("display_name") or data.get("username") or f"Relay User {newapi_user_id}",
                "role": data.get("role") or 1,
                "group": group,
                "remark": data.get("remark") or "",
            }
            await self.newapi_client.update_user(payload)
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_USER_UPDATE_FAILED") from exc

    async def _sync_newapi_token_group(
        self,
        newapi_user_id: int,
        token_id: int,
        group: str,
        *,
        access_token: str,
    ) -> None:
        try:
            tokens = await self.newapi_client.list_tokens(
                user_id=newapi_user_id,
                access_token=access_token,
                page=1,
                page_size=100,
            )
            token = next(
                (
                    item
                    for item in _extract_items(tokens)
                    if _coerce_int(item.get("id")) == token_id
                ),
                None,
            )
            if token is None:
                raise AIRelayAccountError("NEWAPI_TOKEN_NOT_FOUND", "relay token not found in NewAPI")
            payload = {
                "id": token_id,
                "name": token.get("name") or self.token_name,
                "status": token.get("status", 1),
                "expired_time": token.get("expired_time", -1),
                "remain_quota": token.get("remain_quota", 0),
                "unlimited_quota": token.get("unlimited_quota", True),
                "model_limits_enabled": token.get("model_limits_enabled", False),
                "model_limits": token.get("model_limits", ""),
                "allow_ips": token.get("allow_ips", ""),
                "group": group,
                "cross_group_retry": token.get("cross_group_retry", False),
            }
            await self.newapi_client.update_token(
                payload,
                user_id=newapi_user_id,
                access_token=access_token,
            )
        except NewAPIClientError as exc:
            raise _to_service_error(exc, "NEWAPI_TOKEN_UPDATE_FAILED") from exc


class DbRelayAccountRepository:
    def __init__(self, db_pool: Any) -> None:
        self._db_pool = db_pool

    async def get_by_user_id(self, user_id: str) -> RelayAccount | None:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, newapi_user_id, newapi_token_id, newapi_token_key,
                       newapi_user_access_token,
                       "group", preferred_models, credential_mode, status,
                       last_sync_error, created_at, updated_at
                FROM public.ai_relay_accounts
                WHERE user_id = $1
                LIMIT 1
                """,
                user_id,
            )
        if row is None:
            return None
        return _row_to_account(row)

    async def get_account(self, user_id: str) -> RelayAccount | None:
        return await self.get_by_user_id(user_id)

    async def get_by_newapi_token_id(self, token_id: int) -> RelayAccount | None:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, newapi_user_id, newapi_token_id, newapi_token_key,
                       newapi_user_access_token,
                       "group", preferred_models, credential_mode, status,
                       last_sync_error, created_at, updated_at
                FROM public.ai_relay_accounts
                WHERE newapi_token_id = $1
                LIMIT 1
                """,
                token_id,
            )
        if row is None:
            return None
        return _row_to_account(row)

    async def upsert(self, account: RelayAccount) -> RelayAccount:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.ai_relay_accounts (
                    user_id, newapi_user_id, newapi_token_id, newapi_token_key,
                    newapi_user_access_token,
                    "group", preferred_models, credential_mode, status, last_sync_error
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)
                ON CONFLICT (user_id) DO UPDATE SET
                    newapi_user_id = EXCLUDED.newapi_user_id,
                    newapi_token_id = EXCLUDED.newapi_token_id,
                    newapi_token_key = EXCLUDED.newapi_token_key,
                    newapi_user_access_token = EXCLUDED.newapi_user_access_token,
                    "group" = EXCLUDED."group",
                    preferred_models = EXCLUDED.preferred_models,
                    credential_mode = EXCLUDED.credential_mode,
                    status = EXCLUDED.status,
                    last_sync_error = EXCLUDED.last_sync_error,
                    updated_at = now()
                RETURNING user_id, newapi_user_id, newapi_token_id, newapi_token_key,
                          newapi_user_access_token,
                          "group", preferred_models, credential_mode, status,
                          last_sync_error, created_at, updated_at
                """,
                account.user_id,
                account.newapi_user_id,
                account.newapi_token_id,
                account.newapi_token_key,
                account.newapi_user_access_token,
                account.group,
                json.dumps(account.preferred_models),
                account.credential_mode,
                account.status,
                account.last_sync_error,
            )
        return _row_to_account(row)

    async def mark_sync_error(
        self,
        user_id: str,
        *,
        newapi_user_id: int | None,
        group: str,
        error: str,
    ) -> RelayAccount:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.ai_relay_accounts (
                    user_id, newapi_user_id, "group", credential_mode, status, last_sync_error
                )
                VALUES ($1, $2, $3, $4, 'sync_failed', $5)
                ON CONFLICT (user_id) DO UPDATE SET
                    newapi_user_id = COALESCE(EXCLUDED.newapi_user_id, ai_relay_accounts.newapi_user_id),
                    "group" = EXCLUDED."group",
                    status = 'sync_failed',
                    last_sync_error = EXCLUDED.last_sync_error,
                    updated_at = now()
                RETURNING user_id, newapi_user_id, newapi_token_id, newapi_token_key,
                          newapi_user_access_token,
                          "group", preferred_models, credential_mode, status,
                          last_sync_error, created_at, updated_at
                """,
                user_id,
                newapi_user_id,
                group,
                DEFAULT_CREDENTIAL_MODE,
                error[:1000],
            )
        return _row_to_account(row)


class DbQuotaAdjustmentRepository:
    def __init__(self, db_pool: Any) -> None:
        self._db_pool = db_pool

    async def get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT idempotency_key, user_id, newapi_user_id, mode, quota, reason,
                       biz_ref_type, biz_ref_id, status, newapi_response, quota_summary,
                       error, created_at, updated_at
                FROM public.ai_relay_quota_adjustments
                WHERE idempotency_key = $1
                LIMIT 1
                """,
                idempotency_key,
            )
        return _quota_adjustment_row(row) if row is not None else None

    async def list_succeeded_for_newapi_user(
        self,
        *,
        newapi_user_id: int,
        start_timestamp: int | None,
        end_timestamp: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        safe_limit = max(1, min(int(limit), 1000))
        async with self._db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT idempotency_key, user_id, newapi_user_id, mode, quota, reason,
                       biz_ref_type, biz_ref_id, status, newapi_response, quota_summary,
                       error, created_at, updated_at
                FROM public.ai_relay_quota_adjustments
                WHERE newapi_user_id = $1
                  AND status = 'succeeded'
                  AND ($2::bigint IS NULL OR EXTRACT(EPOCH FROM created_at)::bigint >= $2 - $4)
                  AND ($3::bigint IS NULL OR EXTRACT(EPOCH FROM created_at)::bigint <= $3 + $4)
                ORDER BY created_at DESC
                LIMIT $5
                """,
                newapi_user_id,
                start_timestamp,
                end_timestamp,
                QUOTA_LEDGER_ADJUSTMENT_MATCH_WINDOW_SECONDS,
                safe_limit,
            )
        return [_quota_adjustment_row(row) for row in rows]

    async def insert_pending(
        self,
        *,
        idempotency_key: str,
        user_id: str,
        mode: str,
        quota: int,
        reason: str,
        biz_ref_type: str | None,
        biz_ref_id: str | None,
    ) -> dict[str, Any]:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.ai_relay_quota_adjustments (
                    idempotency_key, user_id, mode, quota, reason, biz_ref_type,
                    biz_ref_id, status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
                RETURNING idempotency_key, user_id, newapi_user_id, mode, quota, reason,
                          biz_ref_type, biz_ref_id, status, newapi_response, quota_summary,
                          error, created_at, updated_at
                """,
                idempotency_key,
                user_id,
                mode,
                quota,
                reason,
                biz_ref_type,
                biz_ref_id,
            )
        return _quota_adjustment_row(row)

    async def mark_succeeded(
        self,
        *,
        idempotency_key: str,
        newapi_user_id: int,
        newapi_response: Mapping[str, Any],
        quota_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.ai_relay_quota_adjustments
                SET newapi_user_id = $2,
                    status = 'succeeded',
                    newapi_response = $3::jsonb,
                    quota_summary = $4::jsonb,
                    error = NULL,
                    updated_at = now()
                WHERE idempotency_key = $1
                RETURNING idempotency_key, user_id, newapi_user_id, mode, quota, reason,
                          biz_ref_type, biz_ref_id, status, newapi_response, quota_summary,
                          error, created_at, updated_at
                """,
                idempotency_key,
                newapi_user_id,
                json.dumps(dict(newapi_response)),
                json.dumps(dict(quota_summary)),
            )
        if row is None:
            raise AIRelayAccountError("QUOTA_ADJUSTMENT_NOT_FOUND", "quota adjustment request not found")
        return _quota_adjustment_row(row)

    async def mark_failed(self, *, idempotency_key: str, error: str) -> dict[str, Any]:
        if self._db_pool is None:
            raise AIRelayAccountError("DB_UNAVAILABLE", "database not configured")
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public.ai_relay_quota_adjustments
                SET status = 'failed',
                    error = $2,
                    updated_at = now()
                WHERE idempotency_key = $1
                RETURNING idempotency_key, user_id, newapi_user_id, mode, quota, reason,
                          biz_ref_type, biz_ref_id, status, newapi_response, quota_summary,
                          error, created_at, updated_at
                """,
                idempotency_key,
                error[:1000],
            )
        if row is None:
            raise AIRelayAccountError("QUOTA_ADJUSTMENT_NOT_FOUND", "quota adjustment request not found")
        return _quota_adjustment_row(row)


def _row_to_account(row: Any) -> RelayAccount:
    preferred_models = row["preferred_models"] or {}
    if isinstance(preferred_models, str):
        preferred_models = json.loads(preferred_models)
    return RelayAccount(
        user_id=row["user_id"],
        newapi_user_id=row["newapi_user_id"],
        newapi_token_id=row["newapi_token_id"],
        newapi_token_key=row["newapi_token_key"],
        newapi_user_access_token=row["newapi_user_access_token"],
        has_newapi_user_access_token=bool(row["newapi_user_access_token"]),
        group=row["group"],
        preferred_models=dict(preferred_models),
        credential_mode=row["credential_mode"],
        status=row["status"],
        last_sync_error=row["last_sync_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _quota_adjustment_row(row: Any) -> dict[str, Any]:
    def _json_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value

    return {
        "idempotency_key": row["idempotency_key"],
        "user_id": row["user_id"],
        "newapi_user_id": row["newapi_user_id"],
        "mode": row["mode"],
        "quota": row["quota"],
        "reason": row["reason"],
        "biz_ref_type": row["biz_ref_type"],
        "biz_ref_id": row["biz_ref_id"],
        "status": row["status"],
        "newapi_response": _json_value(row["newapi_response"]) or {},
        "quota_summary": _json_value(row["quota_summary"]) or {},
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _normalize_group(group: str | None) -> str:
    value = (group or "default").strip()
    if not value:
        value = "default"
    if len(value) > 64:
        raise AIRelayAccountError("INVALID_GROUP", "group is too long")
    return value


def _platform_username(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return f"sc_{digest}"


def _display_name(user_id: str, display_name: str | None) -> str:
    value = (display_name or "").strip()
    if value:
        return value[:20]
    return f"SC {user_id[:12]}"[:20]


def _newapi_email(email: str | None) -> str:
    value = (email or "").strip()
    if not value:
        return ""
    if len(value) > NEWAPI_EMAIL_MAX_LENGTH:
        return ""
    return value


def _random_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20))


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        if isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _extract_data_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return dict(data)
    return {}


def _extract_string_data(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    return data.strip() if isinstance(data, str) else ""


def _extract_data_list(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data.get("data"), list):
            return data["data"]
    return []


def _extract_page(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"page": 1, "page_size": 0, "total": 0, "items": []}
    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items")
        if not isinstance(items, list):
            items = data.get("data") if isinstance(data.get("data"), list) else []
        return {
            "page": _coerce_int(data.get("page")) or 1,
            "page_size": _coerce_int(data.get("page_size")) or len(items),
            "total": _coerce_int(data.get("total")) or len(items),
            "items": [item for item in items if isinstance(item, dict)],
        }
    if isinstance(data, list):
        return {"page": 1, "page_size": len(data), "total": len(data), "items": [item for item in data if isinstance(item, dict)]}
    return {"page": 1, "page_size": 0, "total": 0, "items": []}


def _sanitize_log(item: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "user_id",
        "created_at",
        "type",
        "content",
        "username",
        "token_name",
        "model_name",
        "quota",
        "prompt_tokens",
        "completion_tokens",
        "use_time",
        "is_stream",
        "channel",
        "channel_name",
        "token_id",
        "group",
        "request_id",
        "upstream_request_id",
        "other",
    }
    result = {key: item.get(key) for key in allowed if key in item}
    quota = _coerce_int(result.get("quota"))
    if _coerce_int(result.get("type")) == NEWAPI_LOG_TYPE_MANAGE and (quota is None or quota == 0):
        mode = _quota_log_mode(result)
        parsed_quota = _quota_from_manage_log_content(_trimmed_str(result.get("content")), mode or "")
        if parsed_quota is not None:
            result["quota"] = parsed_quota
            quota = parsed_quota
    if _coerce_int(result.get("type")) == NEWAPI_LOG_TYPE_MANAGE:
        cleaned_content = _quota_log_display_content(_trimmed_str(result.get("content")))
        if cleaned_content:
            result["content"] = cleaned_content
    if quota is not None:
        result["display"] = {"quota_credits": _newapi_quota_to_credits(quota)}
    return result


def _enrich_quota_ledger_with_adjustments(
    items: list[dict[str, Any]],
    adjustments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not adjustments:
        return items
    enriched: list[dict[str, Any]] = []
    used_adjustments: set[str] = set()
    for item in items:
        if _coerce_int(item.get("type")) != NEWAPI_LOG_TYPE_MANAGE:
            enriched.append(item)
            continue
        adjustment = _find_quota_adjustment_for_log(item, adjustments, used_adjustments)
        if adjustment is None:
            enriched.append(item)
            continue
        key = str(adjustment.get("idempotency_key") or "")
        if key:
            used_adjustments.add(key)
        enriched.append(_apply_quota_adjustment_to_log(item, adjustment))
    return enriched


def _find_quota_adjustment_for_log(
    item: Mapping[str, Any],
    adjustments: list[dict[str, Any]],
    used_adjustments: set[str],
) -> dict[str, Any] | None:
    log_timestamp = _coerce_timestamp(item.get("created_at"))
    log_mode = _quota_log_mode(item)
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for adjustment in adjustments:
        key = str(adjustment.get("idempotency_key") or "")
        if key and key in used_adjustments:
            continue
        if not _quota_log_user_matches_adjustment(item, adjustment):
            continue
        adjustment_mode = str(adjustment.get("mode") or "")
        if log_mode and adjustment_mode and log_mode != adjustment_mode:
            continue
        adjustment_timestamp = _quota_adjustment_timestamp(adjustment)
        if log_timestamp is not None and adjustment_timestamp is not None:
            timestamp_delta = abs(log_timestamp - adjustment_timestamp)
            if timestamp_delta > QUOTA_LEDGER_ADJUSTMENT_MATCH_WINDOW_SECONDS:
                continue
        else:
            timestamp_delta = QUOTA_LEDGER_ADJUSTMENT_MATCH_WINDOW_SECONDS
        quota_match_rank = _quota_log_adjustment_match_rank(item, adjustment, log_mode)
        if quota_match_rank is None:
            continue
        candidates.append((quota_match_rank, timestamp_delta, adjustment))
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    return candidates[0][2]


def _quota_log_user_matches_adjustment(item: Mapping[str, Any], adjustment: Mapping[str, Any]) -> bool:
    log_user_id = _coerce_int(item.get("user_id"))
    adjustment_user_id = _coerce_int(adjustment.get("newapi_user_id"))
    return log_user_id is None or adjustment_user_id is None or log_user_id == adjustment_user_id


def _quota_log_adjustment_match_rank(
    item: Mapping[str, Any],
    adjustment: Mapping[str, Any],
    log_mode: str | None,
) -> int | None:
    expected_quota = _coerce_int(adjustment.get("quota"))
    if expected_quota is None:
        return None
    actual_quota = _quota_log_match_quota(item, str(adjustment.get("mode") or ""))
    if actual_quota is not None:
        if str(adjustment.get("mode") or "") == "subtract":
            return 0 if abs(actual_quota) == expected_quota else None
        return 0 if actual_quota == expected_quota else None
    if log_mode == "override" and str(adjustment.get("mode") or "") == "override":
        return 1
    return None


def _apply_quota_adjustment_to_log(item: Mapping[str, Any], adjustment: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    reason = _quota_reason_display_text(_trimmed_str(adjustment.get("reason")))
    content = _trimmed_str(result.get("content"))
    quota = _quota_for_enriched_log(result, adjustment)
    if reason and _should_replace_quota_log_content(content, reason):
        result["content"] = reason
    if quota is not None:
        result["quota"] = quota
        result["display"] = {
            **(result["display"] if isinstance(result.get("display"), dict) else {}),
            "quota_credits": _newapi_quota_to_credits(quota),
        }
    result["other"] = _merge_quota_log_admin_info(result.get("other"), adjustment)
    return result


def _should_replace_quota_log_content(content: str, reason: str) -> bool:
    if not content:
        return True
    if reason in content:
        return False
    return _quota_log_mode({"content": content}) is not None


def _quota_log_display_content(content: str) -> str:
    if not content:
        return ""
    if "\uff1a" in content:
        prefix, rest = content.split("\uff1a", 1)
        cleaned = _quota_reason_display_text(content)
        if cleaned != content:
            return cleaned
        cleaned_prefix = _quota_reason_display_text(prefix)
        cleaned_rest = _quota_log_display_content(rest)
        if cleaned_prefix and _quota_log_action_text(rest):
            return cleaned_prefix
        return cleaned_rest
    return _quota_log_action_text(content) or _quota_reason_display_text(content)


def _quota_log_action_text(content: str) -> str:
    for prefix in GENERIC_MANAGE_LOG_PREFIXES.values():
        if content.startswith(prefix):
            return prefix
    return ""


def _quota_reason_display_text(reason: str) -> str:
    if not reason:
        return ""
    result = re.sub(r"\u540e\u53d1\u653e\s*[0-9]+(?:\.[0-9]+)?\s*\u79ef\u5206$", "", reason).rstrip("\uff1a: ，,")
    result = re.sub(r"\s*[\uff04$]\s*[0-9]+(?:\.[0-9]+)?\s*$", "", result).rstrip("\uff1a: ，,")
    return result


def _quota_for_enriched_log(item: Mapping[str, Any], adjustment: Mapping[str, Any]) -> int | None:
    current_quota = _coerce_int(item.get("quota"))
    if current_quota is not None and current_quota != 0:
        return current_quota
    parsed_quota = _quota_from_manage_log_content(_trimmed_str(item.get("content")), str(adjustment.get("mode") or ""))
    if parsed_quota is not None and parsed_quota != 0:
        return parsed_quota
    adjustment_quota = _coerce_int(adjustment.get("quota"))
    if adjustment_quota is None:
        return current_quota
    mode = str(adjustment.get("mode") or "")
    if mode == "add":
        return adjustment_quota
    if mode == "subtract":
        return -adjustment_quota
    return current_quota


def _quota_log_match_quota(item: Mapping[str, Any], mode: str) -> int | None:
    current_quota = _coerce_int(item.get("quota"))
    if current_quota is not None and current_quota != 0:
        return current_quota
    return _quota_from_manage_log_content(_trimmed_str(item.get("content")), mode)


def _quota_from_manage_log_content(content: str, mode: str) -> int | None:
    if not content:
        return None
    amounts = [Decimal(match.group(1)) for match in NEWAPI_LOG_QUOTA_AMOUNT_RE.finditer(content)]
    if not amounts:
        return None
    if mode == "override" and len(amounts) >= 2:
        return _quota_amount_to_points(amounts[-1] - amounts[0])
    quota = _quota_amount_to_points(amounts[0])
    return -quota if mode == "subtract" else quota


def _quota_amount_to_points(amount: Decimal) -> int:
    return int((amount * NEWAPI_QUOTA_POINTS_PER_CREDIT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _quota_log_mode(item: Mapping[str, Any]) -> str | None:
    content = _trimmed_str(item.get("content"))
    for mode, prefix in GENERIC_MANAGE_LOG_PREFIXES.items():
        if content.startswith(prefix):
            return mode
    quota = _coerce_int(item.get("quota"))
    if quota is not None and quota < 0:
        return "subtract"
    return None


def _quota_adjustment_timestamp(adjustment: Mapping[str, Any]) -> int | None:
    value = adjustment.get("created_at")
    if isinstance(value, datetime):
        return int(value.timestamp())
    timestamp = _coerce_timestamp(value)
    if timestamp is not None:
        return timestamp
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _merge_quota_log_admin_info(value: Any, adjustment: Mapping[str, Any]) -> Any:
    info = {
        key: _quota_reason_display_text(_trimmed_str(adjustment.get(key))) if key == "reason" else _trimmed_str(adjustment.get(key))
        for key in ("reason", "biz_ref_type", "biz_ref_id")
        if _trimmed_str(adjustment.get(key))
    }
    if not info:
        return value
    other = _quota_log_other_object(value)
    admin_info = other.get("admin_info")
    if not isinstance(admin_info, dict):
        admin_info = {}
    admin_info.update(info)
    other["admin_info"] = admin_info
    return json.dumps(other, ensure_ascii=False)


def _quota_log_other_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _trimmed_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sanitize_quota_bucket(item: Mapping[str, Any]) -> dict[str, Any]:
    quota = _coerce_int(item.get("quota")) or 0
    return {
        "created_at": item.get("created_at"),
        "model_name": item.get("model_name"),
        "count": _coerce_int(item.get("count")) or 0,
        "quota": quota,
        "token_used": _coerce_int(item.get("token_used")) or 0,
        "display": {
            "quota_credits": _newapi_quota_to_credits(quota),
        },
    }


def _normalize_usage_granularity(value: str | None) -> str:
    normalized = (value or "hour").strip().lower()
    if normalized not in USAGE_SUMMARY_GRANULARITIES:
        raise AIRelayAccountError("INVALID_GRANULARITY", "granularity must be all/hour/day/month")
    return normalized


def _filter_quota_buckets(
    items: list[Mapping[str, Any]],
    *,
    model_name: str | None,
    token_name: str | None,
    group: str | None,
) -> list[Mapping[str, Any]]:
    return [
        item for item in items
        if _bucket_field_matches(item, "model_name", model_name)
        and _bucket_field_matches(item, "token_name", token_name)
        and _bucket_field_matches(item, "group", group)
    ]


def _bucket_field_matches(item: Mapping[str, Any], key: str, expected: str | None) -> bool:
    if expected is None:
        return True
    if key not in item:
        return True
    actual = item.get(key)
    if actual is None:
        return False
    return str(actual).strip() == expected


def _aggregate_model_usage(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for item in items:
        model_key, model_name = _model_bucket_key(item.get("model_name"))
        current = aggregate.setdefault(
            model_key,
            {
                "model_name": model_name,
                "count": 0,
                "quota": 0,
                "token_used": 0,
            },
        )
        current["count"] += _coerce_int(item.get("count")) or 0
        current["quota"] += _coerce_int(item.get("quota")) or 0
        current["token_used"] += _coerce_int(item.get("token_used")) or 0
    items = list(aggregate.values())
    items.sort(key=lambda item: (item["quota"], item["count"], item.get("model_name") or ""), reverse=True)
    return [_with_quota_credit_display(item) for item in items]


def _aggregate_quota_buckets(items: list[Mapping[str, Any]], *, granularity: str) -> list[dict[str, Any]]:
    aggregate: dict[tuple[str, int | None], dict[str, Any]] = {}
    for item in items:
        model_key, model_name = _model_bucket_key(item.get("model_name"))
        bucket_ts = _bucket_timestamp(item.get("created_at"), granularity)
        current = aggregate.setdefault(
            (model_key, bucket_ts),
            {
                "created_at": bucket_ts,
                "model_name": model_name,
                "count": 0,
                "quota": 0,
                "token_used": 0,
            },
        )
        current["count"] += _coerce_int(item.get("count")) or 0
        current["quota"] += _coerce_int(item.get("quota")) or 0
        current["token_used"] += _coerce_int(item.get("token_used")) or 0
    buckets = list(aggregate.values())
    if granularity == "all":
        buckets.sort(key=lambda item: (item["quota"], item["count"], item.get("model_name") or ""), reverse=True)
    else:
        buckets.sort(
            key=lambda item: (
                item["created_at"] if item["created_at"] is not None else -1,
                item["quota"],
                item.get("model_name") or "",
            )
        )
    return [_with_quota_credit_display(item) for item in buckets]


def _model_bucket_key(value: Any) -> tuple[str, str | None]:
    model_name = value.strip() if isinstance(value, str) else ""
    if not model_name:
        return "__unknown__", None
    return model_name, model_name


def _bucket_timestamp(value: Any, granularity: str) -> int | None:
    if granularity == "all":
        return None
    timestamp = _coerce_timestamp(value)
    if timestamp is None:
        return None
    if granularity == "hour":
        return timestamp - (timestamp % 3600)
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if granularity == "day":
        return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if granularity == "month":
        return int(dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    return timestamp


def _coerce_timestamp(value: Any) -> int | None:
    timestamp = _coerce_int(value)
    if timestamp is None:
        return None
    if timestamp > 10_000_000_000:
        return timestamp // 1000
    return timestamp


def _paginate_items(items: list[dict[str, Any]], *, page: int, page_size: int) -> list[dict[str, Any]]:
    safe_page = max(1, page)
    safe_page_size = max(1, page_size)
    offset = (safe_page - 1) * safe_page_size
    return items[offset : offset + safe_page_size]


def _split_timestamp_range(
    start_timestamp: int | None,
    end_timestamp: int | None,
    *,
    max_seconds: int,
) -> list[tuple[int | None, int | None]]:
    if start_timestamp is None or end_timestamp is None or max_seconds <= 0:
        return [(start_timestamp, end_timestamp)]
    if end_timestamp <= start_timestamp or end_timestamp - start_timestamp <= max_seconds:
        return [(start_timestamp, end_timestamp)]

    ranges: list[tuple[int, int]] = []
    current = start_timestamp
    while current < end_timestamp:
        next_end = min(end_timestamp, current + max_seconds)
        ranges.append((current, next_end))
        current = next_end + 1
    return ranges


def _with_quota_credit_display(item: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(item)
    quota = _coerce_int(result.get("quota")) or 0
    result["display"] = {
        **(
            result["display"]
            if isinstance(result.get("display"), dict)
            else {}
        ),
        "quota_credits": _newapi_quota_to_credits(quota),
    }
    return result


def _with_quota_share(item: Mapping[str, Any], total_quota: int) -> dict[str, Any]:
    result = _with_quota_credit_display(item)
    quota = _coerce_int(result.get("quota")) or 0
    share = quota / total_quota if total_quota > 0 else 0.0
    result["quota_share"] = share
    result["quota_percent"] = share * 100
    return result


def _sanitize_channel(item: Mapping[str, Any]) -> dict[str, Any]:
    channel_info = item.get("channel_info")
    if not isinstance(channel_info, dict):
        channel_info = {}
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "type": item.get("type"),
        "status": item.get("status"),
        "group": item.get("group"),
        "models": item.get("models"),
        "used_quota": item.get("used_quota"),
        "balance": item.get("balance"),
        "balance_updated_time": item.get("balance_updated_time"),
        "response_time": item.get("response_time"),
        "test_time": item.get("test_time"),
        "tag": item.get("tag"),
        "priority": item.get("priority"),
        "weight": item.get("weight"),
        "is_multi_key": bool(channel_info.get("is_multi_key")),
        "multi_key_size": _coerce_int(channel_info.get("multi_key_size")) or 0,
    }


def _extract_health_test(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"success": False}
    result = {
        "success": bool(payload.get("success")),
        "message": payload.get("message") or "",
    }
    http_stats = payload.get("http_stats")
    if isinstance(http_stats, dict):
        result["http_stats"] = http_stats
    return result


def _sanitize_token(item: Mapping[str, Any]) -> dict[str, Any]:
    key = item.get("key")
    if isinstance(key, str):
        masked_key = key if _looks_masked_token_key(key) else _mask_secret(key)
    else:
        masked_key = None
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "status": item.get("status"),
        "group": item.get("group"),
        "remain_quota": item.get("remain_quota"),
        "used_quota": item.get("used_quota"),
        "unlimited_quota": bool(item.get("unlimited_quota")),
        "expired_time": item.get("expired_time"),
        "model_limits_enabled": bool(item.get("model_limits_enabled")),
        "model_limits": item.get("model_limits") or "",
        "allow_ips": item.get("allow_ips") or "",
        "cross_group_retry": bool(item.get("cross_group_retry")),
        "key": masked_key,
    }


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _build_token_update_payload(
    token: Mapping[str, Any],
    *,
    token_id: int,
    status: int | None,
    group: str,
    quota_policy: Mapping[str, Any] | None,
    model_limits: Mapping[str, Any] | list[str] | str | None,
    default_name: str,
) -> dict[str, Any]:
    payload = {
        "id": token_id,
        "name": token.get("name") or default_name,
        "status": status if status is not None else token.get("status", 1),
        "expired_time": token.get("expired_time", -1),
        "remain_quota": token.get("remain_quota", 0),
        "unlimited_quota": token.get("unlimited_quota", True),
        "model_limits_enabled": token.get("model_limits_enabled", False),
        "model_limits": token.get("model_limits", ""),
        "allow_ips": token.get("allow_ips", ""),
        "group": group,
        "cross_group_retry": token.get("cross_group_retry", False),
    }
    if quota_policy:
        if "remain_quota" in quota_policy:
            remain_quota = _coerce_int(quota_policy.get("remain_quota"))
            if remain_quota is None or remain_quota < 0:
                raise AIRelayAccountError("INVALID_QUOTA", "quota_policy.remain_quota must be >= 0")
            payload["remain_quota"] = remain_quota
        if "unlimited_quota" in quota_policy:
            payload["unlimited_quota"] = bool(quota_policy.get("unlimited_quota"))
        if "expired_time" in quota_policy:
            expired_time = _coerce_int(quota_policy.get("expired_time"))
            if expired_time is None:
                raise AIRelayAccountError("INVALID_TOKEN", "quota_policy.expired_time must be integer")
            payload["expired_time"] = expired_time
    if model_limits is not None:
        if isinstance(model_limits, str):
            value = model_limits.strip()
        elif isinstance(model_limits, list):
            value = ",".join(str(item).strip() for item in model_limits if str(item).strip())
        elif isinstance(model_limits, Mapping):
            raw_models = model_limits.get("models")
            if isinstance(raw_models, list):
                value = ",".join(str(item).strip() for item in raw_models if str(item).strip())
            elif isinstance(raw_models, str):
                value = raw_models.strip()
            else:
                value = ",".join(str(key).strip() for key, enabled in model_limits.items() if enabled and str(key).strip())
        else:
            raise AIRelayAccountError("INVALID_MODEL_LIMITS", "model_limits must be string/list/object")
        payload["model_limits"] = value
        payload["model_limits_enabled"] = bool(value)
    return payload


def _normalize_quota_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value not in {"add", "subtract", "override"}:
        raise AIRelayAccountError("INVALID_QUOTA_MODE", "mode must be add/subtract/override")
    return value


def _newapi_quota_from_usd(amount: float | int | str, quota_per_usd: int) -> int:
    try:
        value = Decimal(str(amount))
    except InvalidOperation as exc:
        raise AIRelayAccountError("INVALID_QUOTA", "quota amount must be a number") from exc
    if value < 0:
        raise AIRelayAccountError("INVALID_QUOTA", "quota amount must be >= 0")
    if quota_per_usd <= 0:
        raise AIRelayAccountError("INVALID_QUOTA", "quota_per_usd must be positive")
    raw = value * Decimal(quota_per_usd)
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _newapi_quota_to_credits(value: int | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(value) / NEWAPI_QUOTA_POINTS_PER_CREDIT)


def _quota_adjustment_matches(row: Mapping[str, Any], user_id: str, mode: str, quota: int) -> bool:
    return (
        str(row.get("user_id") or "") == user_id
        and str(row.get("mode") or "") == mode
        and (_coerce_int(row.get("quota")) or 0) == quota
    )


def _quota_adjustment_public(row: Mapping[str, Any], *, duplicate: bool) -> dict[str, Any]:
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    return {
        "idempotency_key": row.get("idempotency_key"),
        "user_id": row.get("user_id"),
        "newapi_user_id": row.get("newapi_user_id"),
        "mode": row.get("mode"),
        "quota": row.get("quota"),
        "reason": row.get("reason"),
        "biz_ref_type": row.get("biz_ref_type"),
        "biz_ref_id": row.get("biz_ref_id"),
        "status": row.get("status"),
        "duplicate": duplicate,
        "newapi_response": row.get("newapi_response") or {},
        "quota_summary": row.get("quota_summary") or {},
        "error": row.get("error"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


def _with_quota_display(result: dict[str, Any], *, quota_per_usd: int | None) -> dict[str, Any]:
    if quota_per_usd is None:
        return result
    quota = _coerce_int(result.get("quota")) or 0
    return {
        **result,
        "quota_usd": quota / quota_per_usd,
        "quota_per_usd": quota_per_usd,
    }


def _dict_payload(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, Mapping) else {"value": payload}


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _require_int(value: Any, code: str) -> int:
    result = _coerce_int(value)
    if result is None:
        raise AIRelayAccountError(code, "NewAPI response did not include an integer id")
    return result


def _looks_masked_token_key(value: str) -> bool:
    return not value or "*" in value


def _to_service_error(exc: NewAPIClientError, fallback_code: str) -> AIRelayAccountError:
    if exc.code == "NEWAPI_API_ERROR":
        code = fallback_code
    else:
        code = exc.code or fallback_code
    return AIRelayAccountError(code, exc.message, cause=exc)
