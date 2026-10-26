"""Clock abstraction so idle-TTL logic (later phases) can be tested without real sleeps."""
from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Production clock backed by ``time.monotonic``/``asyncio.sleep``."""

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
