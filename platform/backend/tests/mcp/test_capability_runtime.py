"""M3.2 RouterBackedCapabilityRuntime tests."""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from modules.mcp.contracts.models import (
    CancelResult,
    CommandState,
    DispatchReceipt,
    DispatchState,
    ExecutionContext,
    GameCommand,
    GameCommandResult,
    RouteHealth,
    RouteStatus,
    SideEffect,
)
from modules.mcp.core.audit import AuditEvent, MemoryAuditSink
from modules.mcp.fakes.router import FakeGameSessionRouter, RouteFence
from modules.mcp.capabilities.contracts import build_capability_contract_catalog
from modules.mcp.capabilities.models import (
    CapabilityErrorCode,
    CapabilityContractCatalog,
    CapabilityInvocationHandle,
    CapabilityRef,
    CapabilityRuntimeError,
    CapabilitySnapshot,
    ProtocolCapabilityEntry,
)
from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
from modules.mcp.capabilities.snapshot import compute_capability_snapshot_digest

REPO_ROOT = Path(__file__).resolve().parents[4]

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

_SAY_ENTRY = ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5")
_FACE_ENTRY = ProtocolCapabilityEntry("protocol-2.4.5", "face", "2.4.5")
_SAY_DIGEST = compute_capability_snapshot_digest([_SAY_ENTRY])
_SAY_FACE_DIGEST = compute_capability_snapshot_digest([_SAY_ENTRY, _FACE_ENTRY])

# Authoritative Protocol CommandResult-shaped payload accepted by
# normalize_canonical_result.  Fields match the frozen capability_pb2.CommandResult.
_SAY_PROTO_SUCCESS: dict[str, Any] = {
    "command_id": "cmd-success",
    "capability": "say",
    "success": True,
    "status": "COMMAND_STATUS_COMPLETED",
    "say": {"content_length": 5},
}
_SAY_PROTO_FAILED: dict[str, Any] = {
    "command_id": "cmd-failed",
    "capability": "say",
    "success": False,
    "status": "COMMAND_STATUS_REJECTED",
    "say": {},
    "error": {"code": "EXECUTION_FAILED", "message": "test failure"},
}
# Malformed — will fail output gate
_SAY_PROTO_BAD_SCHEMA: dict[str, Any] = {
    "command_id": "cmd-bad",
    "capability": "say",
    "success": True,
    "status": "COMMAND_STATUS_COMPLETED",
    "say": {"content_length": "not-an-integer"},
}


# ---------------------------------------------------------------------------
# Fake manifest provider
# ---------------------------------------------------------------------------

class FakeCapabilityManifestProvider:
    def __init__(self, entries: Sequence[ProtocolCapabilityEntry | Mapping[str, str]]):
        self._entries = list(entries)

    async def entries(self, context: ExecutionContext) -> Sequence[ProtocolCapabilityEntry | Mapping[str, str]]:
        return list(self._entries)


class _ThrowingManifestProvider:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def entries(self, context: ExecutionContext):
        raise self._exc


# ---------------------------------------------------------------------------
# Fake router that refuses resolve
# ---------------------------------------------------------------------------

