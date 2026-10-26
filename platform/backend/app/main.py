from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_ROOT = BACKEND_ROOT.parent
REPO_ROOT = PLATFORM_ROOT.parent

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "agent"))

from app.bootstrap import create_container
from app.lifecycle import start_all, stop_all


async def main() -> None:
    from common.settings.env import Settings
    from common.observability import configure_json_logging
    settings = Settings.from_env()

    log_dir = configure_json_logging(
        component="gateway",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )

    container = await create_container()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, container.gateway.stop)
        except (NotImplementedError, RuntimeError):
            pass

    logging.getLogger("platform_ws.app").info(
        "Backend starting: ws=%s:%d http=%s:%d (platform_url=%s)",
        container.settings.ws_gateway_host,
        container.settings.ws_gateway_port,
        container.settings.http_host,
        container.settings.http_port,
        container.settings.platform_url,
        extra={"event": "gateway.starting", "log_dir": str(log_dir)},
    )

    await start_all(container)
    try:
        await container.gateway.serve()
    finally:
        await stop_all(container)


if __name__ == "__main__":
    asyncio.run(main())
