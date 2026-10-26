"""Structural proof that N+1 additions remain leaf changes.

The fixture deliberately invents a Proto contract and a controlled Skill that
do not exist in the release inventory.  Production MCP Core, Profile, Router,
projection, and transport code are used unchanged.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time

import pytest

from modules.mcp.capabilities.models import (
    CapabilityContract, CapabilityContractCatalog, PreparedCapabilityCall,
    ProtocolCapabilityEntry,
)
from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
from modules.mcp.capabilities.snapshot import (
    build_capability_snapshot,
    compute_capability_snapshot_digest,
)
from modules.mcp.contracts.models import ContextHandle, ExecutionContext, GameCommandResult, SideEffect
from modules.mcp.contracts.projection import ExposureFacts
from modules.mcp.core.audit import MemoryAuditSink
from modules.mcp.core.projection import McpCallMetadata, McpProjection
from modules.mcp.core.runtime_adapter import SkillRuntimeAdapter
from modules.mcp.fakes.router import FakeGameSessionRouter, RouteFence
from modules.mcp.skills.loader import load_skill_programs
from modules.mcp.skills.models import ExposureContext, RiskDecision, SkillRef
from modules.mcp.skills.registry import SkillRegistry
from modules.mcp.skills.runtime import SkillRuntime


class _ExposurePort:
    def __init__(self, facts: ExposureFacts):
        self.facts = facts

    async def resolve_exposure(self, _handle):
        return self.facts

    async def revalidate_exposure(self, _handle):
        return self.facts


class _MustNotExecute:
    async def start(self, *_args, **_kwargs):  # pragma: no cover - list only
        raise AssertionError("N+1 projection must not execute during tools/list")


def _write_external_skill(root, *, version_range: str = ">=2.4.5,<3.0.0") -> SkillRef:
    package = root / "community.test.observe_addon"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# audited fixture\n", encoding="utf-8")
    (package / "implementation.py").write_text(
        "async def run(ctx, arguments):\n"
        "    result = await ctx.invoke('observe_addon', '>=2.4.5,<3.0.0', arguments)\n"
        "    return result.output\n",
        encoding="utf-8",
    )
    (package / "skill.yaml").write_text(
        f"""id: community.test.observe_addon
version: 1.0.0
description: N plus one controlled Skill fixture
game: stardew-valley
entrypoint: implementation:run
input_schema:
  type: object
  properties:
    subject: {{type: string, minLength: 1}}
  required: [subject]
  additionalProperties: false
output_schema:
  type: object
  properties:
    capability: {{type: string, enum: [observe_addon]}}
  required: [capability]
  additionalProperties: false
requires:
  capabilities:
    - id: observe_addon
      version: "{version_range}"
