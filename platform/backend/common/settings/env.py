from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

VALID_CONFIG_ENVIRONMENTS = frozenset(("dev", "test", "prod"))
VALID_HOSTED_RUNTIME_ADMISSION_MODES = frozenset(("allowlist", "all_authenticated"))
MAX_LOG_BYTES = 1024 * 1024 * 1024
MAX_LOG_BACKUP_COUNT = 100
MAX_ACTIVE_RUNTIMES_GLOBAL = 10_000
MAX_ACTIVE_RUNTIMES_PER_USER = 1_000
MAX_RUNTIME_INPUT_BYTES = 16 * 1024 * 1024
MAX_HOSTED_MCP_INFLIGHT_CALLS = 1_000
MAX_HOSTED_MCP_QUEUE_SIZE = 10_000


def _strip_quotes(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def load_optional_env_files(repo_root: Path) -> None:
    for rel in ("platform/web/.env.local", "platform/backend/.env.local", "platform/supabase/.env"):
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            if t.startswith("export "):
                t = t[7:].strip()
            eq = t.find("=")
            if eq < 1:
                continue
            key = t[:eq].strip()
            val = _strip_quotes(t[eq + 1:])
            if key and key not in os.environ:
                os.environ[key] = val


def resolve_config_environment(raw: str | None) -> str:
    value = (raw or "").strip() or "prod"
    if value not in VALID_CONFIG_ENVIRONMENTS:
        raise ValueError(
            f"STARCOPLAY_CONFIG_ENV must be dev/test/prod, got: {value}"
        )
    return value


def resolve_hosted_runtime_admission_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower() or "allowlist"
    if value not in VALID_HOSTED_RUNTIME_ADMISSION_MODES:
        raise ValueError(
            "STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE must be "
            f"allowlist/all_authenticated, got: {value}"
        )
    return value


def _bounded_positive_int(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer <= {maximum}") from exc
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be a positive integer <= {maximum}")
    return value


def resolve_log_rotation_settings() -> tuple[int, int]:
    return (
        _bounded_positive_int(
            "STARCOPLAY_LOG_MAX_BYTES", 20 * 1024 * 1024, MAX_LOG_BYTES
        ),
        _bounded_positive_int(
            "STARCOPLAY_LOG_BACKUP_COUNT", 7, MAX_LOG_BACKUP_COUNT
        ),
    )


def _exact_csv_set(name: str) -> frozenset[str]:
    return frozenset(
        value for value in (part.strip() for part in os.environ.get(name, "").split(","))
        if value
    )


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    redis_url: str = ""

    ws_gateway_host: str = "0.0.0.0"
    ws_gateway_port: int = 8765
    ws_gateway_path: str = "/ws"

    http_host: str = "0.0.0.0"
    http_port: int = 8766
    http_allow_origin: str = "*"

    newapi_base_url: str = ""
    newapi_admin_access_token: str = ""
    newapi_sys_key: str = ""
    newapi_admin_user_id: int = 0

    platform_url: str = "http://127.0.0.1:3000"
    admin_secret: str = ""
    internal_api_token: str = ""
    config_environment: str = "prod"
    log_level: str = "INFO"
    log_dir: str = "/var/log/starcoplay"
    log_max_bytes: int = 20 * 1024 * 1024
    log_backup_count: int = 7

    supabase_url: str = ""
    supabase_service_role_key: str = ""

    auth_bypass: bool = False
    auth_bypass_user_id: str = ""
    runtime_manager_enabled: bool = False
    runtime_manager_workspace_root: str = "/var/lib/starcoplay/runtimes"
    claude_binary: str = ""
    claude_cli_path: str = ""
    runtime_control_claims_secret: str = ""
    runtime_control_services: dict = field(default_factory=dict)
    runtime_llm_relay_secret: str = ""
    hosted_runtime_admission_mode: str = "allowlist"
    hosted_runtime_allowlist: frozenset[str] = frozenset()
    runtime_max_active_global: int = 20
    runtime_max_active_per_user: int = 1
    runtime_max_input_bytes: int = 64 * 1024
    runtime_max_inflight_inputs: int = 1
    hosted_mcp_max_inflight_calls: int = 1
    hosted_mcp_queue_size: int = 8

    @classmethod
    def from_env(cls) -> Settings:
        log_max_bytes, log_backup_count = resolve_log_rotation_settings()
        return cls(
            database_url=os.environ.get("DATABASE_URL", "").strip(),
            redis_url=os.environ.get("REDIS_URL", "").strip(),
            ws_gateway_host=os.environ.get("WS_GATEWAY_HOST", "0.0.0.0"),
            ws_gateway_port=int(os.environ.get("WS_GATEWAY_PORT", "8765")),
            ws_gateway_path=os.environ.get("WS_GATEWAY_PATH", "/ws"),
            http_host=os.environ.get("WS_HTTP_HOST", "0.0.0.0"),
            http_port=int(os.environ.get("WS_HTTP_PORT", "8766")),
            http_allow_origin=os.environ.get("WS_HTTP_ALLOW_ORIGIN", "*"),
            newapi_base_url=(
                os.environ.get("NEWAPI_BASE_URL", "")
                or os.environ.get("NEW_API_BASE_URL", "")
            ).strip(),
            newapi_admin_access_token=(
                os.environ.get("NEWAPI_ADMIN_ACCESS_TOKEN", "")
                or os.environ.get("NEW_API_ADMIN_ACCESS_TOKEN", "")
            ).strip(),
            newapi_sys_key=os.environ.get("NEW_API_SYS_KEY", "").strip(),
            newapi_admin_user_id=int(os.environ.get("NEWAPI_ADMIN_USER_ID", "0") or "0"),
            platform_url=os.environ.get("STARCOPLAY_PLATFORM_URL", "http://127.0.0.1:3000"),
            admin_secret=os.environ.get("ADMIN_SECRET", ""),
            internal_api_token=(
                os.environ.get("INTERNAL_API_TOKEN", "")
                or os.environ.get("INTERNAL_CONFIG_TOKEN", "")
            ),
            config_environment=resolve_config_environment(
                os.environ.get("STARCOPLAY_CONFIG_ENV")
            ),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            log_dir=os.environ.get("STARCOPLAY_LOG_DIR", "/var/log/starcoplay"),
            log_max_bytes=log_max_bytes,
            log_backup_count=log_backup_count,
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            auth_bypass=os.environ.get("STARCOPLAY_WS_AUTH_BYPASS", "") == "1",
            auth_bypass_user_id=os.environ.get(
                "STARCOPLAY_WS_AUTH_BYPASS_USER_ID",
                "",
            ),
            runtime_manager_enabled=os.environ.get("STARCOPLAY_RUNTIME_MANAGER_ENABLED", "") == "1",
            runtime_manager_workspace_root=os.environ.get(
                "STARCOPLAY_RUNTIME_MANAGER_WORKSPACE_ROOT", "/var/lib/starcoplay/runtimes"
            ),
            claude_binary=os.environ.get("STARCOPLAY_CLAUDE_BINARY", "").strip(),
            claude_cli_path=os.environ.get("STARCOPLAY_CLAUDE_CLI_PATH", "").strip(),
            runtime_control_claims_secret=os.environ.get("STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET", "").strip(),
            runtime_control_services=json.loads(os.environ.get("STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON", "{}")),
            runtime_llm_relay_secret=os.environ.get("STARCOPLAY_RUNTIME_LLM_RELAY_SECRET", "").strip(),
            hosted_runtime_admission_mode=resolve_hosted_runtime_admission_mode(
                os.environ.get("STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE")
            ),
            hosted_runtime_allowlist=(
                _exact_csv_set("STARCOPLAY_CC_ALLOWLIST")
                if os.environ.get("STARCOPLAY_CC_ALLOWLIST") is not None
                else _exact_csv_set("STARCOPLAY_HOSTED_RUNTIME_ALLOWLIST")
            ),
            runtime_max_active_global=_bounded_positive_int(
                "STARCOPLAY_RUNTIME_MAX_ACTIVE_GLOBAL", 20, MAX_ACTIVE_RUNTIMES_GLOBAL
            ),
            runtime_max_active_per_user=_bounded_positive_int(
                "STARCOPLAY_RUNTIME_MAX_ACTIVE_PER_USER", 1, MAX_ACTIVE_RUNTIMES_PER_USER
            ),
            runtime_max_input_bytes=_bounded_positive_int(
                "STARCOPLAY_RUNTIME_MAX_INPUT_BYTES", 64 * 1024, MAX_RUNTIME_INPUT_BYTES
            ),
            runtime_max_inflight_inputs=_bounded_positive_int(
                "STARCOPLAY_RUNTIME_MAX_INFLIGHT_INPUTS", 1, 1
            ),
            hosted_mcp_max_inflight_calls=_bounded_positive_int(
                "STARCOPLAY_HOSTED_MCP_MAX_INFLIGHT_CALLS", 1,
                MAX_HOSTED_MCP_INFLIGHT_CALLS,
            ),
            hosted_mcp_queue_size=_bounded_positive_int(
                "STARCOPLAY_HOSTED_MCP_QUEUE_SIZE", 8, MAX_HOSTED_MCP_QUEUE_SIZE
            ),
        )
