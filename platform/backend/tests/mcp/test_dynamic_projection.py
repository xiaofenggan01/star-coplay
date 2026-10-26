from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from modules.mcp.capabilities.models import CapabilitySnapshot
from modules.mcp.contracts.models import ContextHandle, ExecutionContext
from modules.mcp.contracts.projection import ExposureFacts
from modules.mcp.core.projection import McpCallMetadata, McpProjection, ProjectionError
from modules.mcp.skills.models import ExposureContext, EffectiveSkillPolicy, RiskDecision, SkillRef
from modules.mcp.skills.runtime_models import SkillExecutionHandle, SkillExecutionState, SkillResult, SkillCancelResult


def _facts(cap="cap-a"):
    ec = ExecutionContext("local", "p", "r", 1, "b", 1, "g", "n", 1, frozenset(), frozenset(), cap, 0.0)
    x = ExposureContext("stardew-valley", frozenset({SkillRef("demo", "1.0.0")}), {}, False, "m1", "p1", "b", 1, "g")
    snap = CapabilitySnapshot((), (), (), cap)
    return ExposureFacts(ec, x, snap, "facts")


def _hosted_facts(*allowed_tools: str):
    facts = _facts()
    return replace(
        facts,
        execution_context=replace(
            facts.execution_context,
            profile="hosted",
            allowed_tools=frozenset(allowed_tools),
        ),
    )


def _skill(available=True, *, execution_mode="synchronous", risk=frozenset()):
    manifest = SimpleNamespace(skill_id="demo", version="1.0.0", description="demo", input_schema={"type": "object"}, output_schema={"type": "object"}, risk=risk, cancellable=True, execution_mode=execution_mode)
    package = SimpleNamespace(manifest=manifest, manifest_digest="md")
    return SimpleNamespace(package=package, available=available, reasons=(), dependency_bindings=(), effective_policy=EffectiveSkillPolicy({}, False))


class FakeRegistry:
    def __init__(self, skill): self.skill = skill; self.calls = 0
    def resolve_catalog(self, exposure_context, capability_snapshot):
        self.calls += 1
        return SimpleNamespace(skills=(self.skill,), mount_revision=exposure_context.mount_revision, policy_revision=exposure_context.policy_revision, capability_digest=capability_snapshot.capability_digest, binding_ref=exposure_context.binding_ref, binding_version=exposure_context.binding_version, game_session_ref=exposure_context.game_session_ref)


class FakeExposure:
    def __init__(self): self.current = _facts(); self.resolve_calls = 0
    async def resolve_exposure(self, handle): self.resolve_calls += 1; return self.current
    async def revalidate_exposure(self, handle): return self.current
    async def close(self, handle): pass


class FakeExecution:
    def __init__(self): self.started = []; self.cancelled = []
    async def start(self, *args, **kwargs):
        h = SkillExecutionHandle("e1", SkillRef("demo", "1.0.0"), kwargs["tool_call_id"], 100.0); self.started.append(h); return h
    async def await_result(self, handle, **kwargs): return SkillResult("e1", handle.skill_ref, SkillExecutionState.SUCCEEDED, {"ok": True}, None, {}, (), False)
    async def cancel(self, handle, *, reason): self.cancelled.append(handle); return SkillCancelResult(handle.skill_execution_id, True, SkillExecutionState.CANCELLED, "cancelled")


class BlockingExecution(FakeExecution):
    async def await_result(self, handle, **kwargs):
        import asyncio
        await asyncio.sleep(60)


class ControlledExecution(FakeExecution):
    def __init__(self):
        super().__init__()
        self.finish = asyncio.Event()

    async def await_result(self, handle, **kwargs):
        await self.finish.wait()
        return await super().await_result(handle, **kwargs)


class TerminalExecution(ControlledExecution):
    def __init__(self, result: SkillResult):
        super().__init__()
        self.result = result

    async def await_result(self, handle, **kwargs):
        await self.finish.wait()
        return replace(
            self.result,
            skill_execution_id=handle.skill_execution_id,
            skill_ref=handle.skill_ref,
        )


