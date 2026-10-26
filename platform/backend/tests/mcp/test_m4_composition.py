"""M4 production composition wiring and fail-closed boundary tests."""
from __future__ import annotations

from pathlib import Path
import time

import pytest

from modules.mcp.capabilities.models import CapabilityInvocationHandle, CapabilityRef
from modules.mcp.contracts.models import ContextHandle, ExecutionContext, GameCommandResult, CommandState, SideEffect
from modules.mcp.contracts.projection import ExposureFacts
from modules.mcp.composition import McpCompositionError, build_mcp_composition
from modules.mcp.core.audit import MemoryAuditSink
from modules.mcp.release_manifest import default_mounted_skill_refs
from modules.runtime_manager.session_activation import HOSTED_DEFAULT_RUNTIME_GRANTS
from modules.mcp.skills.models import (
    CapabilityRequirement, DependencyBinding, EffectiveSkillPolicy, ExposureContext,
    ResolvedSkill, ResolvedSkillCatalog, RiskDecision, SkillRef,
)
from modules.mcp.skills.runtime_models import SkillExecutionState


ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    ("controlled_enabled", "expected_packages"),
    [(False, 36), (True, 38)],
)
def test_composition_indexes_release_bound_packages(controlled_enabled, expected_packages):
    composition = build_mcp_composition(
        repo_root=ROOT,
        exposure_port=object(),
        capability_runtime=object(),
        controlled_enabled=controlled_enabled,
    )
    assert len(composition.registry.packages) == expected_packages
    assert len(composition.programs) == expected_packages
    assert composition.release_digest.startswith("sha256:")
    assert composition.controlled_enabled is controlled_enabled
    assert all(
        package.source.value == "builtin"
        for package in composition.registry.packages
    ) is (not controlled_enabled)


@pytest.mark.parametrize(
    ("exposure_port", "capability_runtime", "code"),
    [
        (None, object(), "exposure_port_missing"),
        (object(), None, "capability_runtime_missing"),
    ],
)
def test_composition_requires_real_profile_dependencies(exposure_port, capability_runtime, code):
    with pytest.raises(McpCompositionError) as exc_info:
        build_mcp_composition(
            repo_root=ROOT,
            exposure_port=exposure_port,
            capability_runtime=capability_runtime,
        )
    assert exc_info.value.code == code


def test_hosted_default_does_not_index_controlled_packages():
    composition = build_mcp_composition(
        repo_root=ROOT,
        exposure_port=object(),
        capability_runtime=object(),
        controlled_enabled=False,
    )
    assert not any(
        package.source.value == "controlled"
        for package in composition.registry.packages
    )


def test_profile_default_mount_sets_are_release_bound():
    builtin = ROOT / "platform/backend/modules/mcp/skills/builtin"
    controlled = ROOT / "platform/backend/modules/mcp/skills/controlled-m4"
    local_refs = default_mounted_skill_refs(builtin, controlled)
    hosted_refs = default_mounted_skill_refs(builtin)
    assert len(local_refs) == 38
    assert len(hosted_refs) == 36
    assert {ref.skill_id for ref in local_refs - hosted_refs} == {
        "stardew.daily.water_crops", "stardew.daily.harvest_crops",
    }


@pytest.mark.asyncio
async def test_hosted_default_projects_named_place_navigation_as_background_job():
    from modules.mcp.capabilities.models import CapabilityContract, CapabilitySnapshot
    from modules.mcp.core.projection import McpProjection

    builtin = ROOT / "platform/backend/modules/mcp/skills/builtin"
    navigation_ref = SkillRef("stardew.navigation.go_to_place", "1.0.0")
    execution_context = ExecutionContext(
        "hosted", "principal", "runtime", 1, "binding", 1, "game", "nonce", 1,
        frozenset({"game:write"}), frozenset(HOSTED_DEFAULT_RUNTIME_GRANTS),
        "cap-digest", time.time(),
    )
    exposure_context = ExposureContext(
        "stardew-valley", default_mounted_skill_refs(builtin), {}, False,
        "mount", "policy", "binding", 1, "game",
    )
    capability_snapshot = CapabilitySnapshot((), (
        CapabilityContract(
            "go_to", "2.4.5", "protocol-2.4.5", {}, None,
            "game:write", SideEffect.MUTATING, 180.0, True, True, False, None,
        ),
    ), (), "cap-digest")
    facts = ExposureFacts(
        execution_context, exposure_context, capability_snapshot, "facts",
    )

    class Exposure:
        async def resolve_exposure(self, handle): return facts
        async def revalidate_exposure(self, handle): return facts
        async def close(self, handle): pass

    class Execution:
        async def start(self, *args, **kwargs): raise AssertionError("not executed")
        async def await_result(self, *args, **kwargs): raise AssertionError("not executed")
        async def cancel(self, *args, **kwargs): raise AssertionError("not executed")

    from modules.mcp.skills.registry import SkillRegistry
    projection = McpProjection(SkillRegistry.index(builtin), Exposure(), Execution())
    projected = {tool.name: tool for tool in await projection.list_tools(ContextHandle("hosted"))}
    tool_name = f"skill:{navigation_ref.skill_id}@{navigation_ref.version}"

    assert tool_name in HOSTED_DEFAULT_RUNTIME_GRANTS
    assert projected[tool_name].annotations["execution_mode"] == "background"
    assert projected[tool_name].annotations["cancellable"] is True
    assert "job_cancel" in projected


