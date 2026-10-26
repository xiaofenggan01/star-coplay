from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.runtime_manager.domain import (
    BindingState, DesiredState, RuntimeBinding, RuntimeProductRecord, RuntimeState,
)
from modules.runtime_manager.session_activation import (
    DbDefaultAgenteeResolver, DefaultAgenteeAuthority,
    HOSTED_DEFAULT_RUNTIME_GRANTS, HOSTED_DEFAULT_SKILL_GRANTS,
    HostedRuntimeSessionActivator, stable_runtime_identity,
)


class Repository:
    def __init__(self): self.current = None
    async def get(self, runtime_id): return self.current


class Coordinator:
    def __init__(self, events):
        self.events, self.repository = events, Repository()
        self.authority = SimpleNamespace(is_credential_issued=self.is_credential_issued)
        self.issued = False

    async def provision(self, spec, **_kwargs):
        self.events.append("provision")
        record = RuntimeProductRecord(spec=spec)
        if self.repository.current is not None:
            record = replace(
                self.repository.current,
                spec=spec,
                desired_state=(
                    DesiredState.RUNNING
                    if spec.generation > self.repository.current.generation
                    else self.repository.current.desired_state
                ),
                actual_state=RuntimeState.PROVISIONING,
                ready=False,
                exit_reason=None,
            )
        self.repository.current = record
        return record

    async def ensure_binding(self, *args, **kwargs):
        self.events.append("binding")
        self.binding_calls = getattr(self, "binding_calls", [])
        self.binding_calls.append((args, kwargs))
    async def is_credential_issued(self, *_): return self.issued
    async def ensure(self, spec): self.events.append("ensure")


class Resolver:
    async def resolve(self, user_id):
        return DefaultAgenteeAuthority("agentee-1", "persona/agentee-1/rev-1")


class AuthorityWorker:
    def __init__(self, coordinator, events): self.coordinator, self.events = coordinator, events
    async def run_once(self):
        self.events.append("authority")
        self.coordinator.issued = True
        return 1


def session():
    return SimpleNamespace(
        instance=SimpleNamespace(user_id="user-1"), hosted_runtime_spec=None,
        hosted_game_session_id="game-1",
    )


def controlled_session(game_session_id="game-1", nonce="nonce-1"):
    value = session()
    value.hosted_game_session_id = game_session_id
    value.hosted_facts = SimpleNamespace(game_instance_nonce=nonce)
    return value


@pytest.mark.asyncio
async def test_activation_composes_existing_boundaries_in_order():
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
        credential_timeout_seconds=.1,
    )
    current = session()
    first = await activator.prepare(current)
    events.append("projection")  # Gateway owns this original lifecycle edge.
    await activator.activate(current)

    assert events == ["provision", "binding", "projection", "authority", "ensure"]
    assert first.adapter_kind == "claude"
    assert first.adapter_config_ref == "adapter/default"
    assert first.allowed_capabilities == HOSTED_DEFAULT_RUNTIME_GRANTS
    assert set(HOSTED_DEFAULT_SKILL_GRANTS).issubset(first.allowed_capabilities)
    assert first.idle_ttl_seconds == 3600


@pytest.mark.asyncio
async def test_activation_waits_for_bound_game_capabilities_after_runtime_ready():
    events = []
    coordinator = Coordinator(events)

    async def capability_probe(current, allowed_capabilities):
        events.append("capability_probe")
        assert current.hosted_game_session_id == "game-1"
        assert allowed_capabilities == HOSTED_DEFAULT_RUNTIME_GRANTS
        return events.count("capability_probe") >= 2

    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
        credential_timeout_seconds=.2,
        capability_probe=capability_probe,
        capability_timeout_seconds=.2,
    )
    current = session()
    await activator.prepare(current)

    await activator.activate(current)

    assert events == [
        "provision", "binding", "authority", "ensure",
        "capability_probe", "capability_probe",
    ]


@pytest.mark.asyncio
async def test_activation_fails_closed_when_game_capabilities_never_become_ready():
    events = []
    coordinator = Coordinator(events)

    async def unavailable(*_):
        return False

    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
        capability_probe=unavailable,
        capability_timeout_seconds=.001,
    )
    current = session()
    await activator.prepare(current)

    with pytest.raises(TimeoutError, match="hosted_runtime_capabilities_timeout"):
        await activator.activate(current)


@pytest.mark.asyncio
async def test_reconnect_reuses_stable_identity_and_active_generation():
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
    )
    first = await activator.prepare(session())
    second = await activator.prepare(session())
    assert (second.runtime_id, second.activation_key, second.generation) == (
        first.runtime_id, first.activation_key, 1,
    )
    assert stable_runtime_identity("user-1", "agentee-1") == (
        first.runtime_id, first.activation_key,
    )


