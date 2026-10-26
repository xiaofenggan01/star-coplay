from __future__ import annotations

import logging

from app.container import Container

logger = logging.getLogger(__name__)


async def start_all(container: Container) -> None:
    """Start all services in dependency order."""
    if container.runtime_manager_workers is not None:
        await container.runtime_manager_workers.start()
        logger.info("lifecycle: Runtime Manager workers started")
    await container.http_api.start()
    logger.info("lifecycle: HTTP API started (port %d)", container.settings.http_port)

async def stop_all(container: Container) -> None:
    """Stop all services in reverse dependency order."""
    logger.info("lifecycle: stopping HTTP API")
    await container.http_api.stop()

    # Stop ingress first, then background control-plane loops, before shared DB.
    if container.runtime_manager_workers is not None:
        logger.info("lifecycle: stopping Runtime Manager workers")
        await container.runtime_manager_workers.stop()

    if container.ai_relay_account_service is not None:
        newapi_client = getattr(container.ai_relay_account_service, "newapi_client", None)
        close = getattr(newapi_client, "aclose", None)
        if close is not None:
            logger.info("lifecycle: closing NewAPI client")
            await close()

    if container.system_llm_router is not None:
        logger.info("lifecycle: closing System LLM router")
        await container.system_llm_router.aclose()

    logger.info("lifecycle: stopping gateway")
    container.gateway.stop()

    if container.db_pool is not None:
        logger.info("lifecycle: closing db pool")
        await container.db_pool.close()

    logger.info("lifecycle: shutdown complete")
