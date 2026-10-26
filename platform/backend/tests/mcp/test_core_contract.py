from __future__ import annotations

from dataclasses import replace

import pytest

from modules.mcp.contracts.models import CommandState, DispatchState, GameCommandResult
from modules.mcp.core.audit import MemoryAuditSink
from modules.mcp.core.errors import ERRORS, McpError
from modules.mcp.core.service import McpCore
from modules.mcp.fakes import FakeContextAuthority, FakeGameSessionRouter
from modules.mcp.fakes.router import RouteFence
from tests.mcp.conftest import make_context


@pytest.mark.asyncio
async def test_context_is_authority_injected_and_protected_arguments_are_rejected(stack):
    context, authority, router, catalog = stack
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential-a", "game_session_ref": "attacker"})
    with pytest.raises(McpError) as caught:
        await core.call_tool(handle, "say", {"message": "hi", "lease_epoch": 999}, tool_call_id="tc-1")
    assert caught.value.code == "invalid_arguments"
    assert router.commands == {}

    result = await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="tc-2", command_id="cmd-1")
    command = router.commands[("game-a", "nonce-a", "cmd-1")]
    assert result.state is CommandState.SUCCEEDED
    assert command.game_instance_nonce == context.game_instance_nonce
    assert command.capability_digest == context.capability_digest
    assert command.lease_epoch == context.lease_epoch
    assert command.contract_version == "starcoplay.game-command.v1"
    assert command.message_id
    assert command.occurred_at > 0
    assert command.idempotency_key == command.command_id
    assert "credential-a" not in repr(command)


@pytest.mark.asyncio
async def test_context_a_cannot_route_to_context_b(catalog):
    a = make_context()
    b = make_context(principal="principal-b", session="game-b", nonce="nonce-b", digest="digest-b")
    authority = FakeContextAuthority({"a": a, "b": b})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "digest-a"))
    core = McpCore(authority, router, catalog)
    handle_b = await authority.establish({"credential": "b"})
    with pytest.raises(McpError) as caught:
        await core.call_tool(handle_b, "observe", {}, tool_call_id="tc-b")
    assert caught.value.code == "binding_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("capability_digest", "old"), ("lease_epoch", 0), ("generation", 0), ("binding_version", 0)])
async def test_stale_fencing_is_rejected(stack, field, value):
    context, authority, router, catalog = stack
    handle = await authority.establish({"credential": "credential-a"})
    authority.replace_grant(handle, replace(context, **{field: value}))
    core = McpCore(authority, router, catalog)
    with pytest.raises(McpError) as caught:
        await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="tc-stale")
    assert caught.value.code in {"binding_unavailable", "conflict"}


@pytest.mark.asyncio
async def test_duplicate_command_is_not_dispatched_twice(stack):
    _, authority, router, catalog = stack
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential-a"})
    first = await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="tc-1", command_id="stable")
    second = await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="tc-1", command_id="stable")
    assert first == second
    assert router.dispatch_count == 1


@pytest.mark.asyncio
async def test_same_tool_call_reuses_idempotency_domain_and_new_intent_gets_new_id(stack):
    _, authority, router, catalog = stack
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential-a"})
    await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="same-call")
    first_key = next(iter(router.commands))
    await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="same-call")
    await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="new-intent")
    assert router.dispatch_count == 2
    assert len(router.commands) == 2
    assert first_key in router.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", ["payload", "tool"])
async def test_command_id_collision_requires_identical_domain(stack, collision):
    context, authority, router, catalog = stack
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential-a"})
    await core.call_tool(handle, "say", {"message": "first"}, tool_call_id="tc", command_id="collision")
    original = router.commands[("game-a", "nonce-a", "collision")]
    altered_context = context
    if collision == "payload":
        altered = replace(original, validated_payload={"message": "different"})
    elif collision == "tool":
        altered = replace(original, tool_call_id="other-tool-call", capability_id="other-capability")
    receipt = await router.dispatch(altered_context, altered)
    assert receipt.state is DispatchState.REJECTED
    assert router.dispatch_count == 1


