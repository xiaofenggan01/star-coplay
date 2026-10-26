from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION, CancelResult, CommandState, DispatchState, GameCommand,
    GameCommandResult, RouteHealth, SideEffect,
)
from modules.mcp.core.errors import McpError
from modules.mcp.context import EphemeralSecret
from modules.mcp.routers import LocalModRouter, LocalRouteFence
from tests.mcp.conftest import make_context


class StubMod:
    def __init__(self, *, connected=True):
        self._connected = connected
        self.commands = []
        self.results = {}
        self.timeout = False
        self.dispatch_gate = None

    @property
    def connected(self):
        return self._connected

    async def dispatch(self, command):
        if self.dispatch_gate is not None:
            await self.dispatch_gate.wait()
        self.commands.append(command)

    async def result(self, command_id, deadline):
        if self.timeout:
            raise asyncio.TimeoutError
        return self.results.get(command_id, GameCommandResult(command_id, CommandState.SUCCEEDED))

    async def status(self, command_id):
        if self.timeout:
            raise asyncio.TimeoutError
        return self.results.get(command_id, GameCommandResult(command_id, CommandState.UNKNOWN)).state

    async def cancel(self, command_id):
        current = self.results.get(command_id)
        if current and current.state is CommandState.CANCELLED:
            return CancelResult(command_id, False, CommandState.CANCELLED)
        self.results[command_id] = GameCommandResult(command_id, CommandState.CANCELLED)
        return CancelResult(command_id, True, CommandState.CANCELLED)

    async def health(self):
        return RouteHealth(True)


def fence():
    return LocalRouteFence(
        "handle", "principal-a", "runtime-principal-a", "game-a", "nonce-a",
        1, "binding-principal-a", 1, 1, "digest-a",
    )


def command(command_id="cmd", *, payload=None, operation=SideEffect.MUTATING, lease=1, nonce="nonce-a"):
    return GameCommand(
        GAME_COMMAND_VERSION, f"msg-{command_id}", 100.0, command_id, command_id,
        f"tc-{command_id}", "say", "canonical.v1", "digest-a", operation,
        payload or {"message": "hello"}, 120.0, "handle", nonce, lease,
    )


def stack(*, connected=True, clock=lambda: 100.0):
    mod = StubMod(connected=connected)
    secret = EphemeralSecret("boot-secret")
    router = LocalModRouter(
        fence(), mod, boot_secret=secret, presented_secret="boot-secret", clock=clock
    )
    return replace(make_context(binding_version=1), profile="local_direct"), mod, router


def test_wrong_mod_binding_secret_fails_closed():
    with pytest.raises(McpError) as caught:
        LocalModRouter(
            fence(), StubMod(), boot_secret=EphemeralSecret("boot-secret"),
            presented_secret="wrong",
        )
    assert caught.value.code == "capability_denied"


@pytest.mark.asyncio
async def test_router_implements_resolve_dispatch_result_status_cancel_health():
    context, mod, router = stack()
    assert (await router.resolve(context)).available
    receipt = await router.dispatch(context, command())
    assert receipt.state is DispatchState.ACCEPTED
    assert (await router.await_result(context, "cmd", 120)).state is CommandState.SUCCEEDED
    assert await router.get_status(context, "cmd") is CommandState.UNKNOWN
    assert (await router.cancel(context, "cmd")).accepted
    assert await router.get_status(context, "cmd") is CommandState.CANCELLED
    assert (await router.health(context)).healthy


@pytest.mark.asyncio
async def test_duplicate_collision_and_new_nonce_are_distinct_domains():
    context, mod, router = stack()
    original = command()
    assert (await router.dispatch(context, original)).state is DispatchState.ACCEPTED
    assert (await router.dispatch(context, original)).state is DispatchState.DUPLICATE
    assert (await router.dispatch(context, replace(original, validated_payload={"message": "different"}))).state is DispatchState.REJECTED
    assert len(mod.commands) == 1

    new_fence = replace(fence(), game_instance_nonce="nonce-new")
    new_mod = StubMod()
    new_router = LocalModRouter(
        new_fence, new_mod, boot_secret=EphemeralSecret("boot-secret"),
        presented_secret="boot-secret", clock=lambda: 100.0,
    )
    new_context = replace(context, game_instance_nonce="nonce-new")
    assert (await new_router.dispatch(new_context, command(nonce="nonce-new"))).state is DispatchState.ACCEPTED


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("game_instance_nonce", "stale"), ("generation", 2),
    ("binding_version", 2), ("capability_digest", "stale"), ("profile", "hosted"),
])
async def test_stale_fencing_is_rejected(field, value):
    context, _, router = stack()
    with pytest.raises(McpError) as caught:
        await router.dispatch(replace(context, **{field: value}), command())
    assert caught.value.code == "binding_unavailable"


