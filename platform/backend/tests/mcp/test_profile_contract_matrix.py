from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayRouter, InMemoryGameSessionFacts
from modules.mcp.context import EphemeralSecret, HostedClaimsAuthority, LocalBootAuthority, LocalBootMaterial
from modules.mcp.contracts.models import (
    GAME_COMMAND_VERSION, CancelResult, CommandState, DispatchReceipt, DispatchState,
    GameCommand, GameCommandResult, RouteHealth, SideEffect,
)
from modules.mcp.core.errors import McpError
from modules.mcp.core.catalog import ToolCatalog, ToolDefinition
from modules.mcp.core.service import McpCore
from modules.mcp.fakes import FakeContextAuthority, FakeGameSessionRouter
from modules.mcp.fakes.router import RouteFence
from modules.mcp.routers import LocalModRouter, LocalRouteFence
from modules.runtime_manager.authority import AuthorityMaterial, InMemoryAuthorityMaterialIssuer
from modules.runtime_manager.domain import BindingState, RuntimeBinding, RuntimeProductSpec, SessionProof
from modules.runtime_manager.product_repository import InMemoryBindingRepository, InMemoryRuntimeProductRepository
from modules.runtime_manager.router_validation import RuntimeBindingProjectionValidator
from modules.runtime_manager.services import BindingService, RuntimeService
from tests.mcp.conftest import make_context


class AsyncBindingReader:
    def __init__(self, binding): self.binding = binding
    async def get(self, binding_id): return self.binding if self.binding.binding_id == binding_id else None


class AsyncBindingRepositoryReader:
    def __init__(self, repository): self.repository = repository
    async def get(self, binding_id): return self.repository.get(binding_id)


class Verifier:
    def __init__(self, material): self.material = material; self.revoked = False
    async def verify(self, credential):
        if credential != "hosted-secret": raise ValueError("bad credential")
        return self.material
    async def is_revoked(self, material): return self.revoked


async def authority_stack(profile):
    if profile == "fake":
        context = make_context(tools={"say"})
        return FakeContextAuthority({"secret": context}), {"credential": "secret"}, None
    if profile == "local":
        material = LocalBootMaterial.create(
            capability_digest="digest-a", tool_capabilities={"say": "say"},
            scopes=frozenset({"game:write"}), now=100.0,
        )
        secret = material.secret.reveal()
        return LocalBootAuthority(material, negotiated_capability_digest=lambda: "digest-a", clock=lambda: 101.0), {"credential": secret}, None
    binding = RuntimeBinding(
        "binding-a", "runtime-a", 1, "user-a", "agentee-a", 2, 1,
        BindingState.ACTIVE, "game-a", "nonce-a", "digest-a", "resume-a",
    )
    material = AuthorityMaterial(
        "credential-ref", "starcoplay-runtime-manager", "starcoplay-mcp", "runtime-a",
        "jti-a", "runtime-a", 1, "binding-a", 2, 1, "game-a", "nonce-a",
        "digest-a", "user-a", "agentee-a", ("game:write",), ("say",),
        datetime.fromtimestamp(200, timezone.utc),
    )
    verifier = Verifier(material)
    return HostedClaimsAuthority(verifier, AsyncBindingReader(binding), clock=lambda: 101.0), {"credential": "hosted-secret"}, verifier


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["fake", "local", "hosted"])
async def test_authority_contract_is_shared_across_profiles(profile):
    authority, metadata, verifier = await authority_stack(profile)
    handle = await authority.establish(metadata)
    context = await authority.authorize(handle, "say", "say")
    assert context.allowed_tools == frozenset({"say"})
    assert context.capability_digest == "digest-a"
    with pytest.raises(McpError):
        await authority.authorize(handle, "say", "noncanonical-wire-name")
    assert (await authority.revalidate(handle)).valid
    if verifier is not None:
        verifier.revoked = True
        assert not (await authority.revalidate(handle)).valid
    await authority.close(handle)
    assert not (await authority.revalidate(handle)).valid


