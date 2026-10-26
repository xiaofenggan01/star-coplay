"""Tests for the LLM planner policy, using a scripted client (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parents[1]
for path in (str(REPO_ROOT / "agent"), str(EVAL_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from harness.policies.llm import LlmConfig, LlmPolicy, ScriptedPlannerClient, parse_plan, summarize_world  # noqa: E402
from harness.runner import run_scenario  # noqa: E402


def _spec(name: str) -> dict:
    return json.loads((EVAL_DIR / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))


def _policy(*responses: str) -> LlmPolicy:
    return LlmPolicy(client=ScriptedPlannerClient(list(responses)), config=LlmConfig(model="scripted"))


def test_parse_plan_accepts_a_valid_plan():
    plan, error = parse_plan('{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":5}}]}')
    assert error is None
    assert plan["steps"][0]["skill"] == "chop_trees"


def test_parse_plan_rejects_unknown_skill():
    _plan, error = parse_plan('{"action":"plan","steps":[{"skill":"fly","params":{}}]}')
    assert error and "unknown skill" in error


def test_parse_plan_rejects_bad_params():
    _plan, error = parse_plan('{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":"ten"}}]}')
    assert error and "target_wood" in error


def test_parse_plan_accepts_refusal():
    plan, error = parse_plan('{"action":"refuse","reason":"late_night","note":"go to bed"}')
    assert error is None
    assert plan["action"] == "refuse"


def test_llm_plan_is_executed_and_judged_by_the_world():
    spec = _spec("goal-001-acquire-wood")
    policy = _policy('{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":10}}]}')
    out = run_scenario(spec, policy="llm", llm_policy=policy)
    assert out["status"] == "passed", [c for c in out["checks"] if not c["ok"]]
    assert out["metrics"]["inventory"]["Wood"] >= 10
    assert out["metrics"]["llm_calls"] == 1


def test_llm_refusal_matches_router_semantics():
    spec = _spec("goal-003-late-night")
    policy = _policy('{"action":"refuse","reason":"late_night"}')
    out = run_scenario(spec, policy="llm", llm_policy=policy)
    assert out["status"] == "passed", [c for c in out["checks"] if not c["ok"]]
    assert out["result"]["status"] == "blocked"


def test_llm_satisfied_claim_is_verified_against_the_world():
    spec = _spec("goal-001-acquire-wood")
    policy = _policy('{"action":"satisfied","note":"you already have wood"}')
    out = run_scenario(spec, policy="llm", llm_policy=policy)
    assert out["status"] == "failed"
    assert out["result"]["data"]["blocked_reason"] == "llm_misjudged"


def test_invalid_json_triggers_one_repair_round():
    spec = _spec("goal-001-acquire-wood")
    policy = _policy(
        "not json at all",
        '{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":10}}]}',
    )
    out = run_scenario(spec, policy="llm", llm_policy=policy)
    assert out["status"] == "passed", [c for c in out["checks"] if not c["ok"]]
    assert out["metrics"]["llm_calls"] == 2


def test_world_summary_formats_the_stardew_clock():
    from harness.sim_world import build_world

    world = build_world({"player": {"x": 10, "y": 10}, "start_minutes": 2200})
    summary = summarize_world(world)
    assert summary["time"] == 2200
    assert summary["time_hhmm"] == "22:00"
