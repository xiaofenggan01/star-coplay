from dataclasses import replace

import pytest

from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayRouter, InMemoryGameSessionFacts
from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION, CancelResult, CommandState, DispatchReceipt, DispatchState,
    ExecutionContext, GameCommand, GameCommandResult, RouteHealth, SideEffect,
)
from modules.mcp.core.errors import McpError
from modules.runtime_manager.authority import InMemoryAuthorityMaterialIssuer
from modules.runtime_manager.domain import Conflict, RuntimeProductSpec, SessionProof
from modules.runtime_manager.product_repository import InMemoryBindingRepository, InMemoryRuntimeProductRepository
from modules.runtime_manager.router_validation import RuntimeBindingProjectionValidator
from modules.runtime_manager.services import BindingService, RuntimeService


class Session:
    async def dispatch(self, command):
        return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)
    async def await_result(self, command_id, deadline):
        return GameCommandResult(command_id, CommandState.SUCCEEDED)
    async def cancel(self, command_id): return CancelResult(command_id, True, CommandState.CANCELLED)
    async def get_status(self, command_id): return CommandState.RUNNING
    async def health(self): return RouteHealth(True)


class Registry:
    def __init__(self, authenticated=True):
        self.authenticated = authenticated

    async def resolve_authenticated(self, game_session_id):
        return Session() if self.authenticated and game_session_id == "game-1" else None


class AsyncBindingReader:
    def __init__(self, repository): self.repository = repository
    async def get(self, binding_id): return self.repository.get(binding_id)


def router_stack(authenticated=True):
    runtimes = InMemoryRuntimeProductRepository()
    bindings = InMemoryBindingRepository()
    runtime_service = RuntimeService(runtimes)
    runtime_service.ensure_runtime(RuntimeProductSpec(
        "runtime-1", 1, "activation-1", "user-1", "agentee-1", "fake", "config", "persona"
    ))
    binding_service = BindingService(runtimes, bindings, InMemoryAuthorityMaterialIssuer())
    pending = binding_service.ensure_binding("runtime-1", 1, "game-1", "ensure")
    active = binding_service.activate(pending.binding_id, 1, SessionProof(
        "game-1", "instance-1", "user-1", "digest-1", "resume-1"
    ), "activate")
    context = ExecutionContext(
        "hosted", active.user_id, active.runtime_id, active.generation, active.binding_id,
        active.binding_version, active.game_session_id or "", active.game_instance_nonce or "",
        active.lease_epoch, frozenset({"game:write"}), frozenset({"say"}),
        active.capability_digest or "", 1.0, 100.0,
    )
    facts = InMemoryGameSessionFacts()
    facts.snapshots["game-1"] = GameSessionSnapshot(
        "game-1", "instance-1", "user-1", "connection-1", 1, "digest-1", True
    )
    router = GatewayRouter(
        Registry(authenticated), facts,
        RuntimeBindingProjectionValidator(AsyncBindingReader(bindings)),
    )
    return router, context


def command(context):
    return GameCommand(
        GAME_COMMAND_VERSION, "message-1", 1.0, "command-1", "command-1", "tool-call-1",
        "say", "canonical.v1", context.capability_digest, SideEffect.MUTATING,
        {"text": "hello"}, 10.0, "handle-1", context.game_instance_nonce,
        context.lease_epoch,
    )


@pytest.mark.asyncio
async def test_gateway_router_revalidates_context_and_authenticated_session():
    router, context = router_stack()
    assert (await router.dispatch(context, command(context))).state is DispatchState.ACCEPTED
    assert not hasattr(router, "ensure_runtime")
    assert not hasattr(router, "ensure_binding")

    with pytest.raises(McpError, match="Binding"):
        stale = replace(context, lease_epoch=context.lease_epoch + 1)
        await router.dispatch(stale, command(stale))
    router.snapshots.snapshots["game-1"] = replace(
        router.snapshots.snapshots["game-1"], capability_digest="changed"
    )
    with pytest.raises(McpError, match="GameSession"):
        await router.dispatch(context, command(context))

    unavailable, context = router_stack(False)
    with pytest.raises(McpError, match="authenticated game route"):
        await unavailable.dispatch(context, command(context))


@pytest.mark.asyncio
async def test_gateway_operational_records_keep_server_log_and_execution_ids(caplog):
    router, context = router_stack()
    context = replace(context, trace_context={"log_id": "log_" + "a" * 32})
    with caplog.at_level("INFO", logger="platform_ws.gateway.hosted_router"):
        await router.dispatch(context, command(context))
        await router.await_result(context, "command-1", 10.0)
        await router.cancel(context, "command-1")

    records = {
        record.event: record for record in caplog.records
        if getattr(record, "event", "").startswith("gateway.command.")
    }
    assert set(records) == {
        "gateway.command.dispatch", "gateway.command.result", "gateway.command.cancel",
    }
    assert all(record.log_id == "log_" + "a" * 32 for record in records.values())
    assert records["gateway.command.dispatch"].tool_call_id == "tool-call-1"
    assert all(record.command_id == "command-1" for record in records.values())