class IncrementingExecution(FakeExecution):
    async def start(self, *args, **kwargs):
        execution_id = f"e{len(self.started) + 1}"
        handle = SkillExecutionHandle(
            execution_id,
            SkillRef("demo", "1.0.0"),
            kwargs["tool_call_id"],
            100.0,
        )
        self.started.append(handle)
        return handle

    async def await_result(self, handle, **kwargs):
        return SkillResult(
            handle.skill_execution_id,
            handle.skill_ref,
            SkillExecutionState.SUCCEEDED,
            {"ok": True},
            None,
            {},
            (),
            False,
        )


@pytest.mark.asyncio
async def test_dynamic_list_excludes_unavailable_and_unknown_call():
    exposure, execution = FakeExposure(), FakeExecution()
    projection = McpProjection(FakeRegistry(_skill(False)), exposure, execution)
    assert await projection.list_tools(ContextHandle("h")) == ()
    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(ContextHandle("h"), "skill:demo@1.0.0", {}, metadata=McpCallMetadata("t"))
    assert error.value.code == "capability_denied"


@pytest.mark.asyncio
async def test_hosted_minimal_grant_filters_list_and_rejects_forged_call():
    exposure, execution = FakeExposure(), FakeExecution()
    exposure.current = _hosted_facts("skill:another@1.0.0")
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    handle = ContextHandle("hosted-minimal")

    assert await projection.list_tools(handle) == ()
    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(
            handle,
            "skill:demo@1.0.0",
            {},
            metadata=McpCallMetadata("forged"),
        )

    assert error.value.code == "capability_denied"
    assert execution.started == []


@pytest.mark.asyncio
async def test_hosted_call_revalidates_grant_before_runtime_start():
    exposure, execution = FakeExposure(), FakeExecution()
    exposure.current = _hosted_facts("skill:demo@1.0.0")
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    handle = ContextHandle("hosted-revalidate")
    assert [tool.name for tool in await projection.list_tools(handle)] == [
        "skill:demo@1.0.0"
    ]

    exposure.current = _hosted_facts("skill:another@1.0.0")
    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(
            handle,
            "skill:demo@1.0.0",
            {},
            metadata=McpCallMetadata("revoked"),
        )

    assert error.value.code == "capability_denied"
    assert execution.started == []


@pytest.mark.asyncio
async def test_pin_drift_blocks_before_runtime_and_exact_cancel_handle():
    exposure, execution = FakeExposure(), FakeExecution()
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    h = ContextHandle("h")
    await projection.list_tools(h)
    exposure.current = _facts("changed")
    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(h, "skill:demo@1.0.0", {}, metadata=McpCallMetadata("t"))
    assert error.value.code == "conflict" and not execution.started


