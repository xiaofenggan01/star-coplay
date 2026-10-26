from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from modules.gateway.runtime_contracts import (
    GameSessionSnapshot,
    GatewayEvent,
    GatewayEventType,
    InMemoryGameSessionFacts,
)
from modules.runtime_manager.authority import InMemoryAuthorityMaterialIssuer
from modules.runtime_manager.domain import (
    AlreadyStopping,
    BindingState,
    Conflict,
    DesiredState,
    RuntimeProductSpec,
    RuntimeState,
    SessionProof,
)
from modules.runtime_manager.gateway_projection import (
    GatewaySessionProjection,
    InMemoryGatewayProjectionRepository,
)
from modules.runtime_manager.lifecycle import RuntimeLifecycleCoordinator
from modules.runtime_manager.product_repository import (
    InMemoryBindingRepository,
    InMemoryRuntimeProductRepository,
)
from modules.runtime_manager.services import BindingService, RuntimeService


def spec(**changes):
    base = RuntimeProductSpec(
        runtime_id="runtime-1",
        generation=1,
        activation_key="activation-1",
        user_id="user-1",
        agentee_id="agentee-1",
        adapter_kind="fake",
        adapter_config_ref="adapter/default",
        persona_artifact_ref="persona/rev-1",
    )
    return replace(base, **changes)


def product_stack():
    runtimes = InMemoryRuntimeProductRepository()
    bindings = InMemoryBindingRepository()
    authority = InMemoryAuthorityMaterialIssuer()
    runtime_service = RuntimeService(runtimes)
    binding_service = BindingService(runtimes, bindings, authority)
    return runtimes, bindings, authority, runtime_service, binding_service


def proof(**changes):
    base = SessionProof(
        game_session_id="game-1",
        game_instance_nonce="instance-1",
        owner_user_id="user-1",
        capability_digest="digest-1",
        resume_identity="resume-1",
    )
    return replace(base, **changes)


def test_ensure_runtime_is_activation_idempotent_and_single_active_generation():
    runtimes, _, _, service, _ = product_stack()
    first = service.ensure_runtime(spec())
    assert service.ensure_runtime(spec()) is first
    # runtime_id/generation are server identities, not part of stable activation intent.
    assert service.ensure_runtime(spec(runtime_id="caller-invented", generation=99)) is first
    with pytest.raises(Conflict):
        service.ensure_runtime(spec(runtime_id="other", generation=2, adapter_config_ref="other-config"))
    assert len(runtimes.records) == 1


def test_stopped_activation_can_advance_to_a_new_generation():
    runtimes, _, _, service, _ = product_stack()
    created = service.ensure_runtime(spec())
    draining = service.set_desired_state(
        created.runtime_id, 1, DesiredState.STOPPED, "stop-1", created.status_version
    )
    stopping = service.transition_actual(created.runtime_id, 1, RuntimeState.STOPPING, draining.status_version)
    service.transition_actual(created.runtime_id, 1, RuntimeState.STOPPED, stopping.status_version)
    advanced = service.ensure_runtime(spec(generation=2))
    assert advanced.generation == 2
    assert runtimes.find_activation("user-1", "agentee-1", "activation-1") == advanced
    assert runtimes.get_generation("runtime-1", 1).actual_state is RuntimeState.STOPPED
    assert len(runtimes.records) == 2


def test_runtime_operation_id_and_status_cas():
    _, _, _, service, _ = product_stack()
    created = service.ensure_runtime(spec())
    stopped = service.set_desired_state(
        created.runtime_id, 1, DesiredState.STOPPED, "operation-1", created.status_version
    )
    assert stopped.actual_state is RuntimeState.DRAINING
    assert stopped.status_version == 2
    assert service.set_desired_state(created.runtime_id, 1, DesiredState.STOPPED, "operation-1", 1) == stopped
    # A fresh service proves operation idempotency is repository-persistent.
    restarted_service = RuntimeService(service.repository)
    assert restarted_service.set_desired_state(created.runtime_id, 1, DesiredState.STOPPED, "operation-1", 1) == stopped
    with pytest.raises(Conflict, match="different payload"):
        restarted_service.set_desired_state(created.runtime_id, 1, DesiredState.RUNNING, "operation-1", 1)
    with pytest.raises(Conflict):
        service.set_desired_state(created.runtime_id, 1, DesiredState.RUNNING, "operation-2", 1)
    with pytest.raises(AlreadyStopping) as error:
        service.set_desired_state(created.runtime_id, 1, DesiredState.RUNNING, "reverse-stop", stopped.status_version)
    assert error.value.code == "already_stopping"


