"""Process-local diagnostic correlation for active Hosted Runtime turns."""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ActiveTurnCorrelation:
    log_id: str
    operation_id: str
    interrupted_reason: str | None = None


class ActiveTurnCorrelationRegistry:
    """Best-effort diagnostic state; never an authority or routing input."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, int], ActiveTurnCorrelation] = {}

    def register(
        self, runtime_id: str, generation: int, log_id: str, operation_id: str,
    ) -> ActiveTurnCorrelation:
        correlation = ActiveTurnCorrelation(log_id=log_id, operation_id=operation_id)
        self._active[(runtime_id, generation)] = correlation
        return correlation

    def lookup(self, runtime_id: str, generation: int) -> ActiveTurnCorrelation | None:
        return self._active.get((runtime_id, generation))

    def mark_interrupted(
        self, runtime_id: str, generation: int, reason: str,
    ) -> bool:
        key = (runtime_id, generation)
        current = self._active.get(key)
        if current is None:
            return False
        self._active[key] = replace(current, interrupted_reason=reason or "player_interrupted")
        return True

    def clear_if_matches(
        self, runtime_id: str, generation: int, operation_id: str,
    ) -> bool:
        key = (runtime_id, generation)
        current = self._active.get(key)
        if current is None or current.operation_id != operation_id:
            return False
        del self._active[key]
        return True