risk: [changes_save]
timeout_seconds: 30
cancellable: true
retry: never
failure_codes: [capability_failed]
""",
        encoding="utf-8",
    )
    return SkillRef("community.test.observe_addon", "1.0.0")


def _facts(snapshot, mounted, *, decision: RiskDecision) -> ExposureFacts:
    execution = ExecutionContext(
        "local_direct", "principal", "runtime", 1, "binding", 1, "game", "nonce", 1,
        frozenset({"game:read", "game:write"}), frozenset(),
        snapshot.capability_digest, 0.0, expires_at=9999999999.0,
    )
    exposure = ExposureContext(
        "stardew-valley", frozenset(mounted), {"changes_save": decision}, False,
        "mount-1", "policy-1", "binding", 1, "game",
    )
    return ExposureFacts(
        execution, exposure, snapshot,
        "facts-digest",
    )


class _LeafAdapter:
    """The only execution leaf an N+1 known Proto capability must add."""

    def prepare(self, arguments, command_id):
        assert arguments == {"subject": "farm"}
        return PreparedCapabilityCall("observe_addon", "2.4.5", dict(arguments))

    def normalize(self, handle, result):
        return GameCommandResult(
            handle.command_id, result.state,
            {"capability": "observe_addon"}, result.error_code,
        )


class _ManifestProvider:
    def __init__(self, entries):
        self._entries = entries

    async def entries(self, _context):
        return self._entries


@pytest.mark.asyncio
async def test_n_plus_one_known_proto_and_external_skill_need_no_core_dispatch_branch(tmp_path):
    """Static contract + Manifest + package are sufficient for dynamic projection."""
    builtin = tmp_path / "builtin"
    external = tmp_path / "controlled"
    builtin.mkdir()
    external.mkdir()
    skill_ref = _write_external_skill(external)
    registry = SkillRegistry.index(builtin, external)

    addon = CapabilityContract(
        "observe_addon", "2.4.5", "protocol-2.4.5",
        {"type": "object", "additionalProperties": False},
        {"type": "object", "additionalProperties": False},
        "game:write", SideEffect.MUTATING, 30.0, True, True, False,
        adapter=_LeafAdapter(),
    )
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "observe_addon", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "future_unknown", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    context = ExecutionContext(
        "local_direct", "principal", "runtime", 1, "binding", 1, "game", "nonce", 1,
        frozenset({"game:write"}), frozenset(), digest, 0.0,
    )
    snapshot = build_capability_snapshot(entries, (addon,), context)

    assert [contract.capability_id for contract in snapshot.known_contracts] == [
        "observe_addon"
    ]
    assert {entry.capability_id for entry in snapshot.raw_entries} == {
        "observe_addon", "future_unknown"
    }
    assert [(item.code, item.capability_id) for item in snapshot.diagnostics] == [
        ("unknown_capability", "future_unknown")
    ]

    port = _ExposurePort(_facts(snapshot, {skill_ref}, decision=RiskDecision.ALLOW))
    projection = McpProjection(registry, port, _MustNotExecute())
    tools = await projection.list_tools(ContextHandle("n-plus-one"))
    assert [tool.name for tool in tools] == ["skill:community.test.observe_addon@1.0.0"]
    assert tools[0].skill_ref == skill_ref

    # Load and execute the same directory package through the real generic
    # SkillRuntime -> CapabilityRuntime -> Router chain. No MCP/Core/Profile
    # branch knows the invented capability or Skill id.
    router = FakeGameSessionRouter(RouteFence(
        "game", "nonce", 1, 1, 1, digest,
        principal_id="principal", runtime_ref="runtime", binding_ref="binding",
    ))
    capability_runtime = RouterBackedCapabilityRuntime(
        router, _ManifestProvider(entries), CapabilityContractCatalog((addon,)),
        context_ref=lambda _context: "n-plus-one",
    )
    programs = load_skill_programs(registry.packages)
    runtime = SkillRuntime(
        programs, capability_runtime, MemoryAuditSink(), time.time,
        lambda: "skill-execution-n-plus-one", asyncio.sleep,
    )
    executable = McpProjection(
        registry, port, SkillRuntimeAdapter(runtime),
        implementation_digest_for=lambda ref: programs[ref].implementation_digest,
    )
    handle = ContextHandle("n-plus-one-call")
    assert len(await executable.list_tools(handle)) == 1
    result = await executable.call_tool(
        handle, "skill:community.test.observe_addon@1.0.0",
        {"subject": "farm"}, metadata=McpCallMetadata("tool-call-n-plus-one"),
    )
    assert result.state.value == "SUCCEEDED"
    assert result.output == {"capability": "observe_addon"}
    assert router.dispatch_count == 1
    command = next(iter(router.commands.values()))
    assert command.capability_id == "observe_addon"
    assert command.validated_payload == {"subject": "farm"}

    # The same indexed package remains governed rather than automatically
    # exposed: mount, risk and dependency version each independently close it.
    for suffix, facts in (
        ("unmounted", _facts(snapshot, set(), decision=RiskDecision.ALLOW)),
        ("risk-denied", _facts(snapshot, {skill_ref}, decision=RiskDecision.DENY)),
    ):
        guarded = McpProjection(registry, _ExposurePort(facts), _MustNotExecute())
        assert await guarded.list_tools(ContextHandle(suffix)) == ()

    incompatible_entry = [
        ProtocolCapabilityEntry("protocol-2.4.5", "observe_addon", "3.0.0")
    ]
    incompatible_digest = compute_capability_snapshot_digest(incompatible_entry)
    incompatible_context = replace(context, capability_digest=incompatible_digest)
    incompatible = build_capability_snapshot(
        incompatible_entry, (addon,), incompatible_context,
    )
    guarded = McpProjection(
        registry,
        _ExposurePort(_facts(incompatible, {skill_ref}, decision=RiskDecision.ALLOW)),
        _MustNotExecute(),
    )
    assert await guarded.list_tools(ContextHandle("version-mismatch")) == ()
