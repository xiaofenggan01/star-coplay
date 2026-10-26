from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


class NewAPIClientError(Exception):
    """Raised when NewAPI returns an error response or cannot be reached."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class NewAPIClientConfig:
    base_url: str
    access_token: str
    user_id: int | None = None
    timeout_seconds: float = 10.0
    max_429_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 8.0

    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


class NewAPIClient:
    """Small HTTP wrapper for NewAPI dashboard/admin APIs.

    This class intentionally contains no platform business decisions. It only
    normalizes requests, auth headers, response envelopes, and transport errors.
    """

    def __init__(
        self,
        config: NewAPIClientConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not config.base_url.strip():
            raise ValueError("NewAPI base_url is required")
        if not config.access_token.strip():
            raise ValueError("NewAPI access_token is required")
        self._config = config
        self._session_transport = getattr(client, "_transport", None) if client is not None else None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.normalized_base_url(),
            timeout=httpx.Timeout(config.timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        user_id: int | None = None,
        access_token: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        headers = self._build_headers(
            user_id=user_id,
            access_token=access_token,
            extra_headers=extra_headers,
        )
        url = self._normalize_path(path)
        return await self._send_request(
            method,
            url,
            json_body=json_body,
            params=params,
            headers=headers,
        )

    async def issue_user_access_token(self, *, username: str, password: str, user_id: int) -> Any:
        async with httpx.AsyncClient(
            base_url=self._config.normalized_base_url(),
            timeout=httpx.Timeout(self._config.timeout_seconds),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            transport=self._session_transport,
        ) as session:
            await self._send_request_with_client(
                session,
                "POST",
                "/api/user/login",
                json_body={"username": username, "password": password},
                headers={"Accept": "application/json"},
            )
            return await self._send_request_with_client(
                session,
                "GET",
                "/api/user/token",
                headers={"Accept": "application/json", "New-Api-User": str(user_id)},
            )

    async def _send_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        url = self._normalize_path(path)
        return await self._send_request_with_client(
            self._client,
            method,
            url,
            json_body=json_body,
            params=params,
            headers=headers,
        )

    async def _send_request_with_client(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        url = self._normalize_path(path)
        attempt = 0
        while True:
            try:
                resp = await client.request(
                    method,
                    url,
                    json=json_body,
                    params=self._clean_params(params),
                    headers=dict(headers or {}),
                )
            except httpx.TimeoutException as exc:
                raise NewAPIClientError("NEWAPI_TIMEOUT", "NewAPI request timed out") from exc
            except httpx.TransportError as exc:
                raise NewAPIClientError("NEWAPI_TRANSPORT_ERROR", str(exc)) from exc

            payload = self._parse_response_body(resp)
            if resp.status_code == 429 and attempt < max(0, self._config.max_429_retries):
                await asyncio.sleep(self._retry_delay_seconds(resp, attempt))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise NewAPIClientError(
                    "NEWAPI_HTTP_ERROR",
                    self._extract_message(payload) or f"NewAPI HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    payload=payload,
                )
            break

        if isinstance(payload, dict) and payload.get("success") is False:
            raise NewAPIClientError(
                "NEWAPI_API_ERROR",
                self._extract_message(payload) or "NewAPI returned success=false",
                status_code=resp.status_code,
                payload=payload,
            )
        return payload

    async def get_status(self) -> Any:
        return await self.request("GET", "/api/status", user_id=None)

    async def get_test_status(self) -> Any:
        return await self.request("GET", "/api/status/test")

    async def list_groups(self) -> Any:
        return await self.request("GET", "/api/group/")

    async def list_dashboard_models(self, *, user_id: int, access_token: str | None = None) -> Any:
        return await self.request(
            "GET",
            "/api/models",
            user_id=user_id,
            access_token=access_token,
        )

    async def list_channel_models(self) -> Any:
        return await self.request("GET", "/api/channel/models")

    async def list_enabled_models(self) -> Any:
        return await self.request("GET", "/api/channel/models_enabled")

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
    ) -> Any:
        return await self.request(
            "GET",
            "/api/channel/",
            params={
                "p": page,
                "page_size": page_size,
                "group": group,
                "status": status,
                "type": channel_type,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    async def get_user(self, newapi_user_id: int, *, access_token: str | None = None) -> Any:
        return await self.request("GET", f"/api/user/{newapi_user_id}", access_token=access_token)

    async def get_self_user(
        self,
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/user/self",
            user_id=user_id,
            access_token=access_token,
        )

    async def search_users(
        self,
        *,
        keyword: str,
        group: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/user/search",
            params={"keyword": keyword, "group": group, "p": page, "size": page_size},
        )

    async def create_user(self, payload: Mapping[str, Any]) -> Any:
        return await self.request("POST", "/api/user/", json_body=dict(payload))

    async def update_user(self, payload: Mapping[str, Any]) -> Any:
        return await self.request("PUT", "/api/user/", json_body=dict(payload))

    async def manage_user(self, payload: Mapping[str, Any]) -> Any:
        return await self.request("POST", "/api/user/manage", json_body=dict(payload))

    async def delete_user(self, newapi_user_id: int) -> Any:
        return await self.request("DELETE", f"/api/user/{newapi_user_id}")

    async def list_tokens(
        self,
        *,
        user_id: int,
        access_token: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/token/",
            params={"p": page, "size": page_size},
            user_id=user_id,
            access_token=access_token,
        )

    async def get_token(self, token_id: int, *, user_id: int, access_token: str | None = None) -> Any:
        return await self.request(
            "GET",
            f"/api/token/{token_id}",
            user_id=user_id,
            access_token=access_token,
        )

    async def create_token(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any:
        return await self.request(
            "POST",
            "/api/token/",
            json_body=dict(payload),
            user_id=user_id,
            access_token=access_token,
        )

    async def update_token(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: int,
        access_token: str | None = None,
    ) -> Any:
        return await self.request(
            "PUT",
            "/api/token/",
            json_body=dict(payload),
            user_id=user_id,
            access_token=access_token,
        )

    async def delete_token(self, token_id: int, *, user_id: int, access_token: str | None = None) -> Any:
        return await self.request(
            "DELETE",
            f"/api/token/{token_id}",
            user_id=user_id,
            access_token=access_token,
        )

    async def get_token_key(self, token_id: int, *, user_id: int, access_token: str | None = None) -> Any:
        return await self.request(
            "POST",
            f"/api/token/{token_id}/key",
            user_id=user_id,
            access_token=access_token,
        )

    async def get_token_usage(self, token_key: str) -> Any:
        return await self.request(
            "GET",
            "/api/usage/token/",
            access_token=f"Bearer {token_key}",
        )

    async def list_logs(self, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", "/api/log/", params=params)

    async def get_logs_stat(self, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", "/api/log/stat", params=params)

    async def get_user_logs(
        self,
        *,
        user_id: int,
        params: Mapping[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/log/self",
            params=params,
            user_id=user_id,
            access_token=access_token,
        )

    async def get_user_logs_stat(
        self,
        *,
        user_id: int,
        params: Mapping[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/log/self/stat",
            params=params,
            user_id=user_id,
            access_token=access_token,
        )

    async def list_quota_data(self, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", "/api/data/", params=params)

    async def list_user_quota_data(self, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", "/api/data/users", params=params)

    async def list_self_quota_data(
        self,
        *,
        user_id: int,
        params: Mapping[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any:
        return await self.request(
            "GET",
            "/api/data/self",
            params=params,
            user_id=user_id,
            access_token=access_token,
        )

    def _build_headers(
        self,
        *,
        user_id: int | None,
        access_token: str | None,
        extra_headers: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": access_token or self._config.access_token,
            "Accept": "application/json",
        }
        resolved_user_id = user_id if user_id is not None else self._config.user_id
        if resolved_user_id is not None:
            headers["New-Api-User"] = str(resolved_user_id)
        if extra_headers:
            headers.update(extra_headers)
        return headers

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    @staticmethod
    def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not params:
            return None
        return {key: value for key, value in params.items() if value is not None}

    @staticmethod
    def _parse_response_body(resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text

    def _retry_delay_seconds(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), self._config.retry_max_delay_seconds))
            except ValueError:
                pass
        delay = self._config.retry_base_delay_seconds * (2**attempt)
        return max(0.0, min(delay, self._config.retry_max_delay_seconds))

    @staticmethod
    def _extract_message(payload: Any) -> str:
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error")
            if isinstance(message, str):
                return message
            if isinstance(message, dict):
                nested = message.get("message") or message.get("code")
                if isinstance(nested, str):
                    return nested
        if isinstance(payload, str):
            return payload
        return ""