@pytest.mark.asyncio
async def test_nonce_change_creates_new_idempotency_domain_and_isolates_results(stack):
    context, authority, router, catalog = stack
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential-a"})
    await core.call_tool(handle, "say", {"message": "first"}, tool_call_id="tc", command_id="same-id")
    original = router.commands[("game-a", "nonce-a", "same-id")]
    new_context = replace(context, game_instance_nonce="nonce-new")
    router.fence = replace(router.fence, game_instance_nonce="nonce-new")
    altered = replace(original, game_instance_nonce="nonce-new", message_id="message-new")
    receipt = await router.dispatch(new_context, altered)
    assert receipt.state is DispatchState.ACCEPTED
    assert router.dispatch_count == 2
    old_key = ("game-a", "nonce-a", "same-id")
    new_key = ("game-a", "nonce-new", "same-id")
    assert old_key in router.commands and new_key in router.commands
    router.results[new_key] = GameCommandResult("same-id", CommandState.FAILED)
    assert router.results[old_key].state is CommandState.SUCCEEDED
    assert (await router.await_result(new_context, "same-id", 0)).state is CommandState.FAILED


@pytest.mark.asyncio
async def test_unknown_is_preserved_and_not_replayed(stack):
    _, authority, router, catalog = stack
    audit = MemoryAuditSink()
    core = McpCore(authority, router, catalog, audit)
    handle = await authority.establish({"credential": "credential-a"})
    router.results[("game-a", "nonce-a", "uncertain")] = GameCommandResult("uncertain", CommandState.UNKNOWN)
    result = await core.call_tool(handle, "say", {"message": "hi"}, tool_call_id="tc-u", command_id="uncertain")
    assert result.state is CommandState.UNKNOWN
    assert router.dispatch_count == 1
    assert [(event.event, event.detail.get("state")) for event in audit.events] == [
        ("command.dispatched", None), ("command.result", "UNKNOWN")
    ]


@pytest.mark.asyncio
async def test_only_succeeded_terminal_reports_authoritative_runtime_activity(stack):
    context, authority, router, catalog = stack

    class Activity:
        def __init__(self): self.calls = []
        async def report_succeeded(self, runtime_ref, generation):
            self.calls.append((runtime_ref, generation))

    activity = Activity()
    core = McpCore(authority, router, catalog, activity=activity)
    handle = await authority.establish({"credential": "credential-a"})
    await core.list_tools(handle)
    assert activity.calls == []

    succeeded = await core.call_tool(
        handle, "say", {"message": "ok"}, tool_call_id="success", command_id="success"
    )
    assert succeeded.state is CommandState.SUCCEEDED
    assert activity.calls == [(context.runtime_ref, context.generation)]

    for command_id, state in (("failed", CommandState.FAILED), ("unknown", CommandState.UNKNOWN)):
        router.results[("game-a", "nonce-a", command_id)] = GameCommandResult(command_id, state)
        result = await core.call_tool(
            handle, "say", {"message": state.value}, tool_call_id=command_id,
            command_id=command_id,
        )
        assert result.state is state
    assert activity.calls == [(context.runtime_ref, context.generation)]


@pytest.mark.asyncio
async def test_activity_report_failure_does_not_change_successful_tool_result(stack):
    _, authority, router, catalog = stack

    class BrokenActivity:
        async def report_succeeded(self, runtime_ref, generation):
            raise RuntimeError("activity backend unavailable")

    core = McpCore(authority, router, catalog, activity=BrokenActivity())
    handle = await authority.establish({"credential": "credential-a"})
    result = await core.call_tool(
        handle, "say", {"message": "still succeeds"}, tool_call_id="success"
    )
    assert result.state is CommandState.SUCCEEDED


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_final_state_is_preserved(stack):
    context, _, router, _ = stack
    router.results[("game-a", "nonce-a", "running")] = GameCommandResult("running", CommandState.RUNNING)
    first = await router.cancel(context, "running")
    second = await router.cancel(context, "running")
    assert first.accepted is True
    assert second.accepted is False
    assert second.state is CommandState.CANCELLED


@pytest.mark.asyncio
async def test_final_and_cancel_lifecycle_are_audited_without_payload_or_secret(stack):
    _, authority, router, catalog = stack
    audit = MemoryAuditSink()
    core = McpCore(authority, router, catalog, audit)
    handle = await authority.establish({"credential": "credential-a"})
    secret_payload = "payload-secret-must-not-enter-audit"
    await core.call_tool(
        handle, "say", {"message": secret_payload}, tool_call_id="tc-final", command_id="final"
    )
    router.results[("game-a", "nonce-a", "running")] = GameCommandResult("running", CommandState.RUNNING)
    await core.cancel_command(handle, "say", "running", tool_call_id="tc-cancel")
    rendered = repr(audit.events)
    assert secret_payload not in rendered
    assert "credential-a" not in rendered
    assert [(event.event, event.detail.get("state")) for event in audit.events] == [
        ("command.dispatched", None), ("command.result", "SUCCEEDED"),
        ("command.cancel", "CANCELLED"),
    ]