@pytest.mark.asyncio
async def test_binding_issuance_to_hosted_authority_to_core_mutating_call_e2e():
    runtimes, bindings = InMemoryRuntimeProductRepository(), InMemoryBindingRepository()
    issuer = InMemoryAuthorityMaterialIssuer()
    RuntimeService(runtimes).ensure_runtime(RuntimeProductSpec(
        "runtime-a", 1, "activation-a", "user-a", "agentee-a", "fake",
        "config", "persona", allowed_capabilities=("say",),
    ))
    service = BindingService(runtimes, bindings, issuer)
    pending = service.ensure_binding("runtime-a", 1, "game-a", "ensure-a")
    active = service.activate(pending.binding_id, 1, SessionProof(
        "game-a", "nonce-a", "user-a", "digest-a", "resume-a",
    ), "activate-a")
    material = issuer.material[active.binding_id]
    assert material.scopes == ("game:read", "game:write")
    assert material.allowed_tools == ("say",)

    class IssuedVerifier:
        async def verify(self, credential):
            if credential != material.credential_ref: raise ValueError("invalid")
            return material
        async def is_revoked(self, candidate): return not issuer.accepts(candidate)
    authority = HostedClaimsAuthority(IssuedVerifier(), AsyncBindingReader(active))
    handle = await authority.establish({"credential": material.credential_ref})
    catalog = ToolCatalog([ToolDefinition(
        "say", "say", "canonical.v1", "game:write", SideEffect.MUTATING,
        {"type": "object", "properties": {"message": {"type": "string"}},
         "required": ["message"], "additionalProperties": False},
    )])
    router = FakeGameSessionRouter(RouteFence(
        "game-a", "nonce-a", 1, 2, 1, "digest-a", "user-a", "runtime-a", active.binding_id,
    ))
    core = McpCore(authority, router, catalog)
    assert [tool.name for tool in await core.list_tools(handle)] == ["say"]
    result = await core.call_tool(
        handle, "say", {"message": "hello"}, tool_call_id="tool-call-a", command_id="command-a",
    )
    assert result.state is CommandState.SUCCEEDED
    emitted = router.commands[("game-a", "nonce-a", "command-a")]
    assert emitted.capability_id == "say" and emitted.capability_version == "canonical.v1"
    assert emitted.lease_epoch == active.lease_epoch


class RouteAdapter:
    def __init__(self): self.commands = {}; self.results = {}; self.bad_correlation = False
    @property
    def connected(self): return True
    async def dispatch(self, command):
        existing = self.commands.get(command.command_id)
        if existing is not None:
            same = (existing.validated_payload == command.validated_payload
                    and existing.capability_id == command.capability_id
                    and existing.idempotency_key == command.idempotency_key)
            return DispatchReceipt(command.command_id, DispatchState.DUPLICATE if same else DispatchState.REJECTED)
        self.commands[command.command_id] = command
        return DispatchReceipt(command.command_id, DispatchState.ACCEPTED)
    async def result(self, command_id, deadline):
        if self.bad_correlation: return GameCommandResult("wrong-command", CommandState.SUCCEEDED)
        return self.results.get(command_id, GameCommandResult(command_id, CommandState.UNKNOWN))
    async def status(self, command_id): return self.results.get(command_id, GameCommandResult(command_id, CommandState.UNKNOWN)).state
    async def cancel(self, command_id):
        self.results[command_id] = GameCommandResult(command_id, CommandState.CANCELLED)
        return CancelResult(command_id, True, CommandState.CANCELLED)
    async def health(self): return RouteHealth(True)
    async def await_result(self, command_id, deadline): return await self.result(command_id, deadline)
    async def get_status(self, command_id): return await self.status(command_id)


class Registry:
    def __init__(self, route): self.route = route
    async def resolve_authenticated(self, game_session_ref): return self.route if game_session_ref == "game-a" else None


def router_stack(profile):
    context = replace(
        make_context(principal="user-a", tools={"say"}), profile=profile,
        runtime_ref="runtime-a", binding_ref="binding-a", binding_version=2,
    )
    route = RouteAdapter()
    if profile == "fake":
        router = FakeGameSessionRouter(RouteFence(
            "game-a", "nonce-a", 1, 2, 1, "digest-a", "user-a", "runtime-a", "binding-a"
        ))
        return context, router, route, {"router": router}
    if profile == "local_direct":
        fence = LocalRouteFence(
            "handle-a", "user-a", "runtime-a", "game-a", "nonce-a", 1,
            "binding-a", 2, 1, "digest-a",
        )
        router = LocalModRouter(
            fence, route, boot_secret=EphemeralSecret("boot-secret"),
            presented_secret="boot-secret", clock=lambda: 100.0,
        )
        return context, router, route, {"router": router}
    bindings = InMemoryBindingRepository()
    binding = RuntimeBinding(
        "binding-a", "runtime-a", 1, "user-a", "agentee-a", 2, 1,
        BindingState.ACTIVE, "game-a", "nonce-a", "digest-a", "resume-a",
    )
    bindings.records[binding.binding_id] = binding
    bindings.by_runtime[(binding.runtime_id, binding.generation)] = binding.binding_id
    facts = InMemoryGameSessionFacts()
    facts.snapshots["game-a"] = GameSessionSnapshot(
        "game-a", "nonce-a", "user-a", "connection-a", 1, "digest-a", True,
    )
    return context, GatewayRouter(
        Registry(route), facts,
        RuntimeBindingProjectionValidator(AsyncBindingRepositoryReader(bindings)),
    ), route, {
        "bindings": bindings, "binding": binding,
    }


