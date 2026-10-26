"""Unit tests for the deterministic goal router."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from slow_loop.goal_router import GoalRouter, normalize_item  # noqa: E402


class StubContext:
    def __init__(self, *, minutes=900, stamina=270.0, weather="sunny", unwatered=0, inventory=None):
        self._minutes = minutes
        self._stamina = stamina
        self._weather = weather
        self._unwatered = unwatered
        self._inventory = inventory or {}

    @property
    def minutes(self):
        return self._minutes

    @property
    def stamina(self):
        return self._stamina

    @property
    def weather(self):
        return self._weather

    @property
    def unwatered_crops(self):
        return self._unwatered

    def inventory_count(self, item):
        return self._inventory.get(item, 0)


def test_normalize_item_aliases():
    assert normalize_item("wood") == "Wood"
    assert normalize_item("木头") == "Wood"
    assert normalize_item("Iridium Bar") == "Iridium Bar"


def test_acquire_plans_remaining_quantity():
    plan = GoalRouter().plan({"type": "acquire", "item": "wood", "qty": 10}, StubContext(inventory={"Wood": 4}))
    assert plan.blocked_reason is None
    assert len(plan.steps) == 1
    assert plan.steps[0].skill == "chop_trees"
    assert plan.steps[0].params == {"target_wood": 6}


def test_acquire_already_satisfied_makes_no_plan():
    plan = GoalRouter().plan({"type": "acquire", "item": "wood", "qty": 10}, StubContext(inventory={"Wood": 20}))
    assert plan.satisfied is True
    assert plan.steps == []


def test_acquire_blocked_late_at_night():
    plan = GoalRouter().plan({"type": "acquire", "item": "wood", "qty": 10}, StubContext(minutes=2500))
    assert plan.blocked_reason == "late_night"


def test_acquire_blocked_when_stamina_is_low():
    plan = GoalRouter().plan({"type": "acquire", "item": "wood", "qty": 10}, StubContext(stamina=12))
    assert plan.blocked_reason == "stamina_low"


def test_acquire_unsupported_item_is_reported():
    plan = GoalRouter().plan({"type": "acquire", "item": "Iridium Bar", "qty": 1}, StubContext())
    assert plan.blocked_reason == "unsupported_item"


def test_watering_is_skipped_when_raining():
    plan = GoalRouter().plan({"type": "water_crops"}, StubContext(weather="rain", unwatered=3))
    assert plan.blocked_reason == "raining"


def test_watering_satisfied_when_nothing_to_water():
    plan = GoalRouter().plan({"type": "water_crops"}, StubContext(unwatered=0))
    assert plan.satisfied is True


def test_watering_plans_the_skill_when_needed():
    plan = GoalRouter().plan({"type": "water_crops"}, StubContext(unwatered=2))
    assert plan.blocked_reason is None
    assert plan.steps[0].skill == "water_crops"


def test_sleep_refused_before_evening():
    plan = GoalRouter().plan({"type": "sleep"}, StubContext(minutes=900))
    assert plan.blocked_reason == "too_early"


def test_sleep_allowed_in_the_evening():
    plan = GoalRouter().plan({"type": "sleep"}, StubContext(minutes=2200))
    assert plan.blocked_reason is None
    assert plan.steps[0].skill == "go_bed"


def test_unknown_goal_type_is_rejected():
    plan = GoalRouter().plan({"type": "fly_to_moon"}, StubContext())
    assert plan.blocked_reason == "unsupported_goal"