class _UnavailableRouteRouter:
    def __init__(self):
        self.dispatch_count = 0

    async def resolve(self, context):
        return RouteStatus(False, context.game_session_ref)

    async def dispatch(self, context, command):
        self.dispatch_count += 1
        return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)

    async def await_result(self, context, command_id, deadline):
        return GameCommandResult(command_id, CommandState.UNKNOWN)

    async def cancel(self, context, command_id):
        return CancelResult(command_id, False, CommandState.UNKNOWN)

    async def get_status(self, context, command_id):
        return CommandState.UNKNOWN

    async def health(self, context):
        return RouteHealth(False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(*, digest=_SAY_DIGEST, scopes=None, **overrides):
    defaults: dict[str, Any] = dict(
        profile="fake", principal_id="principal-a",
        runtime_ref="runtime-principal-a", generation=1,
        binding_ref="binding-principal-a", binding_version=1,
        game_session_ref="game-a", game_instance_nonce="nonce-a",
        lease_epoch=1,
        scopes=scopes or frozenset({"game:read", "game:write"}),
        allowed_tools=frozenset({"say"}),
        capability_digest=digest,
        issued_at=time.time(),
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


def _make_runtime(
    *,
    router=None,
    manifest_entries=None,
    contract_catalog=None,
    context_ref=None,
    audit=None,
    activity=None,
    clock=None,
):
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    _router = router or FakeGameSessionRouter(fence)
    manifest = FakeCapabilityManifestProvider(
        manifest_entries if manifest_entries is not None else [_SAY_ENTRY]
    )
    catalog = contract_catalog or build_capability_contract_catalog(REPO_ROOT)
    return RouterBackedCapabilityRuntime(
        router=_router,
        manifest_provider=manifest,
        contract_catalog=catalog,
        context_ref=context_ref or (lambda ctx: ctx.runtime_ref),
        audit=audit,
        activity=activity,
        clock=clock or (lambda: 100.0),
    )


def _say_ref():
    return CapabilityRef("say", "2.4.5")


def _say_args():
    return {"message": "hello"}


def _pre_seed_proto_result(router, command_id, state, output):
    router.results[("game-a", "nonce-a", command_id)] = GameCommandResult(
        command_id, state, output,
    )


class _UnexpectedAdapter:
    def __init__(self, *, fail_prepare: bool = False, fail_normalize: bool = False):
        self._fail_prepare = fail_prepare
        self._fail_normalize = fail_normalize

    def prepare(self, arguments, command_id):
        if self._fail_prepare:
            raise RuntimeError("secret prepare detail")
        raise AssertionError("prepare should not be used by normalize-only test")

    def normalize(self, handle, result):
        if self._fail_normalize:
            raise RuntimeError("secret normalize detail")
        raise AssertionError("normalize should not be used by prepare-only test")


def _catalog_with_adapter(capability_id, adapter):
    base = build_capability_contract_catalog(REPO_ROOT)
    contracts = [
        replace(contract, adapter=adapter)
        if contract.capability_id == capability_id else contract
        for contract in base.contracts
    ]
    return CapabilityContractCatalog(contracts)


# ===================================================================
# start — basic lifecycle
# ===================================================================

@pytest.mark.asyncio
async def test_start_returns_handle_and_dispatches():
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router, context_ref=lambda _: "context-ref-a")
    ctx = _make_context(trace_context={"trace_id": "trace-a"})
    handle = await runtime.start(
        ctx, _say_ref(), _say_args(), tool_call_id="tc-1",
        command_id="cmd-fields", now=100.0, deadline=110.0,
    )
    assert isinstance(handle, CapabilityInvocationHandle)
    assert handle.command_id
    assert handle.tool_call_id == "tc-1"
    assert handle.capability_ref == _say_ref()
    assert handle.validated_arguments == {"message": "hello"}
    command = router.commands[("game-a", "nonce-a", "cmd-fields")]
    assert command.execution_context_ref == "context-ref-a"
    assert command.deadline_at == 110.0
    assert command.game_instance_nonce == "nonce-a"
    assert command.capability_digest == _SAY_DIGEST
    assert command.lease_epoch == 1


@pytest.mark.asyncio
async def test_start_read_only_query_menu_omits_lease_epoch():
    entry = ProtocolCapabilityEntry("protocol-2.4.5", "query_menu", "2.4.5")
    digest = compute_capability_snapshot_digest([entry])
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, digest)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router, manifest_entries=[entry])
    ctx = _make_context(
        digest=digest, scopes=frozenset({"game:read"}),
        allowed_tools=frozenset({"query_menu"}),
    )
    await runtime.start(
        ctx, CapabilityRef("query_menu", "2.4.5"), {},
        tool_call_id="tc-menu", command_id="cmd-menu",
    )
    command = router.commands[("game-a", "nonce-a", "cmd-menu")]
    assert command.operation is SideEffect.READ_ONLY
    assert command.lease_epoch is None


@pytest.mark.asyncio
async def test_start_explicit_command_id_is_preserved():
    runtime = _make_runtime()
    ctx = _make_context()
    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-1", command_id="cmd-stable",
    )
    assert handle.command_id == "cmd-stable"


# ===================================================================
# start — tp (internal-only precedes scope)
# ===================================================================

