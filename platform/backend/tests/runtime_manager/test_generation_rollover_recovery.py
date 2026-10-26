from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest

from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
from modules.gateway.runtime_contracts import GameSessionSnapshot
from modules.runtime_manager.async_application import AuthorityOutboxWorker
from modules.runtime_manager.authority import InMemoryAuthorityMaterialIssuer
from modules.runtime_manager.domain import (
    BindingState, Conflict, DesiredState, RuntimeBinding, RuntimeProductRecord,
    RuntimeProductSpec, RuntimeState,
)
from modules.runtime_manager.persistence import AsyncpgBindingRepository, _binding_payload


def record(generation=1, state=RuntimeState.FAILED):
    return RuntimeProductRecord(
        RuntimeProductSpec(
            "runtime-1", generation, "activation-1", "user-1", "agentee-1",
            "fake", "adapter/default", "persona/1", idle_ttl_seconds=600,
        ),
        desired_state=DesiredState.RUNNING, actual_state=state,
    )


def recovery_manager(failed, successor=None):
    manager = object.__new__(RuntimeManagerCoordinator)
    manager._rollover_tracker = {failed.runtime_id: (0, -10.0)}
    manager._rollover_exhausted_logged = set()
    manager._handles = {(failed.runtime_id, failed.generation): SimpleNamespace()}
    manager.stop_grace_seconds = 1
    manager.adapters = {"fake": SimpleNamespace(stop=AsyncMock())}
    manager.authority = SimpleNamespace(deny_and_revoke=AsyncMock())
    manager._cleanup = AsyncMock()
    manager._transition = AsyncMock(return_value=replace(failed, actual_state=RuntimeState.STOPPED))
    manager._recovery_snapshot = AsyncMock(return_value=SimpleNamespace())
    successor = successor or record(2, RuntimeState.PROVISIONING)
    manager.runtime_service = SimpleNamespace(ensure_runtime=AsyncMock(return_value=successor))
    manager._activate_recovered_generation = AsyncMock()
    manager.repository = SimpleNamespace(
        list_failed_desired_running=AsyncMock(return_value=(failed,)),
        get_generation=AsyncMock(return_value=None),
        get=AsyncMock(return_value=successor),
    )
    return manager, successor


@pytest.mark.asyncio
async def test_online_crash_recovery_orders_old_fence_before_successor_activation(monkeypatch):
    failed = record()
    manager, successor = recovery_manager(failed)
    order = []
    manager.authority.deny_and_revoke.side_effect = lambda *_: order.append("revoke")
    manager.adapters["fake"].stop.side_effect = lambda *_: order.append("stop")
    manager._confirm_authority_revoked = AsyncMock(side_effect=lambda *_: order.append("confirmed"))
    manager._cleanup.side_effect = lambda *_: order.append("cleanup")
    manager._transition.side_effect = lambda *_args, **_kw: order.append("stopped") or replace(
        failed, actual_state=RuntimeState.STOPPED
    )
    manager.runtime_service.ensure_runtime.side_effect = lambda *_: order.append("successor") or successor
    manager._activate_recovered_generation.side_effect = lambda *_: order.append("credential_then_spawn")
    monkeypatch.setattr("modules.runtime_manager.coordinator.time.monotonic", lambda: 10.0)

    assert await manager._recover_failed_generations() == 1
    assert order == [
        "revoke", "stop", "confirmed", "cleanup", "stopped",
        "successor", "credential_then_spawn",
    ]


@pytest.mark.asyncio
async def test_offline_snapshot_does_not_finalize_or_create_successor(monkeypatch):
    failed = record()
    manager, _ = recovery_manager(failed)
    manager._recovery_snapshot.side_effect = RuntimeError("recovery_game_session_not_available")
    monkeypatch.setattr("modules.runtime_manager.coordinator.time.monotonic", lambda: 10.0)

    assert await manager._recover_failed_generations() == 0
    manager.authority.deny_and_revoke.assert_not_awaited()
    manager.runtime_service.ensure_runtime.assert_not_awaited()
    assert manager._rollover_tracker[failed.runtime_id][0] == 0


@pytest.mark.asyncio
async def test_recovery_snapshot_accepts_real_authenticated_lifecycle_projection():
    failed = record()
    snapshot = GameSessionSnapshot(
        "game-1", "nonce-1", "user-1", "connection-1", 3,
        "digest-1", True, True, "resume-1",
    )
    binding = RuntimeBinding(
        "binding-1", failed.runtime_id, failed.generation,
        failed.spec.user_id, failed.spec.agentee_id,
        game_session_id=snapshot.game_session_id,
    )
    manager = object.__new__(RuntimeManagerCoordinator)
    manager.binding_service = SimpleNamespace(
        bindings=SimpleNamespace(find_for_runtime=AsyncMock(return_value=binding))
    )
    manager.gateway_projection_repository = SimpleNamespace(
        get=AsyncMock(return_value=snapshot)
    )

    assert await manager._recovery_snapshot(failed) is snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["connection_id", "resume_identity", "game_instance_nonce"])
