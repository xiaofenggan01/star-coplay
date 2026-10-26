from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from modules.gateway.runtime_contracts import GameSessionSnapshot, GatewayEvent, GatewayEventType
from modules.runtime_manager.async_application import AsyncBindingService
from modules.runtime_manager.coordinator import HostedGatewayEventConsumer, RuntimeManagerCoordinator
from modules.runtime_manager.domain import (
    BindingState, Conflict, NotFound, RuntimeBinding, RuntimeProductRecord, RuntimeProductSpec,
    RuntimeState, StaleGeneration,
)


def runtime_record() -> RuntimeProductRecord:
    return RuntimeProductRecord(
        RuntimeProductSpec(
            "runtime-1", 1, "activation-1", "user-1", "agentee-1", "fake",
            "adapter/default", "persona/rev-1", allowed_capabilities=("say",),
        ),
        actual_state=RuntimeState.READY_UNBOUND,
        ready=True,
    )


class Runtimes:
    def __init__(self, record=None): self.record = record or runtime_record()
    async def get(self, runtime_id):
        return self.record if self.record.runtime_id == runtime_id else None
    async def get_generation(self, runtime_id, generation):
        if self.record.runtime_id == runtime_id and self.record.generation == generation:
            return self.record
        return None

    async def compare_and_swap(self, record, expected):
        assert self.record.status_version == expected
        self.record = replace(record, status_version=expected + 1)
        return self.record


class Bindings:
    def __init__(self):
        self.binding = None
        self.operations = {}
        self.transitions = []

    async def get(self, binding_id):
        return self.binding if self.binding and self.binding.binding_id == binding_id else None

    async def find_for_runtime(self, runtime_id, generation):
        if self.binding and (self.binding.runtime_id, self.binding.generation) == (runtime_id, generation):
            return self.binding
        return None

    async def find_for_gateway_event(self, game_session_id, owner_user_id):
        if self.binding is None or self.binding.user_id != owner_user_id:
            return None
        if self.binding.game_session_id == game_session_id:
            return self.binding
        if self.binding.status is BindingState.PENDING and self.binding.game_session_id is None:
            return self.binding
        return None

    async def insert_with_operation(self, binding, operation):
        key = (operation.owner_ref, operation.operation_id)
        cached = self.operations.get(key)
        if cached is not None:
            if cached.fingerprint != operation.fingerprint:
                raise Conflict("operation_id reused with different payload")
            return cached.result
        if self.binding is None:
            self.binding = binding
        elif (
            self.binding.user_id != binding.user_id
            or self.binding.agentee_id != binding.agentee_id
            or (
                binding.game_session_id is not None
                and self.binding.game_session_id not in (None, binding.game_session_id)
            )
        ):
            raise Conflict("runtime already has a different Binding")
        self.operations[key] = replace(operation, result=self.binding)
        return self.binding

    async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
        assert self.binding.binding_version == expected
        self.binding = binding
        self.transitions.append((operation, revocation, issuance))
        return binding


@pytest.mark.asyncio
async def test_ensure_binding_creates_pending_and_replays_exact_operation():
    bindings = Bindings()
    service = AsyncBindingService(Runtimes(), bindings)
    first = await service.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "binding-op-1"
    )
    replay = await service.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "binding-op-1"
    )
    assert replay == first
    assert first.status is BindingState.PENDING
    assert first.game_session_id is None
    assert first.binding_version == 1 and first.lease_epoch == 0


@pytest.mark.asyncio
async def test_ensure_binding_rejects_operation_collision_owner_and_stale_generation():
    runtimes, bindings = Runtimes(), Bindings()
    service = AsyncBindingService(runtimes, bindings)
    await service.ensure_binding("runtime-1", 1, "user-1", "agentee-1", "same-op")
    with pytest.raises(Conflict, match="operation_id reused"):
        await service.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "same-op", "game-other"
        )
    with pytest.raises(NotFound):
        await service.ensure_binding("runtime-1", 1, "user-other", "agentee-1", "foreign")
    with pytest.raises(StaleGeneration):
        await service.ensure_binding("runtime-1", 2, "user-1", "agentee-1", "stale")


@pytest.mark.asyncio
async def test_ensure_binding_rejects_another_game_session():
    bindings = Bindings()
    service = AsyncBindingService(Runtimes(), bindings)
    pending = await service.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "first", "game-1"
    )
    bindings.binding = replace(
        pending, status=BindingState.ACTIVE, game_session_id="game-1",
        game_instance_nonce="nonce-1", capability_digest="digest-1",
        resume_identity="resume-1", lease_epoch=1,
    )
    with pytest.raises(Conflict, match="another game session"):
        await service.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "second", "game-2"
        )


