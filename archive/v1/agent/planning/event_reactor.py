from __future__ import annotations

from typing import Any

from agent.interface.perceiver import GameContext
from agent.planning.goal import Goal, GoalManager, SubGoal
from agent.planning.plan_queue import PlannedStep

_FAST_INTERRUPT_TYPES = frozenset({
    "agent_action_cancelled",
    "energy_low",
    "npc_nearby",
})


class EventReactor:
    """快环：由事件直接注入 PlannedStep，无需经过 Planner LLM。"""

    @staticmethod
    def is_fast_interrupt(events: list[dict[str, Any]]) -> bool:
        for event in events:
            et = str(event.get("type") or "")
            if et in _FAST_INTERRUPT_TYPES:
                return True
        return False

    def react(
        self,
        events: list[dict[str, Any]],
        context: GameContext,
        goal_manager: GoalManager,
    ) -> list[PlannedStep]:
        steps: list[PlannedStep] = []
        for event in events:
            etype = str(event.get("type") or event.get("event") or "")
            data = event.get("data") or {}
            new_steps = self._handle(etype, data, context, goal_manager)
            if new_steps:
                steps.extend(new_steps)
        return steps

    def _handle(
        self,
        etype: str,
        data: dict[str, Any],
        context: GameContext,
        goal_manager: GoalManager,
    ) -> list[PlannedStep] | None:
        if etype == "npc_nearby":
            name = data.get("name", "")
            talked = data.get("talked_today", False)
            if not talked and name:
                return [PlannedStep(
                    tool_name="talk_to_npc",
                    arguments={"npc_name": name},
                    source="event_reaction",
                    expectation=f"talk_to {name}",
                )]

        if etype == "energy_low":
            pct = data.get("percentage", data.get("percent", 100))
            if isinstance(pct, (int, float)) and pct < 20:
                goal_manager.interrupt(Goal.create(
                    description="energy_low_sleep",
                    sub_goals=[SubGoal(
                        id="sleep",
                        description="go_to_bed",
                        tool_name="go_to_bed",
                        arguments={},
                    )],
                    priority=10,
                ))
                return [PlannedStep(
                    tool_name="go_to_bed",
                    arguments={},
                    source="event_reaction",
                    expectation="go_to_bed",
                )]

        return None
