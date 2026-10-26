"""PostgreSQL evidence for projection-before-Binding active convergence."""
from __future__ import annotations

import asyncio

from modules.gateway.runtime_contracts import (
    GameSessionSnapshot, GatewayEvent, GatewayEventType, InMemoryGameSessionFacts,
)
from modules.runtime_manager.async_application import AsyncGatewayProjectionService, AsyncRuntimeService
from modules.runtime_manager.coordinator import HostedGatewayEventConsumer, RuntimeManagerCoordinator
from modules.runtime_manager.domain import BindingState, RuntimeState
from modules.runtime_manager.persistence import (
    AsyncpgBindingRepository, AsyncpgGatewayProjectionRepository,
    AsyncpgRuntimeProductRepository,
)
from tests.runtime_manager.test_persistence_postgres import fresh_pool, postgres_dsn, spec


async def _scenario(dsn: str, workspace_root) -> None:
    pool = await fresh_pool(dsn)
    try:
        runtimes = AsyncpgRuntimeProductRepository(pool)
        created = await AsyncRuntimeService(runtimes).ensure_runtime(spec())
        assert created.actual_state is RuntimeState.PROVISIONING
        projections = AsyncpgGatewayProjectionRepository(pool)
        projection_service = AsyncGatewayProjectionService(
            projections, InMemoryGameSessionFacts(),
        )
        snapshot = GameSessionSnapshot(
            "online-before-binding", "nonce-before-binding", created.spec.user_id,
            "connection-before-binding", 1, "digest-before-binding", True, True,
            "resume-before-binding",
        )
        await projection_service.consume(GatewayEvent(
            GatewayEventType.AVAILABLE, snapshot,
            message_id="projection-before-binding-message",
        ))

        bindings = AsyncpgBindingRepository(pool)
        converger = HostedGatewayEventConsumer(projection_service, bindings, runtimes)
        manager = RuntimeManagerCoordinator(
            runtimes, object(), object(), {}, workspace_root,
            binding_repository=bindings,
            gateway_projection_repository=projections,
            gateway_snapshot_converger=converger,
        )
        active = await manager.ensure_binding(
            created.runtime_id, created.generation, created.spec.user_id,
            created.spec.agentee_id, "ensure-after-online",
        )
        assert active.status is BindingState.ACTIVE
        assert active.game_session_id == snapshot.game_session_id
        running = await runtimes.get_generation(created.runtime_id, created.generation)
        assert running.actual_state is RuntimeState.PROVISIONING
        issuance_count = await pool.fetchval(
            "SELECT count(*) FROM runtime_authority_outbox WHERE intent_kind='issue'"
        )
        assert issuance_count == 1
        versions = (active.binding_version, active.lease_epoch, running.status_version)

        replay = await manager.ensure_binding(
            created.runtime_id, created.generation, created.spec.user_id,
            created.spec.agentee_id, "ensure-after-online",
        )
        replay_runtime = await runtimes.get_generation(created.runtime_id, created.generation)
        assert (replay.binding_version, replay.lease_epoch,
                replay_runtime.status_version) == versions
        assert await pool.fetchval(
            "SELECT count(*) FROM runtime_authority_outbox WHERE intent_kind='issue'"
        ) == issuance_count
    finally:
        await pool.close()


def test_online_projection_converges_when_binding_is_created_later(postgres_dsn, tmp_path):
    asyncio.run(_scenario(postgres_dsn, tmp_path))
