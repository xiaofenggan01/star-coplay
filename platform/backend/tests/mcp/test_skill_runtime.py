"""M3.4 SkillRuntime tests — authorization, lifecycle, schema, cancel, timeout, audit."""
from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
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
from modules.mcp.skills.loader import LoadedSkillProgram, load_skill_programs
from modules.mcp.skills.models import (
    CapabilityRequirement,
    DependencyBinding,
    EffectiveSkillPolicy,
    ResolvedSkill,
    ResolvedSkillCatalog,
    RiskDecision,
    SkillPackage,
    SkillRef,
    SkillSource,
)
from modules.mcp.skills.runtime import SkillRuntime, _validate_json_compatible
from modules.mcp.skills.runtime_models import (
    SkillAuditEvent,
    SkillCancelResult,
    SkillExecutionAuthorization,
    SkillExecutionHandle,
    SkillExecutionState,
    SkillRuntimeError,
    SkillRuntimeErrorCode,
)
from modules.mcp.skills.schema import parse_skill_package

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skill_runtime"


# ===================================================================
# Fake / Test doubles
# ===================================================================


class FakeCapabilityRuntime:
    def __init__(self):
        self._next_command_id = 0
        self.cancel_calls: list[tuple[str, str]] = []
        self.start_calls: list[dict] = []
        self.await_calls: list[str] = []
        self.status_calls: list[str] = []
        self.start_raises: Exception | None = None
        self.await_result_state: CommandState | None = None
        self.await_result_output: Mapping[str, Any] = {}
        self.await_raises: Exception | None = None
        self.cancel_accepted: bool = True
        self.cancel_state: CommandState = CommandState.CANCELLED
        self.status_state: CommandState = CommandState.SUCCEEDED
        self.status_sequence: list[CommandState] = []
        self.side_effect = SideEffect.MUTATING

    async def snapshot(self, context):
        from modules.mcp.capabilities.models import CapabilitySnapshot
        return CapabilitySnapshot(
            raw_entries=(), known_contracts=(), diagnostics=(),
            capability_digest="fake-digest",
        )

    async def start(self, context, capability_ref, arguments, *,
                    tool_call_id, command_id=None, now=None, deadline=None):
        self.start_calls.append({
            "capability_id": capability_ref.capability_id,
            "arguments": dict(arguments),
            "tool_call_id": tool_call_id,
        })
        if self.start_raises:
            raise self.start_raises
        cid = command_id or f"cmd-{self._next_command_id}"
        self._next_command_id += 1
        return CapabilityInvocationHandle(
            command_id=cid, tool_call_id=tool_call_id,
            deadline_at=deadline or (time.time() + 30),
            dispatch_receipt=None, capability_ref=capability_ref,
            side_effect=self.side_effect,
            validated_arguments=dict(arguments),
        )

    async def await_result(self, context, handle, *, deadline=None):
        self.await_calls.append(handle.command_id)
        if self.await_raises:
            raise self.await_raises
        state = self.await_result_state or CommandState.SUCCEEDED
        return GameCommandResult(
            command_id=handle.command_id, state=state,
            output=dict(self.await_result_output),
        )

    async def invoke(self, context, capability_ref, arguments, *,
                     tool_call_id, command_id=None, now=None, deadline=None):
        h = await self.start(context, capability_ref, arguments,
                             tool_call_id=tool_call_id, command_id=command_id,
                             now=now, deadline=deadline)
        return await self.await_result(context, h, deadline=deadline)

    async def cancel(self, context, handle, *, reason):
        self.cancel_calls.append((handle.command_id, reason))
        return CancelResult(command_id=handle.command_id, accepted=self.cancel_accepted,
                            state=self.cancel_state)

    async def get_status(self, context, command_id):
        self.status_calls.append(command_id)
        if self.status_sequence:
            return self.status_sequence.pop(0)
        return self.status_state


class MemorySkillAuditSink:
    def __init__(self):
        self.events: list[SkillAuditEvent] = []

    def emit(self, event: SkillAuditEvent) -> None:
        self.events.append(event)


class ThrowingSkillAuditSink:
    def emit(self, event: SkillAuditEvent) -> None:
        raise RuntimeError("audit-secret")


# ===================================================================
# Helpers
# ===================================================================