def test_binding_version_matrix_and_old_authority_is_fenced():
    _, _, authority, runtime_service, bindings = product_stack()
    runtime_service.ensure_runtime(spec())
    pending = bindings.ensure_binding("runtime-1", 1, "game-1", "ensure-1")
    assert (pending.status, pending.binding_version, pending.lease_epoch) == (BindingState.PENDING, 1, 0)

    active = bindings.activate(pending.binding_id, 1, proof(), "activate-1")
    old_material = authority.material[active.binding_id]
    assert (active.binding_version, active.lease_epoch) == (2, 1)
    assert (
        old_material.runtime_id,
        old_material.generation,
        old_material.game_session_id,
        old_material.game_instance_nonce,
        old_material.capability_digest,
        old_material.user_id,
        old_material.agentee_id,
    ) == ("runtime-1", 1, "game-1", "instance-1", "digest-1", "user-1", "agentee-1")
    assert old_material.scopes and old_material.expires_at > datetime.now(timezone.utc)
    assert (
        old_material.iss,
        old_material.aud,
        old_material.sub,
        old_material.jti,
        old_material.allowed_tools,
    ) == (
        "starcoplay-runtime-manager",
        "starcoplay-mcp",
        "runtime-1",
        f"{active.binding_id}:2:1",
        (),
    )

    suspended = bindings.suspend(active.binding_id, 2, "disconnect", "suspend-1")
    assert (suspended.binding_version, suspended.lease_epoch) == (3, 1)
    assert not authority.accepts(old_material)
    assert runtime_service.repository.get("runtime-1").actual_state is RuntimeState.PROVISIONING

    resumed = bindings.activate(suspended.binding_id, 3, proof(), "activate-2")
    assert (resumed.binding_version, resumed.lease_epoch) == (4, 1)

    changed = bindings.update_capabilities(resumed.binding_id, 4, "digest-2", "cap-1")
    assert (changed.binding_version, changed.lease_epoch, changed.capability_digest) == (5, 1, "digest-2")

    revoked = bindings.revoke(changed.binding_id, 5, "stop", "revoke-1")
    assert (revoked.status, revoked.binding_version, revoked.lease_epoch) == (BindingState.REVOKED, 6, 2)
    assert bindings.revoke(changed.binding_id, 5, "stop", "revoke-1") == revoked


def test_binding_cas_failure_never_revokes_authority_and_operation_payload_conflicts():
    _, _, authority, runtime_service, bindings = product_stack()
    runtime_service.ensure_runtime(spec())
    pending = bindings.ensure_binding("runtime-1", 1, "game-1", "ensure-1")
    active = bindings.activate(pending.binding_id, 1, proof(), "activate-1")
    material = authority.material[active.binding_id]
    with pytest.raises(Conflict):
        bindings.suspend(active.binding_id, 1, "disconnect", "suspend-stale")
    assert authority.accepts(material)
    assert not bindings.repository.pending_revocations()
    with pytest.raises(Conflict, match="different payload"):
        bindings.activate(pending.binding_id, 1, proof(capability_digest="other"), "activate-1")


