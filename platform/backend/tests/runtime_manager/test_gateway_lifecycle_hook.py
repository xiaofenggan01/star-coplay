from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from common.auth.token_authenticator import AuthResult
from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayEvent, GatewayEventType
from modules.gateway.server import Gateway, GatewayConfig
from modules.runtime_manager.coordinator import (
    DurableGatewayEventPublisher, GatewayEventLifecycleHook, HostedGatewayEventConsumer,
)
from modules.runtime_manager.domain import (
    BindingState, Conflict, RuntimeBinding, RuntimeProductRecord, RuntimeProductSpec,
    RuntimeState,
)


class Authenticator:
    async def authenticate(self, token): return AuthResult(True, "user-1")


class WebSocket:
    def __init__(self): self.sent = []
    async def recv(self): return '{"type":"hello","platform_token":"valid"}'
    async def send(self, payload): self.sent.append(payload)
    async def close(self): return None


class Source:
    def __init__(self): self.transitions = []
    async def event_for(self, connection_id, owner_user_id, transition):
        self.transitions.append(transition)
        event_type = {
            "available": GatewayEventType.AVAILABLE,
            "resumed": GatewayEventType.RESUMED,
            "suspended": GatewayEventType.SUSPENDED,
        }[transition]
        return GatewayEvent(event_type, GameSessionSnapshot(
            "formal-game-session-1", "formal-nonce-1", owner_user_id,
            None if transition == "suspended" else connection_id,
            len(self.transitions), "formal-digest-1", transition != "suspended", True,
            "formal-resume-identity-1",
        ))


class Projection:
    async def consume(self, event):
        return type("Result", (), {"duplicate": False, "stale": False})()


class Bindings:
    def __init__(self):
        self.binding = RuntimeBinding(
            "binding-1", "runtime-1", 1, "user-1", "agentee-1", 2, 1,
            BindingState.ACTIVE, "formal-game-session-1", "formal-nonce-1",
            "formal-digest-1", "formal-resume-identity-1",
        )
    async def find_for_game_session(self, game_session_id): return self.binding
    async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
        self.binding = binding
        return binding


@pytest.mark.asyncio
async def test_authenticated_gateway_lifecycle_invokes_real_hook_and_disconnect_suspends():
    source, bindings = Source(), Bindings()
    hook = GatewayEventLifecycleHook(source, HostedGatewayEventConsumer(Projection(), bindings))
    gateway = Gateway(
        GatewayConfig(resume_token_ttl_s=0.01), AsyncMock(),
        authenticator=Authenticator(), game_session_event_hook=hook,
    )
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(WebSocket())
    assert source.transitions == ["available", "suspended"]
    assert bindings.binding.status is BindingState.SUSPENDED
    assert bindings.binding.binding_version == 3


@pytest.mark.asyncio
async def test_missing_formal_game_session_facts_fail_closed_before_hello_ok():
    class MissingSource:
        async def event_for(self, connection_id, owner_user_id, transition):
            raise RuntimeError("gateway_event_fact_unavailable")
    hook = GatewayEventLifecycleHook(MissingSource(), HostedGatewayEventConsumer(Projection(), Bindings()))
    ws = WebSocket()
    gateway = Gateway(GatewayConfig(), AsyncMock(), authenticator=Authenticator(), game_session_event_hook=hook)
    gateway._run_session = AsyncMock(return_value=None)
    await gateway._handle_connection(ws)
    assert not any("hello_ok" in payload for payload in ws.sent)
    gateway._run_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_feature_disabled_gateway_has_zero_runtime_hook_behavior():
    gateway = Gateway(GatewayConfig(resume_token_ttl_s=0.01), AsyncMock(), authenticator=Authenticator())
    gateway._run_session = AsyncMock(return_value=None)
    ws = WebSocket()
    await gateway._handle_connection(ws)
    assert any("hello_ok" in payload for payload in ws.sent)
    hello_ok = next(json.loads(payload) for payload in ws.sent if "hello_ok" in payload)
    assert hello_ok["accepted_agent_control_revision"] == 0
    gateway._run_session.assert_awaited_once()