def _make_ctx(principal="principal-a", session="game-a", nonce="nonce-a",
              generation=1, binding_version=1, digest="digest-a"):
    return ExecutionContext(
        profile="fake", principal_id=principal,
        runtime_ref=f"runtime-{principal}", generation=generation,
        binding_ref=f"binding-{principal}", binding_version=binding_version,
        game_session_ref=session, game_instance_nonce=nonce,
        lease_epoch=1, scopes=frozenset({"game:read", "game:write"}),
        allowed_tools=frozenset({"observe", "say"}), capability_digest=digest,
        issued_at=time.time(), trace_context={"trace_id": f"trace-{principal}"},
    )


def _parse_pkg(name: str) -> SkillPackage:
    return parse_skill_package(FIXTURES / name, SkillSource.BUILTIN, FIXTURES)


def _load_prog(pkg: SkillPackage) -> tuple[SkillRef, LoadedSkillProgram]:
    result = load_skill_programs([pkg])
    ref = SkillRef(skill_id=pkg.manifest.skill_id, version=pkg.manifest.version)
    return ref, result[ref]


def _dependency_bindings(pkg: SkillPackage) -> tuple[DependencyBinding, ...]:
    return tuple(
        DependencyBinding(
            requirement=requirement,
            capability_ref=CapabilityRef(
                capability_id=requirement.capability_id,
                contract_version="1.0.0",
            ),
        )
        for requirement in pkg.manifest.requires
    )


def _make_auth(pkg: SkillPackage, prog: LoadedSkillProgram,
               exec_ctx: ExecutionContext, dependency_bindings=None,
               effective_policy=None) -> SkillExecutionAuthorization:
    if effective_policy is None:
        effective_policy = EffectiveSkillPolicy(decisions={}, requires_confirmation=False)
    if dependency_bindings is None:
        dependency_bindings = _dependency_bindings(pkg)
    return SkillExecutionAuthorization(
        skill_ref=SkillRef(skill_id=pkg.manifest.skill_id, version=pkg.manifest.version),
        manifest_digest=pkg.manifest_digest,
        implementation_digest=prog.implementation_digest,
        mount_revision="mount-v1", policy_revision="policy-v1",
        capability_digest=exec_ctx.capability_digest,
        binding_ref=exec_ctx.binding_ref, binding_version=exec_ctx.binding_version,
        game_session_ref=exec_ctx.game_session_ref,
        effective_policy=effective_policy, dependency_bindings=dependency_bindings,
    )


def _make_resolved(pkg: SkillPackage, deps=None, policy=None, available=True) -> ResolvedSkill:
    if policy is None:
        policy = EffectiveSkillPolicy(decisions={}, requires_confirmation=False)
    if deps is None:
        deps = _dependency_bindings(pkg)
    return ResolvedSkill(
        package=pkg, available=available, reasons=(),
        dependency_bindings=deps, effective_policy=policy,
    )


def _make_catalog(
    resolved: ResolvedSkill, exec_ctx: ExecutionContext,
    *, skills: tuple[ResolvedSkill, ...] | None = None,
) -> ResolvedSkillCatalog:
    return ResolvedSkillCatalog(
        skills=skills if skills is not None else (resolved,),
        mount_revision="mount-v1", policy_revision="policy-v1",
        capability_digest=exec_ctx.capability_digest,
        binding_ref=exec_ctx.binding_ref,
        binding_version=exec_ctx.binding_version,
        game_session_ref=exec_ctx.game_session_ref,
    )


def _make_runtime(programs, cap_runtime=None, audit=None):
    return SkillRuntime(
        programs=programs,
        capability_runtime=cap_runtime or FakeCapabilityRuntime(),
        audit=audit or MemorySkillAuditSink(),
        clock=time.time,
        uuid_factory=lambda: "exec-001",
        wait=asyncio.sleep,
    )


# ===================================================================
# Authorization tests
# ===================================================================