@pytest.mark.asyncio
async def test_same_instance_resume_reuses_generation_without_stop():
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
    )
    first = await activator.prepare(controlled_session())
    coordinator.binding_service = SimpleNamespace(bindings=SimpleNamespace(
        find_for_runtime=AsyncMock(return_value=RuntimeBinding(
            "binding-1", first.runtime_id, 1, "user-1", "agentee-1",
            status=BindingState.ACTIVE, game_session_id="game-1",
            game_instance_nonce="nonce-1",
        ))
    ))
    coordinator.stop = AsyncMock()

    resumed = await activator.prepare(controlled_session())

    assert resumed.generation == 1
    coordinator.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_instance_replacement_requires_revoked_binding_then_stops_before_gen_plus_one():
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
    )
    first = await activator.prepare(controlled_session())
    old = RuntimeBinding(
        "binding-1", first.runtime_id, 1, "user-1", "agentee-1",
        status=BindingState.ACTIVE, game_session_id="game-1",
        game_instance_nonce="nonce-1",
    )
    lookup = AsyncMock(return_value=old)
    coordinator.binding_service = SimpleNamespace(
        bindings=SimpleNamespace(find_for_runtime=lookup)
    )
    coordinator.stop = AsyncMock(return_value=replace(
        coordinator.repository.current, desired_state=DesiredState.STOPPED,
        actual_state=RuntimeState.STOPPED,
    ))

    with pytest.raises(RuntimeError, match="replacement_binding_not_revoked"):
        await activator.prepare(controlled_session("game-2", "nonce-2"))
    coordinator.stop.assert_not_awaited()

    lookup.return_value = replace(old, status=BindingState.REVOKED)
    successor = await activator.prepare(controlled_session("game-2", "nonce-2"))

    assert successor.generation == 2
    coordinator.stop.assert_awaited_once()
    assert coordinator.stop.await_args.kwargs["require_durable_revocation"] is True


@pytest.mark.asyncio
async def test_explicit_close_stops_without_creating_successor_and_next_start_rolls_generation():
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
    )
    first = await activator.prepare(controlled_session())
    coordinator.binding_service = SimpleNamespace(bindings=SimpleNamespace(
        find_for_runtime=AsyncMock(return_value=RuntimeBinding(
            "binding-1", first.runtime_id, 1, "user-1", "agentee-1",
            status=BindingState.REVOKED, game_session_id="game-1",
            game_instance_nonce="nonce-1",
        ))
    ))

    async def stop(runtime_id, generation, operation_id, reason, **kwargs):
        events.append("stop")
        coordinator.repository.current = replace(
            coordinator.repository.current, desired_state=DesiredState.STOPPED,
            actual_state=RuntimeState.STOPPED,
        )
        return coordinator.repository.current

    coordinator.stop = AsyncMock(side_effect=stop)
    await activator.close("user-1", operation_id="control-close:7")

    assert coordinator.repository.current.generation == 1
    assert coordinator.repository.current.desired_state is DesiredState.STOPPED
    assert events[-1] == "stop"

    restarted = await activator.prepare(controlled_session("game-2", "nonce-2"))
    assert restarted.generation == 2
    assert coordinator.repository.current.desired_state is DesiredState.RUNNING


@pytest.mark.asyncio
async def test_stopped_activation_creates_generation_plus_one():
    """STOPPED is terminal for its generation; a new activation creates gen+1."""
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
    )
    first = await activator.prepare(session())
    coordinator.repository.current = replace(
        coordinator.repository.current, actual_state=RuntimeState.STOPPED
    )
    second = await activator.prepare(session())
    assert second.runtime_id == first.runtime_id
    assert second.activation_key == first.activation_key
    assert second.generation == first.generation + 1
    assert second.generation == 2


@pytest.mark.asyncio
async def test_failed_activation_rolls_over_to_generation_plus_one():
    """FAILED is terminal for its generation; a new activation creates gen+1
    after finalizing FAILED → STOPPED."""
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), AuthorityWorker(coordinator, events),
    )
    first = await activator.prepare(session())
    coordinator.repository.current = replace(
        coordinator.repository.current, actual_state=RuntimeState.FAILED
    )

    second = await activator.prepare(session())

    assert second.runtime_id == first.runtime_id
    assert second.activation_key == first.activation_key
    # Generation must increment — FAILED is terminal for its generation.
    assert second.generation == first.generation + 1
    assert second.generation == 2
    # Binding call uses the new generation.
    binding_args, binding_kwargs = coordinator.binding_calls[-1]
    assert binding_args[:4] == (
        second.runtime_id, second.generation, second.user_id, second.agentee_id,
    )
    assert binding_kwargs["game_session_id"] == "game-1"



@pytest.mark.asyncio
async def test_default_agentee_resolver_uses_product_authority_sql_shape():
    class Pool:
        async def fetchrow(self, query, user_id):
            assert "public.agentee_profiles" in query
            assert "public.agentee_personas" in query
            assert "p.is_default=true" in query
            assert user_id == "user-1"
            return {"agentee_id": "agentee-1", "persona_card": "persona",
                    "updated_at": SimpleNamespace(isoformat=lambda: "rev-1")}
    authority = await DbDefaultAgenteeResolver(Pool()).resolve("user-1")
    assert authority == DefaultAgenteeAuthority(
        "agentee-1", "persona/agentee-1/rev-1"
    )


@pytest.mark.asyncio
async def test_credential_wait_is_bounded_and_does_not_start_runtime():
    class NeverIssues:
        async def run_once(self): return 0
    events = []
    coordinator = Coordinator(events)
    activator = HostedRuntimeSessionActivator(
        coordinator, Resolver(), NeverIssues(), credential_timeout_seconds=.001,
    )
    current = session()
    await activator.prepare(current)
    with pytest.raises(TimeoutError, match="hosted_runtime_credential_timeout"):
        await activator.activate(current)
    assert "ensure" not in events