@pytest.mark.asyncio
async def test_real_loaded_program_call_uses_implementation_digest():
    """A production graph preserves request correlation through Capability calls."""
    class FakeCapabilityRuntime:
        def __init__(self):
            self.contexts = []

        async def start(self, context, capability_ref, arguments, *, tool_call_id,
                        command_id=None, now=None, deadline=None):
            self.contexts.append(context)
            return CapabilityInvocationHandle(
                command_id="fake-command", tool_call_id=tool_call_id,
                deadline_at=deadline or time.time() + 5,
                dispatch_receipt=None, capability_ref=capability_ref,
                side_effect=SideEffect.READ_ONLY,
                validated_arguments=dict(arguments),
            )

        async def await_result(self, context, handle, *, deadline=None):
            return GameCommandResult(
                "fake-command",
                CommandState.SUCCEEDED,
                {
                    "command_id": "fake-command",
                    "capability": "query_runtime_snapshot",
                    "success": True,
                    "status": "COMPLETED",
                },
            )

        async def cancel(self, context, handle, *, reason):
            from modules.mcp.contracts.models import CancelResult
            return CancelResult(handle.command_id, True, CommandState.CANCELLED)

    selected = SkillRef("stardew.proto.query_runtime_snapshot", "1.0.0")
    capability_runtime = FakeCapabilityRuntime()
    audit = MemoryAuditSink()
    composition = build_mcp_composition(
        repo_root=ROOT, exposure_port=object(),
        capability_runtime=capability_runtime, controlled_enabled=False,
        audit=audit,
    )
    package = next(p for p in composition.registry.packages
                   if SkillRef(p.manifest.skill_id, p.manifest.version) == selected)
    program = composition.programs[selected]
    requirement = package.manifest.requires[0]
    binding = DependencyBinding(requirement, CapabilityRef(requirement.capability_id, "2.4.5"))
    resolved = ResolvedSkill(package, True, (), (binding,), EffectiveSkillPolicy({}, False))

    class Registry:
        def resolve_catalog(self, exposure_context, capability_snapshot):
            return ResolvedSkillCatalog(
                (resolved,), exposure_context.mount_revision, exposure_context.policy_revision,
                capability_snapshot.capability_digest, exposure_context.binding_ref,
                exposure_context.binding_version, exposure_context.game_session_ref,
            )

    context = ExecutionContext(
        "local_direct", "principal", "runtime", 1, "binding", 1, "game", "nonce", 1,
        frozenset({"game.read"}), frozenset({selected.skill_id}), "cap-digest", time.time(),
    )
    exposure = ExposureContext(
        "stardew-valley", frozenset({selected}), {}, False, "mount", "policy", "binding", 1, "game",
    )
    from modules.mcp.capabilities.models import CapabilitySnapshot
    facts = ExposureFacts(context, exposure, CapabilitySnapshot((), (), (), "cap-digest"), "facts")

    class Exposure:
        async def resolve_exposure(self, handle): return facts
        async def revalidate_exposure(self, handle): return facts
        async def close(self, handle): pass

    from modules.mcp.core.projection import McpProjection, McpCallMetadata
    projection = McpProjection(
        Registry(), Exposure(), composition.execution_port,
        implementation_digest_for=lambda ref: composition.programs[ref].implementation_digest,
    )
    result = await projection.call_tool(
        ContextHandle("game"), f"skill:{selected.skill_id}@{selected.version}", {},
        metadata=McpCallMetadata("tool-call", request_id="rpc-request-42"),
    ) if await projection.list_tools(ContextHandle("game")) else None
    assert result is not None
    assert result.state is SkillExecutionState.SUCCEEDED
    assert program.implementation_digest != package.manifest_digest
    assert len(capability_runtime.contexts) == 1
    capability_context = capability_runtime.contexts[0]
    assert capability_context.trace_context["request_id"] == "rpc-request-42"
    assert capability_context.trace_context["skill_execution_id"] == result.skill_execution_id

    correlated = [event for event in audit.events
                  if getattr(event, "skill_execution_id", None) == result.skill_execution_id]
    assert {event.event for event in correlated} >= {
        "skill.execution.started",
        "skill.capability.started",
        "skill.capability.result",
        "skill.execution.finished",
    }
    assert {event.request_id for event in correlated} == {"rpc-request-42"}
    assert {event.tool_call_id for event in correlated} == {"tool-call"}
    command_events = [event for event in correlated if event.command_id is not None]
    assert command_events
    assert {event.command_id for event in command_events} == {"fake-command"}