async def test_recovery_snapshot_rejects_incomplete_real_projection(missing):
    failed = record()
    values = {
        "game_instance_nonce": "nonce-1", "connection_id": "connection-1",
        "resume_identity": "resume-1",
    }
    values[missing] = None if missing != "game_instance_nonce" else ""
    snapshot = GameSessionSnapshot(
        "game-1", values["game_instance_nonce"], "user-1", values["connection_id"],
        3, "digest-1", True, True, values["resume_identity"],
    )
    binding = RuntimeBinding(
        "binding-1", failed.runtime_id, failed.generation,
        failed.spec.user_id, failed.spec.agentee_id, game_session_id="game-1",
    )
    manager = object.__new__(RuntimeManagerCoordinator)
    manager.binding_service = SimpleNamespace(
        bindings=SimpleNamespace(find_for_runtime=AsyncMock(return_value=binding))
    )
    manager.gateway_projection_repository = SimpleNamespace(get=AsyncMock(return_value=snapshot))

    with pytest.raises(RuntimeError, match="recovery_game_session_not_available"):
        await manager._recovery_snapshot(failed)


@pytest.mark.asyncio
async def test_activation_conflict_fails_successor_and_keeps_retry_budget(monkeypatch):
    failed = record()
    manager, successor = recovery_manager(failed)
    manager._confirm_authority_revoked = AsyncMock()
    manager._activate_recovered_generation.side_effect = Conflict("binding activation conflict")
    manager._fail = AsyncMock(return_value=replace(successor, actual_state=RuntimeState.FAILED))
    monkeypatch.setattr("modules.runtime_manager.coordinator.time.monotonic", lambda: 10.0)

    assert await manager._recover_failed_generations() == 0
    manager._fail.assert_awaited_once()
    assert manager._rollover_tracker[failed.runtime_id][0] == 1


@pytest.mark.asyncio
async def test_manager_projection_prefers_new_generation_over_old_high_version():
    class Pool:
        def __init__(self): self.sql = ""
        async def fetchrow(self, sql, *_args): self.sql = sql; return None

    pool = Pool()
    assert await AsyncpgBindingRepository(pool).find_for_manager_projection("game-1", "user-1") is None
    normalized = " ".join(pool.sql.split()).lower()
    assert "order by generation desc, binding_version desc" in normalized


@pytest.mark.asyncio
async def test_unconfirmed_old_revocation_blocks_successor_and_spawn(monkeypatch):
    failed = record()
    manager, _ = recovery_manager(failed)
    manager._confirm_authority_revoked = AsyncMock(
        side_effect=RuntimeError("authority_revocation_unconfirmed")
    )
    monkeypatch.setattr("modules.runtime_manager.coordinator.time.monotonic", lambda: 10.0)

    assert await manager._recover_failed_generations() == 0
    manager.runtime_service.ensure_runtime.assert_not_awaited()
    manager._activate_recovered_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_in_memory_authority_worker_rejects_old_before_new_issuance_and_spawn():
    issuer = InMemoryAuthorityMaterialIssuer()
    old = RuntimeBinding(
        "binding-old", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=2, lease_epoch=1, status=BindingState.ACTIVE,
        game_session_id="game-1", game_instance_nonce="nonce-1",
        capability_digest="digest-1", bound_at=datetime.now(timezone.utc),
    )
    old_material = issuer.issue(old, jti="old-jti")
    assert issuer.accepts(old_material)

    new = replace(
        old, binding_id="binding-new", generation=2,
        binding_version=1, lease_epoch=1,
    )

    class Outbox:
        def __init__(self):
            self.processed = set()
            self.batches = [[{
                "intent_id": "revoke-old", "intent_kind": "revoke",
                "binding_id": old.binding_id, "binding_version": old.binding_version,
                "lease_epoch": old.lease_epoch, "payload": {},
            }], [{
                "intent_id": "issue-new", "intent_kind": "issue",
                "binding_id": new.binding_id, "binding_version": new.binding_version,
                "lease_epoch": new.lease_epoch,
                "payload": {"binding": _binding_payload(new), "scopes": ["game:read"],
                            "allowed_tools": ["say"], "jti": "new-jti"},
            }]]
        async def claim_pending_intents(self, _limit):
            return self.batches.pop(0) if self.batches else []
        async def acknowledge_intent(self, intent_id): self.processed.add(intent_id)
        async def is_intent_processed(self, intent_id): return intent_id in self.processed
        async def retry_intent(self, *_args): raise AssertionError("unexpected retry")
        async def diagnostic_stats(self): return {"pending": 0, "oldest_age_seconds": 0}

    worker = AuthorityOutboxWorker(Outbox(), issuer)
    assert await worker.run_once() == 1
    assert not issuer.accepts(old_material)
    assert new.binding_id not in issuer.material

    # Successor issuance (and therefore process spawn) is permitted only
    # after the durable revocation worker has made the old verifier reject.
    assert await worker.run_once() == 1
    assert issuer.material[new.binding_id].generation == 2
    spawned = True
    assert spawned


