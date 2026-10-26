from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from aiohttp import web

from modules.ai_relay_account.service import (
    AIRelayAccountError,
    AIRelayAccountService,
    EnsureRelayAccountRequest,
    RelayAccount,
)

CheckInternalToken = Callable[[web.Request], web.Response | None]


class AIRelayAccountRoutes:
    def __init__(
        self,
        service: AIRelayAccountService | None,
        check_internal_api_token: CheckInternalToken,
    ) -> None:
        self._service = service
        self._check_internal_api_token = check_internal_api_token

    def setup(self, app: web.Application) -> None:
        for prefix in ("", "/api"):
            app.router.add_put(f"{prefix}/internal/ai/relay/users/{{user_id}}/ensure", self.ensure_user)
            app.router.add_get(f"{prefix}/internal/ai/relay/health", self.get_health)
            app.router.add_get(f"{prefix}/internal/ai/relay/health/overview", self.get_health)
            app.router.add_get(f"{prefix}/internal/ai/relay/health/models", self.get_health_models)
            app.router.add_get(f"{prefix}/internal/ai/relay/health/channels", self.get_health_channels)
            app.router.add_get(f"{prefix}/internal/ai/relay/health/users", self.get_health_users)
            app.router.add_get(f"{prefix}/internal/ai/relay/groups", self.list_groups)
            app.router.add_post(f"{prefix}/internal/ai/relay/quota-adjustments", self.adjust_quota)
            app.router.add_get(f"{prefix}/internal/ai/relay/quota", self.get_quota_by_query)
            app.router.add_get(f"{prefix}/internal/ai/relay/quota-ledger", self.list_quota_ledger_by_query)
            app.router.add_get(f"{prefix}/internal/ai/relay/usage/logs", self.list_usage_logs_by_query)
            app.router.add_get(f"{prefix}/internal/ai/relay/usage/summary", self.get_usage_summary_by_query)
            app.router.add_get(f"{prefix}/internal/ai/relay/users/{{user_id}}/quota", self.get_quota)
            app.router.add_get(f"{prefix}/internal/ai/relay/users/{{user_id}}/quota-ledger", self.list_quota_ledger)
            app.router.add_get(f"{prefix}/internal/ai/relay/users/{{user_id}}/usage/logs", self.list_usage_logs)
            app.router.add_get(f"{prefix}/internal/ai/relay/users/{{user_id}}/usage/summary", self.get_usage_summary)
            app.router.add_get(f"{prefix}/internal/ai/relay/users/{{user_id}}/tokens", self.list_tokens)
            app.router.add_get(f"{prefix}/internal/ai/relay/users/{{user_id}}", self.get_user)
            app.router.add_patch(f"{prefix}/internal/ai/relay/users/{{user_id}}", self.update_user)
            app.router.add_delete(f"{prefix}/internal/ai/relay/users/{{user_id}}", self.disable_user)
            app.router.add_patch(f"{prefix}/internal/ai/relay/tokens/{{token_id}}", self.update_token)
            app.router.add_delete(f"{prefix}/internal/ai/relay/tokens/{{token_id}}", self.delete_token)

    async def ensure_user(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        body, err = await _read_json_body(request)
        if err is not None:
            return err

        user_id = request.match_info["user_id"]
        try:
            account = await service.ensure_account(
                EnsureRelayAccountRequest(
                    user_id=user_id,
                    email=_optional_str(body.get("email")),
                    display_name=_optional_str(body.get("display_name")),
                    default_group=_optional_str(body.get("default_group")) or "default",
                    preferred_models=_string_map(body.get("preferred_models")),
                )
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": _public_account(account)})

    async def get_user(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            account = await service.get_account(request.match_info["user_id"])
        except AIRelayAccountError as exc:
            return _error_response(exc)
        if account is None:
            return _error_payload("ACCOUNT_NOT_FOUND", "relay account not found", status=404)
        return web.json_response({"data": _public_account(account)})

    async def get_quota(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            payload = await service.get_quota(request.match_info["user_id"])
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_quota_by_query(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service
        user_id, err = _query_required_str(request, "user_id")
        if err is not None:
            return err
        try:
            payload = await service.get_quota(user_id)
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def list_quota_ledger(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            log_types = _query_log_types(request)
        except AIRelayAccountError as exc:
            return _error_response(exc)
        try:
            payload = await service.list_quota_ledger(
                request.match_info["user_id"],
                page=parsed["page"],
                page_size=parsed["page_size"],
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                log_types=log_types,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def list_quota_ledger_by_query(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service
        user_id, err = _query_required_str(request, "user_id")
        if err is not None:
            return err
        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            log_types = _query_log_types(request)
        except AIRelayAccountError as exc:
            return _error_response(exc)
        try:
            payload = await service.list_quota_ledger(
                user_id,
                page=parsed["page"],
                page_size=parsed["page_size"],
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                log_types=log_types,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def list_usage_logs(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            include_errors = _query_bool(request, "include_errors", True)
        except AIRelayAccountError as exc:
            return _error_response(exc)
        try:
            payload = await service.list_usage_logs(
                request.match_info["user_id"],
                page=parsed["page"],
                page_size=parsed["page_size"],
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                model_name=_query_str(request, "model_name"),
                token_name=_query_str(request, "token_name"),
                group=_query_str(request, "group"),
                request_id=_query_str(request, "request_id"),
                include_errors=include_errors,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def list_usage_logs_by_query(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service
        user_id, err = _query_required_str(request, "user_id")
        if err is not None:
            return err
        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            include_errors = _query_bool(request, "include_errors", True)
        except AIRelayAccountError as exc:
            return _error_response(exc)
        try:
            payload = await service.list_usage_logs(
                user_id,
                page=parsed["page"],
                page_size=parsed["page_size"],
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                model_name=_query_str(request, "model_name"),
                token_name=_query_str(request, "token_name"),
                group=_query_str(request, "group"),
                request_id=_query_str(request, "request_id"),
                include_errors=include_errors,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_usage_summary(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            payload = await service.get_usage_summary(
                request.match_info["user_id"],
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                model_name=_query_str(request, "model_name"),
                token_name=_query_str(request, "token_name"),
                group=_query_str(request, "group"),
                granularity=_query_str(request, "granularity"),
                page=parsed["page"],
                page_size=parsed["page_size"],
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_usage_summary_by_query(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service
        user_id, err = _query_required_str(request, "user_id")
        if err is not None:
            return err
        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            payload = await service.get_usage_summary(
                user_id,
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                model_name=_query_str(request, "model_name"),
                token_name=_query_str(request, "token_name"),
                group=_query_str(request, "group"),
                granularity=_query_str(request, "granularity"),
                page=parsed["page"],
                page_size=parsed["page_size"],
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_health(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            payload = await service.get_health(group=_query_str(request, "group"))
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_health_models(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            payload = await service.get_health_models(group=_query_str(request, "group"))
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_health_channels(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            include_balance = _query_bool(request, "include_balance", False)
        except AIRelayAccountError as exc:
            return _error_response(exc)
        try:
            payload = await service.get_health_channels(
                group=_query_str(request, "group"),
                model=_query_str(request, "model"),
                include_balance=include_balance,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def get_health_users(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        parsed, err = _read_query(request)
        if err is not None:
            return err
        try:
            payload = await service.get_health_users(
                start_timestamp=parsed["start_timestamp"],
                end_timestamp=parsed["end_timestamp"],
                granularity=_query_str(request, "granularity"),
                page=parsed["page"],
                page_size=parsed["page_size"],
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def list_groups(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            payload = await service.list_groups(
                include_disabled=_query_bool(request, "include_disabled", False),
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def list_tokens(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        try:
            page = _query_int(request, "page", 1)
            page_size = _query_int(request, "page_size", 20)
        except ValueError as exc:
            return _error_payload("INVALID_QUERY", str(exc), status=400)
        if page < 1:
            return _error_payload("INVALID_QUERY", "page must be >= 1", status=400)
        if page_size < 1 or page_size > 100:
            return _error_payload("INVALID_QUERY", "page_size must be between 1 and 100", status=400)

        try:
            payload = await service.list_tokens(
                request.match_info["user_id"],
                page=page,
                page_size=page_size,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def update_token(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        token_id, err = _path_int(request, "token_id", "INVALID_TOKEN")
        if err is not None:
            return err
        body, err = await _read_json_body(request)
        if err is not None:
            return err

        try:
            status = _optional_int_body(body, "status")
            payload = await service.update_token(
                token_id,
                status=status,
                group=_optional_str(body.get("group")),
                quota_policy=_optional_record(body.get("quota_policy"), "quota_policy"),
                model_limits=body.get("model_limits") if "model_limits" in body else None,
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def delete_token(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        token_id, err = _path_int(request, "token_id", "INVALID_TOKEN")
        if err is not None:
            return err
        try:
            payload = await service.delete_token(
                token_id,
                reason=_query_str(request, "reason") or "",
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def adjust_quota(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        body, err = await _read_json_body(request)
        if err is not None:
            return err
        try:
            user_id = _required_str_body(body, "user_id")
            mode = _required_str_body(body, "mode")
            reason = _optional_str(body.get("reason")) or f"后台额度调整：{mode}"
            idempotency_key = _required_str_body(body, "idempotency_key")
            quota = _optional_int_body(body, "quota")
            quota_usd = body.get("quota_usd") if "quota_usd" in body else None
            if quota is None and quota_usd is None:
                raise AIRelayAccountError("INVALID_PARAM", "quota or quota_usd is required")
            payload = await service.adjust_quota(
                user_id=user_id,
                mode=mode,
                quota=quota,
                quota_usd=quota_usd,
                reason=reason,
                idempotency_key=idempotency_key,
                biz_ref_type=_optional_str(body.get("biz_ref_type")),
                biz_ref_id=_optional_str(body.get("biz_ref_id")),
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": payload})

    async def update_user(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        body, err = await _read_json_body(request)
        if err is not None:
            return err

        try:
            account = await service.update_account(
                request.match_info["user_id"],
                group=_optional_str(body.get("group")),
                preferred_models=(
                    _string_map(body["preferred_models"])
                    if "preferred_models" in body
                    else None
                ),
                status=_optional_str(body.get("status")),
            )
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": _public_account(account)})

    async def disable_user(self, request: web.Request) -> web.Response:
        err = self._authorize(request)
        if err is not None:
            return err
        service = self._require_service()
        if isinstance(service, web.Response):
            return service

        mode = request.query.get("mode", "disable")
        if mode != "disable":
            return _error_payload(
                "UNSUPPORTED_DELETE_MODE",
                "only mode=disable is supported for relay accounts",
                status=400,
            )
        try:
            account = await service.disable_account(request.match_info["user_id"])
        except AIRelayAccountError as exc:
            return _error_response(exc)
        return web.json_response({"data": _public_account(account)})

    def _authorize(self, request: web.Request) -> web.Response | None:
        return self._check_internal_api_token(request)

    def _require_service(self) -> AIRelayAccountService | web.Response:
        if self._service is None:
            return _error_payload(
                "AI_RELAY_NOT_CONFIGURED",
                "AI Relay Account Service is not configured",
                status=503,
            )
        return self._service


def setup_ai_relay_account_routes(
    app: web.Application,
    service: AIRelayAccountService | None,
    check_internal_api_token: CheckInternalToken,
) -> None:
    AIRelayAccountRoutes(service, check_internal_api_token).setup(app)


async def _read_json_body(request: web.Request) -> tuple[dict[str, Any], web.Response | None]:
    if not request.can_read_body:
        return {}, None
    try:
        value = await request.json()
    except json.JSONDecodeError:
        return {}, _error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
    except Exception:
        return {}, _error_payload("INVALID_JSON", "request body must be valid JSON", status=400)
    if value is None:
        return {}, None
    if not isinstance(value, dict):
        return {}, _error_payload("INVALID_JSON", "request body must be a JSON object", status=400)
    return value, None


def _public_account(account: RelayAccount) -> dict[str, Any]:
    return {
        "user_id": account.user_id,
        "newapi_user_id": account.newapi_user_id,
        "newapi_token_id": account.newapi_token_id,
        "has_token_key": bool(account.newapi_token_key),
        "has_user_access_token": account.has_newapi_user_access_token,
        "group": account.group,
        "preferred_models": account.preferred_models,
        "credential_mode": account.credential_mode,
        "status": account.status,
        "last_sync_error": account.last_sync_error,
        "created_at": _isoformat(account.created_at),
        "updated_at": _isoformat(account.updated_at),
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _read_query(request: web.Request) -> tuple[dict[str, int | None], web.Response | None]:
    try:
        page = _query_int(request, "page", 1)
        page_size = _query_int(request, "page_size", 20)
        start_timestamp = _query_optional_int(request, "start_timestamp")
        end_timestamp = _query_optional_int(request, "end_timestamp")
    except ValueError as exc:
        return {}, _error_payload("INVALID_QUERY", str(exc), status=400)
    if page < 1:
        return {}, _error_payload("INVALID_QUERY", "page must be >= 1", status=400)
    if page_size < 1 or page_size > 100:
        return {}, _error_payload("INVALID_QUERY", "page_size must be between 1 and 100", status=400)
    return {
        "page": page,
        "page_size": page_size,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
    }, None


def _query_str(request: web.Request, key: str) -> str | None:
    value = request.query.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _query_required_str(request: web.Request, key: str) -> tuple[str, web.Response | None]:
    value = _query_str(request, key)
    if value is None:
        return "", _error_payload("INVALID_QUERY", f"{key} is required", status=400)
    return value, None


def _query_int(request: web.Request, key: str, default: int) -> int:
    value = request.query.get(key)
    if value is None or not value.strip():
        return default
    return int(value)


def _query_optional_int(request: web.Request, key: str) -> int | None:
    value = request.query.get(key)
    if value is None or not value.strip():
        return None
    return int(value)


def _query_bool(request: web.Request, key: str, default: bool) -> bool:
    value = request.query.get(key)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AIRelayAccountError("INVALID_QUERY", f"{key} must be boolean")


def _query_log_types(request: web.Request) -> set[int] | None:
    raw = request.query.get("types")
    if raw is None or not raw.strip():
        return None
    values: set[int] = set()
    for item in raw.split(","):
        part = item.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError as exc:
            raise AIRelayAccountError("INVALID_QUERY", "types must be comma-separated integers") from exc
    return values or None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AIRelayAccountError("INVALID_PARAM", "expected string parameter")
    stripped = value.strip()
    return stripped or None


def _required_str_body(body: dict[str, Any], key: str) -> str:
    value = _optional_str(body.get(key))
    if value is None:
        raise AIRelayAccountError("INVALID_PARAM", f"{key} is required")
    return value


def _required_int_body(body: dict[str, Any], key: str) -> int:
    if key not in body:
        raise AIRelayAccountError("INVALID_PARAM", f"{key} is required")
    value = body.get(key)
    if isinstance(value, bool):
        raise AIRelayAccountError("INVALID_PARAM", f"{key} must be integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise AIRelayAccountError("INVALID_PARAM", f"{key} must be integer")


def _optional_int_body(body: dict[str, Any], key: str) -> int | None:
    if key not in body or body.get(key) is None:
        return None
    return _required_int_body(body, key)


def _optional_record(value: Any, key: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AIRelayAccountError("INVALID_PARAM", f"{key} must be an object")
    return dict(value)


def _path_int(request: web.Request, key: str, code: str) -> tuple[int, web.Response | None]:
    value = request.match_info.get(key, "")
    try:
        parsed = int(value)
    except ValueError:
        return 0, _error_payload(code, f"{key} must be integer", status=400)
    if parsed <= 0:
        return 0, _error_payload(code, f"{key} must be positive", status=400)
    return parsed, None


def _string_map(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AIRelayAccountError("INVALID_PARAM", "preferred_models must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise AIRelayAccountError("INVALID_PARAM", "preferred_models must map strings to strings")
        k = key.strip()
        v = item.strip()
        if k and v:
            result[k] = v
    return result


def _error_response(exc: AIRelayAccountError) -> web.Response:
    status = 400
    if exc.code == "ACCOUNT_NOT_FOUND":
        status = 404
    elif exc.code in {"DB_UNAVAILABLE", "AI_RELAY_NOT_CONFIGURED"}:
        status = 503
    elif exc.code.startswith("NEWAPI_"):
        status = 502
    return _error_payload(exc.code, exc.message, status=status)


def _error_payload(code: str, message: str, *, status: int) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}},
        status=status,
    )
