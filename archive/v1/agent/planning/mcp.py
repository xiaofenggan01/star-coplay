from __future__ import annotations

from typing import Any

from agent.memory.manager import MemoryManager
from agent.planning.task import Task, TaskLogManager, TaskStatus


class MCPCallback:

    def __init__(self, task_log: TaskLogManager, memory: MemoryManager) -> None:
        self.task_log = task_log
        self.memory = memory

    @property
    def active_task(self) -> Task | None:
        return self.task_log.active_task

    def on_task_created(
        self, task: Task, day_key: str, game_time: int, turn_id: str,
    ) -> None:
        task.update_status(TaskStatus.RUNNING)
        self.task_log.set_active_task(task)
        self.memory.record_event(
            category="task_lifecycle",
            summary=f"task_created | {task.task_id[:8]} | {task.description[:60]}",
            payload=task.as_dict(),
            day_key=day_key,
            game_time=game_time,
            turn_id=turn_id,
            priority=2,
        )

    def report_progress(
        self, step_info: str, day_key: str, game_time: int, turn_id: str,
    ) -> None:
        task = self.task_log.active_task
        if task is None:
            return
        task.increment_turn()
        self.task_log.log_task_change(task)

    def report_result(
        self, success: bool, message: str, day_key: str, game_time: int, turn_id: str,
    ) -> None:
        task = self.task_log.active_task
        if task is None:
            return
        new_status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        task.update_status(new_status)
        self.task_log.log_task_change(task)
        self.task_log.set_active_task(None)
        self.memory.record_event(
            category="task_lifecycle",
            summary=f"task_{new_status.value} | {task.task_id[:8]} | {message[:80]}",
            payload=task.as_dict(),
            day_key=day_key,
            game_time=game_time,
            turn_id=turn_id,
            priority=2,
        )

    def report_timeout(
        self, day_key: str, game_time: int, turn_id: str,
    ) -> None:
        task = self.task_log.active_task
        if task is None:
            return
        task.update_status(TaskStatus.TIMEOUT)
        self.task_log.log_task_change(task)
        self.task_log.set_active_task(None)
        self.memory.record_event(
            category="task_lifecycle",
            summary=f"task_timeout | {task.task_id[:8]} | elapsed={task.elapsed_turns}",
            payload=task.as_dict(),
            day_key=day_key,
            game_time=game_time,
            turn_id=turn_id,
            priority=3,
        )

    def cancel_active_task(
        self, reason: str, day_key: str, game_time: int, turn_id: str,
    ) -> None:
        task = self.task_log.active_task
        if task is None:
            return
        task.update_status(TaskStatus.FAILED)
        self.task_log.log_task_change(task)
        self.task_log.set_active_task(None)
        self.memory.record_event(
            category="task_lifecycle",
            summary=f"task_cancelled | {task.task_id[:8]} | {reason[:80]}",
            payload=task.as_dict(),
            day_key=day_key,
            game_time=game_time,
            turn_id=turn_id,
            priority=2,
        )
