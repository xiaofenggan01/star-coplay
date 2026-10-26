"""Real subprocess + PostgreSQL evidence for owned-process health reconcile."""
from __future__ import annotations

import asyncio
import os
import signal

import pytest

from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
from modules.runtime_manager.domain import RuntimeState
from modules.runtime_manager.persistence import AsyncpgRuntimeProductRepository
from tests.runtime_manager.test_owned_health_reconcile import Authority, PersonaReader, _spec
from tests.runtime_manager.test_persistence_postgres import fresh_pool, postgres_dsn


async def _scenario(dsn: str, workspace_root) -> None:
    pool = await fresh_pool(dsn)
    adapter = FakeAgentRuntimeAdapter()
    authority = Authority()
    repository = AsyncpgRuntimeProductRepository(pool)
    manager = RuntimeManagerCoordinator(
        repository, PersonaReader(), authority, {"fake": adapter}, workspace_root,
    )
    key = ("runtime-live-health", 1)
    try:
        record = await manager.ensure(_spec(runtime_id=key[0]))
        handle = manager._handles[key]
        pid = handle.transport.pid
        assert pid is not None

        assert await manager.reconcile_all() == 0
        healthy = await repository.get_generation(*key)
        healthy_version = healthy.status_version
        assert healthy.actual_state is RuntimeState.READY_UNBOUND

        os.kill(pid, signal.SIGKILL)
        for _ in range(100):
            if (await adapter.inspect(handle)).returncode is not None:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("real Fake runtime did not report process exit")

        assert await manager.reconcile_all() == 1
        failed = await AsyncpgRuntimeProductRepository(pool).get_generation(*key)
        assert failed.actual_state is RuntimeState.FAILED
        assert failed.ready is False
        assert failed.status_version == healthy_version + 1
        assert "process_exited:returncode=" in (failed.exit_reason or "")
        assert any(event[0] == "revoke" and event[1:3] == key for event in authority.events)
        # Keep ownership until stop/shutdown: an inspect exception does not prove
        # the child is gone, so dropping the handle here could orphan a process.
        assert key in manager._handles
    finally:
        handle = manager._handles.get(key)
        if handle is not None:
            await adapter.stop(handle, "test_teardown", 1.0)
        await pool.close()


def test_real_fake_process_health_is_persisted_in_postgres(postgres_dsn, tmp_path):
    asyncio.run(_scenario(postgres_dsn, tmp_path))
