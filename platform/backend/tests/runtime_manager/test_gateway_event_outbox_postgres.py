from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import asyncpg
import pytest

from modules.gateway.runtime_contracts import (
    GameSessionSnapshot, GatewayEvent, GatewayEventType, InMemoryGameSessionFacts,
)
from modules.runtime_manager.async_application import (
    AsyncBindingService, AsyncGatewayProjectionService, AsyncRuntimeService,
)
from modules.runtime_manager.coordinator import (
    DurableGatewayEventPublisher, GatewayEventOutboxWorker, HostedGatewayEventConsumer,
)
from modules.runtime_manager.domain import BindingState, Conflict, RuntimeState
from modules.runtime_manager.persistence import (
    AsyncpgBindingRepository, AsyncpgGatewayEventOutboxRepository,
    AsyncpgGatewayProjectionRepository, AsyncpgRuntimeProductRepository,
)
from tests.runtime_manager.test_persistence_postgres import fresh_pool, postgres_dsn, spec


MIGRATION = Path(__file__).parents[3] / "supabase" / "migrations" / "20260715010000_gateway_event_outbox.sql"


async def _scenario(dsn: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(MIGRATION.read_text())
    finally:
        await connection.close()
    pool = await fresh_pool(dsn)
    runtimes = AsyncpgRuntimeProductRepository(pool)
    created = await AsyncRuntimeService(runtimes).ensure_runtime(spec())
    ready = await runtimes.compare_and_swap(
        replace(created, actual_state=RuntimeState.READY_UNBOUND, ready=True),
        created.status_version,
    )
    bindings = AsyncpgBindingRepository(pool)
    pending = await AsyncBindingService(runtimes, bindings).ensure_binding(
        ready.runtime_id, ready.generation, ready.spec.user_id, ready.spec.agentee_id,
        "durable-binding",
    )
    outbox = AsyncpgGatewayEventOutboxRepository(pool)
    publisher = DurableGatewayEventPublisher(outbox, eager_apply=False)
    facts = InMemoryGameSessionFacts()
    projection = AsyncGatewayProjectionService(AsyncpgGatewayProjectionRepository(pool), facts)

    def event(event_type: GatewayEventType, version: int, message_id: str) -> GatewayEvent:
        offline = event_type in {GatewayEventType.SUSPENDED, GatewayEventType.CLOSED}
        closed = event_type is GatewayEventType.CLOSED
        return GatewayEvent(
            event_type,
            GameSessionSnapshot(
                "game-durable", "nonce-durable", "user-1",
                None if offline else f"connection-{version}", version,
                "digest-durable", not offline, not closed, "resume-durable",
            ),
            message_id=message_id,
        )

    available = event(GatewayEventType.AVAILABLE, 1, "durable-available")
    await publisher.publish(available)
    assert (await bindings.get(pending.binding_id)).status is BindingState.PENDING
    assert await pool.fetchval(
        "SELECT application_stage FROM gateway_event_outbox WHERE message_id=$1",
        available.message_id,
    ) == "pending"

    class FailBindingOnce:
        def __init__(self, delegate): self.delegate = delegate; self.failed = False
        def __getattr__(self, name): return getattr(self.delegate, name)
        async def transition_with_operation(self, *args, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected_projection_binding_crash")
            return await self.delegate.transition_with_operation(*args, **kwargs)

    assert await GatewayEventOutboxWorker(
        outbox, HostedGatewayEventConsumer(projection, FailBindingOnce(bindings), runtimes),
        "worker-before-crash",
    ).run_once() == 0
    assert await pool.fetchval(
        "SELECT application_stage FROM gateway_event_outbox WHERE message_id=$1",
        available.message_id,
    ) == "projected"
    assert (await bindings.get(pending.binding_id)).status is BindingState.PENDING

    restarted = HostedGatewayEventConsumer(projection, bindings, runtimes)
    assert await GatewayEventOutboxWorker(outbox, restarted, "worker-after-restart").run_once() == 1
    active = await bindings.get(pending.binding_id)
    running = await runtimes.get_generation(ready.runtime_id, ready.generation)
    assert active.status is BindingState.ACTIVE and active.binding_version == 2
    assert running.actual_state is RuntimeState.RUNNING_BOUND
    versions = (active.binding_version, active.lease_epoch, running.status_version)
    assert await GatewayEventOutboxWorker(outbox, restarted, "worker-duplicate").run_once() == 0
    unchanged_binding = await bindings.get(pending.binding_id)
    unchanged_runtime = await runtimes.get_generation(ready.runtime_id, ready.generation)
    assert (unchanged_binding.binding_version, unchanged_binding.lease_epoch,
            unchanged_runtime.status_version) == versions

    suspended_event = event(GatewayEventType.SUSPENDED, 2, "durable-suspended")
    await publisher.publish(suspended_event)

    class FailRuntimeCas:
        def __init__(self, delegate): self.delegate = delegate; self.conflicts = 3
        def __getattr__(self, name): return getattr(self.delegate, name)
        async def compare_and_swap(self, record, expected):
            if self.conflicts:
                self.conflicts -= 1
                raise Conflict("injected_binding_runtime_crash")
            return await self.delegate.compare_and_swap(record, expected)

    assert await GatewayEventOutboxWorker(
        outbox, HostedGatewayEventConsumer(projection, bindings, FailRuntimeCas(runtimes)),
        "worker-runtime-crash",
    ).run_once() == 0
    suspended = await bindings.get(pending.binding_id)
    assert suspended.status is BindingState.SUSPENDED
    assert suspended.binding_version == active.binding_version + 1
    assert await pool.fetchval(
        "SELECT application_stage FROM gateway_event_outbox WHERE message_id=$1",
        suspended_event.message_id,
    ) == "bound"
    assert (await runtimes.get_generation(ready.runtime_id, ready.generation)).actual_state is RuntimeState.RUNNING_BOUND

    fresh_worker = GatewayEventOutboxWorker(
        AsyncpgGatewayEventOutboxRepository(pool),
        HostedGatewayEventConsumer(
            AsyncGatewayProjectionService(AsyncpgGatewayProjectionRepository(pool), facts),
            AsyncpgBindingRepository(pool), AsyncpgRuntimeProductRepository(pool),
        ),
        "worker-runtime-restart",
    )
    assert await fresh_worker.run_once() == 1
    repaired_binding = await bindings.get(pending.binding_id)
    repaired_runtime = await runtimes.get_generation(ready.runtime_id, ready.generation)
    assert repaired_binding.binding_version == suspended.binding_version
    assert repaired_runtime.actual_state is RuntimeState.IDLE

    resumed_event = event(GatewayEventType.RESUMED, 3, "durable-resumed")
    await publisher.publish(resumed_event)
    claimed = await outbox.claim_pending("dead-worker")
    assert [row["message_id"] for row in claimed] == [resumed_event.message_id]
    assert await GatewayEventOutboxWorker(outbox, restarted, "premature-worker").run_once() == 0
    await pool.execute(
        "UPDATE gateway_event_outbox SET claimed_at=now()-interval '6 minutes' WHERE message_id=$1",
        resumed_event.message_id,
    )
    assert await GatewayEventOutboxWorker(outbox, restarted, "lease-recovery-worker").run_once() == 1
    resumed = await bindings.get(pending.binding_id)
    resumed_runtime = await runtimes.get_generation(ready.runtime_id, ready.generation)
    assert resumed.status is BindingState.ACTIVE
    assert resumed.binding_version == repaired_binding.binding_version + 1
    assert resumed_runtime.actual_state is RuntimeState.RUNNING_BOUND

    # A newer terminal snapshot may be projected before an older outbox row is
    # delivered. Replaying that stale row must converge Binding/Runtime from the
    # durable latest snapshot, never revive the old AVAILABLE transition.
    closed_event = event(GatewayEventType.CLOSED, 4, "durable-closed-latest")
    await publisher.publish(closed_event)
    assert await GatewayEventOutboxWorker(outbox, restarted, "latest-first-worker").run_once() == 1
    closed_binding = await bindings.get(pending.binding_id)
    closed_runtime = await runtimes.get_generation(ready.runtime_id, ready.generation)
    assert closed_binding.status is BindingState.REVOKED
    assert closed_runtime.actual_state is RuntimeState.IDLE
    closed_versions = (
        closed_binding.binding_version, closed_binding.lease_epoch,
        closed_runtime.status_version,
    )

    stale_available = event(GatewayEventType.AVAILABLE, 1, "durable-stale-v1-after-v4")
    await publisher.publish(stale_available)
    assert await GatewayEventOutboxWorker(outbox, restarted, "stale-replay-worker").run_once() == 1
    converged_binding = await bindings.get(pending.binding_id)
    converged_runtime = await runtimes.get_generation(ready.runtime_id, ready.generation)
    assert converged_binding.status is BindingState.REVOKED
    assert converged_runtime.actual_state is RuntimeState.IDLE
    assert (
        converged_binding.binding_version, converged_binding.lease_epoch,
        converged_runtime.status_version,
    ) == closed_versions

    with pytest.raises(Conflict, match="message_id reused"):
        await publisher.publish(replace(
            resumed_event,
            snapshot=replace(resumed_event.snapshot, capability_digest="collision"),
        ))
    await pool.close()


def test_real_postgres_gateway_event_outbox_recovers_every_application_crash_window(
    postgres_dsn,
) -> None:
    asyncio.run(_scenario(postgres_dsn))