@pytest.mark.asyncio
async def test_start_tp_raises_internal_control_unavailable():
    manifest_entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "tp", "2.4.5"),
        _SAY_ENTRY,
    ]
    digest = compute_capability_snapshot_digest(manifest_entries)
    runtime = _make_runtime(manifest_entries=manifest_entries)
    ctx = _make_context(digest=digest, scopes=frozenset({"game:read", "game:write", "game:admin"}))
    tp_ref = CapabilityRef("tp", "2.4.5")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, tp_ref, {}, tool_call_id="tc-tp")
    # tp always returns INTERNAL_CONTROL_UNAVAILABLE, even with admin scope
    assert exc.value.code is CapabilityErrorCode.INTERNAL_CONTROL_UNAVAILABLE


# ===================================================================
# start — protected arguments / invalid arguments
# ===================================================================

@pytest.mark.asyncio
async def test_start_protected_arguments_raises_invalid_arguments():
    runtime = _make_runtime()
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(
            ctx, _say_ref(), {"message": "hi", "principal_id": "attacker"},
            tool_call_id="tc-1",
        )
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_start_invalid_arguments_schema_raises_invalid_arguments():
    runtime = _make_runtime()
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, _say_ref(), {"bad_field": 123}, tool_call_id="tc-1")
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_start_unexpected_prepare_exception_is_sanitized_internal():
    runtime = _make_runtime(contract_catalog=_catalog_with_adapter(
        "say", _UnexpectedAdapter(fail_prepare=True),
    ))
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(_make_context(), _say_ref(), _say_args(), tool_call_id="tc-prepare")
    assert exc.value.code is CapabilityErrorCode.INTERNAL
    assert exc.value.retryable is True
    assert "secret prepare detail" not in exc.value.safe_message


# ===================================================================
# start — scope
# ===================================================================

@pytest.mark.asyncio
async def test_start_missing_scope_raises_scope_denied():
    runtime = _make_runtime()
    ctx = _make_context(scopes=frozenset({"game:read"}))  # say needs game:write
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-1")
    assert exc.value.code is CapabilityErrorCode.SCOPE_DENIED


# ===================================================================
# start — unknown capability / unsupported version
# ===================================================================

@pytest.mark.asyncio
async def test_start_unknown_capability_raises_unavailable():
    runtime = _make_runtime()
    ctx = _make_context()
    ref = CapabilityRef("nonexistent", "2.4.5")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, ref, {}, tool_call_id="tc-1")
    assert exc.value.code is CapabilityErrorCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_start_unsupported_version_raises_version_unsupported():
    runtime = _make_runtime()
    ctx = _make_context()
    ref = CapabilityRef("say", "9.9.9")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, ref, _say_args(), tool_call_id="tc-1")
    assert exc.value.code is CapabilityErrorCode.VERSION_UNSUPPORTED


@pytest.mark.asyncio
async def test_start_missing_manifest_capability_raises_unavailable():
    """start refuses a capability whose id is in the static catalog but not in the
    current manifest snapshot — snapshot auth fails with UNAVAILABLE."""
    runtime = _make_runtime(manifest_entries=[_SAY_ENTRY])
    ctx = _make_context()  # digest matches only say
    face_ref = CapabilityRef("face", "2.4.5")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, face_ref, {}, tool_call_id="tc-face")
    assert exc.value.code is CapabilityErrorCode.UNAVAILABLE


# ===================================================================
# start — timing
# ===================================================================

@pytest.mark.asyncio
async def test_start_non_finite_now_raises_invalid_arguments():
    runtime = _make_runtime()
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(
            ctx, _say_ref(), _say_args(),
            tool_call_id="tc-1", now=float("inf"),
        )
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_start_explicit_deadline_not_finite_raises_invalid_arguments():
    runtime = _make_runtime()
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(
            ctx, _say_ref(), _say_args(),
            tool_call_id="tc-1", deadline=float("nan"),
        )
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_start_explicit_deadline_not_greater_than_issued_raises_invalid_arguments():
    runtime = _make_runtime(clock=lambda: 100.0)
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(
            ctx, _say_ref(), _say_args(),
            tool_call_id="tc-1", deadline=90.0,
        )
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


# ===================================================================
# start — route resolution
# ===================================================================

@pytest.mark.asyncio
async def test_start_refuses_unavailable_route_with_zero_dispatch():
    """When router.resolve returns unavailable, start raises ROUTE_UNAVAILABLE
    and never dispatches."""
    router = _UnavailableRouteRouter()
    runtime = _make_runtime(router=router)
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-route")
    assert exc.value.code is CapabilityErrorCode.ROUTE_UNAVAILABLE
    assert router.dispatch_count == 0


