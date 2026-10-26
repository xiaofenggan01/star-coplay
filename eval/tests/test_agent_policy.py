"""Tests for the tool-calling agent policy (scripted client, no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parents[1]
for path in (str(REPO_ROOT / "agent"), str(EVAL_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from harness.policies.agent import AgentPolicy  # noqa: E402
from harness.policies.llm import LlmConfig, ScriptedPlannerClient  # noqa: E402
from harness.runner import run_scenario  # noqa: E402


def _spec(name: str) -> dict:
    return json.loads((EVAL_DIR / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))


def _policy(*responses: str, max_turns: int = 4) -> AgentPolicy:
    return AgentPolicy(
        client=ScriptedPlannerClient(list(responses)),
        config=LlmConfig(model="scripted"),
        max_turns=max_turns,
    )


def test_agent_continues_after_a_partial_step():
    spec = _spec("agent-001-replan")
    policy = _policy(
        '{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":5}}]}',
        '{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":10}}]}',
    )
    out = run_scenario(spec, policy="agent", llm_policy=policy)
    assert out["status"] == "passed", [c for c in out["checks"] if not c["ok"]]
    assert out["metrics"]["inventory"]["Wood"] >= 10
    assert out["metrics"]["llm_calls"] == 2


def test_agent_refusal_is_reported():
    spec = _spec("goal-003-late-night")
    policy = _policy('{"action":"refuse","reason":"late_night"}')
    out = run_scenario(spec, policy="agent", llm_policy=policy)
    assert out["status"] == "passed", [c for c in out["checks"] if not c["ok"]]
    assert out["result"]["status"] == "blocked"


def test_agent_false_satisfied_claim_is_caught():
    spec = _spec("goal-001-acquire-wood")
    policy = _policy('{"action":"satisfied","note":"done"}')
    out = run_scenario(spec, policy="agent", llm_policy=policy)
    assert out["status"] == "failed"
    assert out["result"]["data"]["blocked_reason"] == "llm_misjudged"


def test_agent_stops_at_max_turns_with_partial_result():
    spec = _spec("agent-001-replan")
    policy = _policy(
        '{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":5}}]}',
        '{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":5}}]}',
        max_turns=2,
    )
    out = run_scenario(spec, policy="agent", llm_policy=policy)
    # Two undershooting turns: wood is 5/10, so the goal is partial, not passed.
    assert out["result"]["data"]["goal_satisfied"] is False
    assert out["result"]["status"] == "partial"
