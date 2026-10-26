"""TDD contract tests for platform backend system config client.

The backend must not import or depend on Vercel's in-process ConfigClient.
It reads system_configs through a backend-local repository/database access
layer and keeps its own 60s TTL cache inside the backend process.
"""

import pytest

from modules.config.system_config_client import (
    CONFIG_CACHE_TTL_SECONDS,
    ConfigError,
    SystemConfigClient,
)


class FakeClock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeConfigRepository:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def fetch_config(self, domain, config_key, environment):
        self.calls.append((domain, config_key, environment))
        if not self.scripts:
            raise AssertionError("FakeConfigRepository scripts exhausted")
        next_item = self.scripts.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


def config_payload(value_type="int", parsed_value=1, config_value="1"):
    return {
        "domain": "agent_runtime",
        "config_key": "max_loop_steps",
        "environment": "prod",
        "value_type": value_type,
        "value_type_code": {"bool": 1, "int": 2, "decimal": 3, "string": 4, "json": 5}[
            value_type
        ],
        "config_value": config_value,
        "parsed_value": parsed_value,
        "updated_at": "2026-05-22T00:00:00.000Z",
    }


@pytest.mark.asyncio
async def test_backend_config_client_fetches_through_local_repository():
    repo = FakeConfigRepository([config_payload(value_type="string", parsed_value="balanced", config_value="balanced")])
    client = SystemConfigClient(
        repository=repo,
        default_environment="test",
        clock=lambda: 1000.0,
    )

    value = await client.get_string("agent_runtime", "default_model_tier")

    assert value == "balanced"
    assert repo.calls == [("agent_runtime", "default_model_tier", "test")]


@pytest.mark.asyncio
async def test_backend_config_client_caches_successful_reads_for_60_seconds():
    clock = FakeClock()
    repo = FakeConfigRepository(
        [
            config_payload(parsed_value=8, config_value="8"),
            config_payload(parsed_value=9, config_value="9"),
        ]
    )
    client = SystemConfigClient(
        repository=repo,
        default_environment="prod",
        clock=clock.now,
    )

    assert CONFIG_CACHE_TTL_SECONDS == 60
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 8
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 8
    assert len(repo.calls) == 1

    clock.advance(60)
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 9
    assert len(repo.calls) == 2


@pytest.mark.asyncio
async def test_backend_config_client_does_not_cache_errors_or_type_mismatches():
    repo = FakeConfigRepository(
        [
            ConfigError("CONFIG_DISABLED", "disabled"),
            config_payload(value_type="string", parsed_value="true", config_value="true"),
            config_payload(value_type="bool", parsed_value=True, config_value="true"),
        ]
    )
    client = SystemConfigClient(
        repository=repo,
        default_environment="prod",
        clock=lambda: 1000.0,
    )

    with pytest.raises(ConfigError) as disabled:
        await client.get_bool("agent_runtime", "feature_enabled", "prod")
    assert disabled.value.code == "CONFIG_DISABLED"

    with pytest.raises(ConfigError) as mismatch:
        await client.get_bool("agent_runtime", "feature_enabled", "prod")
    assert mismatch.value.code == "CONFIG_TYPE_MISMATCH"

    assert await client.get_bool("agent_runtime", "feature_enabled", "prod") is True
    assert len(repo.calls) == 3


@pytest.mark.asyncio
async def test_backend_typed_getters_evict_cache_on_type_mismatch():
    """类型不匹配后缓存被清除，下次调用重新 fetch 而不是从缓存返回旧错误。"""
    cases = [
        ("get_int",     "string", "42",   "int",     42),
        ("get_decimal", "string", "1.5",  "decimal", 1.5),
        ("get_string",  "bool",   "true", "string",  "ok"),
        ("get_json",    "string", "{}",   "json",    {}),
    ]
    for method, wrong_type, wrong_val, right_type, right_val in cases:
        repo = FakeConfigRepository(
            [
                config_payload(value_type=wrong_type, parsed_value=wrong_val, config_value=wrong_val),
                config_payload(value_type=right_type, parsed_value=right_val, config_value=str(right_val)),
            ]
        )
        client = SystemConfigClient(
            repository=repo,
            default_environment="prod",
            clock=lambda: 1000.0,
        )
        with pytest.raises(ConfigError) as exc:
            await getattr(client, method)("agent_runtime", "cfg", "prod")
        assert exc.value.code == "CONFIG_TYPE_MISMATCH", method

        result = await getattr(client, method)("agent_runtime", "cfg", "prod")
        assert result == right_val, method
        assert len(repo.calls) == 2, f"{method}: expected 2 repository calls, got {len(repo.calls)}"


@pytest.mark.asyncio
async def test_backend_config_client_cache_key_includes_environment():
    repo = FakeConfigRepository(
        [
            config_payload(value_type="string", parsed_value="dev-value", config_value="dev-value"),
            config_payload(value_type="string", parsed_value="prod-value", config_value="prod-value"),
        ]
    )
    client = SystemConfigClient(
        repository=repo,
        default_environment="dev",
        clock=lambda: 1000.0,
    )

    assert await client.get_string("agent_runtime", "default_model_tier") == "dev-value"
    assert await client.get_string("agent_runtime", "default_model_tier", "prod") == "prod-value"
    assert await client.get_string("agent_runtime", "default_model_tier") == "dev-value"
    assert len(repo.calls) == 2


def test_backend_config_client_rejects_invalid_default_environment():
    with pytest.raises(ConfigError) as exc:
        SystemConfigClient(repository=FakeConfigRepository([]), default_environment="stage")
    assert exc.value.code == "CONFIG_INVALID_ENVIRONMENT"