# ===================================================================
# start — snapshot rejection (does not dispatch)
# ===================================================================

@pytest.mark.asyncio
async def test_start_does_not_dispatch_after_snapshot_digest_mismatch():
    """Wrong context digest → SNAPSHOT_CONFLICT in snapshot → start never dispatches."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context(digest="sha256:0000000000000000000000000000000000000000000000000000000000000000")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-reject")
    assert exc.value.code is CapabilityErrorCode.SNAPSHOT_CONFLICT
    assert router.dispatch_count == 0


# ===================================================================
# start — provider failure mapping
# ===================================================================

@pytest.mark.asyncio
async def test_start_provider_failure_is_mapped():
    """Manifest provider exception is neutral-mapped through snapshot()."""
    exc = Exception("provider failure")
    exc.code = "binding_unavailable"  # type: ignore[attr-defined]
    manifest = _ThrowingManifestProvider(exc)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    runtime = RouterBackedCapabilityRuntime(
        router=FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)),
        manifest_provider=manifest,
        contract_catalog=catalog,
        context_ref=lambda ctx: ctx.runtime_ref,
    )
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc_info:
        await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-prov")
    assert exc_info.value.code is CapabilityErrorCode.BINDING_UNAVAILABLE


# ===================================================================
# Dispatch: duplicate, collision (via FakeGameSessionRouter)
# ===================================================================

@pytest.mark.asyncio
async def test_duplicate_dispatch_same_payload_returns_accepted_then_duplicate():
    """An explicit command_id dispatched twice with identical domain returns ACCEPTED
    then DUPLICATE with dispatch_count == 1."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    h1 = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-dup", command_id="cmd-stable",
    )
    assert h1.dispatch_receipt.state is DispatchState.ACCEPTED
    assert router.dispatch_count == 1

    h2 = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-dup", command_id="cmd-stable",
    )
    assert h2.dispatch_receipt.state is DispatchState.DUPLICATE
    assert router.dispatch_count == 1  # no second execution


@pytest.mark.asyncio
async def test_collision_different_payload_raises_dispatch_conflict():
    """Changing payload with same command_id returns conflict through Router REJECTED."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    await runtime.start(
        ctx, _say_ref(), {"message": "hello"},
        tool_call_id="tc-col", command_id="cmd-stable",
    )
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(
            ctx, _say_ref(), {"message": "changed"},
            tool_call_id="tc-col", command_id="cmd-stable",
        )
    assert exc.value.code is CapabilityErrorCode.DISPATCH_CONFLICT


# ===================================================================
# invoke / await_result — success with authoritative Proto fixture
# ===================================================================

@pytest.mark.asyncio
async def test_invoke_success_with_proto_shaped_result():
    """invoke returns SUCCEEDED with normalized output from authoritative Proto payload."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    # Pre-seed a valid Protocol CommandResult-shaped success payload
    _pre_seed_proto_result(router, "cmd-proto-ok", CommandState.SUCCEEDED, dict(_SAY_PROTO_SUCCESS))
    # Patch command_id into the payload (normalize expects match)
    router.results[("game-a", "nonce-a", "cmd-proto-ok")].output["command_id"] = "cmd-proto-ok"

    result = await runtime.invoke(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-invoke", command_id="cmd-proto-ok",
    )
    assert result.state is CommandState.SUCCEEDED
    assert result.output.get("success") is True
    assert result.output.get("capability") == "say"


# ===================================================================
# await_result — stable failure with authoritative Proto fixture
# ===================================================================

@pytest.mark.asyncio
async def test_await_result_stable_failure_normalizes():
    """FAILED result passes normalization with valid Proto payload."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    _pre_seed_proto_result(router, "cmd-fail", CommandState.FAILED, dict(_SAY_PROTO_FAILED))
    router.results[("game-a", "nonce-a", "cmd-fail")].output["command_id"] = "cmd-fail"

    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-fail", command_id="cmd-fail",
    )
    result = await runtime.await_result(ctx, handle)
    assert result.state is CommandState.FAILED
    assert result.output.get("success") is False
    assert result.output.get("error") is not None


# ===================================================================
# await_result — output gate (malformed schema)
# ===================================================================

@pytest.mark.asyncio
async def test_await_result_output_gate_rejects_malformed_proto():
    """A Proto result that violates the output schema raises UPSTREAM_CONTRACT_VIOLATION."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    _pre_seed_proto_result(router, "cmd-bad", CommandState.SUCCEEDED, dict(_SAY_PROTO_BAD_SCHEMA))
    router.results[("game-a", "nonce-a", "cmd-bad")].output["command_id"] = "cmd-bad"

    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-bad", command_id="cmd-bad",
    )
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.await_result(ctx, handle)
    assert exc.value.code is CapabilityErrorCode.UPSTREAM_CONTRACT_VIOLATION


