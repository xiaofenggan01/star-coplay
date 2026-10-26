"""
统一业务配置系统 — Agent Runtime ConfigClient

职责：
  - 通过内部 HTTP 接口读取 active 配置
  - 进程内 60 秒 TTL 缓存（成功结果才缓存，错误不缓存）
  - 类型化 getter，拒绝跨类型强转
  - 缓存 key 包含 environment/domain/config_key
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

VALID_CONFIG_ENVIRONMENTS = frozenset(("dev", "test", "prod"))

CONFIG_CACHE_TTL_SECONDS: int = 60

_VALUE_TYPE_NAME_TO_CODE: dict[str, int] = {
    "default": 0,
    "bool": 1,
    "int": 2,
    "decimal": 3,
    "string": 4,
    "json": 5,
}


class ConfigError(Exception):
    """配置读取或解析失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"ConfigError(code={self.code!r}, message={self.message!r})"


@dataclass
class _CacheEntry:
    payload: dict[str, Any]
    fetched_at: float


def resolve_config_environment(environment: str | None) -> str:
    value = (environment or "").strip() or "prod"
    if value not in VALID_CONFIG_ENVIRONMENTS:
        raise ConfigError(
            "CONFIG_INVALID_ENVIRONMENT",
            f"environment must be dev/test/prod, got: {value}",
        )
    return value


@dataclass
class SystemConfigClient:
    """Agent Runtime 统一配置读取客户端。

    参数：
        base_url:      platform backend HTTP API 地址，不是 Web/Vercel 地址
        service_token: INTERNAL_API_TOKEN 环境变量值
        http_client:   实现 async get_json(path, headers) 的对象；
                       生产环境传 None 时自动创建 httpx.AsyncClient 适配器
        clock:         返回当前时间戳（float）的可调用对象，默认 time.time
    """

    base_url: str
    service_token: str
    default_environment: str = field(
        default_factory=lambda: resolve_config_environment(
            os.environ.get("STARCOPLAY_CONFIG_ENV")
        )
    )
    http_client: Any = None
    clock: Callable[[], float] = field(default_factory=lambda: time.time)

    _cache: dict[str, _CacheEntry] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.default_environment = resolve_config_environment(self.default_environment)
        if self.http_client is None:
            self.http_client = _HttpxAdapter(self.base_url)

    # ── 公开 API ──────────────────────────────────────────────────────────

    async def get_config(
        self, domain: str, config_key: str, environment: str | None = None
    ) -> dict[str, Any]:
        """读取配置，返回完整 payload dict。成功结果写入 TTL 缓存。"""
        environment = resolve_config_environment(environment or self.default_environment)
        cache_key = f"{environment}:{domain}:{config_key}"
        now = self.clock()

        entry = self._cache.get(cache_key)
        if entry is not None and (now - entry.fetched_at) < CONFIG_CACHE_TTL_SECONDS:
            return entry.payload

        path = f"/internal/configs/{domain}/{config_key}?env={environment}"
        headers = {"authorization": f"Bearer {self.service_token}"}

        payload = await self.http_client.get_json(path, headers)

        # 只缓存成功结果
        self._cache[cache_key] = _CacheEntry(payload=payload, fetched_at=now)
        return payload

    async def get_bool(
        self, domain: str, config_key: str, environment: str | None = None
    ) -> bool:
        payload = await self.get_config(domain, config_key, environment)
        self._assert_type_or_evict(payload, "bool", domain, config_key, environment)
        return bool(payload["parsed_value"])

    async def get_int(
        self, domain: str, config_key: str, environment: str | None = None
    ) -> int:
        payload = await self.get_config(domain, config_key, environment)
        self._assert_type_or_evict(payload, "int", domain, config_key, environment)
        return int(payload["parsed_value"])

    async def get_decimal(
        self, domain: str, config_key: str, environment: str | None = None
    ) -> float:
        payload = await self.get_config(domain, config_key, environment)
        self._assert_type_or_evict(payload, "decimal", domain, config_key, environment)
        return float(payload["parsed_value"])

    async def get_string(
        self, domain: str, config_key: str, environment: str | None = None
    ) -> str:
        payload = await self.get_config(domain, config_key, environment)
        self._assert_type_or_evict(payload, "string", domain, config_key, environment)
        return str(payload["parsed_value"])

    async def get_json(
        self, domain: str, config_key: str, environment: str | None = None
    ) -> Any:
        payload = await self.get_config(domain, config_key, environment)
        self._assert_type_or_evict(payload, "json", domain, config_key, environment)
        return payload["parsed_value"]

    def clear_cache(self) -> None:
        """清空进程内缓存，下次读取强制重新请求。"""
        self._cache.clear()

    def _assert_type_or_evict(
        self,
        payload: dict[str, Any],
        expected: str,
        domain: str,
        config_key: str,
        environment: str | None,
    ) -> None:
        """校验 value_type；类型不匹配时清除缓存条目再抛出，避免错误类型被缓存。"""
        environment = resolve_config_environment(environment or self.default_environment)
        actual = payload.get("value_type", "")
        if actual != expected:
            self._cache.pop(f"{environment}:{domain}:{config_key}", None)
            raise ConfigError(
                "CONFIG_TYPE_MISMATCH",
                f"{domain}/{config_key}: expected value_type={expected!r}, got {actual!r}",
            )


class _HttpxAdapter:
    """生产环境 httpx 适配器（测试时由 FakeHttpClient 替换）。"""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def get_json(self, path: str, headers: dict[str, str]) -> dict[str, Any]:
        import httpx  # lazy import，避免在测试环境强依赖

        url = self._base_url + path
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10.0)

        if resp.status_code == 401:
            raise ConfigError("CONFIG_UNAUTHORIZED", "unauthorized")
        if resp.status_code == 404:
            raise ConfigError("CONFIG_NOT_FOUND", "config not found")
        if resp.status_code == 409:
            try:
                body = resp.json()
                code = body.get("error", {}).get("code", "CONFIG_DISABLED")
                msg = body.get("error", {}).get("message", "")
            except Exception:
                code, msg = "CONFIG_DISABLED", resp.text[:200]
            raise ConfigError(code, msg)
        if not resp.is_success:
            try:
                body = resp.json()
                code = body.get("error", {}).get("code", "CONFIG_SERVICE_ERROR")
                msg = body.get("error", {}).get("message", str(resp.status_code))
            except Exception:
                code, msg = "CONFIG_SERVICE_ERROR", resp.text[:200] or str(resp.status_code)
            raise ConfigError(code, msg)

        return resp.json()
