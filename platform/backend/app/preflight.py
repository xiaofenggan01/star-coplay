"""Fail-fast deployment checks for the Hosted Runtime process profile."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from common.settings.env import Settings


class HostedRuntimePreflightError(RuntimeError):
    pass


def _resolve_binary(value: str) -> str | None:
    if not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return str(candidate) if candidate.is_file() else None
    return shutil.which(value)


def validate_hosted_runtime_startup(
    settings: Settings,
    *,
    hosted_route_enabled: bool,
    probe: Callable[[Sequence[str]], tuple[int, str]] | None = None,
) -> None:
    if not settings.runtime_manager_enabled:
        return

    missing: list[str] = []
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if len(settings.runtime_control_claims_secret) < 32:
        missing.append("STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET")
    if not settings.runtime_control_services:
        missing.append("STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON")
    if len(settings.runtime_llm_relay_secret) < 32:
        missing.append("STARCOPLAY_RUNTIME_LLM_RELAY_SECRET")
    if not hosted_route_enabled:
        missing.append("HOSTED_GAME_SESSION_ROUTE_ENABLED=1")
    if (
        settings.hosted_runtime_admission_mode == "allowlist"
        and not settings.hosted_runtime_allowlist
    ):
        missing.append("STARCOPLAY_CC_ALLOWLIST")
    if missing:
        raise HostedRuntimePreflightError(
            "Hosted Runtime configuration is incomplete: " + ", ".join(missing)
        )

    binary = _resolve_binary(settings.claude_binary)
    if binary is None:
        raise HostedRuntimePreflightError(
            "STARCOPLAY_CLAUDE_BINARY must resolve to an installed executable"
        )
    command = [binary]
    if settings.claude_cli_path:
        cli_path = Path(settings.claude_cli_path)
        if not cli_path.is_file():
            raise HostedRuntimePreflightError(
                "STARCOPLAY_CLAUDE_CLI_PATH must resolve to an installed CLI file"
            )
        command.append(str(cli_path))
    command.append("--version")

    if probe is None:
        def probe(args: Sequence[str]) -> tuple[int, str]:
            completed = subprocess.run(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=20,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return completed.returncode, completed.stdout
    try:
        return_code, _version_output = probe(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostedRuntimePreflightError(
            "Claude Code launch preflight could not execute"
        ) from exc
    if return_code != 0:
        raise HostedRuntimePreflightError(
            "Claude Code launch preflight returned a non-zero status"
        )
    contract_code, _contract_output = probe([
        *command[:-1],
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--strict-mcp-config",
        "--tools", "",
        "--permission-mode", "default",
        "--mcp-config", "__starcoplay_preflight_missing__.json",
        "--append-system-prompt-file", "__starcoplay_preflight_missing__.txt",
        "--allowedTools", "mcp__starcoplay-hosted__*",
        "--help",
    ])
    if contract_code != 0:
        raise HostedRuntimePreflightError(
            "Claude Code is incompatible with the Hosted Runtime command contract"
        )


def main() -> int:
    try:
        settings = Settings.from_env()
        validate_hosted_runtime_startup(
            settings,
            hosted_route_enabled=(
                os.environ.get("HOSTED_GAME_SESSION_ROUTE_ENABLED", "").strip().lower()
                in ("1", "true", "yes")
            ),
        )
    except (ValueError, HostedRuntimePreflightError) as exc:
        print(f"Hosted Runtime preflight failed: {exc}")
        return 1
    print("Hosted Runtime preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