@pytest.mark.asyncio
async def test_revoked_binding_is_terminal_new_generation_required():
    """REVOKED is terminal — never returns to PENDING. A new generation
    creates a new binding_id naturally (no existing binding for gen+1)."""
    runtime = replace(
        runtime_record(), actual_state=RuntimeState.PROVISIONING, ready=False,
    )
    runtimes, bindings = Runtimes(runtime), Bindings()
    revoked_at = datetime.now(timezone.utc)
    bindings.binding = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=3, lease_epoch=2, status=BindingState.REVOKED,
        game_session_id="old-game", game_instance_nonce="old-nonce",
        capability_digest="old-digest", resume_identity="old-resume",
        bound_at=revoked_at, revoked_at=revoked_at,
    )

    service = AsyncBindingService(runtimes, bindings)
    # Calling ensure_binding on same generation with a REVOKED binding
    # must fail — REVOKED is terminal.
    with pytest.raises(Conflict, match="terminal binding"):
        await service.ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "revoked-terminal", "new-game"
        )

    # A new generation (gen 2) naturally gets a new binding because
    # find_for_runtime is scoped by generation — no REVOKED binding exists
    # for gen 2.
    runtimes.record = replace(runtime, spec=replace(runtime.spec, generation=2))
    generation_two_bindings = Bindings()
    service = AsyncBindingService(runtimes, generation_two_bindings)
    fresh = await service.ensure_binding(
        "runtime-1", 2, "user-1", "agentee-1", "gen-2-binding", "new-game"
    )
    assert fresh.status is BindingState.PENDING
    assert fresh.binding_id != "binding-1"
    assert fresh.generation == 2
    assert fresh.binding_version == 1 and fresh.lease_epoch == 0


@pytest.mark.asyncio
async def test_revoked_binding_always_terminal_regardless_of_runtime_state():
    """REVOKED is terminal unconditionally — even when runtime is PROVISIONING
    with a fresh game_session_id."""
    runtimes, bindings = Runtimes(), Bindings()
    bindings.binding = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=2, lease_epoch=1, status=BindingState.REVOKED,
        game_session_id="old-game", revoked_at=datetime.now(timezone.utc),
    )
    with pytest.raises(Conflict, match="terminal binding"):
        await AsyncBindingService(runtimes, bindings).ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "not-provisioning", "new-game"
        )
    assert bindings.transitions == []


@pytest.mark.asyncio
async def test_suspended_binding_rejects_fresh_session_without_mutation():
    runtimes, bindings = Runtimes(), Bindings()
    suspended_at = datetime.now(timezone.utc)
    bindings.binding = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=6, lease_epoch=4, status=BindingState.SUSPENDED,
        game_session_id="old-game", game_instance_nonce="old-nonce",
        capability_digest="old-digest", resume_identity="old-resume",
        bound_at=suspended_at, suspended_at=suspended_at,
    )
    with pytest.raises(Conflict, match="another game session"):
        await AsyncBindingService(runtimes, bindings).ensure_binding(
            "runtime-1", 1, "user-1", "agentee-1", "fresh-session", "new-game",
        )
    assert bindings.binding.status is BindingState.SUSPENDED
    assert bindings.binding.binding_version == 6
    assert bindings.transitions == []


@pytest.mark.asyncio
async def test_exact_suspended_session_is_not_reset():
    runtimes, bindings = Runtimes(), Bindings()
    bindings.binding = RuntimeBinding(
        "binding-1", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=3, lease_epoch=2, status=BindingState.SUSPENDED,
        game_session_id="same-game", game_instance_nonce="same-nonce",
        capability_digest="same-digest", resume_identity="same-resume",
        suspended_at=datetime.now(timezone.utc),
    )
    same = await AsyncBindingService(runtimes, bindings).ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "same-session", "same-game",
    )
    assert same == bindings.binding
    assert same.status is BindingState.SUSPENDED
    assert same.binding_version == 3 and same.lease_epoch == 2
    assert bindings.transitions == []


