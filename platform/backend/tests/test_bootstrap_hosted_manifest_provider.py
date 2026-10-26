from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bootstrap import _HostedSessionManifestProvider, _hosted_audit_path
from common.settings.env import Settings
from modules.gateway.hosted_session import HostedSessionRegistry
from modules.mcp.capabilities.contracts import build_capability_contract_catalog
from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
from modules.mcp.capabilities.snapshot import compute_capability_snapshot_digest
from modules.mcp.contracts.models import ExecutionContext


ROOT = Path(__file__).resolve().parents[3]


class _UnusedRouter:
    pass


@pytest.mark.asyncio
async def test_hosted_bootstrap_manifest_provider_awaits_real_async_registry_shape():
    """Regression: CapabilityRuntime must receive entries, never a coroutine."""
    entries = (
        ("protocol-2.4.5", "say", "2.4.5"),
        ("protocol-9.0", "future_unknown", "9.0.0"),
    )
    registry = HostedSessionRegistry()
    # Keep the real registry method/lock/async boundary while installing the
    # smallest authenticated route facts needed by capability_entries().
    registry._routes["hosted-game"] = SimpleNamespace(
        connection_id="connection-1",
        facts=SimpleNamespace(capability_entries=entries),
    )
    provider = _HostedSessionManifestProvider(registry)
    context = ExecutionContext(
        "hosted", "principal", "runtime", 1, "binding", 1,
        "hosted-game", "nonce", 1, frozenset({"game:write"}), frozenset(),
        compute_capability_snapshot_digest(entries), 0.0,
    )

    resolved = await provider.entries(context)
    assert not inspect.isawaitable(resolved)
    assert [entry["capability_id"] for entry in resolved] == [
        "say", "future_unknown"
    ]

    runtime = RouterBackedCapabilityRuntime(
        _UnusedRouter(), provider, build_capability_contract_catalog(ROOT),
        context_ref=lambda value: value.game_session_ref,
    )
    snapshot = await runtime.snapshot(context)
    assert [contract.capability_id for contract in snapshot.known_contracts] == ["say"]
    assert [(item.code, item.capability_id) for item in snapshot.diagnostics] == [
        ("unknown_source_surface", "future_unknown")
    ]


def test_formal_hosted_audit_path_is_under_canonical_log_dir(tmp_path):
    settings = Settings(log_dir=str(tmp_path / "logs"))
    assert _hosted_audit_path(settings) == (
        tmp_path / "logs/audit/hosted-mcp-audit.jsonl"
    ).resolve()
