from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubGoal:
    id: str
    description: str
    tool_name: str | None
    arguments: dict[str, Any]
    preconditions: list[str] = field(default_factory=list)
    status: str = "pending"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "tool_name": self.tool_name,
            "status": self.status,
        }


@dataclass
class Goal:
    id: str
    description: str
    sub_goals: list[SubGoal]
    status: str = "pending"
    priority: int = 0
    on_complete_action: str | None = None
    on_complete_args: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def create(description: str, sub_goals: list[SubGoal], priority: int = 0,
               on_complete_action: str | None = None,
               on_complete_args: dict[str, Any] | None = None) -> Goal:
        return Goal(
            id=uuid.uuid4().hex[:12],
            description=description,
            sub_goals=sub_goals,
            priority=priority,
            on_complete_action=on_complete_action,
            on_complete_args=on_complete_args or {},
        )

    def next_pending(self) -> SubGoal | None:
        for sg in self.sub_goals:
            if sg.status == "pending":
                return sg
        return None

    def progress(self) -> dict[str, Any]:
        total = len(self.sub_goals)
        done = sum(1 for s in self.sub_goals if s.status in ("done", "skipped"))
        return {"goal": self.description, "status": self.status,
                "done": done, "total": total}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "sub_goals": [s.as_dict() for s in self.sub_goals],
        }


class GoalManager:
    def __init__(self) -> None:
        self._stack: list[Goal] = []

    @property
    def current_goal(self) -> Goal | None:
        return self._stack[-1] if self._stack else None

    def is_idle(self) -> bool:
        return not self._stack

    def set_goal(self, goal: Goal) -> None:
        goal.status = "active"
        self._stack.append(goal)

    def interrupt(self, new_goal: Goal) -> None:
        current = self.current_goal
        if current:
            current.status = "interrupted"
        new_goal.status = "active"
        self._stack.append(new_goal)

    def advance(self) -> SubGoal | None:
        goal = self.current_goal
        if goal is None:
            return None
        for sg in goal.sub_goals:
            if sg.status == "active":
                sg.status = "done"
                break
        nxt = goal.next_pending()
        if nxt is not None:
            nxt.status = "active"
            return nxt
        goal.status = "done"
        self._stack.pop()
        if self._stack and self._stack[-1].status == "interrupted":
            self._stack[-1].status = "active"
        return None

    def mark_current_subgoal_failed(self) -> None:
        goal = self.current_goal
        if goal is None:
            return
        for sg in goal.sub_goals:
            if sg.status == "active":
                sg.status = "skipped"
                break

    def day_summary(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for g in self._stack:
            out.append(g.progress())
        return out

    def clear(self) -> None:
        self._stack.clear()

    def goal_info_for_prompt(self) -> dict[str, Any] | None:
        goal = self.current_goal
        if goal is None:
            return None
        return goal.progress()
