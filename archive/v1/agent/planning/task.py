from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class Task:
    description: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = TaskStatus.PENDING
    timeout_turns: int = 50
    elapsed_turns: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def update_status(self, new_status: TaskStatus) -> None:
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def increment_turn(self) -> None:
        self.elapsed_turns += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()
        if (
            self.timeout_turns > 0
            and self.elapsed_turns >= self.timeout_turns
            and self.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ):
            self.status = TaskStatus.TIMEOUT

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskLogManager:
    def __init__(self, log_file_path: Path | str) -> None:
        self.log_file = Path(log_file_path)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._active_task: Task | None = None

    @property
    def active_task(self) -> Task | None:
        return self._active_task

    def set_active_task(self, task: Task | None) -> None:
        self._active_task = task
        if task is not None:
            self.log_task_change(task)

    def log_task_change(self, task: Task) -> None:
        task_data = task.as_dict()
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(task_data, ensure_ascii=False) + "\n")

    def load_all_logs(self) -> list[dict[str, Any]]:
        if not self.log_file.is_file():
            return []
        logs = []
        try:
            with self.log_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        logs.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
        return logs