def test_revocation_outbox_survives_issuer_failure_and_retry_flushes_it():
    class FlakyAuthority(InMemoryAuthorityMaterialIssuer):
        def __init__(self):
            super().__init__()
            self.fail = True

        def revoke(self, *args):
            if self.fail:
                self.fail = False
                raise RuntimeError("temporary issuer failure")
            return super().revoke(*args)

    runtimes = InMemoryRuntimeProductRepository()
    binding_repo = InMemoryBindingRepository()
    authority = FlakyAuthority()
    runtime_service = RuntimeService(runtimes)
    bindings = BindingService(runtimes, binding_repo, authority)
    runtime_service.ensure_runtime(spec())
    pending = bindings.ensure_binding("runtime-1", 1, "game-1", "ensure")
    active = bindings.activate(pending.binding_id, 1, proof(), "activate")
    with pytest.raises(RuntimeError, match="temporary"):
        bindings.suspend(active.binding_id, 2, "disconnect", "suspend")
    assert binding_repo.get(active.binding_id).status is BindingState.SUSPENDED
    assert binding_repo.pending_revocations()
    assert bindings.suspend(active.binding_id, 2, "disconnect", "suspend").status is BindingState.SUSPENDED
    assert not binding_repo.pending_revocations()
    assert active.binding_id not in authority.material


def test_issuance_outbox_survives_issue_failure_and_rebuilt_service_flushes_cached_operation():
    class FlakyIssueAuthority(InMemoryAuthorityMaterialIssuer):
        def __init__(self):
            super().__init__()
            self.fail = True

        def issue(self, *args, **kwargs):
            if self.fail:
                self.fail = False
                raise RuntimeError("temporary issue failure")
            return super().issue(*args, **kwargs)

    runtimes = InMemoryRuntimeProductRepository()
    binding_repo = InMemoryBindingRepository()
    authority = FlakyIssueAuthority()
    runtime_service = RuntimeService(runtimes)
    runtime_service.ensure_runtime(spec(allowed_capabilities=("query_state", "say")))
    service = BindingService(runtimes, binding_repo, authority)
    pending = service.ensure_binding("runtime-1", 1, "game-1", "ensure")
    with pytest.raises(RuntimeError, match="temporary issue"):
        service.activate(pending.binding_id, 1, proof(), "activate")
    active = binding_repo.get(pending.binding_id)
    assert active.status is BindingState.ACTIVE
    assert binding_repo.pending_issuances()
    assert active.binding_id not in authority.material

    rebuilt = BindingService(runtimes, binding_repo, authority)
    assert rebuilt.activate(pending.binding_id, 1, proof(), "activate") == active
    assert not binding_repo.pending_issuances()
    material = authority.material[active.binding_id]
    assert material.allowed_tools == ("query_state", "say")
    assert material.jti == f"{active.binding_id}:2:1"


def test_active_capability_change_uses_issuance_outbox():
    class FailNextIssue(InMemoryAuthorityMaterialIssuer):
        fail = False

        def issue(self, *args, **kwargs):
            if self.fail:
                self.fail = False
                raise RuntimeError("issue failed")
            return super().issue(*args, **kwargs)

    runtimes = InMemoryRuntimeProductRepository()
    binding_repo = InMemoryBindingRepository()
    authority = FailNextIssue()
    runtime_service = RuntimeService(runtimes)
    runtime_service.ensure_runtime(spec(allowed_capabilities=("say",)))
    service = BindingService(runtimes, binding_repo, authority)
    pending = service.ensure_binding("runtime-1", 1, "game-1", "ensure")
    active = service.activate(pending.binding_id, 1, proof(), "activate")
    authority.fail = True
    with pytest.raises(RuntimeError, match="issue failed"):
        service.update_capabilities(active.binding_id, 2, "digest-2", "cap-change")
    changed = binding_repo.get(active.binding_id)
    assert changed.binding_version == 3
    assert binding_repo.pending_issuances()
    rebuilt = BindingService(runtimes, binding_repo, authority)
    assert rebuilt.update_capabilities(active.binding_id, 2, "digest-2", "cap-change") == changed
    assert authority.material[active.binding_id].jti == f"{active.binding_id}:3:1"


def test_failed_generation_cannot_be_replaced_until_stopped():
    _, _, _, service, _ = product_stack()
    created = service.ensure_runtime(spec())
    failed = service.transition_actual("runtime-1", 1, RuntimeState.FAILED, created.status_version)
    with pytest.raises(Conflict, match="must reach STOPPED"):
        service.ensure_runtime(spec(generation=2))
    with pytest.raises(Conflict, match="must reach STOPPED"):
        service.ensure_runtime(spec(runtime_id="replacement", generation=1))
    service.transition_actual("runtime-1", 1, RuntimeState.STOPPED, failed.status_version)
    assert service.ensure_runtime(spec(generation=2)).generation == 2


