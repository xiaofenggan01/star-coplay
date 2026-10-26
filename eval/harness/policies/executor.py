"""Shared plan execution and post-condition checks for goal policies.

Both the deterministic router and the LLM planner produce a list of skill
steps; this module runs them and verifies the goal actually moved. Keeping it
shared means an LLM plan is judged by the same physical outcome as a router
plan, not by what the model says it did.
"""

from __future__ import annotations

import asyncio
from typing import Any

from slow_loop.goal_router import normalize_item

from ..sim_world import SimWorld
from ..skills import load_skill


STEP_TIMEOUT_S = 20.0


async def execute_plan(steps: list[dict[str, Any]], ctx: Any, world: SimWorld) -> list[dict[str, Any]]:
    """Run plan steps in order, stopping at the first failing step."""
    results: list[dict[str, Any]] = []
    for step in steps:
        skill_name = str(step.get("skill") or "")
        params = dict(step.get("params") or {})
        try:
            skill = load_skill(skill_name)
            outcome = await asyncio.wait_for(skill(ctx, **params), timeout=STEP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - surface as a failed step
            outcome = {
                "success": False,
                "status": "error",
                "data": {},
                "observation": f"{type(exc).__name__}: {exc}",
            }
        results.append({"skill": skill_name, "params": params, "result": outcome})
        if not outcome.get("success"):
            break
    return results


def postcheck(goal: dict[str, Any], world: SimWorld, before: dict[str, Any]) -> tuple[bool, str, str]:
    """Compare the world after the plan against the goal."""
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


def result_payload(
    *,
    goal_type: str,
    success: bool,
    status: str,
    observation: str,
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    satisfied: bool,
    blocked_reason: str | None = None,
    goal_status: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "goal": goal_type,
        "plan": plan,
        "goal_satisfied": satisfied,
        "goal_status": goal_status or ("satisfied" if satisfied else ("blocked" if blocked_reason else status)),
        "blocked_reason": blocked_reason,
        "steps": steps,
        "before": before,
        "after": after,
    }
    if extra:
        data.update(extra)
    payload: dict[str, Any] = {
        "success": success,
        "status": status,
        "observation": observation,
        "data": data,
    }
    if blocked_reason:
        payload["error"] = {"code": blocked_reason, "message": observation}
    return payload