class ProductBindings:
    def __init__(self):
        self.binding = RuntimeBinding(
            "binding-product", "runtime-product", 1, "user-1", "agentee-1"
        )

    async def find_for_gateway_event(self, game_session_id, owner_user_id):
        if self.binding.user_id != owner_user_id:
            return None
        if self.binding.game_session_id == game_session_id:
            return self.binding
        if self.binding.status is BindingState.PENDING and self.binding.game_session_id is None:
            return self.binding
        return None

    async def find_for_manager_projection(self, game_session_id, owner_user_id):
        if (
            self.binding.user_id == owner_user_id
            and self.binding.game_session_id == game_session_id
        ):
            return self.binding
        return None

    async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
        assert self.binding.binding_version == expected
        self.binding = binding
        return binding


class ProductRuntimes:
    def __init__(self, state=RuntimeState.READY_UNBOUND, generation=1, conflicts=0):
        self.record = RuntimeProductRecord(
            RuntimeProductSpec(
                "runtime-product", generation, "activation-product", "user-1", "agentee-1",
                "fake", "adapter/default", "persona/ref", allowed_capabilities=("say",),
            ),
            actual_state=state, status_version=1, ready=True,
        )
        self.conflicts = conflicts

    async def get_generation(self, runtime_id, generation):
        if (runtime_id, generation) == (self.record.runtime_id, self.record.generation):
            return self.record
        return None

    async def compare_and_swap(self, record, expected):
        if self.conflicts:
            self.conflicts -= 1
            raise Conflict("runtime status_version mismatch")
        assert self.record.status_version == expected
        self.record = replace(record, status_version=expected + 1)
        return self.record


class ProductProjection:
    def __init__(self): self.duplicate = False; self.stale = False; self.snapshot = None
    async def consume(self, event):
        if not self.duplicate and not self.stale:
            self.snapshot = event.snapshot
        return type("Result", (), {
            "duplicate": self.duplicate, "stale": self.stale, "snapshot": self.snapshot,
        })()


def product_event(event_type, version):
    return GatewayEvent(event_type, GameSessionSnapshot(
        "game-product", "nonce-product", "user-1",
        None if event_type in {GatewayEventType.SUSPENDED, GatewayEventType.CLOSED} else "connection-product",
        version, "digest-product", event_type not in {GatewayEventType.SUSPENDED, GatewayEventType.CLOSED},
        event_type is not GatewayEventType.CLOSED, "resume-product",
    ))


@pytest.mark.asyncio
async def test_binding_events_cas_runtime_between_running_bound_and_idle():
    projection, bindings, runtimes = ProductProjection(), ProductBindings(), ProductRuntimes()
    consumer = HostedGatewayEventConsumer(projection, bindings, runtimes)
    expected = (
        (GatewayEventType.AVAILABLE, RuntimeState.RUNNING_BOUND),
        (GatewayEventType.SUSPENDED, RuntimeState.IDLE),
        (GatewayEventType.RESUMED, RuntimeState.RUNNING_BOUND),
        (GatewayEventType.CLOSED, RuntimeState.IDLE),
    )
    for version, (event_type, state) in enumerate(expected, start=1):
        await consumer.consume(product_event(event_type, version))
        assert runtimes.record.actual_state is state
        version_after_transition = runtimes.record.status_version
        projection.duplicate = True
        await consumer.consume(product_event(event_type, version))
        projection.duplicate = False
        assert runtimes.record.status_version == version_after_transition
    assert runtimes.record.status_version == 5
    assert runtimes.record.desired_state.value == "running"
    assert bindings.binding.status is BindingState.REVOKED


@pytest.mark.asyncio
async def test_duplicate_stale_and_terminal_runtime_do_not_increment_or_resurrect():
    projection, bindings, runtimes = ProductProjection(), ProductBindings(), ProductRuntimes()
    consumer = HostedGatewayEventConsumer(projection, bindings, runtimes)
    available = product_event(GatewayEventType.AVAILABLE, 1)
    await consumer.consume(available)
    versions = (bindings.binding.binding_version, runtimes.record.status_version)
    projection.duplicate = True
    await consumer.consume(available)
    assert (bindings.binding.binding_version, runtimes.record.status_version) == versions

    projection.duplicate = False
    runtimes.record = replace(runtimes.record, actual_state=RuntimeState.DRAINING)
    await consumer.consume(product_event(GatewayEventType.AVAILABLE, 2))
    assert runtimes.record.actual_state is RuntimeState.DRAINING
    assert runtimes.record.status_version == versions[1]