def command(context, command_id="command-a"):
    return GameCommand(
        GAME_COMMAND_VERSION, "message-a", 100.0, command_id, command_id, "tool-call-a",
        "say", "canonical.v1", "digest-a", SideEffect.MUTATING, {"message": "hi"},
        120.0, "handle-a", context.game_instance_nonce, context.lease_epoch,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["fake", "local_direct", "hosted"])
async def test_router_contract_is_shared_across_profiles(profile):
    context, router, route, control = router_stack(profile)
    assert (await router.resolve(context)).available
    first = await router.dispatch(context, command(context))
    second = await router.dispatch(context, command(context))
    assert first.command_id == second.command_id == "command-a"
    assert first.state is DispatchState.ACCEPTED and second.state is DispatchState.DUPLICATE
    collision = await router.dispatch(
        context, replace(command(context), validated_payload={"message": "different"})
    )
    assert collision.state is DispatchState.REJECTED
    if profile == "fake":
        router.results[(context.game_session_ref, context.game_instance_nonce, "command-a")] = GameCommandResult(
            "command-a", CommandState.UNKNOWN
        )
    assert (await router.await_result(context, "command-a", 120)).state is CommandState.UNKNOWN
    assert await router.get_status(context, "command-a") is CommandState.UNKNOWN
    assert (await router.cancel(context, "command-a")).command_id == "command-a"
    assert (await router.health(context)).healthy
    with pytest.raises(McpError):
        stale = replace(context, lease_epoch=99)
        await router.dispatch(stale, command(stale))
    with pytest.raises(McpError):
        foreign = replace(context, principal_id="other-user")
        await router.dispatch(foreign, command(foreign))


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["fake", "local_direct", "hosted"])
@pytest.mark.parametrize("field", [
    "generation", "binding_version", "lease_epoch", "game_instance_nonce",
    "capability_digest", "principal_id", "binding_state",
])
async def test_every_router_operation_revalidates_each_authority_dimension(profile, field):
    context, router, route, control = router_stack(profile)
    if field == "binding_state":
        if profile == "fake":
            control["router"].fence = replace(control["router"].fence, binding_version=99)
        elif profile == "local_direct":
            control["router"].invalidate_boot()
        else:
            binding = control["binding"]
            control["bindings"].records[binding.binding_id] = replace(binding, status=BindingState.SUSPENDED)
        stale = context
    else:
        replacements = {
            "generation": context.generation + 1,
            "binding_version": context.binding_version + 1,
            "lease_epoch": context.lease_epoch + 1,
            "game_instance_nonce": "stale-nonce",
            "capability_digest": "stale-digest",
            "principal_id": "other-user",
        }
        stale = replace(context, **{field: replacements[field]})
    operations = (
        lambda: router.resolve(stale),
        lambda: router.dispatch(stale, command(stale)),
        lambda: router.await_result(stale, "command-a", 120),
        lambda: router.cancel(stale, "command-a"),
        lambda: router.get_status(stale, "command-a"),
        lambda: router.health(stale),
    )
    for operation in operations:
        with pytest.raises(McpError):
            await operation()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["fake", "local_direct", "hosted"])
async def test_result_response_correlation_fails_closed(profile):
    context, router, route, control = router_stack(profile)
    if profile == "fake":
        router.results[(context.game_session_ref, context.game_instance_nonce, "command-a")] = GameCommandResult(
            "wrong-command", CommandState.SUCCEEDED
        )
    else:
        route.bad_correlation = True
    with pytest.raises(McpError, match="correlation"):
        await router.await_result(context, "command-a", 120)
