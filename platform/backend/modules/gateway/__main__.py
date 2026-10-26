from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from common.observability import configure_json_logging
from common.settings.env import Settings, load_optional_env_files
from modules.gateway.server import Gateway, GatewayConfig, Session


async def _echo_worker(session: Session, msg: dict[str, Any]) -> None:
    t = msg.get("type")
    if t == "command_result":
        return
    if t == "state_snapshot":
        return
    if t == "game_event":
        return
    if t == "agent_status":
        return
    if t == "telemetry":
        return


async def main() -> None:
    load_optional_env_files(REPO_ROOT)
    settings = Settings.from_env()
    configure_json_logging(
        component="gateway",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    config = GatewayConfig.from_env()
    gw = Gateway(config, worker_handle=_echo_worker)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, gw.stop)
        except (NotImplementedError, RuntimeError):
            pass
    await gw.serve()


if __name__ == "__main__":
    asyncio.run(main())
