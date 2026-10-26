from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TurnRecord:
    action: str
    arguments: dict[str, Any]
    expectation: str
    result: Any
    outcome: str = ""
    lesson: str = ""


class WorkingMemory:
    def __init__(self, capacity: int = 15) -> None:
        self._buffer: deque[TurnRecord] = deque(maxlen=capacity)
        self.consecutive_failures: int = 0
        self.standing_orders: list[str] = []
        self.force_switch: bool = False
        self.last_failure: dict[str, Any] | None = None
        self.player_commands: deque[str] = deque(maxlen=10)
        self.current_emotion: str = "neutral"
        self.emotion_history: deque[str] = deque(maxlen=10)

    def push(self, record: TurnRecord) -> None:
        self._buffer.append(record)
        if record.outcome == "fail":
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3:
                self.force_switch = True
        else:
            self.consecutive_failures = 0
            self.force_switch = False

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        items = list(self._buffer)[-n:]
        return [
            {
                "action": r.action,
                "args": r.arguments,
                "expectation": r.expectation,
                "result": r.result,
                "outcome": r.outcome,
                "lesson": r.lesson,
            }
            for r in items
        ]

    def last(self) -> TurnRecord | None:
        return self._buffer[-1] if self._buffer else None

    def consecutive_success_count(self, action: str) -> int:
        count = 0
        for r in reversed(self._buffer):
            if r.action == action and r.outcome == "success":
                count += 1
            else:
                break
        return count

    def add_standing_order(self, order: str) -> None:
        if order and order not in self.standing_orders:
            self.standing_orders.append(order)

    def add_player_command(self, command: str) -> None:
        """添加玩家通过 / 前缀发出的即时指令。"""
        if command:
            self.player_commands.append(command)

    def pop_player_commands(self) -> list[str]:
        """取出并清空所有待处理的玩家指令。"""
        commands = list(self.player_commands)
        self.player_commands.clear()
        return commands

    def restore_player_commands(self, commands: list[str]) -> None:
        for c in reversed(commands):
            if c:
                self.player_commands.appendleft(c)

    def set_emotion(self, emotion: str) -> None:
        if emotion and emotion != "neutral":
            self.current_emotion = emotion
            self.emotion_history.append(emotion)

    def clear(self) -> None:
        self._buffer.clear()
        self.consecutive_failures = 0
        self.force_switch = False
        self.last_failure = None