@pytest.mark.asyncio
async def test_protected_arguments_are_rejected():
    exposure, execution = FakeExposure(), FakeExecution()
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    h = ContextHandle("h"); await projection.list_tools(h)
    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(h, "skill:demo@1.0.0", {"binding_ref": "forged"}, metadata=McpCallMetadata("t"))
    assert error.value.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_json_tree_gate_rejects_cycle_and_non_json_values():
    exposure, execution = FakeExposure(), FakeExecution()
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    h = ContextHandle("h"); await projection.list_tools(h)
    cyclic = {}; cyclic["self"] = cyclic
    for value in (cyclic, {"x": (1, 2)}, {"x": float("nan")}, {1: "bad"}):
        with pytest.raises(ProjectionError) as error:
            await projection.call_tool(h, "skill:demo@1.0.0", value, metadata=McpCallMetadata("t"))
        assert error.value.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_pending_cancel_is_applied_to_handle_after_start():
    exposure, execution = FakeExposure(), BlockingExecution()
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    h = ContextHandle("h"); await projection.list_tools(h)
    import asyncio
    task = asyncio.create_task(projection.call_tool(h, "skill:demo@1.0.0", {}, metadata=McpCallMetadata("pending")))
    await asyncio.sleep(0.01)
    cancel = await projection.cancel(h, McpCallMetadata("pending"))
    assert cancel.accepted
    assert execution.cancelled == execution.started
    task.cancel(); await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_uses_exact_registered_execution_handle():
    exposure, execution = FakeExposure(), BlockingExecution()
    projection = McpProjection(FakeRegistry(_skill()), exposure, execution)
    h = ContextHandle("h"); await projection.list_tools(h)
    import asyncio
    task = asyncio.create_task(projection.call_tool(h, "skill:demo@1.0.0", {}, metadata=McpCallMetadata("t")))
    await asyncio.sleep(0.01)
    result = await projection.cancel(h, McpCallMetadata("t"))
    assert result.accepted and execution.cancelled == execution.started
    task.cancel(); await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_background_skill_returns_receipt_and_is_polled_in_same_session():
    exposure, execution = FakeExposure(), ControlledExecution()
    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background", risk=frozenset({"changes_save"}))),
        exposure,
        execution,
    )
    handle = ContextHandle("background-owner")
    tools = await projection.list_tools(handle)
    assert [tool.name for tool in tools] == [
        "skill:demo@1.0.0", "job_status", "job_cancel",
    ]

    receipt = await projection.call_tool(
        handle, "skill:demo@1.0.0", {}, metadata=McpCallMetadata("background-call"),
    )
    assert receipt.output == {"job_id": "e1", "skill_id": "demo", "status": "running"}

    running = await projection.call_tool(
        handle, "job_status", {"job_id": "e1"}, metadata=McpCallMetadata("status-1"),
    )
    assert running.output == {
        "job_id": "e1",
        "skill_id": "demo",
        "status": "running",
        "output": {},
        "partial_output": {},
        "error_code": None,
        "side_effects_may_have_occurred": False,
    }

    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(
            ContextHandle("other"), "job_status", {"job_id": "e1"},
            metadata=McpCallMetadata("forged-status"),
        )
    assert error.value.code in {"capability_denied", "context_expired"}

    execution.finish.set()
    await asyncio.sleep(0)
    completed = await projection.call_tool(
        handle, "job_status", {"job_id": "e1"}, metadata=McpCallMetadata("status-2"),
    )
    assert completed.output["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "error_code"),
    [
        (SkillExecutionState.SUCCEEDED, None),
        (SkillExecutionState.FAILED, "capability_failed"),
        (SkillExecutionState.PARTIAL, "capability_failed"),
        (SkillExecutionState.CANCELLED, "cancelled"),
        (SkillExecutionState.TIMED_OUT, "timeout"),
    ],
)
async def test_job_status_returns_complete_terminal_result(state, error_code):
    terminal = SkillResult(
        "placeholder",
        SkillRef("demo", "1.0.0"),
        state,
        {"petted": 0, "target_count": 2},
        error_code,
        {"petted": 1},
        (),
        True,
    )
    exposure, execution = FakeExposure(), TerminalExecution(terminal)
    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background")),
        exposure,
        execution,
    )
    handle = ContextHandle(f"terminal-{state.value.lower()}")
    await projection.list_tools(handle)
    await projection.call_tool(
        handle, "skill:demo@1.0.0", {}, metadata=McpCallMetadata("terminal-job"),
    )

    execution.finish.set()
    for _ in range(10):
        status = await projection.call_tool(
            handle, "job_status", {"job_id": "e1"},
            metadata=McpCallMetadata("terminal-status"),
        )
        if status.output["status"] != "running":
            break
        await asyncio.sleep(0)

    assert status.output == {
        "job_id": "e1",
        "skill_id": "demo",
        "status": state.value.lower(),
        "output": {"petted": 0, "target_count": 2},
        "partial_output": {"petted": 1},
        "error_code": error_code,
        "side_effects_may_have_occurred": True,
    }