@pytest.mark.asyncio
async def test_runtime_projection_retries_cas_conflict_and_generation_mismatch_fails_closed():
    projection, bindings = ProductProjection(), ProductBindings()
    runtimes = ProductRuntimes(conflicts=1)
    await HostedGatewayEventConsumer(projection, bindings, runtimes).consume(
        product_event(GatewayEventType.AVAILABLE, 1)
    )
    assert runtimes.record.actual_state is RuntimeState.RUNNING_BOUND
    assert runtimes.record.status_version == 2

    mismatch_bindings = ProductBindings()
    mismatch_bindings.binding = replace(mismatch_bindings.binding, generation=2)
    mismatch_runtime = ProductRuntimes(generation=1)
    await HostedGatewayEventConsumer(projection, mismatch_bindings, mismatch_runtime).consume(
        product_event(GatewayEventType.AVAILABLE, 2)
    )
    assert mismatch_runtime.record.actual_state is RuntimeState.READY_UNBOUND
    assert mismatch_runtime.record.status_version == 1


@pytest.mark.asyncio
async def test_duplicate_repairs_exhausted_runtime_cas_without_rewriting_binding():
    projection, bindings = ProductProjection(), ProductBindings()
    runtimes = ProductRuntimes(conflicts=3)
    consumer = HostedGatewayEventConsumer(projection, bindings, runtimes)
    event = product_event(GatewayEventType.AVAILABLE, 1)
    with pytest.raises(Conflict, match="CAS exhausted"):
        await consumer.consume(event)
    assert bindings.binding.status is BindingState.ACTIVE
    assert bindings.binding.binding_version == 2
    assert runtimes.record.actual_state is RuntimeState.READY_UNBOUND

    projection.duplicate = True
    await consumer.consume(event)
    assert bindings.binding.binding_version == 2
    assert runtimes.record.actual_state is RuntimeState.RUNNING_BOUND
    assert runtimes.record.status_version == 2


@pytest.mark.asyncio
async def test_durable_publish_succeeds_when_best_effort_eager_application_fails():
    class Outbox:
        def __init__(self): self.published = []; self.released = []; self.acknowledged = []
        async def publish(self, message_id, payload): self.published.append((message_id, payload))
        async def update_stage(self, message_id, stage): raise AssertionError("consumer fails first")
        async def release_claim(self, message_id, error): self.released.append((message_id, error))
        async def acknowledge(self, message_id): self.acknowledged.append(message_id)

    class Consumer:
        async def consume(self, event, stage_recorder):
            raise RuntimeError("injected eager failure")

    outbox = Outbox()
    publisher = DurableGatewayEventPublisher(outbox, Consumer())
    event = product_event(GatewayEventType.AVAILABLE, 1)
    await publisher.publish(event)
    assert [item[0] for item in outbox.published] == [event.message_id]
    assert outbox.released == [(event.message_id, "injected eager failure")]
    assert outbox.acknowledged == []


@pytest.mark.asyncio
async def test_gap_recovery_applies_latest_projection_snapshot_not_old_event_type():
    latest = GameSessionSnapshot(
        "game-product", "nonce-product", "user-1", None, 3,
        "digest-product", False, False, "resume-product",
    )

    class GapProjection:
        async def consume(self, event):
            return type("Result", (), {
                "duplicate": False, "stale": True, "snapshot": latest,
            })()

    bindings, runtimes = ProductBindings(), ProductRuntimes()
    bindings.binding = replace(
        bindings.binding, status=BindingState.ACTIVE,
        game_session_id="game-product", game_instance_nonce="nonce-product",
        capability_digest="digest-product", resume_identity="resume-product",
        binding_version=2, lease_epoch=1,
    )
    consumer = HostedGatewayEventConsumer(GapProjection(), bindings, runtimes)
    old_event = product_event(GatewayEventType.AVAILABLE, 1)
    await consumer.consume(old_event)
    assert bindings.binding.status is BindingState.REVOKED
    assert bindings.binding.binding_version == 3
    assert runtimes.record.actual_state is RuntimeState.IDLE
    versions = (bindings.binding.binding_version, bindings.binding.lease_epoch,
                runtimes.record.status_version)
    await consumer.consume(old_event)
    assert (bindings.binding.binding_version, bindings.binding.lease_epoch,
            runtimes.record.status_version) == versions
