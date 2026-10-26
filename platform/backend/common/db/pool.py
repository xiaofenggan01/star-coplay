from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 4) -> Any:
    if not dsn:
        logger.info("DATABASE_URL not set, skipping asyncpg pool creation")
        return None
    try:
        import asyncpg
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        logger.info("asyncpg pool created (min=%d, max=%d)", min_size, max_size)
        return pool
    except Exception as exc:
        logger.warning("asyncpg pool init failed: %s", exc)
        return None