@pytest.mark.asyncio
async def test_await_result_unexpected_normalize_exception_is_sanitized_internal():
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    base_catalog = build_capability_contract_catalog(REPO_ROOT)
    base_adapter = base_catalog.find_exact("say", "2.4.5").adapter

    class _NormalizeOnlyUnexpectedAdapter:
        def prepare(self, arguments, command_id):
            return base_adapter.prepare(arguments, command_id)

        def normalize(self, handle, result):
            raise RuntimeError("secret normalize detail")

    runtime = _make_runtime(
        router=router,
        contract_catalog=_catalog_with_adapter("say", _NormalizeOnlyUnexpectedAdapter()),
    )
    _pre_seed_proto_result(router, "cmd-normalize-error", CommandState.SUCCEEDED, dict(_SAY_PROTO_SUCCESS))
    router.results[("game-a", "nonce-a", "cmd-normalize-error")].output["command_id"] = "cmd-normalize-error"
    handle = await runtime.start(
        _make_context(), _say_ref(), _say_args(),
        tool_call_id="tc-normalize", command_id="cmd-normalize-error",
    )
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.await_result(_make_context(), handle)
    assert exc.value.code is CapabilityErrorCode.INTERNAL
    assert exc.value.retryable is True
    assert "secret normalize detail" not in exc.value.safe_message


@pytest.mark.asyncio
async def test_await_result_timed_out_with_empty_output_returns_unchanged_without_replay():
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()
    _pre_seed_proto_result(router, "cmd-timeout-empty", CommandState.TIMED_OUT, {})
    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-timeout-empty", command_id="cmd-timeout-empty",
    )
    result = await runtime.await_result(ctx, handle)
    assert result.state is CommandState.TIMED_OUT
    assert result.output == {}
    assert router.dispatch_count == 1


@pytest.mark.asyncio
async def test_await_result_timed_out_with_authoritative_proto_payload_normalizes():
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()
    timeout_payload = {
        "command_id": "cmd-timeout-proto",
        "capability": "say",
        "success": False,
        "status": "COMMAND_STATUS_TIMEOUT",
        "say": {},
        "error": {"code": "TIMEOUT", "message": "deadline reached"},
    }
    _pre_seed_proto_result(
        router, "cmd-timeout-proto", CommandState.TIMED_OUT, timeout_payload,
    )
    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-timeout-proto", command_id="cmd-timeout-proto",
    )
    result = await runtime.await_result(ctx, handle)
    assert result.state is CommandState.TIMED_OUT
    assert result.output["status"] == "TIMEOUT"
    assert result.output["success"] is False
    assert router.dispatch_count == 1


# ===================================================================
# await_result — UNKNOWN passes through unchanged
# ===================================================================

@pytest.mark.asyncio
async def test_await_result_unknown_state_returns_unchanged():
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    key = ("game-a", "nonce-a", "unknown-cmd")
    router.results[key] = GameCommandResult("unknown-cmd", CommandState.UNKNOWN)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    handle = CapabilityInvocationHandle(
        command_id="unknown-cmd", tool_call_id="tc-u",
        deadline_at=999.0,
        dispatch_receipt=DispatchReceipt("unknown-cmd", DispatchState.ACCEPTED),
        capability_ref=_say_ref(),
        side_effect=SideEffect.MUTATING,
        validated_arguments={"message": "hi"},
    )
    result = await runtime.await_result(ctx, handle)
    assert result.state is CommandState.UNKNOWN
    assert result.output == {}


