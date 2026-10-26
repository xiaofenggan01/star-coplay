from __future__ import annotations

from agent.execution.tool_base import ToolDecision


class Decomposer:
    def decompose(self, decision: ToolDecision) -> list[ToolDecision]:
        return [decision]