@pytest.mark.asyncio
async def test_gateway_available_activates_manager_created_binding_and_queues_authority():
    runtimes, bindings = Runtimes(), Bindings()
    pending = await AsyncBindingService(runtimes, bindings).ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "binding-op"
    )

    class Projection:
        async def consume(self, event):
            return type("Result", (), {"duplicate": False, "stale": False})()

    snapshot = GameSessionSnapshot(
        "game-1", "nonce-1", "user-1", "connection-1", 1, "digest-1",
        True, True, "resume-1",
    )
    await HostedGatewayEventConsumer(Projection(), bindings, runtimes).consume(
        GatewayEvent(
            GatewayEventType.AVAILABLE, snapshot, message_id="gateway-message-1",
            occurred_at=datetime.now(timezone.utc),
        )
    )
    active = bindings.binding
    assert active.binding_id == pending.binding_id
    assert active.status is BindingState.ACTIVE
    assert active.game_session_id == "game-1"
    assert active.binding_version == 2 and active.lease_epoch == 1
    assert runtimes.record.actual_state is RuntimeState.RUNNING_BOUND
    assert runtimes.record.status_version == 2
    operation, revocation, issuance = bindings.transitions[-1]
    assert revocation is None
    assert issuance.binding == active
    assert issuance.allowed_tools == ("say",)


@pytest.mark.asyncio
async def test_runtime_manager_product_entry_owns_binding_creation(tmp_path):
    runtimes, bindings = Runtimes(), Bindings()

    class UnusedPersona:
        pass

    class UnusedAuthority:
        pass

    manager = RuntimeManagerCoordinator(
        runtimes, UnusedPersona(), UnusedAuthority(), {}, tmp_path,
        binding_repository=bindings,
    )
    binding = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "product-binding-op"
    )
    assert binding == bindings.binding
    assert binding.status is BindingState.PENDING


@pytest.mark.asyncio
async def test_ensure_binding_converges_preexisting_online_projection_without_new_event(tmp_path):
    runtimes, bindings = Runtimes(), Bindings()
    snapshot = GameSessionSnapshot(
        "game-before-binding", "nonce-before-binding", "user-1", "connection-1",
        7, "digest-before-binding", True, True, "resume-before-binding",
    )

    class TrustedProjection:
        async def find_available_for_owner(self, owner_user_id):
            return snapshot if owner_user_id == "user-1" else None
        async def get(self, game_session_id):
            return snapshot if game_session_id == snapshot.game_session_id else None

    converger = HostedGatewayEventConsumer(None, bindings, runtimes)
    manager = RuntimeManagerCoordinator(
        runtimes, object(), object(), {}, tmp_path, binding_repository=bindings,
        gateway_projection_repository=TrustedProjection(),
        gateway_snapshot_converger=converger,
    )
    active = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "late-binding-op"
    )
    assert active.status is BindingState.ACTIVE
    assert active.game_session_id == snapshot.game_session_id
    assert active.game_instance_nonce == snapshot.game_instance_nonce
    assert active.capability_digest == snapshot.capability_digest
    assert runtimes.record.actual_state is RuntimeState.RUNNING_BOUND
    versions = (active.binding_version, active.lease_epoch, runtimes.record.status_version)
    replay = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "late-binding-op"
    )
    assert (replay.binding_version, replay.lease_epoch,
            runtimes.record.status_version) == versions
    assert len(bindings.transitions) == 1


@pytest.mark.asyncio
async def test_available_projection_before_provisioning_binding_enqueues_credential_issue(tmp_path):
    runtime = replace(runtime_record(), actual_state=RuntimeState.PROVISIONING, ready=False)
    runtimes, bindings = Runtimes(runtime), Bindings()
    snapshot = GameSessionSnapshot(
        "game-before-provisioning-binding", "nonce-before-binding", "user-1",
        "connection-before-binding", 1, "digest-before-binding", True, True,
        "resume-before-binding",
    )

    class TrustedProjection:
        async def find_available_for_owner(self, owner_user_id):
            return snapshot if owner_user_id == "user-1" else None
        async def get(self, game_session_id):
            return snapshot if game_session_id == snapshot.game_session_id else None

    manager = RuntimeManagerCoordinator(
        runtimes, object(), object(), {}, tmp_path, binding_repository=bindings,
        gateway_projection_repository=TrustedProjection(),
        gateway_snapshot_converger=HostedGatewayEventConsumer(None, bindings, runtimes),
    )
    active = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "late-provisioning-binding",
        game_session_id=snapshot.game_session_id,
    )

    assert active.status is BindingState.ACTIVE
    assert active.game_session_id == snapshot.game_session_id
    assert bindings.transitions[-1][2] is not None
    assert bindings.transitions[-1][2].intent_id == f"issue:{active.binding_id}:2"
    # Credential authority is established before adapter startup. Runtime
    # remains PROVISIONING until HostedRuntimeSessionActivator.activate calls ensure().
    assert runtimes.record.actual_state is RuntimeState.PROVISIONING


