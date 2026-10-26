"""TDD contract tests for Agent Runtime system config client.

These tests intentionally target a module that does not exist yet:
``agent/config/system_config_client.py``.

The contract is:
  - Agent Runtime reads configs through the backend internal HTTP config endpoint.
  - Agent Runtime keeps its own 60s in-process TTL cache.
  - The cache key includes environment/domain/config_key.
  - Error responses are never cached.
  - Requests must include an internal service token header.
  - Typed getters reject mismatched value_type instead of coercing.
"""

import pytest

from config.system_config_client import (
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


class FakeHttpClient:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def get_json(self, path, headers):
        self.calls.append({"path": path, "headers": headers})
        if not self.scripts:
            raise AssertionError("FakeHttpClient scripts exhausted")
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
async def test_agent_config_client_fetches_with_internal_service_token():
    http = FakeHttpClient([config_payload(value_type="string", parsed_value="balanced", config_value="balanced")])
    client = SystemConfigClient(
        base_url="https://backend.star-coplay.example",
        service_token="internal-token",
        default_environment="test",
        http_client=http,
        clock=lambda: 1000.0,
    )

    value = await client.get_string("agent_runtime", "default_model_tier")

    assert value == "balanced"
    assert http.calls == [
        {
            "path": "/internal/configs/agent_runtime/default_model_tier?env=test",
            "headers": {"authorization": "Bearer internal-token"},
        }
    ]


@pytest.mark.asyncio
async def test_agent_config_client_caches_successful_reads_for_60_seconds():
    clock = FakeClock()
    http = FakeHttpClient(
        [
            config_payload(parsed_value=8, config_value="8"),
            config_payload(parsed_value=9, config_value="9"),
        ]
    )
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        default_environment="prod",
        http_client=http,
        clock=clock.now,
    )

    assert CONFIG_CACHE_TTL_SECONDS == 60
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 8
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 8
    assert len(http.calls) == 1

    clock.advance(59.9)
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 8
    assert len(http.calls) == 1

    clock.advance(0.1)
    assert await client.get_int("agent_runtime", "max_loop_steps", "prod") == 9
    assert len(http.calls) == 2


@pytest.mark.asyncio
async def test_agent_config_client_cache_key_includes_environment():
    http = FakeHttpClient(
        [
            config_payload(value_type="string", parsed_value="dev-value", config_value="dev-value"),
            config_payload(value_type="string", parsed_value="prod-value", config_value="prod-value"),
        ]
    )
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        default_environment="dev",
        http_client=http,
        clock=lambda: 1000.0,
    )

    assert await client.get_string("agent_runtime", "default_model_tier", "dev") == "dev-value"
    assert await client.get_string("agent_runtime", "default_model_tier", "prod") == "prod-value"
    assert await client.get_string("agent_runtime", "default_model_tier") == "dev-value"
    assert len(http.calls) == 2


@pytest.mark.asyncio
async def test_agent_config_client_does_not_cache_errors():
    http = FakeHttpClient(
        [
            ConfigError("CONFIG_NOT_FOUND", "missing"),
            ConfigError("CONFIG_DISABLED", "disabled"),
            config_payload(value_type="bool", parsed_value=True, config_value="true"),
        ]
    )
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        default_environment="prod",
        http_client=http,
        clock=lambda: 1000.0,
    )

    with pytest.raises(ConfigError) as not_found:
        await client.get_bool("agent_runtime", "feature_enabled", "prod")
    assert not_found.value.code == "CONFIG_NOT_FOUND"

    with pytest.raises(ConfigError) as disabled:
        await client.get_bool("agent_runtime", "feature_enabled", "prod")
    assert disabled.value.code == "CONFIG_DISABLED"

    assert await client.get_bool("agent_runtime", "feature_enabled", "prod") is True
    assert len(http.calls) == 3


@pytest.mark.asyncio
async def test_agent_config_client_typed_getters_reject_mismatched_value_type():
    http = FakeHttpClient([config_payload(value_type="string", parsed_value="true", config_value="true")])
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        default_environment="prod",
        http_client=http,
        clock=lambda: 1000.0,
    )

    with pytest.raises(ConfigError) as exc:
        await client.get_bool("agent_runtime", "feature_enabled", "prod")
    assert exc.value.code == "CONFIG_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_agent_config_client_clear_cache_forces_refetch():
    http = FakeHttpClient(
        [
            config_payload(value_type="string", parsed_value="first", config_value="first"),
            config_payload(value_type="string", parsed_value="second", config_value="second"),
        ]
    )
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        default_environment="prod",
        http_client=http,
        clock=lambda: 1000.0,
    )

    assert await client.get_string("agent_runtime", "default_model_tier", "prod") == "first"
    client.clear_cache()
    assert await client.get_string("agent_runtime", "default_model_tier", "prod") == "second"
    assert len(http.calls) == 2


@pytest.mark.asyncio
async def test_agent_typed_getters_evict_cache_on_type_mismatch():
    """类型不匹配后缓存被清除，下次调用重新 fetch 而不是从缓存返回旧错误。"""
    # 每个 typed getter 各测一次：先返回错误类型，再返回正确类型
    cases = [
        ("get_int",     "string", "42",   "int",     42,    int),
        ("get_decimal", "string", "1.5",  "decimal", 1.5,   float),
        ("get_string",  "bool",   "true", "string",  "ok",  str),
        ("get_json",    "string", "{}",   "json",    {},    dict),
    ]
    for method, wrong_type, wrong_val, right_type, right_val, _ in cases:
        http = FakeHttpClient(
            [
                config_payload(value_type=wrong_type, parsed_value=wrong_val, config_value=wrong_val),
                config_payload(value_type=right_type, parsed_value=right_val, config_value=str(right_val)),
            ]
        )
        client = SystemConfigClient(
            base_url="https://star-coplay.example",
            service_token="internal-token",
            default_environment="prod",
            http_client=http,
            clock=lambda: 1000.0,
        )
        with pytest.raises(ConfigError) as exc:
            await getattr(client, method)("agent_runtime", "cfg", "prod")
        assert exc.value.code == "CONFIG_TYPE_MISMATCH", method

        # 类型不匹配后缓存已清除，第二次调用必须重新 fetch
        result = await getattr(client, method)("agent_runtime", "cfg", "prod")
        assert result == right_val, method
        assert len(http.calls) == 2, f"{method}: expected 2 HTTP calls, got {len(http.calls)}"


def test_agent_config_client_reads_default_environment_from_process_env(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_CONFIG_ENV", "test")
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        http_client=FakeHttpClient([]),
        clock=lambda: 1000.0,
    )
    assert client.default_environment == "test"


def test_agent_config_client_defaults_to_prod_when_env_missing(monkeypatch):
    monkeypatch.delenv("STARCOPLAY_CONFIG_ENV", raising=False)
    client = SystemConfigClient(
        base_url="https://star-coplay.example",
        service_token="internal-token",
        http_client=FakeHttpClient([]),
        clock=lambda: 1000.0,
    )
    assert client.default_environment == "prod"


def test_agent_config_client_rejects_invalid_default_environment(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_CONFIG_ENV", "stage")
    with pytest.raises(ConfigError) as exc:
        SystemConfigClient(
            base_url="https://star-coplay.example",
            service_token="internal-token",
            http_client=FakeHttpClient([]),
            clock=lambda: 1000.0,
        )
    assert exc.value.code == "CONFIG_INVALID_ENVIRONMENT"
