from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from modules.config.system_config_client import ConfigError


AI_RELAY_CONFIG_DOMAIN = "ai_relay"
DEFAULT_GROUP = "default"
DEFAULT_INITIAL_USER_QUOTA_USD = 100.0


class AIRelayConfigError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigClient(Protocol):
    async def get_string(
        self,
        domain: str,
        config_key: str,
        environment: str | None = None,
    ) -> str: ...

    async def get_json(
        self,
        domain: str,
        config_key: str,
        environment: str | None = None,
    ) -> Any: ...

    async def get_decimal(
        self,
        domain: str,
        config_key: str,
        environment: str | None = None,
    ) -> float: ...


@dataclass(frozen=True)
class AIRelayRuntimeConfig:
    default_group: str = DEFAULT_GROUP
    allowed_groups: frozenset[str] = field(default_factory=lambda: frozenset({DEFAULT_GROUP}))
    default_preferences: dict[str, str] = field(default_factory=dict)
    model_tier_mapping: dict[str, Any] = field(default_factory=dict)
    initial_user_quota_usd: float = DEFAULT_INITIAL_USER_QUOTA_USD


@dataclass
class AIRelayConfigProvider:
    config_client: ConfigClient | None = None
    environment: str | None = None

    async def get_runtime_config(self) -> AIRelayRuntimeConfig:
        if self.config_client is None:
            return AIRelayRuntimeConfig()

        default_group = await self._get_string("default_group", DEFAULT_GROUP)
        allowed_groups = await self._get_allowed_groups(default_group)
        default_preferences = await self._get_string_map("default_preferences", {})
        model_tier_mapping = await self._get_json_object("model_tier_mapping", {})
        initial_user_quota_usd = await self._get_decimal(
            "initial_user_quota_usd",
            DEFAULT_INITIAL_USER_QUOTA_USD,
        )

        if default_group not in allowed_groups:
            allowed_groups = frozenset({*allowed_groups, default_group})

        return AIRelayRuntimeConfig(
            default_group=default_group,
            allowed_groups=allowed_groups,
            default_preferences=default_preferences,
            model_tier_mapping=model_tier_mapping,
            initial_user_quota_usd=initial_user_quota_usd,
        )

    async def resolve_group(self, requested_group: str | None) -> str:
        runtime = await self.get_runtime_config()
        group = _normalize_group(requested_group or runtime.default_group)
        if group not in runtime.allowed_groups:
            raise AIRelayConfigError("INVALID_GROUP", f"group is not allowed: {group}")
        return group

    async def resolve_preferences(
        self,
        requested_preferences: Mapping[str, str] | None,
    ) -> dict[str, str]:
        runtime = await self.get_runtime_config()
        merged = dict(runtime.default_preferences)
        merged.update(_string_map(requested_preferences or {}))
        return merged

    async def get_model_tier_mapping(self) -> dict[str, Any]:
        runtime = await self.get_runtime_config()
        return dict(runtime.model_tier_mapping)

    async def get_initial_user_quota_usd(self) -> float:
        runtime = await self.get_runtime_config()
        return runtime.initial_user_quota_usd

    async def _get_string(self, config_key: str, default: str) -> str:
        try:
            value = await self.config_client.get_string(
                AI_RELAY_CONFIG_DOMAIN,
                config_key,
                self.environment,
            )
        except ConfigError as exc:
            if exc.code == "CONFIG_NOT_FOUND":
                return default
            raise _to_ai_relay_config_error(exc) from exc
        return _normalize_group(value) if config_key.endswith("_group") else value.strip()

    async def _get_allowed_groups(self, default_group: str) -> frozenset[str]:
        try:
            raw = await self.config_client.get_json(
                AI_RELAY_CONFIG_DOMAIN,
                "allowed_groups",
                self.environment,
            )
        except ConfigError as exc:
            if exc.code == "CONFIG_NOT_FOUND":
                return frozenset({default_group})
            raise _to_ai_relay_config_error(exc) from exc

        if isinstance(raw, dict):
            raw = raw.get("groups")
        if not isinstance(raw, list):
            raise AIRelayConfigError(
                "AI_RELAY_CONFIG_INVALID",
                "ai_relay.allowed_groups must be a JSON string array",
            )
        groups = frozenset(_normalize_group(item) for item in raw if isinstance(item, str) and item.strip())
        if not groups:
            raise AIRelayConfigError(
                "AI_RELAY_CONFIG_INVALID",
                "ai_relay.allowed_groups must include at least one group",
            )
        return groups

    async def _get_string_map(self, config_key: str, default: dict[str, str]) -> dict[str, str]:
        try:
            raw = await self.config_client.get_json(
                AI_RELAY_CONFIG_DOMAIN,
                config_key,
                self.environment,
            )
        except ConfigError as exc:
            if exc.code == "CONFIG_NOT_FOUND":
                return dict(default)
            raise _to_ai_relay_config_error(exc) from exc
        return _string_map(raw)

    async def _get_json_object(self, config_key: str, default: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = await self.config_client.get_json(
                AI_RELAY_CONFIG_DOMAIN,
                config_key,
                self.environment,
            )
        except ConfigError as exc:
            if exc.code == "CONFIG_NOT_FOUND":
                return dict(default)
            raise _to_ai_relay_config_error(exc) from exc
        if not isinstance(raw, Mapping):
            raise AIRelayConfigError("AI_RELAY_CONFIG_INVALID", "expected a JSON object")
        return dict(raw)

    async def _get_decimal(self, config_key: str, default: float) -> float:
        try:
            value = await self.config_client.get_decimal(
                AI_RELAY_CONFIG_DOMAIN,
                config_key,
                self.environment,
            )
        except ConfigError as exc:
            if exc.code == "CONFIG_NOT_FOUND":
                return default
            raise _to_ai_relay_config_error(exc) from exc
        if value < 0:
            raise AIRelayConfigError(
                "AI_RELAY_CONFIG_INVALID",
                f"ai_relay.{config_key} must be >= 0",
            )
        return value


def _normalize_group(group: str) -> str:
    value = group.strip()
    if not value:
        raise AIRelayConfigError("INVALID_GROUP", "group cannot be empty")
    if len(value) > 64:
        raise AIRelayConfigError("INVALID_GROUP", "group is too long")
    return value


def _string_map(value: Mapping[str, str] | Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AIRelayConfigError("AI_RELAY_CONFIG_INVALID", "expected a JSON object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise AIRelayConfigError("AI_RELAY_CONFIG_INVALID", "expected string keys and values")
        k = key.strip()
        v = item.strip()
        if k and v:
            result[k] = v
    return result


def _to_ai_relay_config_error(exc: ConfigError) -> AIRelayConfigError:
    return AIRelayConfigError("AI_RELAY_CONFIG_ERROR", f"{exc.code}: {exc.message}")
