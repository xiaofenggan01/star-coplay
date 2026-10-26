import pytest

from common.settings.env import Settings


def test_settings_reads_internal_api_token_with_legacy_fallback(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_CONFIG_TOKEN", raising=False)
    assert Settings.from_env().internal_api_token == ""

    monkeypatch.setenv("INTERNAL_CONFIG_TOKEN", "legacy-token")
    assert Settings.from_env().internal_api_token == "legacy-token"

    monkeypatch.setenv("INTERNAL_API_TOKEN", "api-token")
    assert Settings.from_env().internal_api_token == "api-token"


def test_settings_reads_default_config_environment_from_env(monkeypatch):
    monkeypatch.delenv("STARCOPLAY_CONFIG_ENV", raising=False)
    settings = Settings.from_env()
    assert settings.config_environment == "prod"

    monkeypatch.setenv("STARCOPLAY_CONFIG_ENV", "test")
    settings = Settings.from_env()
    assert settings.config_environment == "test"


def test_settings_reads_logging_rotation_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("STARCOPLAY_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("STARCOPLAY_LOG_BACKUP_COUNT", raising=False)
    settings = Settings.from_env()
    assert settings.log_max_bytes == 20 * 1024 * 1024
    assert settings.log_backup_count == 7

    monkeypatch.setenv("STARCOPLAY_LOG_MAX_BYTES", "4096")
    monkeypatch.setenv("STARCOPLAY_LOG_BACKUP_COUNT", "2")
    settings = Settings.from_env()
    assert settings.log_max_bytes == 4096
    assert settings.log_backup_count == 2


def test_settings_reads_exact_hosted_allowlist_and_runtime_limits(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_HOSTED_RUNTIME_ALLOWLIST", " user-a,user-B,user-a,, ")
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MAX_ACTIVE_GLOBAL", "42")
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MAX_ACTIVE_PER_USER", "3")
    settings = Settings.from_env()
    assert settings.hosted_runtime_allowlist == frozenset(("user-a", "user-B"))
    assert "USER-A" not in settings.hosted_runtime_allowlist
    assert settings.runtime_max_active_global == 42
    assert settings.runtime_max_active_per_user == 3


def test_settings_supports_all_authenticated_hosted_admission(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE", "all_authenticated")
    settings = Settings.from_env()
    assert settings.hosted_runtime_admission_mode == "all_authenticated"


def test_settings_rejects_unknown_hosted_admission_mode(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE", "everyone")
    with pytest.raises(ValueError, match="STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE"):
        Settings.from_env()


def test_settings_reads_explicit_claude_binary(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_CLAUDE_BINARY", "C:/tools/claude.cmd")
    assert Settings.from_env().claude_binary == "C:/tools/claude.cmd"


def test_settings_reads_explicit_claude_cli_path(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_CLAUDE_CLI_PATH", "C:/tools/claude-code/cli.js")
    assert Settings.from_env().claude_cli_path == "C:/tools/claude-code/cli.js"


def test_cc_allowlist_name_overrides_legacy_hosted_name(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_CC_ALLOWLIST", "cc-user")
    monkeypatch.setenv("STARCOPLAY_HOSTED_RUNTIME_ALLOWLIST", "legacy-user")
    assert Settings.from_env().hosted_runtime_allowlist == frozenset({"cc-user"})


def test_settings_defaults_to_closed_hosted_allowlist_and_thin_limits(monkeypatch):
    for name in (
        "STARCOPLAY_HOSTED_RUNTIME_ALLOWLIST",
        "STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE",
        "STARCOPLAY_RUNTIME_MAX_ACTIVE_GLOBAL",
        "STARCOPLAY_RUNTIME_MAX_ACTIVE_PER_USER",
        "STARCOPLAY_RUNTIME_MAX_INPUT_BYTES",
        "STARCOPLAY_RUNTIME_MAX_INFLIGHT_INPUTS",
        "STARCOPLAY_HOSTED_MCP_MAX_INFLIGHT_CALLS",
        "STARCOPLAY_HOSTED_MCP_QUEUE_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.hosted_runtime_allowlist == frozenset()
    assert settings.hosted_runtime_admission_mode == "allowlist"
    assert settings.runtime_max_active_global == 20
    assert settings.runtime_max_active_per_user == 1
    assert settings.runtime_max_input_bytes == 65536
    assert settings.runtime_max_inflight_inputs == 1
    assert settings.hosted_mcp_max_inflight_calls == 1
    assert settings.hosted_mcp_queue_size == 8


def test_settings_reads_m5_bounded_ingress_limits(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MAX_INPUT_BYTES", "32768")
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MAX_INFLIGHT_INPUTS", "1")
    monkeypatch.setenv("STARCOPLAY_HOSTED_MCP_MAX_INFLIGHT_CALLS", "3")
    monkeypatch.setenv("STARCOPLAY_HOSTED_MCP_QUEUE_SIZE", "16")
    settings = Settings.from_env()
    assert settings.runtime_max_input_bytes == 32768
    assert settings.runtime_max_inflight_inputs == 1
    assert settings.hosted_mcp_max_inflight_calls == 3
    assert settings.hosted_mcp_queue_size == 16


@pytest.mark.parametrize("name,value", [
    ("STARCOPLAY_RUNTIME_MAX_INPUT_BYTES", "0"),
    ("STARCOPLAY_RUNTIME_MAX_INFLIGHT_INPUTS", "2"),
    ("STARCOPLAY_HOSTED_MCP_MAX_INFLIGHT_CALLS", "0"),
    ("STARCOPLAY_HOSTED_MCP_QUEUE_SIZE", "0"),
])
def test_settings_rejects_invalid_m5_bounded_ingress_limits(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "maximum"),
    (
        ("STARCOPLAY_RUNTIME_MAX_ACTIVE_GLOBAL", "0", 10_000),
        ("STARCOPLAY_RUNTIME_MAX_ACTIVE_GLOBAL", "10001", 10_000),
        ("STARCOPLAY_RUNTIME_MAX_ACTIVE_PER_USER", "bad", 1_000),
        ("STARCOPLAY_RUNTIME_MAX_ACTIVE_PER_USER", "1001", 1_000),
    ),
)
def test_settings_rejects_invalid_runtime_admission_limits(monkeypatch, name, value, maximum):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"{name} must be a positive integer <= {maximum}"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "not-an-int", str(1024 * 1024 * 1024 + 1)])
def test_settings_rejects_invalid_log_max_bytes(monkeypatch, value):
    monkeypatch.setenv("STARCOPLAY_LOG_MAX_BYTES", value)
    with pytest.raises(
        ValueError,
        match="STARCOPLAY_LOG_MAX_BYTES must be a positive integer <= 1073741824",
    ):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "not-an-int", "101"])
def test_settings_rejects_invalid_log_backup_count(monkeypatch, value):
    monkeypatch.setenv("STARCOPLAY_LOG_BACKUP_COUNT", value)
    with pytest.raises(
        ValueError,
        match="STARCOPLAY_LOG_BACKUP_COUNT must be a positive integer <= 100",
    ):
        Settings.from_env()


def test_settings_rejects_invalid_config_environment(monkeypatch):
    monkeypatch.setenv("STARCOPLAY_CONFIG_ENV", "stage")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_reads_newapi_env(monkeypatch):
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://127.0.0.1:13000")
    monkeypatch.setenv("NEWAPI_ADMIN_ACCESS_TOKEN", "newapi-admin-token")
    monkeypatch.setenv("NEW_API_SYS_KEY", "newapi-sys-key")
    monkeypatch.setenv("NEWAPI_ADMIN_USER_ID", "7")

    settings = Settings.from_env()

    assert settings.newapi_base_url == "http://127.0.0.1:13000"
    assert settings.newapi_admin_access_token == "newapi-admin-token"
    assert settings.newapi_sys_key == "newapi-sys-key"
    assert settings.newapi_admin_user_id == 7


def test_settings_reads_newapi_alias_env(monkeypatch):
    monkeypatch.delenv("NEWAPI_BASE_URL", raising=False)
    monkeypatch.delenv("NEWAPI_ADMIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("NEW_API_ADMIN_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("NEW_API_BASE_URL", "http://127.0.0.1:13000")
    monkeypatch.setenv("NEW_API_SYS_KEY", "newapi-sys-key")

    settings = Settings.from_env()

    assert settings.newapi_base_url == "http://127.0.0.1:13000"
    assert settings.newapi_admin_access_token == ""
    assert settings.newapi_sys_key == "newapi-sys-key"
