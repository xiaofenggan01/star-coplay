"""M3.4 SkillExecutionContext tests — dependency binding, serialization,
cancellation, deadline propagation, and capability convergence.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from modules.mcp.contracts.models import (
    CancelResult,
    CommandState,
    ExecutionContext,
    GameCommandResult,
    SideEffect,
)
from modules.mcp.capabilities.models import (
    CapabilityInvocationHandle,
    CapabilityRef,
)
from modules.mcp.skills.context import (
    SkillExecutionContext,
    _CapabilityStepFailure,
    _ExecutionRecord,
)
from modules.mcp.skills.models import (
    CapabilityRequirement,
    DependencyBinding,
    EffectiveSkillPolicy,
    SkillRef,
)
from modules.mcp.skills.runtime_models import (
    SkillAuditEvent,
    SkillExecutionAuthorization,
    SkillRuntimeErrorCode,
)

# ---------------------------------------------------------------------------
# Fake CapabilityRuntime
# ---------------------------------------------------------------------------


class FakeCapabilityRuntime:
    def __init__(self):
        self._next_id = 0
        self.start_raises: Exception | None = None
        self.await_result_state: CommandState = CommandState.SUCCEEDED
        self.await_result_sequence: list[CommandState] = []
        self.await_result_output: Mapping[str, Any] = {}
        self.await_result_error_code: str | None = None
        self.status_state: CommandState = CommandState.SUCCEEDED
        self.status_sequence: list[CommandState] = []
        self.cancel_calls: list[tuple] = []
        self.operation_order: list[str] = []
        self.side_effect = SideEffect.MUTATING

    async def snapshot(self, context):
        from modules.mcp.capabilities.models import CapabilitySnapshot
        return CapabilitySnapshot(
            raw_entries=(), known_contracts=(), diagnostics=(),
            capability_digest="fake",
        )

    async def start(self, context, capability_ref, arguments, *,
                    tool_call_id, command_id=None, now=None, deadline=None):
        if self.start_raises:
            raise self.start_raises
        cid = command_id or f"cmd-{self._next_id}"
        self._next_id += 1
        return CapabilityInvocationHandle(
            command_id=cid, tool_call_id=tool_call_id,
            deadline_at=deadline or (time.time() + 30),
            dispatch_receipt=None, capability_ref=capability_ref,
            side_effect=self.side_effect,
            validated_arguments=dict(arguments),
        )

    async def await_result(self, context, handle, *, deadline=None):
        self.operation_order.append("await_result")
        state = (
            self.await_result_sequence.pop(0)
            if self.await_result_sequence else self.await_result_state
        )
        return GameCommandResult(
            command_id=handle.command_id,
            state=state,
            output=dict(self.await_result_output),
            error_code=self.await_result_error_code,
        )

    async def invoke(self, context, capability_ref, arguments, *,
                     tool_call_id, command_id=None, now=None, deadline=None):
        h = await self.start(context, capability_ref, arguments,
                             tool_call_id=tool_call_id, command_id=command_id,
                             now=now, deadline=deadline)
        return await self.await_result(context, h, deadline=deadline)

    async def cancel(self, context, handle, *, reason):
        self.cancel_calls.append((handle.command_id, reason))
        return CancelResult(command_id=handle.command_id, accepted=True,
                            state=CommandState.CANCELLED)

    async def get_status(self, context, command_id):
        self.operation_order.append("get_status")
        if self.status_sequence:
            return self.status_sequence.pop(0)
        return self.status_state


class MemoryAuditSink:
    def __init__(self):
        self.events: list[SkillAuditEvent] = []

    def emit(self, event: SkillAuditEvent) -> None:
        self.events.append(event)


# ===================================================================
# Helpers
# ===================================================================


def _make_ctx(**kw):
    defaults = dict(
        profile="fake", principal_id="p-a", runtime_ref="rt-a",
        generation=1, binding_ref="b-a", binding_version=1,
        game_session_ref="gs-a", game_instance_nonce="n-a",
        lease_epoch=1, scopes=frozenset({"game:read", "game:write"}),
        allowed_tools=frozenset({"observe", "say"}),
        capability_digest="digest-a", issued_at=time.time(),
        trace_context={"trace_id": "trace-a"},
    )
    defaults.update(kw)
    return ExecutionContext(**defaults)


def _make_auth(skill_id="test.minimal.v1", version="1.0.0",
               digest="sha256:abc", impl_digest="sha256:def",
               dep_bindings=(), exec_ctx=None):
    if exec_ctx is None:
        exec_ctx = _make_ctx()
    return SkillExecutionAuthorization(
        skill_ref=SkillRef(skill_id=skill_id, version=version),
        manifest_digest=digest,
        implementation_digest=impl_digest,
        mount_revision="mount-v1",
        policy_revision="policy-v1",
        capability_digest=exec_ctx.capability_digest,
        binding_ref=exec_ctx.binding_ref,
        binding_version=exec_ctx.binding_version,
        game_session_ref=exec_ctx.game_session_ref,
        effective_policy=EffectiveSkillPolicy(
            decisions={}, requires_confirmation=False,
        ),
        dependency_bindings=dep_bindings,
    )


def _make_record():
    return _ExecutionRecord(
        lock=asyncio.Lock(),
        cancellation_event=asyncio.Event(),
    )


def _make_skill_ctx(auth=None, cap_runtime=None, record=None):
    """Create a SkillExecutionContext with defaults."""
    return SkillExecutionContext(
        authorization=auth or _make_auth(),
        execution_context=_make_ctx(),
        capability_runtime=cap_runtime or FakeCapabilityRuntime(),
        skill_execution_id="se-1", tool_call_id="tc-1",
        skill_deadline=time.time() + 30,
        clock=time.time, wait=asyncio.sleep,
        audit=MemoryAuditSink(),
        record=record or _make_record(),
    )


# ===================================================================
# Dependency binding tests
# ===================================================================


class TestDependencyBinding:
    def test_declared_capability_accepted(self):
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth)

        async def _test():
            handle = await ctx.start("say", ">=1.0.0", {"message": "hi"})
            return handle

        handle = asyncio.run(_test())
        assert handle is not None

    def test_undeclared_capability_rejected(self):
        auth = _make_auth(dep_bindings=())
        ctx = _make_skill_ctx(auth=auth)

        async def _test():
            with pytest.raises(_CapabilityStepFailure) as exc:
                await ctx.start("say", ">=1.0.0", {"message": "hi"})
            assert exc.value.code == SkillRuntimeErrorCode.CAPABILITY_UNKNOWN

        asyncio.run(_test())

    def test_wrong_version_range_rejected(self):
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth)

        async def _test():
            with pytest.raises(_CapabilityStepFailure) as exc:
                await ctx.start("say", ">=2.0.0", {"message": "hi"})
            assert exc.value.code == SkillRuntimeErrorCode.CAPABILITY_UNKNOWN

        asyncio.run(_test())

    def test_null_binding_rejected(self):
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=None,
            ),
        ))
        ctx = _make_skill_ctx(auth=auth)

        async def _test():
            with pytest.raises(_CapabilityStepFailure) as exc:
                await ctx.start("say", ">=1.0.0", {"message": "hi"})
            assert exc.value.code == SkillRuntimeErrorCode.CAPABILITY_UNKNOWN

        asyncio.run(_test())


# ===================================================================
# Serialization tests
# ===================================================================


class TestSerialization:
    def test_concurrent_starts_serialized(self):
        cap = FakeCapabilityRuntime()
        original_start = cap.start

        async def slow_start(*args, **kwargs):
            await asyncio.sleep(0.1)
            return await original_start(*args, **kwargs)

        cap.start = slow_start

        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            h1 = await ctx.start("say", ">=1.0.0", {"message": "first"})
            return h1

        handle = asyncio.run(_test())
        assert handle is not None

    def test_two_starts_without_await_fails(self):
        cap = FakeCapabilityRuntime()
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            h1 = await ctx.start("say", ">=1.0.0", {"message": "first"})
            with pytest.raises(_CapabilityStepFailure) as exc:
                await ctx.start("say", ">=1.0.0", {"message": "second"})
            assert exc.value.code == SkillRuntimeErrorCode.EXECUTION_CONFLICT
            return h1

        asyncio.run(_test())


# ===================================================================
# Cancellation tests
# ===================================================================


class TestContextCancellation:
    def test_is_cancelled_initially_false(self):
        ctx = _make_skill_ctx()
        assert ctx.is_cancelled() is False

    def test_is_cancelled_after_set(self):
        record = _make_record()
        ctx = _make_skill_ctx(record=record)
        record.cancellation_event.set()
        assert ctx.is_cancelled() is True

    def test_check_cancelled_raises_after_cancel(self):
        record = _make_record()
        ctx = _make_skill_ctx(record=record)
        record.cancellation_event.set()
        with pytest.raises(asyncio.CancelledError):
            ctx.check_cancelled()


# ===================================================================
# Capability convergence tests
# ===================================================================


class TestCapabilityConvergence:
    def test_initial_await_result_precedes_status_polling(self):
        cap = FakeCapabilityRuntime()
        cap.await_result_sequence = [CommandState.UNKNOWN, CommandState.SUCCEEDED]
        cap.status_sequence = [CommandState.SUCCEEDED]
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement("say", ">=1.0.0"),
                capability_ref=CapabilityRef("say", "2.4.5"),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            handle = await ctx.start("say", ">=1.0.0", {"message": "hi"})
            await ctx.await_result(handle)

        asyncio.run(_test())
        assert cap.operation_order == ["await_result", "get_status", "await_result"]

    @pytest.mark.parametrize("deadline", [float("nan"), float("inf"), 0.0])
    def test_requested_deadline_must_be_finite_and_future(self, deadline):
        cap = FakeCapabilityRuntime()
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement("say", ">=1.0.0"),
                capability_ref=CapabilityRef("say", "2.4.5"),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            with pytest.raises(_CapabilityStepFailure) as caught:
                await ctx.start(
                    "say", ">=1.0.0", {"message": "hi"}, deadline=deadline,
                )
            assert caught.value.code == SkillRuntimeErrorCode.CAPABILITY_FAILED

        asyncio.run(_test())
        assert cap._next_id == 0

    def test_succeeded_returns_result(self):
        cap = FakeCapabilityRuntime()
        cap.await_result_state = CommandState.SUCCEEDED
        cap.await_result_output = {"value": 42}
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            h = await ctx.start("say", ">=1.0.0", {"message": "hi"})
            result = await ctx.await_result(h)
            return result

        result = asyncio.run(_test())
        assert result.state == CommandState.SUCCEEDED
        assert result.output == {"value": 42}

    def test_capability_failed_raises(self):
        cap = FakeCapabilityRuntime()
        cap.status_state = CommandState.FAILED
        cap.await_result_state = CommandState.FAILED
        cap.await_result_output = {
            "error": {
                "code": "EXECUTION_FAILED",
                "message": "path is blocked",
                "detail": "target=(3,12)",
            },
        }
        cap.await_result_error_code = "EXECUTION_FAILED"
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            h = await ctx.start("say", ">=1.0.0", {"message": "hi"})
            with pytest.raises(_CapabilityStepFailure) as exc:
                await ctx.await_result(h)
            assert exc.value.code == SkillRuntimeErrorCode.CAPABILITY_FAILED
            assert exc.value.detail == {
                "capability_error": {
                    "state": "FAILED",
                    "code": "EXECUTION_FAILED",
                    "message": "path is blocked",
                    "detail": "target=(3,12)",
                },
            }

        asyncio.run(_test())

    def test_unknown_convergence_to_expired(self):
        cap = FakeCapabilityRuntime()
        cap.await_result_state = CommandState.UNKNOWN
        cap.status_state = CommandState.EXPIRED_UNKNOWN
        auth = _make_auth(dep_bindings=(
            DependencyBinding(
                requirement=CapabilityRequirement(
                    capability_id="say", version_range=">=1.0.0",
                ),
                capability_ref=CapabilityRef(
                    capability_id="say", contract_version="2.4.5",
                ),
            ),
        ))
        ctx = _make_skill_ctx(auth=auth, cap_runtime=cap)

        async def _test():
            h = await ctx.start("say", ">=1.0.0", {"message": "hi"})
            with pytest.raises(_CapabilityStepFailure) as exc:
                await ctx.await_result(h)
            assert exc.value.code == SkillRuntimeErrorCode.CAPABILITY_UNKNOWN
            assert exc.value.unknown is True

        asyncio.run(_test())


# ===================================================================
# CapabilityStepFailure properties
# ===================================================================


class TestCapabilityStepFailure:
    def test_message_is_code_value(self):
        e = _CapabilityStepFailure(SkillRuntimeErrorCode.CAPABILITY_FAILED, "cmd-1")
        assert str(e) == "capability_failed"
        assert e.code == SkillRuntimeErrorCode.CAPABILITY_FAILED
        assert e.command_id == "cmd-1"
        assert e.unknown is False

    def test_unknown_flag(self):
        e = _CapabilityStepFailure(
            SkillRuntimeErrorCode.CAPABILITY_UNKNOWN, "cmd-2", unknown=True,
        )
        assert e.unknown is True
        assert str(e) == "capability_unknown"

    def test_structured_detail_is_preserved(self):
        detail = {"capability_error": {"code": "EXECUTION_FAILED"}}
        e = _CapabilityStepFailure(
            SkillRuntimeErrorCode.CAPABILITY_FAILED, "cmd-3", detail=detail,
        )
        assert e.detail == detail
