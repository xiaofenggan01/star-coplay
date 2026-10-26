from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import RuntimeSpec


def make_spec(
    tmp_path: Path,
    *,
    runtime_id: str = "rt-1",
    generation: int = 1,
    idle_ttl_seconds: float = 3600.0,
    env: dict | None = None,
    **fake_flags,
) -> RuntimeSpec:
    return RuntimeSpec(
        runtime_id=runtime_id,
        generation=generation,
        user_id="user-1",
        agentee_id="agentee-1",
        provider="fake",
        workspace_root=tmp_path,
        idle_ttl_seconds=idle_ttl_seconds,
        env=env or {},
        provider_private_metadata=fake_flags,
    )


class ManualClock:
    """Deterministic test clock: ``advance()`` wakes waiters whose deadline has passed.

    Never touches real wall-clock time -- idle-TTL tests must not sleep 600
    real seconds (spec 6).
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._waiters: list[tuple[float, asyncio.Event]] = []

    def monotonic(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        target = self._now + seconds
        if target <= self._now:
            return
        event = asyncio.Event()
        self._waiters.append((target, event))
        await event.wait()

    async def advance(self, seconds: float) -> None:
        self._now += seconds
        still_waiting = []
        for target, event in self._waiters:
            if target <= self._now:
                event.set()
            else:
                still_waiting.append((target, event))
        self._waiters = still_waiting
        await asyncio.sleep(0)  # let any newly-woken tasks run before returning


async def wait_no_zombie(pid: int, timeout: float = 3.0) -> None:
    """Best-effort: give the OS a moment to reap the child so ps checks are clean."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root