@pytest.mark.asyncio
async def test_background_completion_publishes_verified_runtime_identity():
    exposure, execution = FakeExposure(), ControlledExecution()
    completions = []

    async def capture(completion):
        completions.append(completion)

    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background")),
        exposure,
        execution,
        background_completion_hook=capture,
    )
    handle = ContextHandle("completion-owner")
    await projection.list_tools(handle)
    await projection.call_tool(
        handle,
        "skill:demo@1.0.0",
        {},
        metadata=McpCallMetadata("completion-job"),
    )

    execution.finish.set()
    for _ in range(10):
        if completions:
            break
        await asyncio.sleep(0)

    assert len(completions) == 1
    completion = completions[0]
    assert (completion.runtime_ref, completion.generation) == ("r", 1)
    assert completion.principal_id == "p"
    assert completion.binding_ref == "b"
    assert completion.game_session_ref == "g"
    assert completion.job_id == "e1"
    assert completion.skill_ref == SkillRef("demo", "1.0.0")
    assert completion.state is SkillExecutionState.SUCCEEDED
    assert completion.output == {"ok": True}


@pytest.mark.asyncio
async def test_hosted_background_job_controls_inherit_skill_grant():
    exposure, execution = FakeExposure(), ControlledExecution()
    skill_name = "skill:demo@1.0.0"
    exposure.current = _hosted_facts(skill_name)
    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background")),
        exposure,
        execution,
    )
    handle = ContextHandle("hosted-background")

    assert [tool.name for tool in await projection.list_tools(handle)] == [
        skill_name, "job_status", "job_cancel",
    ]
    receipt = await projection.call_tool(
        handle, skill_name, {}, metadata=McpCallMetadata("hosted-job"),
    )
    status = await projection.call_tool(
        handle,
        "job_status",
        {"job_id": receipt.output["job_id"]},
        metadata=McpCallMetadata("hosted-status"),
    )
    assert status.output["status"] == "running"

    exposure.current = _hosted_facts("skill:another@1.0.0")
    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(
            handle,
            "job_status",
            {"job_id": receipt.output["job_id"]},
            metadata=McpCallMetadata("revoked-status"),
        )
    assert error.value.code == "capability_denied"
    await projection.close(handle)


@pytest.mark.asyncio
async def test_hosted_runtime_can_cancel_its_active_background_job_without_job_id():
    exposure, execution = FakeExposure(), ControlledExecution()
    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background")),
        exposure,
        execution,
    )
    handle = ContextHandle("hosted-player-cancel")
    await projection.list_tools(handle)
    await projection.call_tool(
        handle,
        "skill:demo@1.0.0",
        {},
        metadata=McpCallMetadata("hosted-job"),
    )

    cancelled_job_id = await projection.cancel_active(
        runtime_id="r",
        generation=1,
        game_session_id="g",
        reason="player_requested",
    )

    assert cancelled_job_id == "e1"
    assert execution.cancelled == execution.started


@pytest.mark.asyncio
async def test_second_background_job_is_rejected_before_runtime_start():
    exposure, execution = FakeExposure(), ControlledExecution()
    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background")),
        exposure,
        execution,
    )
    first = ContextHandle("first-owner")
    second = ContextHandle("second-owner")
    await projection.list_tools(first)
    await projection.list_tools(second)
    await projection.call_tool(
        first,
        "skill:demo@1.0.0",
        {},
        metadata=McpCallMetadata("first-job"),
    )

    with pytest.raises(ProjectionError) as error:
        await projection.call_tool(
            second,
            "skill:demo@1.0.0",
            {},
            metadata=McpCallMetadata("second-job"),
        )

    assert error.value.code == "conflict"
    assert len(execution.started) == 1
    await projection.close(first)


@pytest.mark.asyncio
async def test_terminal_background_job_cache_is_bounded():
    exposure, execution = FakeExposure(), IncrementingExecution()
    projection = McpProjection(
        FakeRegistry(_skill(execution_mode="background")),
        exposure,
        execution,
    )
    handle = ContextHandle("many-jobs")
    await projection.list_tools(handle)

    for index in range(130):
        await projection.call_tool(
            handle,
            "skill:demo@1.0.0",
            {},
            metadata=McpCallMetadata(f"job-{index}"),
        )
        await asyncio.sleep(0)

    assert len(projection._background_jobs) == 128
    assert "e1" not in projection._background_jobs
    assert "e2" not in projection._background_jobs