@pytest.mark.asyncio
async def test_list_and_call_use_same_authorized_surface(catalog):
    context = make_context(tools={"observe"})
    authority = FakeContextAuthority({"credential": context})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "digest-a"))
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential"})
    assert [tool.name for tool in await core.list_tools(handle)] == ["observe"]
    with pytest.raises(McpError) as caught:
        await core.call_tool(handle, "say", {"message": "no"}, tool_call_id="tc")
    assert caught.value.code == "capability_denied"


@pytest.mark.asyncio
async def test_list_pins_catalog_and_negotiated_capability_digest(catalog):
    context = make_context(tools={"observe"}, digest="snapshot-a")
    authority = FakeContextAuthority({"credential": context})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "snapshot-a"))
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential"})
    await core.list_tools(handle)
    authority.replace_grant(handle, replace(context, capability_digest="snapshot-b"))
    with pytest.raises(McpError, match="new MCP session") as caught:
        await core.call_tool(handle, "observe", {}, tool_call_id="changed")
    assert caught.value.code == "conflict"


@pytest.mark.asyncio
async def test_direct_call_atomically_pins_surface_before_dispatch(catalog):
    context = make_context(digest="snapshot-a")
    authority = FakeContextAuthority({"credential": context})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "snapshot-a"))
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential"})
    await core.call_tool(handle, "observe", {}, tool_call_id="first")
    authority.replace_grant(handle, replace(context, capability_digest="snapshot-b"))
    with pytest.raises(McpError, match="new MCP session") as caught:
        await core.call_tool(handle, "observe", {}, tool_call_id="second")
    assert caught.value.code == "conflict"
    assert router.dispatch_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["allowed_tools", "scope"])
async def test_surface_pin_requires_authorize_success_allowed_tool_and_scope(catalog, mismatch):
    context = make_context(tools={"observe", "say"})
    if mismatch == "allowed_tools":
        context = replace(context, allowed_tools=frozenset({"observe"}))
    else:
        context = replace(context, scopes=frozenset({"game:read"}))

    class PermissiveAuthority(FakeContextAuthority):
        async def authorize(self, handle, tool_name, capability):
            status = await self.revalidate(handle)
            assert status.valid
            return self._handles[handle.value]

    authority = PermissiveAuthority({"credential": context})
    router = FakeGameSessionRouter(RouteFence("game-a", "nonce-a", 1, 1, 1, "digest-a"))
    core = McpCore(authority, router, catalog)
    handle = await authority.establish({"credential": "credential"})
    assert [tool.name for tool in await core.list_tools(handle)] == ["observe"]
    with pytest.raises(McpError) as caught:
        await core.call_tool(handle, "say", {"message": "no"}, tool_call_id="denied")
    assert caught.value.code == "capability_denied"
    assert router.dispatch_count == 0


def test_stable_errors_always_declare_retryability():
    expected = {"invalid_arguments", "capability_denied", "context_expired", "binding_unavailable",
                "route_unavailable", "command_timeout", "command_cancelled", "conflict",
                "upstream_protocol_error", "internal_error"}
    assert set(ERRORS) == expected
    assert all(isinstance(item.retryable, bool) for item in ERRORS.values())


@pytest.mark.asyncio
async def test_invalid_credential_error_is_redacted(catalog):
    authority = FakeContextAuthority({})
    secret = "super-secret-never-log"
    with pytest.raises(McpError) as caught:
        await authority.establish({"credential": secret})
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.as_dict())


@pytest.mark.asyncio
async def test_adapter_private_error_is_mapped_and_redacted(stack):
    _, authority, router, catalog = stack
    handle = await authority.establish({"credential": "credential-a"})
    secret = "private-upstream-secret"

    async def fail(_context):
        raise RuntimeError(secret)

    router.resolve = fail
    with pytest.raises(McpError) as caught:
        await McpCore(authority, router, catalog).call_tool(
            handle, "observe", {}, tool_call_id="tc-private"
        )
    assert caught.value.code == "internal_error"
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.as_dict())