def test_stopping_activation_cannot_be_replaced_and_stopped_generation_is_contiguous():
    _, _, _, service, _ = product_stack()
    created = service.ensure_runtime(spec())
    draining = service.set_desired_state(
        created.runtime_id, 1, DesiredState.STOPPED, "stop", created.status_version
    )
    with pytest.raises(Conflict, match="stopping activation"):
        service.ensure_runtime(spec(runtime_id="replacement", generation=1))
    stopping = service.transition_actual("runtime-1", 1, RuntimeState.STOPPING, draining.status_version)
    stopped = service.transition_actual("runtime-1", 1, RuntimeState.STOPPED, stopping.status_version)
    with pytest.raises(Conflict, match="same runtime_id"):
        service.ensure_runtime(spec(runtime_id="replacement", generation=1))
    with pytest.raises(Conflict, match="exactly one"):
        service.ensure_runtime(spec(generation=3))
    assert service.ensure_runtime(spec(generation=2)).generation == 2


def test_resume_identity_is_required_and_must_match():
    _, _, _, runtime_service, bindings = product_stack()
    runtime_service.ensure_runtime(spec())
    pending = bindings.ensure_binding("runtime-1", 1, "game-1", "ensure-1")
    active = bindings.activate(pending.binding_id, 1, proof(), "activate-1")
    suspended = bindings.suspend(active.binding_id, 2, "disconnect", "suspend-1")
    with pytest.raises(Conflict, match="resume identity mismatch"):
        bindings.activate(suspended.binding_id, 3, proof(resume_identity="attacker"), "resume-bad")


def test_binding_rejects_wrong_owner():
    _, _, _, runtime_service, bindings = product_stack()
    runtime_service.ensure_runtime(spec())
    pending = bindings.ensure_binding("runtime-1", 1, "game-1", "ensure-1")
    with pytest.raises(Conflict):
        bindings.activate(pending.binding_id, 1, proof(owner_user_id="other-user"), "activate-1")


def snapshot(version: int, *, available: bool = True, digest: str = "digest-1"):
    return GameSessionSnapshot(
        game_session_id="game-1",
        game_instance_nonce="instance-1",
        owner_user_id="user-1",
        connection_id="connection-1" if available else None,
        session_version=version,
        capability_digest=digest,
        available=available,
        resumable=not available,
    )


@pytest.mark.asyncio
async def test_gateway_event_dedup_stale_and_gap_snapshot_convergence():
    facts = InMemoryGameSessionFacts()
    repository = InMemoryGatewayProjectionRepository()
    projection = GatewaySessionProjection(facts, repository)
    first = GatewayEvent(GatewayEventType.AVAILABLE, snapshot(1), message_id="message-1")
    await facts.publish(first)
    assert (await projection.consume(first)).snapshot.session_version == 1
    assert (await projection.consume(first)).duplicate

    third = GatewayEvent(GatewayEventType.RESUMED, snapshot(3, digest="digest-3"), message_id="message-3")
    await facts.publish(third)
    result = await projection.consume(third)
    assert result.converged_from_snapshot
    assert result.snapshot.session_version == 3

    stale = GatewayEvent(GatewayEventType.SUSPENDED, snapshot(2, available=False), message_id="message-2")
    assert (await projection.consume(stale)).stale
    # Publishing facts does not create Runtime or Binding state.
    restarted = GatewaySessionProjection(facts, repository)
    assert (await restarted.consume(third)).duplicate


@pytest.mark.asyncio
async def test_gateway_gap_failure_does_not_consume_message_and_can_retry():
    facts = InMemoryGameSessionFacts()
    repository = InMemoryGatewayProjectionRepository()
    projection = GatewaySessionProjection(facts, repository)
    gap = GatewayEvent(GatewayEventType.RESUMED, snapshot(3), message_id="gap-message")
    with pytest.raises(ValueError, match="snapshot_unavailable"):
        await projection.consume(gap)
    assert not await repository.has_message("gap-message")
    await facts.publish(gap)
    assert (await projection.consume(gap)).converged_from_snapshot
    assert await repository.has_message("gap-message")


