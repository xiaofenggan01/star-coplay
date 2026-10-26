from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent.bridge.file_bridge import write_json_atomic


class StatusWriter:
    """写入 agent_status.json，供 Mod/HUD 展示 Agent 状态。

    协议规格状态枚举: idle / thinking / acting / chatting / error / shutdown
    """

    def __init__(self, bridge_dir: Path) -> None:
        self.path = bridge_dir / "agent_status.json"
        self._current: dict[str, Any] = {
            "status": "idle",
            "current_task": None,
            "thinking_about": None,
            "last_updated": time.time(),
            "error": None,
        }

    def update(self, status: str, current_task: str | None = None,
               thinking_about: str | None = None, error: str | None = None) -> None:
        self._current = {
            "status": status,
            "current_task": current_task,
            "thinking_about": thinking_about,
            "last_updated": time.time(),
            "error": error,
        }
        write_json_atomic(self.path, self._current)

    def idle(self) -> None:
        self.update("idle")

    def thinking(self, about: str | None = None) -> None:
        self.update("thinking", thinking_about=about)

    def acting(self, task: str) -> None:
        self.update("acting", current_task=task)

    def chatting(self) -> None:
        self.update("chatting")

    def error(self, message: str) -> None:
        self.update("error", error=message)

    def shutdown(self) -> None:
        self.update("shutdown")


class NoOpStatusWriter:
    """dry_run 模式下的空操作。"""

    def update(self, status: str, **kwargs: Any) -> None:
        pass

    def idle(self) -> None:
        pass

    def thinking(self, about: str | None = None) -> None:
        pass

    def acting(self, task: str) -> None:
        pass

    def chatting(self) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def shutdown(self) -> None:
        pass