@pytest.mark.asyncio
async def test_ensure_binding_does_not_activate_without_unique_online_projection(tmp_path):
    runtimes, bindings = Runtimes(), Bindings()

    class NoTrustedOnlineProjection:
        async def find_available_for_owner(self, owner_user_id): return None
        async def get(self, game_session_id): return None

    manager = RuntimeManagerCoordinator(
        runtimes, object(), object(), {}, tmp_path, binding_repository=bindings,
        gateway_projection_repository=NoTrustedOnlineProjection(),
        gateway_snapshot_converger=HostedGatewayEventConsumer(None, bindings, runtimes),
    )
    pending = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "offline-binding-op"
    )
    assert pending.status is BindingState.PENDING
    assert runtimes.record.actual_state is RuntimeState.READY_UNBOUND
    assert bindings.transitions == []


@pytest.mark.asyncio
async def test_concurrent_newer_offline_fact_wins_over_ensure_binding_snapshot(tmp_path):
    runtimes = Runtimes()
    online = GameSessionSnapshot(
        "game-race", "nonce-race", "user-1", "connection-race", 1,
        "digest-race", True, True, "resume-race",
    )
    offline = replace(online, connection_id=None, session_version=2, available=False)

    class MutableProjection:
        snapshot = online
        async def find_available_for_owner(self, owner_user_id):
            return self.snapshot if self.snapshot.available else None
        async def get(self, game_session_id):
            return self.snapshot if game_session_id == self.snapshot.game_session_id else None

    projection = MutableProjection()

    class RacingBindings(Bindings):
        async def transition_with_operation(self, binding, expected, operation, revocation, issuance=None):
            if self.binding.status is BindingState.PENDING:
                self.binding = replace(
                    self.binding, status=BindingState.SUSPENDED,
                    game_session_id=offline.game_session_id,
                    game_instance_nonce=offline.game_instance_nonce,
                    capability_digest=offline.capability_digest,
                    resume_identity=offline.resume_identity,
                    binding_version=3, lease_epoch=1,
                )
                projection.snapshot = offline
                runtimes.record = replace(
                    runtimes.record, actual_state=RuntimeState.RUNNING_BOUND,
                    status_version=2,
                )
                raise Conflict("concurrent newer GatewayEvent")
            return await super().transition_with_operation(
                binding, expected, operation, revocation, issuance,
            )

    bindings = RacingBindings()
    converger = HostedGatewayEventConsumer(None, bindings, runtimes)
    manager = RuntimeManagerCoordinator(
        runtimes, object(), object(), {}, tmp_path, binding_repository=bindings,
        gateway_projection_repository=projection,
        gateway_snapshot_converger=converger,
    )
    result = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "race-binding-op"
    )
    assert result.status is BindingState.SUSPENDED
    assert result.binding_version == 3
    assert runtimes.record.actual_state is RuntimeState.IDLE


@pytest.mark.asyncio
async def test_ensure_replay_repairs_projection_closed_before_binding_transition(tmp_path):
    runtimes, bindings = Runtimes(), Bindings()
    bindings.binding = replace(
        bindings.binding or RuntimeBinding(
            "binding-closed-gap", "runtime-1", 1, "user-1", "agentee-1"
        ),
        status=BindingState.ACTIVE, game_session_id="game-closed-gap",
        game_instance_nonce="nonce-closed-gap", capability_digest="digest-closed-gap",
        resume_identity="resume-closed-gap", binding_version=2, lease_epoch=1,
    )
    runtimes.record = replace(
        runtimes.record, actual_state=RuntimeState.RUNNING_BOUND, status_version=2,
    )
    closed = GameSessionSnapshot(
        "game-closed-gap", "nonce-closed-gap", "user-1", None, 3,
        "digest-closed-gap", False, False, "resume-closed-gap",
    )

    class ClosedProjection:
        async def find_available_for_owner(self, owner_user_id): return None
        async def get(self, game_session_id):
            return closed if game_session_id == closed.game_session_id else None

    manager = RuntimeManagerCoordinator(
        runtimes, object(), object(), {}, tmp_path, binding_repository=bindings,
        gateway_projection_repository=ClosedProjection(),
        gateway_snapshot_converger=HostedGatewayEventConsumer(None, bindings, runtimes),
    )
    revoked = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "closed-gap-op"
    )
    assert revoked.status is BindingState.REVOKED
    assert runtimes.record.actual_state is RuntimeState.IDLE
    versions = (revoked.binding_version, revoked.lease_epoch, runtimes.record.status_version)
    replay = await manager.ensure_binding(
        "runtime-1", 1, "user-1", "agentee-1", "closed-gap-op"
    )
    assert (replay.binding_version, replay.lease_epoch,
            runtimes.record.status_version) == versions