@pytest.mark.asyncio
async def test_invoke_does_not_replay_on_unknown():
    """UNKNOWN result is returned unchanged; invoke does not issue a second dispatch."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    key = ("game-a", "nonce-a", "cmd-u")
    router.results[key] = GameCommandResult("cmd-u", CommandState.UNKNOWN)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    result = await runtime.invoke(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-u", command_id="cmd-u",
    )
    assert result.state is CommandState.UNKNOWN
    assert router.dispatch_count == 1  # only the initial dispatch, no replay


# ===================================================================
# cancel — seeded non-terminal for convergence
# ===================================================================

@pytest.mark.asyncio
async def test_cancel_convergence_and_audit():
    """Cancel a running command; seed non-terminal so convergence path is exercised."""
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    audit = MemoryAuditSink()
    runtime = _make_runtime(router=router, audit=audit)
    ctx = _make_context()

    # Seed non-terminal (RUNNING) so cancel actually converges
    _pre_seed_proto_result(router, "cmd-cancel", CommandState.RUNNING, {})

    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-cancel", command_id="cmd-cancel",
    )
    result = await runtime.cancel(ctx, handle, reason="user requested cancellation")
    # Fake router's cancel sets CANCELLED on the result
    assert result.accepted is True
    assert result.command_id == "cmd-cancel"

    # Audit emitted with reason in detail
    cancel_events = [e for e in audit.events if e.event == "command.cancel"]
    assert len(cancel_events) == 1
    assert cancel_events[0].command_id == "cmd-cancel"
    assert cancel_events[0].detail["reason"] == "user requested cancellation"


@pytest.mark.asyncio
async def test_cancel_empty_reason_raises_invalid_arguments():
    runtime = _make_runtime()
    ctx = _make_context()
    handle = await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-1")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.cancel(ctx, handle, reason="")
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_cancel_reason_too_long_raises_invalid_arguments():
    runtime = _make_runtime()
    ctx = _make_context()
    handle = await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-1")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.cancel(ctx, handle, reason="x" * 600)
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


# ===================================================================
# get_status
# ===================================================================

@pytest.mark.asyncio
async def test_get_status_delegates_to_router():
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router)
    ctx = _make_context()

    handle = await runtime.start(
        ctx, _say_ref(), _say_args(),
        tool_call_id="tc-status", command_id="cmd-status",
    )
    state = await runtime.get_status(ctx, handle.command_id)
    assert isinstance(state, CommandState)


# ===================================================================
# snapshot
# ===================================================================

@pytest.mark.asyncio
async def test_runtime_snapshot_returns_capability_snapshot():
    runtime = _make_runtime()
    ctx = _make_context()
    snap = await runtime.snapshot(ctx)
    assert isinstance(snap, CapabilitySnapshot)
    assert len(snap.known_contracts) >= 1
    assert snap.capability_digest == _SAY_DIGEST


# ===================================================================
# Audit
# ===================================================================

@pytest.mark.asyncio
async def test_runtime_audits_dispatch_and_result():
    audit = MemoryAuditSink()
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router, audit=audit)
    ctx = _make_context()

    _pre_seed_proto_result(router, "cmd-audit", CommandState.SUCCEEDED, dict(_SAY_PROTO_SUCCESS))
    router.results[("game-a", "nonce-a", "cmd-audit")].output["command_id"] = "cmd-audit"

    await runtime.invoke(ctx, _say_ref(), _say_args(), tool_call_id="tc-audit", command_id="cmd-audit")
    dispatch_events = [e for e in audit.events if e.event == "command.dispatched"]
    result_events = [e for e in audit.events if e.event == "command.result"]
    assert len(dispatch_events) >= 1
    assert len(result_events) >= 1


# ===================================================================
# Activity reporter (best-effort)
# ===================================================================

class _FakeActivityReporter:
    def __init__(self):
        self.reported: list[tuple[str, int]] = []

    async def report_succeeded(self, runtime_ref: str, generation: int) -> None:
        self.reported.append((runtime_ref, generation))


@pytest.mark.asyncio
async def test_activity_reporter_called_on_success():
    activity = _FakeActivityReporter()
    fence = RouteFence("game-a", "nonce-a", 1, 1, 1, _SAY_DIGEST)
    router = FakeGameSessionRouter(fence)
    runtime = _make_runtime(router=router, activity=activity)
    ctx = _make_context()

    _pre_seed_proto_result(router, "cmd-activity", CommandState.SUCCEEDED, dict(_SAY_PROTO_SUCCESS))
    router.results[("game-a", "nonce-a", "cmd-activity")].output["command_id"] = "cmd-activity"

    await runtime.invoke(ctx, _say_ref(), _say_args(), tool_call_id="tc-activity", command_id="cmd-activity")
    assert len(activity.reported) == 1
    assert activity.reported[0] == (ctx.runtime_ref, ctx.generation)


# ===================================================================
# Boundary exception mapping
# ===================================================================

class _FaultyRouter:
    """Router that raises exceptions with a .code string attribute."""

    def __init__(self, code: str):
        self._code = code

    async def resolve(self, context):
        exc = Exception("test")
        exc.code = self._code  # type: ignore[attr-defined]
        raise exc

    async def dispatch(self, context, command):
        exc = Exception("test")
        exc.code = self._code  # type: ignore[attr-defined]
        raise exc

    async def await_result(self, context, command_id, deadline):
        exc = Exception("test")
        exc.code = self._code  # type: ignore[attr-defined]
        raise exc

    async def cancel(self, context, command_id):
        exc = Exception("test")
        exc.code = self._code  # type: ignore[attr-defined]
        raise exc

    async def get_status(self, context, command_id):
        exc = Exception("test")
        exc.code = self._code  # type: ignore[attr-defined]
        raise exc

    async def health(self, context):
        exc = Exception("test")
        exc.code = self._code  # type: ignore[attr-defined]
        raise exc


@pytest.mark.asyncio
@pytest.mark.parametrize("router_code,expected", [
    ("invalid_arguments", CapabilityErrorCode.INVALID_ARGUMENTS),
    ("capability_denied", CapabilityErrorCode.SCOPE_DENIED),
    ("context_expired", CapabilityErrorCode.CONTEXT_STALE),
    ("binding_unavailable", CapabilityErrorCode.BINDING_UNAVAILABLE),
    ("route_unavailable", CapabilityErrorCode.ROUTE_UNAVAILABLE),
    ("conflict", CapabilityErrorCode.DISPATCH_CONFLICT),
    ("upstream_protocol_error", CapabilityErrorCode.UPSTREAM_CONTRACT_VIOLATION),
    ("unknown_weird_code", CapabilityErrorCode.INTERNAL),
])
async def test_boundary_maps_router_exception_codes(router_code, expected):
    runtime = _make_runtime(router=_FaultyRouter(router_code))
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-err")
    assert exc.value.code is expected
    assert "test" not in exc.value.safe_message


@pytest.mark.asyncio
async def test_boundary_exception_without_code_returns_internal():
    class _NoCodeRouter:
        async def resolve(self, context): raise RuntimeError("boom")
        async def dispatch(self, context, command): raise RuntimeError("boom")
        async def await_result(self, context, command_id, deadline): raise RuntimeError("boom")
        async def cancel(self, context, command_id): raise RuntimeError("boom")
        async def get_status(self, context, command_id): raise RuntimeError("boom")
        async def health(self, context): raise RuntimeError("boom")

    runtime = _make_runtime(router=_NoCodeRouter())
    ctx = _make_context()
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-err")
    assert exc.value.code is CapabilityErrorCode.INTERNAL
    assert exc.value.retryable is True
    assert "boom" not in exc.value.safe_message


@pytest.mark.asyncio
async def test_await_result_explicit_deadline_not_finite_raises():
    runtime = _make_runtime()
    ctx = _make_context()
    handle = await runtime.start(ctx, _say_ref(), _say_args(), tool_call_id="tc-1")
    with pytest.raises(CapabilityRuntimeError) as exc:
        await runtime.await_result(ctx, handle, deadline=float("nan"))
    assert exc.value.code is CapabilityErrorCode.INVALID_ARGUMENTS


# ===================================================================
# snapshot via runtime — unknown surface only in raw
# ===================================================================

@pytest.mark.asyncio
async def test_runtime_snapshot_unknown_surface_only_in_raw_entries():
    entries = [
        _SAY_ENTRY,
        ProtocolCapabilityEntry("protocol-9.0", "future_action", "9.0"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    runtime = _make_runtime(manifest_entries=entries)
    ctx = _make_context(digest=digest)
    snap = await runtime.snapshot(ctx)

    raw_ids = {e.capability_id for e in snap.raw_entries}
    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" in raw_ids and "say" in known_ids
    assert "future_action" in raw_ids
    assert "future_action" not in known_ids

    unknown = [d for d in snap.diagnostics if d.code == "unknown_source_surface"]
    assert len(unknown) == 1
    assert unknown[0].capability_id == "future_action"