class TestAuthorization:
    @pytest.mark.parametrize(
        "field,bad",
        [
            ("manifest_digest", ""),
            ("implementation_digest", ""),
            ("mount_revision", ""),
            ("policy_revision", ""),
            ("capability_digest", ""),
            ("binding_ref", ""),
            ("game_session_ref", ""),
            ("binding_version", 0),
            ("binding_version", False),
        ],
    )
    def test_authorization_shape_fails_closed(self, field, bad):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = dataclasses.replace(_make_auth(pkg, prog, ctx), **{field: bad})
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                    tool_call_id="tc-shape",
                )
            assert caught.value.code == SkillRuntimeErrorCode.AUTHORIZATION_STALE
            assert rt._store == {}

        asyncio.run(_test())

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("mount_revision", "stale-mount"),
            ("policy_revision", "stale-policy"),
            ("capability_digest", "stale-capabilities"),
            ("binding_ref", "stale-binding"),
            ("binding_version", 99),
            ("game_session_ref", "stale-session"),
        ],
    )
    def test_catalog_pins_are_independently_enforced(self, field, bad):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        catalog = dataclasses.replace(_make_catalog(resolved, ctx), **{field: bad})
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    catalog, resolved, auth, {}, ctx, tool_call_id="tc-pin",
                )
            assert caught.value.code == SkillRuntimeErrorCode.AUTHORIZATION_STALE

        asyncio.run(_test())

    @pytest.mark.parametrize("skills", [(), None])
    def test_catalog_requires_exactly_one_equal_target(self, skills):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        selected = (resolved, resolved) if skills is None else skills
        catalog = _make_catalog(resolved, ctx, skills=selected)
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    catalog, resolved, auth, {}, ctx, tool_call_id="tc-target",
                )
            assert caught.value.code == SkillRuntimeErrorCode.SKILL_UNAVAILABLE

        asyncio.run(_test())

    def test_catalog_rejects_different_target_value(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        different = dataclasses.replace(resolved, available=False)
        catalog = _make_catalog(resolved, ctx, skills=(different,))
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    catalog, resolved, auth, {}, ctx, tool_call_id="tc-target",
                )
            assert caught.value.code == SkillRuntimeErrorCode.SKILL_UNAVAILABLE

        asyncio.run(_test())

    @pytest.mark.parametrize("pin", ["implementation", "dependencies", "policy"])
    def test_authorization_material_pins(self, pin):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        if pin == "implementation":
            auth = dataclasses.replace(auth, implementation_digest="sha256:stale")
        elif pin == "dependencies":
            auth = dataclasses.replace(auth, dependency_bindings=())
        else:
            auth = dataclasses.replace(
                auth,
                effective_policy=EffectiveSkillPolicy(
                    decisions={"changes_save": RiskDecision.ALLOW},
                    requires_confirmation=False,
                ),
            )
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                    tool_call_id="tc-material-pin",
                )
            assert caught.value.code == SkillRuntimeErrorCode.AUTHORIZATION_STALE

        asyncio.run(_test())

    def test_unavailable_skill_rejected(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg, available=False)

        async def _test():
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            assert exc.value.code == SkillRuntimeErrorCode.SKILL_UNAVAILABLE

        asyncio.run(_test())

    def test_deny_policy_rejected(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        policy = EffectiveSkillPolicy(
            decisions={"changes_save": RiskDecision.DENY},
            requires_confirmation=False,
        )
        resolved = _make_resolved(pkg, policy=policy)

        async def _test():
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            assert exc.value.code == SkillRuntimeErrorCode.SKILL_UNAVAILABLE

        asyncio.run(_test())

    def test_confirmation_required_rejected(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        policy = EffectiveSkillPolicy(decisions={}, requires_confirmation=True)
        resolved = _make_resolved(pkg, policy=policy)

        async def _test():
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            assert exc.value.code == SkillRuntimeErrorCode.CONFIRMATION_REQUIRED

        asyncio.run(_test())

    def test_context_mismatch_capability_digest(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx(digest="digest-a")
        auth = _make_auth(pkg, prog, ctx)
        auth_bad = SkillExecutionAuthorization(
            skill_ref=auth.skill_ref, manifest_digest=auth.manifest_digest,
            implementation_digest=auth.implementation_digest,
            mount_revision=auth.mount_revision, policy_revision=auth.policy_revision,
            capability_digest="different-digest",
            binding_ref=auth.binding_ref, binding_version=auth.binding_version,
            game_session_ref=auth.game_session_ref,
            effective_policy=auth.effective_policy,
            dependency_bindings=auth.dependency_bindings,
        )
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.execute(_make_catalog(resolved, ctx), resolved, auth_bad, {}, ctx, tool_call_id="tc-1")
            assert exc.value.code == SkillRuntimeErrorCode.AUTHORIZATION_STALE

        asyncio.run(_test())

    def test_manifest_digest_mismatch(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        bad_auth = SkillExecutionAuthorization(
            skill_ref=auth.skill_ref, manifest_digest="sha256:deadbeef",
            implementation_digest=auth.implementation_digest,
            mount_revision=auth.mount_revision, policy_revision=auth.policy_revision,
            capability_digest=auth.capability_digest,
            binding_ref=auth.binding_ref, binding_version=auth.binding_version,
            game_session_ref=auth.game_session_ref,
            effective_policy=auth.effective_policy,
            dependency_bindings=auth.dependency_bindings,
        )
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.execute(_make_catalog(resolved, ctx), resolved, bad_auth, {}, ctx, tool_call_id="tc-1")
            assert exc.value.code == SkillRuntimeErrorCode.AUTHORIZATION_STALE

        asyncio.run(_test())

    def test_auth_stale_binding_ref(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        bad_auth = SkillExecutionAuthorization(
            skill_ref=auth.skill_ref, manifest_digest=auth.manifest_digest,
            implementation_digest=auth.implementation_digest,
            mount_revision=auth.mount_revision, policy_revision=auth.policy_revision,
            capability_digest=auth.capability_digest,
            binding_ref="wrong-binding", binding_version=auth.binding_version,
            game_session_ref=auth.game_session_ref,
            effective_policy=auth.effective_policy,
            dependency_bindings=auth.dependency_bindings,
        )
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.execute(_make_catalog(resolved, ctx), resolved, bad_auth, {}, ctx, tool_call_id="tc-1")
            assert exc.value.code == SkillRuntimeErrorCode.AUTHORIZATION_STALE

        asyncio.run(_test())


# ===================================================================
# Lifecycle tests
# ===================================================================


class TestLifecycle:
    def test_start_rejects_boolean_deadline(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                    tool_call_id="tc-deadline", deadline=True,
                )
            assert caught.value.code == SkillRuntimeErrorCode.INVALID_ARGUMENTS

        asyncio.run(_test())

    def test_authorization_none_policy_is_neutral_internal(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = dataclasses.replace(_make_auth(pkg, prog, ctx), effective_policy=None)
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.execute(
                    _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                    tool_call_id="tc-policy",
                )
            assert caught.value.code == SkillRuntimeErrorCode.INTERNAL

        asyncio.run(_test())

    def test_invalid_uuid_factory_is_internal(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        for factory in (lambda: "", lambda: False):
            rt = SkillRuntime(
                {ref: prog}, FakeCapabilityRuntime(), MemorySkillAuditSink(),
                time.time, factory, asyncio.sleep,
            )

            async def _test():
                with pytest.raises(SkillRuntimeError) as caught:
                    await rt.start(
                        _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                        tool_call_id="tc-uuid",
                    )
                assert caught.value.code == SkillRuntimeErrorCode.INTERNAL

            asyncio.run(_test())

    def test_throwing_uuid_factory_is_internal(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)

        def explode():
            raise RuntimeError("uuid-secret")

        rt = SkillRuntime(
            {ref: prog}, FakeCapabilityRuntime(), MemorySkillAuditSink(),
            time.time, explode, asyncio.sleep,
        )

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                    tool_call_id="tc-uuid",
                )
            assert caught.value.code == SkillRuntimeErrorCode.INTERNAL

        asyncio.run(_test())

    def test_two_capability_steps_release_terminal_handle(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        calls: list[str] = []

        async def two_steps(ctx, arguments):
            first = await ctx.invoke("say", ">=1.0.0", {"message": "one"})
            calls.append(first.command_id)
            second = await ctx.invoke("say", ">=1.0.0", {"message": "two"})
            calls.append(second.command_id)
            return {}

        prog = dataclasses.replace(prog, run=two_steps)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        cap = FakeCapabilityRuntime()
        rt = _make_runtime({ref: prog}, cap_runtime=cap)

        result = asyncio.run(rt.execute(
            _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
            tool_call_id="tc-two-step",
        ))
        assert result.state == SkillExecutionState.SUCCEEDED
        assert calls == ["cmd-0", "cmd-1"]
        assert len(cap.start_calls) == 2

    @pytest.mark.parametrize(
        ("side_effect", "expected"),
        [(SideEffect.READ_ONLY, False), (SideEffect.MUTATING, True)],
    )
    def test_success_side_effect_flag_comes_from_capability_contract_handle(
        self, side_effect, expected,
    ):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)

        async def invoke_once(ctx, arguments):
            await ctx.invoke("say", ">=1.0.0", {"message": "hello"})
            return {}

        prog = dataclasses.replace(prog, run=invoke_once)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        cap = FakeCapabilityRuntime()
        cap.side_effect = side_effect
        rt = _make_runtime({ref: prog}, cap_runtime=cap)

        result = asyncio.run(rt.execute(
            _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
            tool_call_id="tc-side-effect-success",
        ))
        assert result.state is SkillExecutionState.SUCCEEDED
        assert result.capability_command_ids == ("cmd-0",)
        assert result.side_effects_may_have_occurred is expected

    def test_read_only_capability_failure_never_reports_side_effects(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)

        async def invoke_then_fail(ctx, arguments):
            await ctx.invoke("say", ">=1.0.0", {"message": "hello"})
            raise RuntimeError("implementation failed after read")

        prog = dataclasses.replace(prog, run=invoke_then_fail)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        cap = FakeCapabilityRuntime()
        cap.side_effect = SideEffect.READ_ONLY
        rt = _make_runtime({ref: prog}, cap_runtime=cap)

        result = asyncio.run(rt.execute(
            _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
            tool_call_id="tc-side-effect-failure",
        ))
        assert result.state is SkillExecutionState.FAILED
        assert result.capability_command_ids == ("cmd-0",)
        assert result.side_effects_may_have_occurred is False

    def test_execute_success(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            return await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")

        result = asyncio.run(_test())
        assert result.state == SkillExecutionState.SUCCEEDED
        assert result.output == {}

    def test_execute_is_start_plus_await(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            handle = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            result = await rt.await_result(handle)
            return result

        result = asyncio.run(_test())
        assert result.state == SkillExecutionState.SUCCEEDED

    def test_duplicate_execution_id_rejected(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            h1 = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1",
                                skill_execution_id="dup-id")
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-2",
                               skill_execution_id="dup-id")
            assert exc.value.code == SkillRuntimeErrorCode.EXECUTION_CONFLICT
            # Await the first handle to clean up
            await rt.await_result(h1)

        asyncio.run(_test())

    def test_repeated_await_raises_not_found(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            handle = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            await rt.await_result(handle)
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.await_result(handle)
            assert exc.value.code == SkillRuntimeErrorCode.EXECUTION_NOT_FOUND

        asyncio.run(_test())

    def test_forged_handle_raises_not_found(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            h = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            await rt.await_result(h)
            forged = SkillExecutionHandle(
                skill_execution_id="nonexistent", skill_ref=ref,
                tool_call_id="tc-1", deadline_at=time.time() + 30,
            )
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.await_result(forged)
            assert exc.value.code == SkillRuntimeErrorCode.EXECUTION_NOT_FOUND

        asyncio.run(_test())


# ===================================================================
# Cancel tests
# ===================================================================


class TestCancel:
    def test_cancel_non_cancellable_skill(self):
        pkg = _parse_pkg("test.non.cancellable")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            handle = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            result = await rt.cancel(handle, reason="test cancel")
            return result

        result = asyncio.run(_test())
        assert result.accepted is False
        assert result.error_code == SkillRuntimeErrorCode.NOT_CANCELLABLE.value

    def test_cancel_cancellable_skill(self):
        pkg = _parse_pkg("test.runtime.cancellable")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            handle = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            await asyncio.sleep(0.05)
            result = await rt.cancel(handle, reason="test cancel")
            return result

        result = asyncio.run(_test())
        assert result.skill_execution_id is not None
        assert result.state is not None

    def test_cancel_invalid_reason_raises(self):
        pkg = _parse_pkg("test.runtime.cancellable")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        rt = _make_runtime({ref: prog})
        resolved = _make_resolved(pkg)

        async def _test():
            handle = await rt.start(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")
            with pytest.raises(SkillRuntimeError) as exc:
                await rt.cancel(handle, reason="")
            assert exc.value.code == SkillRuntimeErrorCode.INVALID_ARGUMENTS

        asyncio.run(_test())


# ===================================================================
# Schema tests
# ===================================================================


class TestSchemaValidation:
    def test_invalid_json_type_rejected(self):
        with pytest.raises(SkillRuntimeError):
            _validate_json_compatible(b"bytes")

    def test_tuple_rejected(self):
        with pytest.raises(SkillRuntimeError):
            _validate_json_compatible((1, 2))

    @pytest.mark.parametrize("payload", [{1: "value"}, {"nested": {2: "value"}}])
    def test_public_start_rejects_non_string_keys(self, payload):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, payload, ctx,
                    tool_call_id="tc-json",
                )
            assert caught.value.code == SkillRuntimeErrorCode.INVALID_ARGUMENTS

        asyncio.run(_test())

    def test_public_start_rejects_cycle(self):
        payload: dict[str, Any] = {}
        payload["cycle"] = payload
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog})

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, payload, ctx,
                    tool_call_id="tc-cycle",
                )
            assert caught.value.code == SkillRuntimeErrorCode.INVALID_ARGUMENTS

        asyncio.run(_test())

    @pytest.mark.parametrize("returned", [42, {"unexpected": True}])
    def test_public_execute_preserves_output_contract_violation(self, returned):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)

        async def invalid_output(ctx, arguments):
            return returned

        prog = dataclasses.replace(prog, run=invalid_output)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog})
        result = asyncio.run(rt.execute(
            _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
            tool_call_id="tc-output",
        ))
        assert result.state == SkillExecutionState.FAILED
        assert result.error_code == SkillRuntimeErrorCode.OUTPUT_CONTRACT_VIOLATION.value


    def test_set_rejected(self):
        with pytest.raises(SkillRuntimeError):
            _validate_json_compatible({1, 2})

    def test_non_finite_float_rejected(self):
        with pytest.raises(SkillRuntimeError):
            _validate_json_compatible(float("nan"))

    def test_non_string_key_rejected(self):
        with pytest.raises(SkillRuntimeError):
            _validate_json_compatible({1: "value"})

    def test_depth_limit(self):
        deep = {}
        current = deep
        for _ in range(70):
            current["nested"] = {}
            current = current["nested"]
        with pytest.raises(SkillRuntimeError):
            _validate_json_compatible(deep)

    def test_valid_types_accepted(self):
        _validate_json_compatible(None)
        _validate_json_compatible(True)
        _validate_json_compatible("hello")
        _validate_json_compatible(42)
        _validate_json_compatible(3.14)
        _validate_json_compatible([1, "two", 3.0])
        _validate_json_compatible({"a": 1, "b": [2]})


# ===================================================================
# RuntimeError properties
# ===================================================================


class TestRuntimeError:
    def test_safe_message_is_code_value(self):
        e = SkillRuntimeError(SkillRuntimeErrorCode.INTERNAL)
        assert e.safe_message == "internal"

    def test_retryable_codes(self):
        retryable = {
            SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED,
            SkillRuntimeErrorCode.CAPABILITY_FAILED,
            SkillRuntimeErrorCode.TIMEOUT,
            SkillRuntimeErrorCode.INTERNAL,
        }
        for code in SkillRuntimeErrorCode:
            e = SkillRuntimeError(code)
            if code in retryable:
                assert e.retryable is True, f"{code} should be retryable"
            else:
                assert e.retryable is False, f"{code} should not be retryable"

    def test_no_message_or_retryable_override(self):
        e = SkillRuntimeError(SkillRuntimeErrorCode.INVALID_ARGUMENTS)
        assert e.safe_message == "invalid_arguments"
        assert e.retryable is False


# ===================================================================
# Audit tests
# ===================================================================


class TestAudit:
    def test_started_audit_failure_creates_no_record_or_task(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        invoked = False

        async def observable_run(ctx, arguments):
            nonlocal invoked
            invoked = True
            return {}

        prog = dataclasses.replace(prog, run=observable_run)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        rt = _make_runtime({ref: prog}, audit=ThrowingSkillAuditSink())

        async def _test():
            with pytest.raises(SkillRuntimeError) as caught:
                await rt.start(
                    _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                    tool_call_id="tc-audit-fail",
                )
            assert caught.value.code == SkillRuntimeErrorCode.INTERNAL
            assert rt._store == {}
            await asyncio.sleep(0)
            assert invoked is False

        asyncio.run(_test())
    def test_started_event_emitted(self):
        pkg = _parse_pkg("test.with.capability")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        audit = MemorySkillAuditSink()
        rt = _make_runtime({ref: prog}, audit=audit)
        resolved = _make_resolved(pkg)

        async def _test():
            await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")

        asyncio.run(_test())
        started = [e for e in audit.events if e.event == "skill.execution.started"]
        assert len(started) == 1
        assert started[0].detail["state"] == "CREATED"

    def test_cancel_audit_preserves_full_start_correlation(self):
        pkg = _parse_pkg("test.runtime.cancellable")
        ref, prog = _load_prog(pkg)
        release = asyncio.Event()

        async def slow(ctx, arguments):
            await release.wait()
            return {}

        prog = dataclasses.replace(prog, run=slow)
        ctx = dataclasses.replace(
            _make_ctx(), trace_context={"request_id": "request-cancel"},
        )
        auth = _make_auth(pkg, prog, ctx)
        resolved = _make_resolved(pkg)
        audit = MemorySkillAuditSink()
        rt = _make_runtime({ref: prog}, audit=audit)

        async def _test():
            handle = await rt.start(
                _make_catalog(resolved, ctx), resolved, auth, {}, ctx,
                tool_call_id="tool-cancel", skill_execution_id="skill-cancel",
            )
            await asyncio.sleep(0)
            result = await rt.cancel(handle, reason="user requested")
            assert result.accepted is True

        asyncio.run(_test())
        correlated = [
            event for event in audit.events
            if event.event in {
                "skill.execution.started",
                "skill.execution.cancel_requested",
                "skill.execution.finished",
            }
        ]
        assert [event.event for event in correlated] == [
            "skill.execution.started",
            "skill.execution.cancel_requested",
            "skill.execution.finished",
        ]
        for event in correlated:
            assert event.request_id == "request-cancel"
            assert event.tool_call_id == "tool-cancel"
            assert event.skill_execution_id == "skill-cancel"
            assert event.manifest_digest == auth.manifest_digest
            assert event.implementation_digest == auth.implementation_digest
            assert event.runtime_ref == ctx.runtime_ref
            assert event.binding_ref == auth.binding_ref
            assert event.binding_version == auth.binding_version
            assert event.game_session_ref == auth.game_session_ref
            assert event.capability_digest == auth.capability_digest

    def test_finished_event_emitted(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        audit = MemorySkillAuditSink()
        rt = _make_runtime({ref: prog}, audit=audit)
        resolved = _make_resolved(pkg)

        async def _test():
            await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-1")

        asyncio.run(_test())
        finished = [e for e in audit.events if e.event == "skill.execution.finished"]
        assert len(finished) == 1
        assert finished[0].detail["state"] == "SUCCEEDED"

    def test_audit_has_tool_call_id_linkage(self):
        pkg = _parse_pkg("test.minimal.v1")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        audit = MemorySkillAuditSink()
        rt = _make_runtime({ref: prog}, audit=audit)
        resolved = _make_resolved(pkg)

        async def _test():
            await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {}, ctx, tool_call_id="tc-my-link")

        asyncio.run(_test())
        for e in audit.events:
            assert e.tool_call_id == "tc-my-link"

    def test_audit_never_discloses_secrets(self):
        pkg = _parse_pkg("test.with.capability")
        ref, prog = _load_prog(pkg)
        ctx = _make_ctx()
        auth = _make_auth(pkg, prog, ctx)
        audit = MemorySkillAuditSink()
        rt = _make_runtime({ref: prog}, audit=audit)
        resolved = _make_resolved(pkg)

        async def _test():
            await rt.execute(_make_catalog(resolved, ctx), resolved, auth, {"message": "top-secret"}, ctx, tool_call_id="tc-1")

        asyncio.run(_test())
        for e in audit.events:
            assert "arguments" not in str(e.detail)
            assert "output" not in str(e.detail)
            assert "top-secret" not in str(e.detail)
