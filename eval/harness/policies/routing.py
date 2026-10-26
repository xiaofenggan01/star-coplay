"""Routing policy: executes a deterministic GoalRouter plan against the world."""

from __future__ import annotations

from typing import Any

from slow_loop.goal_router import GoalPlan, GoalRouter

from ..sim_world import SimWorld
from .executor import execute_plan, postcheck, result_payload


class _WorldContext:
    """Adapts SimWorld to the router's GoalContext protocol."""

    def __init__(self, world: SimWorld):
        self._world = world

    @property
    def minutes(self) -> int:
        return self._world.minutes

    @property
    def stamina(self) -> float:
        return self._world.player.stamina

    @property
    def weather(self) -> str:
        return self._world.weather

    @property
    def unwatered_crops(self) -> int:
        return len([c for c in self._world.crops.values() if not c.dead and not c.watered])

    def inventory_count(self, item: str) -> int:
        return self._world.player.count(item)


class RoutingPolicy:
    name = "routing"

    def __init__(self, router: GoalRouter | None = None):
        self.router = router or GoalRouter()

    async def run(self, goal: dict[str, Any], ctx: Any, world: SimWorld) -> dict[str, Any]:
        plan = self.router.plan(goal, _WorldContext(world))
        before = world.snapshot()
        plan_dict = plan.to_dict()

        if plan.blocked_reason:
            return result_payload(
                goal_type=plan.goal_type,
                success=False,
                status="blocked",
                observation=f"goal blocked: {plan.blocked_reason}",
                plan=plan_dict,
                steps=[],
                before=before,
                after=world.snapshot(),
                satisfied=False,
                blocked_reason=plan.blocked_reason,
            )

        if plan.satisfied or not plan.steps:
            return result_payload(
                goal_type=plan.goal_type,
                success=True,
                status="ok",
                observation=plan.note or "goal already satisfied",
                plan=plan_dict,
                steps=[],
                before=before,
                after=world.snapshot(),
                satisfied=True,
            )

        steps = [{"skill": s.skill, "params": s.params, "reason": s.reason} for s in plan.steps]
        step_results = await execute_plan(steps, ctx, world)
        satisfied, status, observation = postcheck(goal, world, before)
        return result_payload(
            goal_type=plan.goal_type,
            success=satisfied or status == "partial",
            status=status,
            observation=observation,
            plan=plan_dict,
            steps=step_results,
            before=before,
            after=world.snapshot(),
            satisfied=satisfied,
        )

    # Kept for callers that build a GoalPlan manually.
    def _plan_dict(self, plan: GoalPlan) -> dict[str, Any]:
        return plan.to_dict()
