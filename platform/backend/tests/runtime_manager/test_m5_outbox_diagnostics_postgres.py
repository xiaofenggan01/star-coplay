from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg

from modules.runtime_manager.async_application import AsyncRuntimeService
from modules.runtime_manager.persistence import (
    AsyncpgBindingRepository, AsyncpgGatewayEventOutboxRepository,
    AsyncpgRuntimeProductRepository,
)
from modules.runtime_manager.health import RuntimeManagerHealthService
from tests.runtime_manager.test_persistence_postgres import fresh_pool, postgres_dsn, spec


GATEWAY_MIGRATION = (
    Path(__file__).parents[3] / "supabase/migrations/20260715010000_gateway_event_outbox.sql"
)


async def _scenario(dsn: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(GATEWAY_MIGRATION.read_text())
    finally:
        await connection.close()
    pool = await fresh_pool(dsn)
    try:
        await AsyncRuntimeService(AsyncpgRuntimeProductRepository(pool)).ensure_runtime(spec())
        await pool.execute(
            """INSERT INTO public.runtime_bindings(
                 binding_id,runtime_id,generation,user_id,agentee_id,status,contract_version)
               VALUES('m5-binding','runtime-1',1,'user-1','agentee-1','pending','starcoplay.binding.v1')"""
        )
        await pool.execute(
            """INSERT INTO public.runtime_authority_outbox(
                 intent_id,intent_kind,binding_id,binding_version,lease_epoch,payload,created_at)
               VALUES('m5-revoke','revoke','m5-binding',1,1,'{}'::jsonb,now()-interval '3 seconds')"""
        )
        authority = AsyncpgBindingRepository(pool)
        authority_stats = await authority.diagnostic_stats()
        assert authority_stats["pending"] == 1
        assert authority_stats["claimed"] == 0
        assert authority_stats["oldest_age_seconds"] >= 2
        await authority.acknowledge_intent("m5-revoke")
        assert await authority.diagnostic_stats() == {
            "pending": 0, "claimed": 0, "oldest_age_seconds": 0.0,
        }

        gateway = AsyncpgGatewayEventOutboxRepository(pool)
        await gateway.publish("m5-message", {"contract_version": "starcoplay.gateway-event.v1"})
        gateway_stats = await gateway.diagnostic_stats()
        assert gateway_stats["pending"] == 1
        assert gateway_stats["claimed"] == 0
        assert gateway_stats["oldest_age_seconds"] >= 0

        class Coordinator:
            repository = AsyncpgRuntimeProductRepository(pool)
            @staticmethod
            def owned_handle_keys_snapshot(): return frozenset()
            @staticmethod
            def provider_state_snapshot(): return {}

        before_authority = tuple(dict(row) for row in await pool.fetch(
            "SELECT * FROM public.runtime_authority_outbox ORDER BY intent_id"
        ))
        before_gateway = tuple(dict(row) for row in await pool.fetch(
            "SELECT * FROM public.gateway_event_outbox ORDER BY message_id"
        ))
        health = await RuntimeManagerHealthService(
            Coordinator(), authority_outbox=authority, gateway_outbox=gateway,
        ).snapshot()
        after_authority = tuple(dict(row) for row in await pool.fetch(
            "SELECT * FROM public.runtime_authority_outbox ORDER BY intent_id"
        ))
        after_gateway = tuple(dict(row) for row in await pool.fetch(
            "SELECT * FROM public.gateway_event_outbox ORDER BY message_id"
        ))
        assert before_authority == after_authority
        assert before_gateway == after_gateway
        assert health["outboxes"]["gateway"]["pending_count"] == 1
        assert health["outboxes"]["gateway"]["claimed_count"] == 0
        await gateway.acknowledge("m5-message")
        assert await gateway.diagnostic_stats() == {
            "pending": 0, "claimed": 0, "oldest_age_seconds": 0.0,
        }
    finally:
        await pool.close()


def test_postgres_outbox_diagnostic_stats_read_global_durable_rows(postgres_dsn):
    asyncio.run(_scenario(postgres_dsn))
