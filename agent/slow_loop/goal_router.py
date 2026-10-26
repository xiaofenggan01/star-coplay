"""Deterministic goal router: the fallback planner used when no model is in the loop.

It turns a structured goal (``Acquire(Wood, 10)``, ``WaterCrops``, ``Sleep``)
into a short plan of L3 skill calls, after the same guard checks a normal player
would do: is the goal already met, is it too late, is there enough energy, is it
raining.

This module is intentionally pure: it never touches the game or the context,
only the small ``GoalContext`` surface.  That keeps it unit-testable and lets the
eval harness drive it against the simulator, and later lets the LLM planner share
the same plan shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Item aliases a player might say in either language.
_ITEM_ALIASES = {
    "wood": "Wood",
    "木头": "Wood",
    "木材": "Wood",
    "logs": "Wood",
    "sap": "Sap",
    "树液": "Sap",
}

# Goals this router can plan today.
GATHER_SKILLS = {"Wood": "chop_trees"}

LATE_NIGHT_MINUTES = 2400  # midnight; after this a normal player goes to bed
EARLY_SLEEP_MINUTES = 1200  # 20:00; going to bed before this is suspicious
MIN_STAMINA_FOR_WORK = 20.0
MIN_STAMINA_FOR_WATERING = 10.0


def normalize_item(name: object) -> str:
    """Map player-facing aliases to the canonical item name used by skills."""
    raw = str(name or "").strip()
    return _ITEM_ALIASES.get(raw.lower(), raw)


class GoalContext(Protocol):
    """What the router is allowed to know about the world."""

    @property
    def minutes(self) -> int: ...

    @property
    def stamina(self) -> float: ...

    @property
    def weather(self) -> str: ...

    @property
    def unwatered_crops(self) -> int: ...

    def inventory_count(self, item: str) -> int: ...


@dataclass
class PlanStep:
    skill: str
    params: dict
    reason: str


@dataclass
class GoalPlan:
    goal_type: str
    steps: list[PlanStep] = field(default_factory=list)
    blocked_reason: str | None = None
    satisfied: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "goal_type": self.goal_type,
            "steps": [{"skill": s.skill, "params": s.params, "reason": s.reason} for s in self.steps],
            "blocked_reason": self.blocked_reason,
            "satisfied": self.satisfied,
            "note": self.note,
        }


class GoalRouter:
    def __init__(
        self,
        *,
        late_night_minutes: int = LATE_NIGHT_MINUTES,
        early_sleep_minutes: int = EARLY_SLEEP_MINUTES,
        min_stamina_for_work: float = MIN_STAMINA_FOR_WORK,
        min_stamina_for_watering: float = MIN_STAMINA_FOR_WATERING,
    ):
        self.late_night_minutes = late_night_minutes
        self.early_sleep_minutes = early_sleep_minutes
        self.min_stamina_for_work = min_stamina_for_work
        self.min_stamina_for_watering = min_stamina_for_watering

    def plan(self, goal: dict, ctx: GoalContext) -> GoalPlan:
        goal_type = str(goal.get("type") or "").strip().lower()
        if goal_type in ("acquire", "get", "gather"):
            return self._plan_acquire(goal, ctx)
        if goal_type in ("water", "water_crops", "watercrops"):
            return self._plan_water(goal, ctx)
        if goal_type in ("sleep", "go_bed", "gotobed"):
            return self._plan_sleep(goal, ctx)
        if goal_type in ("composite", "routine"):
            return self._plan_composite(goal, ctx)
        return GoalPlan(goal_type=goal_type or "unknown", blocked_reason="unsupported_goal")

    # ── goal types ───────────────────────────────────────────────────────

    def _plan_acquire(self, goal: dict, ctx: GoalContext) -> GoalPlan:
        item = normalize_item(goal.get("item"))
        raw_qty = goal.get("qty")
        if raw_qty is None or raw_qty == "":
            return GoalPlan(
                "acquire",
                blocked_reason="needs_clarification",
                note=f"how many {item}?",
            )
        try:
            qty = int(raw_qty)
        except (TypeError, ValueError):
            return GoalPlan("acquire", blocked_reason="invalid_qty")
        if qty <= 0:
            return GoalPlan(
                "acquire",
                blocked_reason="needs_clarification",
                note=f"how many {item}?",
            )

        have = ctx.inventory_count(item)
        if have >= qty:
            return GoalPlan(
                "acquire",
                satisfied=True,
                note=f"already have {have}/{qty} {item}",
            )

        if ctx.minutes >= self.late_night_minutes:
            return GoalPlan("acquire", blocked_reason="late_night", note="past midnight")
        if ctx.stamina < self.min_stamina_for_work:
            return GoalPlan(
                "acquire",
                blocked_reason="stamina_low",
                note=f"stamina {ctx.stamina:.0f} < {self.min_stamina_for_work:.0f}",
            )

        skill = GATHER_SKILLS.get(item)
        if skill is None:
            return GoalPlan("acquire", blocked_reason="unsupported_item", note=f"cannot gather {item}")

        need = qty - have
        return GoalPlan(
            "acquire",
            steps=[PlanStep(skill=skill, params={"target_wood": qty}, reason=f"need {need} more {item}")],
            note=f"have {have}/{qty} {item}",
        )

    def _plan_water(self, goal: dict, ctx: GoalContext) -> GoalPlan:
        if ctx.unwatered_crops <= 0:
            return GoalPlan("water_crops", satisfied=True, note="all crops are watered")
        if str(ctx.weather).lower() in ("rain", "rainy", "storm"):
            return GoalPlan("water_crops", blocked_reason="raining", note="nature waters today")
        if ctx.minutes >= self.late_night_minutes:
            return GoalPlan("water_crops", blocked_reason="late_night")
        if ctx.stamina < self.min_stamina_for_watering:
            return GoalPlan(
                "water_crops",
                blocked_reason="stamina_low",
                note=f"stamina {ctx.stamina:.0f}",
            )
        return GoalPlan(
            "water_crops",
            steps=[PlanStep(skill="water_crops", params={}, reason=f"{ctx.unwatered_crops} unwatered crops")],
        )

    def _plan_composite(self, goal: dict, ctx: GoalContext) -> GoalPlan:
        subs = goal.get("steps")
        if not isinstance(subs, list) or not subs:
            return GoalPlan("composite", blocked_reason="empty_composite")

        steps: list[PlanStep] = []
        notes: list[str] = []
        all_satisfied = True
        for index, sub in enumerate(subs):
            if not isinstance(sub, dict):
                return GoalPlan("composite", blocked_reason="invalid_composite_step")
            plan = self.plan(sub, ctx)
            if plan.blocked_reason:
                return GoalPlan(
                    "composite",
                    blocked_reason=plan.blocked_reason,
                    note=f"step {index + 1}: {plan.note or plan.blocked_reason}",
                )
            steps.extend(plan.steps)
            all_satisfied = all_satisfied and (plan.satisfied or not plan.steps)
            if plan.note:
                notes.append(f"step {index + 1}: {plan.note}")

        return GoalPlan(
            "composite",
            steps=steps,
            satisfied=all_satisfied and not steps,
            note="; ".join(notes),
        )

    def _plan_sleep(self, goal: dict, ctx: GoalContext) -> GoalPlan:
        if ctx.minutes < self.early_sleep_minutes:
            return GoalPlan("sleep", blocked_reason="too_early", note=f"time {ctx.minutes}")
        return GoalPlan("sleep", steps=[PlanStep(skill="go_bed", params={}, reason="end the day")])