@pytest.mark.asyncio
async def test_gateway_rejects_mismatched_aggregate_without_consuming_message():
    facts = InMemoryGameSessionFacts()
    repository = InMemoryGatewayProjectionRepository()
    event = GatewayEvent(
        GatewayEventType.AVAILABLE,
        snapshot(1),
        aggregate_id="other-session",
        aggregate_version=1,
        message_id="bad-envelope",
    )
    with pytest.raises(ValueError, match="snapshot_identity"):
        await GatewaySessionProjection(facts, repository).consume(event)
    assert not await repository.has_message("bad-envelope")


def _advance_to_idle(service, runtime_id="runtime-1"):
    current = service.repository.get(runtime_id)
    current = service.transition_actual(runtime_id, current.generation, RuntimeState.STARTING, current.status_version)
    current = service.transition_actual(runtime_id, current.generation, RuntimeState.READY_UNBOUND, current.status_version, ready=True)
    return service.transition_actual(runtime_id, current.generation, RuntimeState.IDLE, current.status_version, ready=True)


class FakeProcessLifecycle:
    def __init__(self, authority=None, binding_id=None, alive=True):
        self.authority = authority
        self.binding_id = binding_id
        self.alive = alive
        self.calls = []

    async def drain(self, runtime_id, generation, timeout_seconds):
        self.calls.append(("drain", self._revoked()))

    async def checkpoint(self, runtime_id, generation, deadline_seconds):
        self.calls.append(("checkpoint", self._revoked()))

    async def stop(self, runtime_id, generation, reason, grace_seconds):
        self.calls.append(("stop", self._revoked()))
        self.alive = False

    async def cleanup(self, runtime_id, generation):
        self.calls.append(("cleanup", self._revoked()))

    async def is_alive(self, runtime_id, generation):
        return self.alive

    def _revoked(self):
        return self.binding_id not in self.authority.material if self.authority else None


@pytest.mark.asyncio
async def test_idle_ttl_revokes_authority_before_bounded_lifecycle():
    runtimes, binding_repo, authority, runtime_service, binding_service = product_stack()
    created = runtime_service.ensure_runtime(spec())
    active_runtime = _advance_to_idle(runtime_service)
    runtimes.records[(created.runtime_id, 1)] = replace(
        active_runtime,
        last_effective_activity_at=datetime.now(timezone.utc) - timedelta(seconds=3601),
    )
    pending = binding_service.ensure_binding("runtime-1", 1, "game-1", "ensure-1")
    binding_service.activate(pending.binding_id, 1, proof(), "activate-1")
    process = FakeProcessLifecycle(authority, pending.binding_id)
    lifecycle = RuntimeLifecycleCoordinator(
        runtimes, binding_repo, runtime_service, binding_service, process
    )
    assert await lifecycle.enforce_idle_ttl("runtime-1")
    assert process.calls == [("drain", True), ("checkpoint", True), ("stop", True), ("cleanup", True)]
    stopped = runtimes.get("runtime-1")
    assert (stopped.desired_state, stopped.actual_state, stopped.ready) == (
        DesiredState.STOPPED,
        RuntimeState.STOPPED,
        False,
    )


@pytest.mark.asyncio
async def test_reconcile_persists_missing_process_failure():
    runtimes, binding_repo, _, runtime_service, binding_service = product_stack()
    runtime_service.ensure_runtime(spec())

    process = FakeProcessLifecycle(alive=False)
    lifecycle = RuntimeLifecycleCoordinator(runtimes, binding_repo, runtime_service, binding_service, process)
    reconciled = await lifecycle.reconcile_runtime("runtime-1")
    assert reconciled.actual_state is RuntimeState.FAILED
    assert runtimes.get("runtime-1").actual_state is RuntimeState.FAILED