@pytest.mark.asyncio
async def test_stale_lease_and_second_mutating_owner_are_rejected():
    context, mod, router = stack()
    with pytest.raises(McpError) as caught:
        await router.dispatch(replace(context, lease_epoch=2), command(lease=2))
    assert caught.value.code == "conflict"
    await router.dispatch(context, command())
    other = replace(context, principal_id="other", runtime_ref="other-runtime")
    with pytest.raises(McpError) as caught:
        await router.dispatch(other, command("other"))
    assert caught.value.code == "conflict"
    assert len(mod.commands) == 1


@pytest.mark.asyncio
async def test_concurrent_second_owner_cannot_race_owner_acquisition():
    context, mod, router = stack()
    mod.dispatch_gate = asyncio.Event()
    first = asyncio.create_task(router.dispatch(context, command("first")))
    await asyncio.sleep(0)
    other = replace(context, principal_id="other", runtime_ref="other-runtime")
    second = asyncio.create_task(router.dispatch(other, command("second")))
    await asyncio.sleep(0)
    mod.dispatch_gate.set()
    assert (await first).state is DispatchState.ACCEPTED
    with pytest.raises(McpError) as caught:
        await second
    assert caught.value.code == "conflict"
    assert [item.command_id for item in mod.commands] == ["first"]


@pytest.mark.asyncio
async def test_private_mod_errors_are_redacted_and_result_correlation_is_checked():
    context, mod, router = stack()
    secret = "private-mod-secret"

    async def fail(_command):
        raise RuntimeError(secret)

    mod.dispatch = fail
    with pytest.raises(McpError) as caught:
        await router.dispatch(context, command())
    assert caught.value.code == "internal_error"
    assert secret not in str(caught.value)

    context, mod, router = stack()
    mod.results["cmd"] = GameCommandResult("wrong-id", CommandState.SUCCEEDED)
    with pytest.raises(McpError) as caught:
        await router.await_result(context, "cmd", 120)
    assert caught.value.code == "upstream_protocol_error"


@pytest.mark.asyncio
async def test_timeout_and_disconnected_mod_preserve_unknown_or_actionable_route():
    context, mod, router = stack()
    mod.timeout = True
    assert (await router.await_result(context, "uncertain", 0)).state is CommandState.UNKNOWN
    assert await router.get_status(context, "uncertain") is CommandState.UNKNOWN

    context, _, disconnected = stack(connected=False)
    assert not (await disconnected.resolve(context)).available
    assert not (await disconnected.health(context)).healthy
    assert (await disconnected.cancel(context, "missing")).state is CommandState.UNKNOWN
    with pytest.raises(McpError) as caught:
        await disconnected.dispatch(context, command())
    assert caught.value.code == "route_unavailable"


@pytest.mark.asyncio
async def test_router_enforces_its_own_bounded_deadline_without_trusting_connection():
    context, mod, router = stack(clock=lambda: 100.0)
    gate = asyncio.Event()

    async def never_returns(_command_id, _deadline):
        await gate.wait()

    mod.result = never_returns
    started = asyncio.get_running_loop().time()
    result = await router.await_result(context, "uncertain", 100.01)
    elapsed = asyncio.get_running_loop().time() - started
    assert result.state is CommandState.UNKNOWN
    assert result.error_code == "command_timeout"
    assert elapsed < 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["resolve", "await_result", "get_status", "cancel", "health"])
async def test_second_owner_is_rejected_across_entire_router_surface(surface):
    context, _, router = stack()
    await router.resolve(context)
    other = replace(context, principal_id="other", runtime_ref="other-runtime")
    with pytest.raises(McpError) as caught:
        if surface == "resolve":
            await router.resolve(other)
        elif surface == "await_result":
            await router.await_result(other, "cmd", 120)
        elif surface == "get_status":
            await router.get_status(other, "cmd")
        elif surface == "cancel":
            await router.cancel(other, "cmd")
        else:
            await router.health(other)
    assert caught.value.code == "conflict"


@pytest.mark.asyncio
async def test_read_only_dispatch_also_establishes_single_boot_owner():
    context, _, router = stack()
    await router.dispatch(context, command("read", operation=SideEffect.READ_ONLY, lease=None))
    other = replace(context, principal_id="other", runtime_ref="other-runtime")
    with pytest.raises(McpError) as caught:
        await router.dispatch(other, command("other-read", operation=SideEffect.READ_ONLY, lease=None))
    assert caught.value.code == "conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["resolve", "health"])
