"""
统一业务配置系统 — platform backend ConfigClient

职责：
  - 通过 ConfigRepository 抽象读取 system_configs
  - 进程内 60 秒 TTL 缓存（成功结果才缓存，错误不缓存）
  - 类型化 getter，拒绝跨类型强转
  - 缓存 key 包含 environment/domain/config_key
"""

from __future__ import annotations

import json as _json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

VALID_CONFIG_ENVIRONMENTS = frozenset(("dev", "test", "prod"))

CONFIG_CACHE_TTL_SECONDS: int = 60

_VALUE_TYPE_CODE_TO_NAME: dict[int, str] = {
    0: "default",
    1: "bool",
    2: "int",
    3: "decimal",
    4: "string",
    5: "json",
}


class ConfigError(Exception):
    """配置读取或解析失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"ConfigError(code={self.code!r}, message={self.message!r})"


class ConfigRepository(Protocol):
    """配置数据源抽象，生产环境由 DbConfigRepository 实现，测试由 FakeConfigRepository 替换。"""

    async def fetch_config(
        self, domain: str, config_key: str, environment: str
    ) -> dict[str, Any]: ...


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
    """platform backend 统一配置读取客户端。

    参数：
        repository: 实现 fetch_config(domain, config_key, environment) 的对象
        clock:      返回当前时间戳（float）的可调用对象，默认 time.time
    """

    repository: Any
    default_environment: str = "prod"
    clock: Callable[[], float] = field(default_factory=lambda: time.time)

    _cache: dict[str, _CacheEntry] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.default_environment = resolve_config_environment(self.default_environment)

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

        payload = await self.repository.fetch_config(domain, config_key, environment)

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
        """清空进程内缓存，下次读取强制重新查询。"""
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


# ── 生产环境 Repository 实现 ──────────────────────────────────────────────

class DbConfigRepository:
    """直连 asyncpg 连接池读取 system_configs 表。"""

    def __init__(self, db_pool: Any) -> None:
        self._db_pool = db_pool

    async def fetch_config(
        self, domain: str, config_key: str, environment: str
    ) -> dict[str, Any]:
        if self._db_pool is None:
            raise ConfigError("CONFIG_SERVICE_ERROR", "database not configured")

        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, domain, config_key, environment,
                           status, value_type, config_value, updated_at
                    FROM public.system_configs
                    WHERE domain = $1 AND config_key = $2 AND environment = $3
                    LIMIT 1
                    """,
                    domain, config_key, environment,
                )
        except Exception as exc:
            raise ConfigError("CONFIG_SERVICE_ERROR", str(exc)) from exc

        if row is None:
            raise ConfigError(
                "CONFIG_NOT_FOUND",
                f"config not found: {domain}/{config_key}/{environment}",
            )

        if row["status"] == 0:
            raise ConfigError("CONFIG_DISABLED", "config is disabled")

        vt_code: int = row["value_type"]
        vt_name = _VALUE_TYPE_CODE_TO_NAME.get(vt_code, "default")
        config_value: str = row["config_value"]
        parsed = _parse_value(vt_code, config_value, domain, config_key)

        updated_at = row["updated_at"]
        updated_at_str = updated_at.isoformat() if updated_at else ""

        return {
            "domain": row["domain"],
            "config_key": row["config_key"],
            "environment": row["environment"],
            "value_type": vt_name,
            "value_type_code": vt_code,
            "config_value": config_value,
            "parsed_value": parsed,
            "updated_at": updated_at_str,
        }


# ── 解析工具 ──────────────────────────────────────────────────────────────

def _parse_value(vt_code: int, config_value: str, domain: str, config_key: str) -> Any:
    try:
        if vt_code == 0 or vt_code == 4:  # default / string
            return config_value
        if vt_code == 1:  # bool
            if config_value == "true":
                return True
            if config_value == "false":
                return False
            raise ValueError(f"invalid bool: {config_value!r}")
        if vt_code == 2:  # int
            return int(config_value)
        if vt_code == 3:  # decimal
            return float(config_value)
        if vt_code == 5:  # json
            return _json.loads(config_value)
    except (ValueError, _json.JSONDecodeError) as exc:
        raise ConfigError(
            "CONFIG_PARSE_ERROR",
            f"{domain}/{config_key}: cannot parse value_type={vt_code}: {exc}",
        ) from exc
    raise ConfigError("CONFIG_PARSE_ERROR", f"unknown value_type code: {vt_code}")
