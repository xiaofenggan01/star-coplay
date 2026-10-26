from __future__ import annotations

from agent.interface.perceiver import GameContext
from agent.planning.plan_queue import PlannedStep


class PreconditionChecker:
    """前置条件检查器。Mod 的 batch 命令（water_crops 等）已内置前置处理，
    此处仅保留 Agent 侧需要的简单检查。"""

    def check(self, step: PlannedStep, context: GameContext) -> list[PlannedStep]:
        return []
