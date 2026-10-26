from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.bridge.file_bridge import get_mtime, read_json


@dataclass(slots=True)
class StateSnapshot:
    payload: dict[str, Any]
    mtime: float


class StateWatcher:
    def __init__(
        self,
        bridge_dir: Path,
        poll_interval: float,
        heartbeat_seconds: float = 30.0,
    ) -> None:
        self.path = bridge_dir / "game_state.json"
        self.poll_interval = poll_interval
        self.heartbeat_seconds = max(0.5, heartbeat_seconds)

    def wait_until_ready(self) -> StateSnapshot:
        while True:
            payload = read_json(self.path)
            if isinstance(payload, dict) and payload.get("player"):
                return StateSnapshot(payload=payload, mtime=get_mtime(self.path))
            time.sleep(self.poll_interval)

    def wait_for_next(self, last_mtime: float) -> StateSnapshot:
        start = time.monotonic()
        while True:
            current_mtime = get_mtime(self.path)
            payload = read_json(self.path)
            if isinstance(payload, dict) and payload.get("player"):
                if current_mtime > last_mtime:
                    return StateSnapshot(payload=payload, mtime=current_mtime)
                if time.monotonic() - start >= self.heartbeat_seconds:
                    return StateSnapshot(payload=payload, mtime=current_mtime)
            time.sleep(self.poll_interval)
