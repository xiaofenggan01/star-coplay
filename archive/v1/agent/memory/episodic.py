from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EpisodicMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._ensure_file()

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            self.path.write_text("# Episode Log\n\n", encoding="utf-8")

    def record(
        self,
        action: str,
        context_summary: str,
        result: Any,
        lesson: str,
        game_time: int = 0,
    ) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = (
            f"## {ts} | game_time={game_time}\n"
            f"- **action**: {action}\n"
            f"- **context**: {context_summary}\n"
            f"- **result**: {result}\n"
            f"- **lesson**: {lesson}\n\n"
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def recent_text(self, chars: int = 2000) -> str:
        if not self.path.is_file():
            return ""
        text = self.path.read_text(encoding="utf-8")
        return text[-chars:] if len(text) > chars else text
