from __future__ import annotations

import pytest

from modules.ai_relay_account.config import (
    AIRelayConfigError,
    AIRelayConfigProvider,
    AIRelayRuntimeConfig,
)
from modules.config.system_config_client import ConfigError


class FakeConfigClient:
    def __init__(self, values):
        self.values = dict(values)
        self.calls = []

    async def get_string(self, domain, config_key, environment=None):
        self.calls.append(("string", domain, config_key, environment))
        value = self.values.get((domain, config_key, environment))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise ConfigError("CONFIG_NOT_FOUND", "not found")
        return value

    async def get_json(self, domain, config_key, environment=None):
        self.calls.append(("json", domain, config_key, environment))
        value = self.values.get((domain, config_key, environment))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise ConfigError("CONFIG_NOT_FOUND", "not found")
        return value

    async def get_decimal(self, domain, config_key, environment=None):
        self.calls.append(("decimal", domain, config_key, environment))
        value = self.values.get((domain, config_key, environment))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise ConfigError("CONFIG_NOT_FOUND", "not found")
        return float(value)


@pytest.mark.asyncio
async def test_ai_relay_config_provider_uses_safe_defaults_without_config_client():
    provider = AIRelayConfigProvider()

    runtime = await provider.get_runtime_config()

    assert runtime == AIRelayRuntimeConfig(
        default_group="default",
        allowed_groups=frozenset({"default"}),
        default_preferences={},
        model_tier_mapping={},
        initial_user_quota_usd=100.0,
    )


@pytest.mark.asyncio
async def test_ai_relay_config_provider_reads_system_configs():
    config = FakeConfigClient(
        {
            ("ai_relay", "default_group", "prod"): "pro",
            ("ai_relay", "allowed_groups", "prod"): ["default", "pro"],
            ("ai_relay", "default_preferences", "prod"): {"balanced": "gpt-4o"},
            ("ai_relay", "model_tier_mapping", "prod"): {
                "balanced": {"default": "gpt-4o", "models": ["gpt-4o", "gpt-4o-mini"]}
            },
            ("ai_relay", "initial_user_quota_usd", "prod"): 25,
        }
    )
    provider = AIRelayConfigProvider(config_client=config, environment="prod")

    runtime = await provider.get_runtime_config()

    assert runtime.default_group == "pro"
    assert runtime.allowed_groups == frozenset({"default", "pro"})
    assert runtime.default_preferences == {"balanced": "gpt-4o"}
    assert runtime.model_tier_mapping == {
        "balanced": {"default": "gpt-4o", "models": ["gpt-4o", "gpt-4o-mini"]}
    }
    assert runtime.initial_user_quota_usd == 25.0


@pytest.mark.asyncio
async def test_ai_relay_config_provider_resolves_requested_group_and_preferences():
    config = FakeConfigClient(
        {
            ("ai_relay", "default_group", "test"): "default",
            ("ai_relay", "allowed_groups", "test"): ["default", "premium"],
            ("ai_relay", "default_preferences", "test"): {"balanced": "gpt-4o"},
            ("ai_relay", "model_tier_mapping", "test"): {"fast": "gpt-4o-mini"},
        }
    )
    provider = AIRelayConfigProvider(config_client=config, environment="test")

    group = await provider.resolve_group("premium")
    preferences = await provider.resolve_preferences({"fast": "gpt-4o-mini"})

    assert group == "premium"
    assert preferences == {"balanced": "gpt-4o", "fast": "gpt-4o-mini"}
    assert await provider.get_model_tier_mapping() == {"fast": "gpt-4o-mini"}


@pytest.mark.asyncio
async def test_ai_relay_config_provider_rejects_group_not_in_allowed_groups():
    config = FakeConfigClient(
        {
            ("ai_relay", "default_group", "prod"): "default",
            ("ai_relay", "allowed_groups", "prod"): ["default"],
        }
    )
    provider = AIRelayConfigProvider(config_client=config, environment="prod")

    with pytest.raises(AIRelayConfigError) as exc:
        await provider.resolve_group("premium")

    assert exc.value.code == "INVALID_GROUP"


@pytest.mark.asyncio
async def test_ai_relay_config_provider_rejects_invalid_allowed_groups_shape():
    config = FakeConfigClient(
        {
            ("ai_relay", "default_group", "prod"): "default",
            ("ai_relay", "allowed_groups", "prod"): {"bad": "shape"},
        }
    )
    provider = AIRelayConfigProvider(config_client=config, environment="prod")

    with pytest.raises(AIRelayConfigError) as exc:
        await provider.get_runtime_config()

    assert exc.value.code == "AI_RELAY_CONFIG_INVALID"


@pytest.mark.asyncio
async def test_ai_relay_config_provider_maps_config_errors():
    config = FakeConfigClient(
        {
            ("ai_relay", "default_group", "prod"): ConfigError(
                "CONFIG_DISABLED",
                "disabled",
            )
        }
    )
    provider = AIRelayConfigProvider(config_client=config, environment="prod")

    with pytest.raises(AIRelayConfigError) as exc:
        await provider.get_runtime_config()

    assert exc.value.code == "AI_RELAY_CONFIG_ERROR"
    assert "CONFIG_DISABLED" in exc.value.message
