from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlannedStep:
    tool_name: str
    arguments: dict[str, Any]
    preconditions: list[str] = field(default_factory=list)
    source: str = "goal"
    expectation: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "source": self.source,
            "expectation": self.expectation,
        }


class PlanQueue:
    def __init__(self) -> None:
        self._queue: deque[PlannedStep] = deque()

    def push(self, step: PlannedStep) -> None:
        self._queue.append(step)

    def push_many(self, steps: list[PlannedStep]) -> None:
        self._queue.extend(steps)

    def push_front(self, step: PlannedStep) -> None:
        self._queue.appendleft(step)

    def push_front_many(self, steps: list[PlannedStep]) -> None:
        for s in reversed(steps):
            self._queue.appendleft(s)

    def pop(self) -> PlannedStep | None:
        return self._queue.popleft() if self._queue else None

    def peek(self) -> PlannedStep | None:
        return self._queue[0] if self._queue else None

    def clear(self) -> None:
        self._queue.clear()

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def __len__(self) -> int:
        return len(self._queue)
