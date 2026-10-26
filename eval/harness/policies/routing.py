"""Routing policy: executes a deterministic GoalRouter plan against the world."""

from __future__ import annotations

import asyncio
from typing import Any

from slow_loop.goal_router import GoalPlan, GoalRouter, normalize_item

from ..sim_world import SimWorld
from ..skills import load_skill


STEP_TIMEOUT_S = 20.0


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

        if plan.blocked_reason:
            return self._result(
                success=False,
                status="blocked",
                observation=f"goal blocked: {plan.blocked_reason}",
                data={"blocked_reason": plan.blocked_reason},
                plan=plan,
                before=before,
                after=world.snapshot(),
            )

        if plan.satisfied or not plan.steps:
            return self._result(
                success=True,
                status="ok",
                observation=plan.note or "goal already satisfied",
                data={"blocked_reason": None, "goal_status": "satisfied"},
                plan=plan,
                before=before,
                after=world.snapshot(),
                satisfied=True,
            )

        step_results: list[dict[str, Any]] = []
        for step in plan.steps:
            skill = load_skill(step.skill)
            try:
                result = await asyncio.wait_for(skill(ctx, **step.params), timeout=STEP_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - surface as a failed step
                result = {"success": False, "status": "error", "data": {}, "observation": f"{type(exc).__name__}: {exc}"}
            step_results.append({"skill": step.skill, "params": step.params, "result": result})
            if not result.get("success"):
                break

        satisfied, status, observation = self._postcheck(goal, world, before)
        return self._result(
            success=satisfied or status == "partial",
            status=status,
            observation=observation,
            data={
                "blocked_reason": None,
                "goal_status": "satisfied" if satisfied else status,
                "steps": step_results,
            },
            plan=plan,
            before=before,
            after=world.snapshot(),
            satisfied=satisfied,
        )

    # ── post-conditions ──────────────────────────────────────────────────

    def _postcheck(
        self,
        goal: dict[str, Any],
        world: SimWorld,
        before: dict[str, Any],
    ) -> tuple[bool, str, str]:
        goal_type = str(goal.get("type") or "").strip().lower()
        if goal_type in ("acquire", "get", "gather"):
            item = normalize_item(goal.get("item"))
            target = int(goal.get("qty") or 1)
            have = world.player.count(item)
            if have >= target:
                return True, "ok", f"goal satisfied: {have}/{target} {item}"
            gained = have - int(before.get("inventory", {}).get(item, 0))
            return False, "partial", f"goal partial: {have}/{target} {item} (+{gained})"

        if goal_type in ("water", "water_crops", "watercrops"):
            remaining = len([c for c in world.crops.values() if not c.dead and not c.watered])
            if remaining == 0:
                return True, "ok", "goal satisfied: all crops watered"
            return False, "partial", f"goal partial: {remaining} crops still unwatered"

        if goal_type in ("sleep", "go_bed", "gotobed"):
            if world.day > int(before.get("day", world.day)):
                return True, "ok", f"goal satisfied: day {before.get('day')} -> {world.day}"
            return False, "partial", "goal partial: day did not advance"

        return False, "partial", "goal unsupported by post-check"

    # ── result shaping ───────────────────────────────────────────────────

    def _result(
        self,
        *,
        success: bool,
        status: str,
        observation: str,
        data: dict[str, Any],
        plan: GoalPlan,
        before: dict[str, Any],
        after: dict[str, Any],
        satisfied: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "goal": plan.goal_type,
            "plan": plan.to_dict(),
            "goal_satisfied": satisfied,
            "goal_status": data.get("goal_status") or ("blocked" if not success else "ok"),
            "blocked_reason": data.get("blocked_reason"),
            "steps": data.get("steps") or [],
            "before": before,
            "after": after,
        }
        result: dict[str, Any] = {
            "success": success,
            "status": status,
            "observation": observation,
            "data": payload,
        }
        if not success and payload["blocked_reason"]:
            result["error"] = {"code": payload["blocked_reason"], "message": observation}
        return result
