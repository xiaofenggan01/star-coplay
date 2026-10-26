from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Protocol

import httpx

from modules.ai_relay_account.config import AIRelayConfigError, AIRelayConfigProvider
from modules.ai_relay_account.service import DEFAULT_CREDENTIAL_MODE, RelayAccount
from modules.llm.resolver import ModelResolutionError, ResolvedModel, resolve_model

log = logging.getLogger("platform_ws.system_llm_router")


class RelayAccountReader(Protocol):
    async def get_account(self, user_id: str) -> RelayAccount | None: ...


class SystemLLMRouterError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        upstream_status: int | None = None,
        upstream_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.upstream_status = upstream_status
        self.upstream_body = upstream_body


@dataclass(frozen=True)
class SystemLLMContext:
    user_id: str
    agent_session_id: str = ""
    request_id: str = ""
    capability: str = ""
    model_tier: str = ""


@dataclass(frozen=True)
class SystemLLMResult:
    response: httpx.Response
    resolved_model: str
    model_tier: str
    source: str


class SystemLLMRouter:
    def __init__(
        self,
        *,
        account_reader: RelayAccountReader,
        config_provider: AIRelayConfigProvider,
        newapi_base_url: str,
        db_pool: Any = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not newapi_base_url.strip():
            raise ValueError("newapi_base_url is required")
        self._account_reader = account_reader
        self._config_provider = config_provider
        self._db_pool = db_pool
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=newapi_base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def chat_completions(
        self,
        payload: Mapping[str, Any],
        *,
        context: SystemLLMContext,
        stream: bool,
    ) -> SystemLLMResult:
        return await self._route_request(
            payload, context=context, stream=stream,
            endpoint="/v1/chat/completions", upstream_headers=None,
        )

    async def anthropic_messages(
        self,
        payload: Mapping[str, Any],
        *,
        context: SystemLLMContext,
        stream: bool,
        anthropic_version: str = "2023-06-01",
        anthropic_beta: str = "",
    ) -> SystemLLMResult:
        headers = {"anthropic-version": anthropic_version or "2023-06-01"}
        if anthropic_beta:
            headers["anthropic-beta"] = anthropic_beta
        return await self._route_request(
            payload, context=context, stream=stream,
            endpoint="/v1/messages", upstream_headers=headers,
        )

    async def _route_request(
        self, payload: Mapping[str, Any], *, context: SystemLLMContext,
        stream: bool, endpoint: str,
        upstream_headers: Mapping[str, str] | None,
    ) -> SystemLLMResult:
        started_at = time.monotonic()
        resolved: ResolvedModel | None = None
        route_group = ""
        status_code = 0
        error_code = ""
        try:
            account = await self._load_ready_account(context.user_id)
            route_group = account.group
            resolved = await self._resolve_model(account, context.model_tier)
            request_body = dict(payload)
            request_body["model"] = resolved.model
            if stream:
                request_body["stream"] = True

            response = await self._send_to_newapi(
                request_body,
                token_key=account.newapi_token_key or "",
                stream=stream,
                endpoint=endpoint,
                extra_headers=upstream_headers,
            )
            status_code = response.status_code
            if response.status_code >= 400:
                error = await self._normalize_upstream_error(response)
                status_code = error.status
                error_code = error.code
                raise error
            return SystemLLMResult(
                response=response,
                resolved_model=resolved.model,
                model_tier=resolved.model_tier,
                source=resolved.source,
            )
        except SystemLLMRouterError as exc:
            error_code = exc.code
            raise
        except httpx.TimeoutException as exc:
            error_code = "NEWAPI_TIMEOUT"
            raise SystemLLMRouterError(
                "NEWAPI_TIMEOUT",
                "NewAPI request timed out",
                status=504,
            ) from exc
        except httpx.TransportError as exc:
            error_code = "NEWAPI_UNAVAILABLE"
            raise SystemLLMRouterError(
                "NEWAPI_UNAVAILABLE",
                "NewAPI is unavailable",
                status=503,
            ) from exc
        finally:
            await self._write_audit_event(
                context=context,
                resolved_model=resolved.model if resolved else "",
                group=route_group,
                latency_ms=(time.monotonic() - started_at) * 1000,
                status=status_code,
                error=error_code,
                stream=stream,
            )

    async def stream_response(self, response: httpx.Response) -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()

    async def _load_ready_account(self, user_id: str) -> RelayAccount:
        account = await self._account_reader.get_account(user_id)
        if account is None:
            raise SystemLLMRouterError(
                "AI_ACCOUNT_NOT_FOUND",
                "AI relay account not found",
                status=404,
            )
        if account.status != "active":
            raise SystemLLMRouterError(
                "AI_ACCOUNT_DISABLED",
                "AI relay account is not active",
                status=409,
            )
        if account.credential_mode != DEFAULT_CREDENTIAL_MODE:
            raise SystemLLMRouterError(
                "AI_CREDENTIAL_MODE_UNSUPPORTED",
                "AI relay credential mode is not supported",
                status=409,
            )
        if not account.newapi_token_key:
            raise SystemLLMRouterError(
                "AI_ACCOUNT_NOT_READY",
                "AI relay token is not ready",
                status=409,
            )
        return account

    async def _resolve_model(self, account: RelayAccount, model_tier: str) -> ResolvedModel:
        try:
            mapping = await self._config_provider.get_model_tier_mapping()
            return resolve_model(
                model_tier=model_tier,
                preferred_models=account.preferred_models,
                tier_mapping=mapping,
            )
        except AIRelayConfigError as exc:
            raise SystemLLMRouterError("AI_RELAY_CONFIG_ERROR", exc.message, status=500) from exc
        except ModelResolutionError as exc:
            status = 409 if exc.code in {"MODEL_NOT_CONFIGURED", "MODEL_TIER_CONFIG_INVALID"} else 400
            raise SystemLLMRouterError(exc.code, exc.message, status=status) from exc

    async def _send_to_newapi(
        self,
        payload: Mapping[str, Any],
        *,
        token_key: str,
        stream: bool,
        endpoint: str = "/v1/chat/completions",
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token_key}",
            "Content-Type": "application/json",
        }
        headers.update(dict(extra_headers or {}))
        request = self._client.build_request(
            "POST",
            endpoint,
            headers=headers,
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        return await self._client.send(request, stream=stream)

    async def _normalize_upstream_error(self, response: httpx.Response) -> SystemLLMRouterError:
        try:
            raw_body = await response.aread()
        finally:
            await response.aclose()
        payload = _parse_error_payload(raw_body, response.headers.get("content-type", ""))
        code = _upstream_error_code(response.status_code)
        return SystemLLMRouterError(
            code,
            _extract_upstream_message(payload) or f"NewAPI returned HTTP {response.status_code}",
            status=_public_error_status(response.status_code),
            upstream_status=response.status_code,
            upstream_body=payload,
        )

    async def _write_audit_event(
        self,
        *,
        context: SystemLLMContext,
        resolved_model: str,
        group: str,
        latency_ms: float,
        status: int,
        error: str,
        stream: bool,
    ) -> None:
        level = logging.ERROR if error else logging.INFO
        log.log(
            level,
            "system llm call completed status=%s model_tier=%s model=%s latency_ms=%.1f error=%s",
            status,
            context.model_tier,
            resolved_model,
            latency_ms,
            error or "",
            extra={
                "event": "gateway.system_llm.call",
                "user_id": context.user_id,
                "agent_instance_id": context.agent_session_id,
                "session_id": context.agent_session_id,
                "request_id": context.request_id,
                "capability": context.capability or "llm_completion",
                "error_code": error or "",
                "model_tier": context.model_tier,
                "model": resolved_model,
                "resolved_model": resolved_model,
                "group": group,
                "latency_ms": round(latency_ms, 1),
                "status": status,
                "stream": stream,
            },
        )


def _upstream_error_code(status_code: int) -> str:
    if status_code in (401, 403):
        return "NEWAPI_AUTH_FAILED"
    if status_code == 402:
        return "NEWAPI_QUOTA_EXHAUSTED"
    if status_code == 429:
        return "NEWAPI_RATE_LIMITED"
    if status_code >= 500:
        return "UPSTREAM_ERROR"
    return "UPSTREAM_BAD_REQUEST"


def _public_error_status(upstream_status: int) -> int:
    if upstream_status == 402:
        return 402
    if upstream_status == 429:
        return 429
    if upstream_status in (401, 403):
        return 502
    if upstream_status >= 500:
        return 502
    return 400


def _parse_error_payload(raw: bytes, content_type: str) -> Any:
    if not raw:
        return None
    if "json" not in content_type.lower():
        return raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def _extract_upstream_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            if isinstance(message, str):
                return message
        if isinstance(error, str):
            return error
        message = payload.get("message")
        if isinstance(message, str):
            return message
    if isinstance(payload, str):
        return payload
    return ""