async def test_attacker_cannot_become_owner_by_calling_non_command_surface_first(surface):
    context, _, router = stack()
    attacker = replace(context, principal_id="attacker", runtime_ref="attacker-runtime")
    with pytest.raises(McpError) as caught:
        if surface == "resolve":
            await router.resolve(attacker)
        else:
            await router.health(attacker)
    assert caught.value.code == "conflict"
    # The frozen boot owner remains valid after the rejected first call.
    assert (await router.resolve(context)).available


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,lease", [
    (SideEffect.READ_ONLY, None), (SideEffect.MUTATING, 1),
])
@pytest.mark.parametrize("deadline", [99.0, 100.0])
async def test_expired_or_exact_boundary_command_is_never_written_downstream(operation, lease, deadline):
    context, mod, router = stack(clock=lambda: 100.0)
    expired = replace(
        command("expired", operation=operation, lease=lease),
        occurred_at=90.0, deadline_at=deadline,
    )
    with pytest.raises(McpError) as caught:
        await router.dispatch(context, expired)
    assert caught.value.code == "conflict"
    assert mod.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,lease", [
    (SideEffect.READ_ONLY, None), (SideEffect.MUTATING, 1),
])
async def test_command_strictly_after_deadline_boundary_may_dispatch(operation, lease):
    context, mod, router = stack(clock=lambda: 100.0)
    live = replace(command("live", operation=operation, lease=lease), deadline_at=100.001)
    assert (await router.dispatch(context, live)).state is DispatchState.ACCEPTED
    assert [item.command_id for item in mod.commands] == ["live"]


@pytest.mark.asyncio
async def test_deadline_is_rechecked_after_waiting_for_dispatch_lock():
    now = [100.0]
    context, mod, router = stack(clock=lambda: now[0])
    mod.dispatch_gate = asyncio.Event()
    first = asyncio.create_task(router.dispatch(
        context, replace(command("first"), deadline_at=200.0)
    ))
    await asyncio.sleep(0)
    expiring = asyncio.create_task(router.dispatch(
        context, replace(command("expiring"), deadline_at=101.0)
    ))
    await asyncio.sleep(0)
    now[0] = 101.0
    mod.dispatch_gate.set()
    assert (await first).state is DispatchState.ACCEPTED
    with pytest.raises(McpError) as caught:
        await expiring
    assert caught.value.code == "conflict"
    assert [item.command_id for item in mod.commands] == ["first"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"contract_version": "starcoplay.game-command.v0"},
    {"execution_context_ref": "other-context"},
    {"game_instance_nonce": "other-nonce"},
    {"capability_digest": "other-digest"},
    {"message_id": ""},
    {"occurred_at": 121.0},
    {"operation": "mutating"},
])
async def test_malformed_game_command_fails_closed(change):
    context, mod, router = stack()
    with pytest.raises(McpError) as caught:
        await router.dispatch(context, replace(command(), **change))
    assert caught.value.code == "upstream_protocol_error"
    assert mod.commands == []


@pytest.mark.asyncio
async def test_read_only_lease_shape_and_mutating_epoch_fail_closed():
    context, mod, router = stack()
    read = command("read", operation=SideEffect.READ_ONLY, lease=None)
    assert (await router.dispatch(context, read)).state is DispatchState.ACCEPTED
    with pytest.raises(McpError) as caught:
        await router.dispatch(context, command("bad-read", operation=SideEffect.READ_ONLY, lease=1))
    assert caught.value.code == "upstream_protocol_error"
    with pytest.raises(McpError) as caught:
        await router.dispatch(context, command("bad-write", lease=None))
    assert caught.value.code == "conflict"
    assert [item.command_id for item in mod.commands] == ["read"]


@pytest.mark.asyncio
async def test_cancel_result_command_id_must_correlate():
    context, mod, router = stack()

    async def wrong(_command_id):
        return CancelResult("other", True, CommandState.CANCELLED)

    mod.cancel = wrong
    with pytest.raises(McpError) as caught:
        await router.cancel(context, "expected")
    assert caught.value.code == "upstream_protocol_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["result", "status", "cancel", "health"])
async def test_malformed_mod_responses_fail_closed(surface):
    context, mod, router = stack()
    if surface == "result":
        async def malformed(*_args): return {"command_id": "cmd", "state": "SUCCEEDED"}
        mod.result = malformed
        call = router.await_result(context, "cmd", 120)
    elif surface == "status":
        async def malformed(*_args): return "SUCCEEDED"
        mod.status = malformed
        call = router.get_status(context, "cmd")
    elif surface == "cancel":
        async def malformed(*_args): return {"command_id": "cmd"}
        mod.cancel = malformed
        call = router.cancel(context, "cmd")
    else:
        async def malformed(*_args): return {"healthy": True}
        mod.health = malformed
        call = router.health(context)
    with pytest.raises(McpError) as caught:
        await call
    assert caught.value.code == "upstream_protocol_error"
