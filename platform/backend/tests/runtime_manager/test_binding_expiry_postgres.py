from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import socket
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

if os.environ.get("STARCOPLAY_REQUIRE_PRODUCT_EVIDENCE") == "1":
    import asyncpg
else:
    asyncpg = pytest.importorskip("asyncpg")

from modules.runtime_manager.async_application import AsyncRuntimeService
from modules.runtime_manager.admission import RuntimeAdmissionPolicy
from modules.runtime_manager.coordinator import BindingExpiryWorker, HostedGatewayEventConsumer
from modules.runtime_manager.domain import BindingState, RuntimeBinding, RuntimeProductSpec, RuntimeState
from modules.runtime_manager.persistence import AsyncpgBindingRepository, AsyncpgRuntimeProductRepository
from modules.runtime_manager.product_repository import OperationResult


ROOT = Path(__file__).parents[3]
MIGRATIONS = tuple(sorted((ROOT / "supabase/migrations").glob("2026071*.sql")))
INITDB = shutil.which("initdb") or "/opt/homebrew/bin/initdb"
PG_CTL = shutil.which("pg_ctl") or "/opt/homebrew/bin/pg_ctl"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def expiry_postgres_dsn(tmp_path_factory):
    if not Path(INITDB).exists() or not Path(PG_CTL).exists():
        pytest.fail("real PostgreSQL binaries required")
    data = tmp_path_factory.mktemp("binding-expiry-postgres") / "data"
    port = free_port()
    subprocess.run([INITDB, "-D", str(data), "-A", "trust", "-U", "postgres", "--no-locale"], check=True, capture_output=True)
    subprocess.run([PG_CTL, "-D", str(data), "-o", f"-p {port} -h 127.0.0.1", "-w", "start"], check=True, stdout=subprocess.DEVNULL)
    dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"

    async def migrate():
        conn = await asyncpg.connect(dsn)
        try:
            for migration in MIGRATIONS:
                await conn.execute(migration.read_text())
        finally:
            await conn.close()
    asyncio.run(migrate())
    yield dsn
    subprocess.run([PG_CTL, "-D", str(data), "-w", "stop"], check=True, stdout=subprocess.DEVNULL)


def runtime_spec(runtime_id):
    return RuntimeProductSpec(
        runtime_id, 1, runtime_id, "user-1", "agentee-1", "fake",
        "adapter/default", "persona/rev-1",
    )


def test_real_postgres_binding_expiry_is_single_writer_and_projection_recovers(expiry_postgres_dsn):
    async def scenario():
        pool = await asyncpg.create_pool(expiry_postgres_dsn, min_size=1, max_size=6)
        runtimes = AsyncpgRuntimeProductRepository(
            pool, RuntimeAdmissionPolicy(max_active_global=20, max_active_per_user=20)
        )
        bindings = AsyncpgBindingRepository(pool)
        now = datetime.now(timezone.utc)

        async def create(runtime_id, binding_id, expires_at, runtime_state=RuntimeState.RUNNING_BOUND):
            record = await AsyncRuntimeService(runtimes).ensure_runtime(runtime_spec(runtime_id))
            await runtimes.compare_and_swap(
                replace(record, actual_state=runtime_state, ready=runtime_state is not RuntimeState.STOPPED),
                record.status_version,
            )
            item = RuntimeBinding(
                binding_id, runtime_id, 1, "user-1", "agentee-1",
                binding_version=2, lease_epoch=1, status=BindingState.ACTIVE,
                game_session_id=f"session-{binding_id}",
                game_instance_nonce=f"nonce-{binding_id}",
                capability_digest="a" * 64,
                resume_identity=f"resume-{binding_id}",
                bound_at=now,
                expires_at=expires_at,
            )
            digest = hashlib.sha256(binding_id.encode()).hexdigest()
            await bindings.insert_with_operation(
                item, OperationResult(f"binding:{binding_id}", f"create:{binding_id}", digest, None),
            )

        await create("runtime-due", "binding-due", now - timedelta(seconds=1))
        await create("runtime-future", "binding-future", now + timedelta(hours=1))
        await create("runtime-null", "binding-null", None)
        await create("runtime-terminal", "binding-terminal", now - timedelta(seconds=1), RuntimeState.STOPPED)

        first, second = await asyncio.gather(
            AsyncpgBindingRepository(pool).expire_due(), AsyncpgBindingRepository(pool).expire_due(),
        )
        assert len(first) + len(second) == 2
        due = await bindings.get("binding-due")
        assert (due.status, due.binding_version, due.lease_epoch) == (BindingState.EXPIRED, 3, 2)
        assert await pool.fetchval(
            "SELECT count(*) FROM runtime_authority_outbox WHERE intent_id=$1", "expire:binding-due:3"
        ) == 1
        assert (await bindings.get("binding-future")).status is BindingState.ACTIVE
        assert (await bindings.get("binding-null")).status is BindingState.ACTIVE

        # Simulate crash after Binding/outbox commit but before Runtime projection.
        projector = HostedGatewayEventConsumer(None, bindings, runtimes)
        assert (await runtimes.get_generation("runtime-due", 1)).actual_state is RuntimeState.RUNNING_BOUND
        assert await BindingExpiryWorker(bindings, projector).run_once() == 0
        assert (await runtimes.get_generation("runtime-due", 1)).actual_state is RuntimeState.IDLE
        assert (await bindings.get("binding-due")).binding_version == 3
        assert (await runtimes.get_generation("runtime-terminal", 1)).actual_state is RuntimeState.STOPPED
        assert await bindings.expire_due() == ()
        await pool.close()

    asyncio.run(scenario())


def test_production_container_composes_binding_expiry_with_shared_authorities(
    expiry_postgres_dsn, monkeypatch, tmp_path,
):
    from app.bootstrap import create_container

    class Authority:
        def issue(self, *_args, **_kwargs):
            return None

        def revoke(self, *_args, **_kwargs):
            return None

    monkeypatch.setenv("DATABASE_URL", expiry_postgres_dsn)
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MANAGER_ENABLED", "1")
    monkeypatch.setenv("STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET", "test-control-secret")
    monkeypatch.setenv("STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON", '{"test-service":"test-secret"}')
    monkeypatch.setenv("STARCOPLAY_RUNTIME_MANAGER_WORKSPACE_ROOT", str(tmp_path / "runtimes"))
    monkeypatch.setenv("STARCOPLAY_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("HOSTED_GAME_SESSION_ROUTE_ENABLED", "1")
    monkeypatch.setenv("WS_GATEWAY_PORT", "0")
    monkeypatch.setenv("WS_HTTP_PORT", "0")

    async def scenario():
        container = await create_container(runtime_authority_issuer=Authority())
        try:
            worker = container.runtime_manager_workers.binding_expiry_worker
            assert worker is not None
            assert worker.bindings is container.runtime_manager.binding_service.bindings
            assert worker.projector is container.gateway_event_consumer
            assert worker.projector is container.runtime_manager.gateway_snapshot_converger
        finally:
            await container.gateway.authenticator.close()
            await container.db_pool.close()

    asyncio.run(scenario())