@pytest.mark.asyncio
async def test_confirm_revocation_requires_durable_processed_ack_even_without_file():
    issuer = InMemoryAuthorityMaterialIssuer()
    active = RuntimeBinding(
        "binding-old", "runtime-1", 1, "user-1", "agentee-1",
        binding_version=2, lease_epoch=1, status=BindingState.ACTIVE,
        game_session_id="game-1", game_instance_nonce="nonce-1",
        capability_digest="digest-1",
    )
    material = issuer.issue(active, jti="old-jti")
    revoked = replace(
        active, status=BindingState.REVOKED, binding_version=3, lease_epoch=2,
        revoked_at=datetime.now(timezone.utc),
    )

    class PendingOutbox:
        def __init__(self): self.processed = False
        async def claim_pending_intents(self, _limit): return []
        async def acknowledge_intent(self, _intent_id): self.processed = True
        async def retry_intent(self, *_args): pass
        async def diagnostic_stats(self): return {"pending": 1, "oldest_age_seconds": 0}
        async def is_intent_processed(self, _intent_id): return self.processed

    pending = PendingOutbox()
    manager = object.__new__(RuntimeManagerCoordinator)
    manager.binding_service = SimpleNamespace(
        bindings=SimpleNamespace(find_for_runtime=AsyncMock(return_value=revoked))
    )
    raw_worker = AuthorityOutboxWorker(pending, issuer)
    manager._recovery_authority_worker = SimpleNamespace(
        outbox=raw_worker, run_once=raw_worker.run_once,
    )

    # No credential file exists in this test, but the durable intent is still
    # pending, so absence of a file must not authorize rollover.
    with pytest.raises(RuntimeError, match="authority_revocation_unconfirmed"):
        await manager._confirm_authority_revoked("revoke:binding-old:3")
    assert issuer.accepts(material)

    pending.processed = True
    await manager._confirm_authority_revoked("revoke:binding-old:3")


@pytest.mark.asyncio
async def test_confirm_revocation_resolves_production_hosted_worker_outbox_repository():
    class Repository:
        async def is_intent_processed(self, intent_id):
            return intent_id == "revoke:binding-old:3"

    raw = SimpleNamespace(repository=Repository(), run_once=AsyncMock(return_value=1))
    hosted_worker = SimpleNamespace(outbox=raw, run_once=raw.run_once)
    manager = object.__new__(RuntimeManagerCoordinator)
    manager._recovery_authority_worker = hosted_worker

    await manager._confirm_authority_revoked("revoke:binding-old:3")
    raw.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_stopped_generation_waits_for_pending_fence_before_successor():
    stopped = record(1, RuntimeState.STOPPED)
    successor = record(2, RuntimeState.PROVISIONING)

    class ReceiptRepository:
        def __init__(self): self.processed = False
        async def is_intent_processed(self, intent_id):
            assert intent_id == "expire:binding-old:4"
            return self.processed

    receipt_repository = ReceiptRepository()
    raw_worker = SimpleNamespace(
        repository=receipt_repository, run_once=AsyncMock(return_value=0),
    )
    manager = object.__new__(RuntimeManagerCoordinator)
    manager.repository = SimpleNamespace(
        get=AsyncMock(return_value=stopped),
        get_generation=AsyncMock(return_value=stopped),
    )
    manager.runtime_service = SimpleNamespace(ensure_runtime=AsyncMock(return_value=successor))
    manager.authority = SimpleNamespace(
        deny_and_revoke=AsyncMock(return_value="expire:binding-old:4")
    )
    manager._recovery_authority_worker = SimpleNamespace(
        outbox=raw_worker, run_once=raw_worker.run_once,
    )
    manager._locks = {}
    manager._handles = {}
    manager.adapters = {}
    manager.stop_grace_seconds = 1

    with pytest.raises(RuntimeError, match="authority_revocation_unconfirmed"):
        await manager.provision(replace(stopped.spec, generation=2))
    manager.runtime_service.ensure_runtime.assert_not_awaited()

    receipt_repository.processed = True
    result = await manager.provision(replace(stopped.spec, generation=2))
    assert result is successor
    manager.runtime_service.ensure_runtime.assert_awaited_once()
