from pathlib import Path

import pytest

from app.preflight import HostedRuntimePreflightError, validate_hosted_runtime_startup
from common.settings.env import Settings


def hosted_settings(binary: Path, **changes) -> Settings:
    values = dict(
        runtime_manager_enabled=True,
        database_url="postgresql://example",
        runtime_control_claims_secret="c" * 32,
        runtime_control_services={"platform": {"all_tenants": True}},
        runtime_llm_relay_secret="r" * 32,
        hosted_runtime_admission_mode="all_authenticated",
        claude_binary=str(binary),
    )
    values.update(changes)
    return Settings(**values)


def test_disabled_runtime_skips_hosted_checks():
    validate_hosted_runtime_startup(Settings(), hosted_route_enabled=False)


def test_all_authenticated_profile_passes_without_allowlist(tmp_path):
    binary = tmp_path / "node"
    binary.write_text("placeholder", encoding="utf-8")
    commands = []
    validate_hosted_runtime_startup(
        hosted_settings(binary),
        hosted_route_enabled=True,
        probe=lambda command: (
            commands.append(tuple(command)) or
            (0, "2.1.220")
        ),
    )
    assert commands[0] == (str(binary), "--version")
    assert commands[1][-1] == "--help"
    assert "--tools" in commands[1]


def test_allowlist_profile_requires_at_least_one_tenant(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text("placeholder", encoding="utf-8")
    with pytest.raises(HostedRuntimePreflightError, match="STARCOPLAY_CC_ALLOWLIST"):
        validate_hosted_runtime_startup(
            hosted_settings(binary, hosted_runtime_admission_mode="allowlist"),
            hosted_route_enabled=True,
            probe=lambda _command: (0, ""),
        )


def test_preflight_checks_explicit_node_and_cli_pair(tmp_path):
    binary = tmp_path / "node"
    cli = tmp_path / "cli.js"
    binary.write_text("placeholder", encoding="utf-8")
    cli.write_text("placeholder", encoding="utf-8")
    commands = []
    validate_hosted_runtime_startup(
        hosted_settings(binary, claude_cli_path=str(cli)),
        hosted_route_enabled=True,
        probe=lambda command: (
            commands.append(tuple(command)) or
            (0, "2.1.220")
        ),
    )
    assert commands[0] == (str(binary), str(cli), "--version")
    assert commands[1][:2] == (str(binary), str(cli))
    assert commands[1][-1] == "--help"


def test_preflight_rejects_failed_claude_probe(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text("placeholder", encoding="utf-8")
    with pytest.raises(HostedRuntimePreflightError, match="non-zero"):
        validate_hosted_runtime_startup(
            hosted_settings(binary),
            hosted_route_enabled=True,
            probe=lambda _command: (1, ""),
        )


def test_preflight_rejects_incompatible_runtime_command_contract(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text("placeholder", encoding="utf-8")
    calls = 0
    def probe(_command):
        nonlocal calls
        calls += 1
        return (0, "old") if calls == 1 else (1, "unknown option --tools")
    with pytest.raises(HostedRuntimePreflightError, match="command contract"):
        validate_hosted_runtime_startup(
            hosted_settings(binary),
            hosted_route_enabled=True,
            probe=probe,
        )
