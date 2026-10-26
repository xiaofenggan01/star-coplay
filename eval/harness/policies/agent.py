"""Lightweight tool-calling agent policy.

One decision per turn: run a skill, look at the result and the updated world,
then decide again.  This is the seam for testing "see a partial result and
continue" behaviour without wiring the full DeliberativeLoop.

The model still only chooses skills; execution and post-conditions remain
deterministic and shared with the router.
"""

from __future__ import annotations

import json
from typing import Any

from ..sim_world import SimWorld
from .executor import execute_plan, postcheck, result_payload
from .llm import (
    SYSTEM_PROMPT,
    LlmConfig,
    OpenAIPlannerClient,
    PlannerClient,
    parse_plan,
    summarize_world,
)


AGENT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

You act one step at a time. After each step you will receive the step result and
the updated world; then decide the next step. You have at most {max_turns} steps.
Do not repeat a step that already succeeded."""

MAX_TURNS = 4
REPAIR_ROUNDS = 1


def build_agent_messages(
    goal: dict[str, Any],
    world: SimWorld,
    history: list[dict[str, Any]],
    *,
    max_turns: int,
) -> list[dict[str, str]]:
    system = AGENT_SYSTEM_PROMPT.replace("{max_turns}", str(max_turns))
    user = json.dumps(
        {
            "goal": goal,
            "world": summarize_world(world),
            "history": history,
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def summarize_step(skill: str, result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") or {}
    keep = {}
    for key in (
        "trees_chopped",
        "wood_gained",
        "watered",
        "remaining_unwatered",
        "stopped_reason",
        "day_advanced",
    ):
        if key in data:
            keep[key] = data[key]
    return {
        "skill": skill,
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "observation": result.get("observation"),
        "data": keep,
    }


class AgentPolicy:
    name = "agent"

    def __init__(
        self,
        client: PlannerClient | None = None,
        config: LlmConfig | None = None,
        *,
        max_turns: int = MAX_TURNS,
    ):
        self.client = client or OpenAIPlannerClient()
        self.config = config or LlmConfig.from_env()
        self.max_turns = max_turns

    async def run(self, goal: dict[str, Any], ctx: Any, world: SimWorld) -> dict[str, Any]:
        before = world.snapshot()
        history: list[dict[str, Any]] = []
        calls = 0
        tokens = 0
        latency = 0
        valid = True
        raws: list[str] = []
        turns = 0

        for turn in range(1, self.max_turns + 1):
            satisfied, status, observation = postcheck(goal, world, before)
            if satisfied:
                break

            messages = build_agent_messages(goal, world, history, max_turns=self.max_turns)
            parsed: dict[str, Any] | None = None
            error: str | None = None
            for _attempt in range(REPAIR_ROUNDS + 1):
                response = self.client.complete(messages, self.config)
                calls += 1
                latency += int(response.get("latency_ms") or 0)
                tokens += int((response.get("usage") or {}).get("total_tokens") or 0)
                raw = str(response.get("text") or "")
                raws.append(raw)
                parsed, error = parse_plan(raw)
                if parsed is not None:
                    break
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Invalid plan: {error}. Reply again with valid JSON only."},
                ]

            if parsed is None:
                valid = False
                return self._result(
                    goal, world, before, history,
                    success=False, status="error", observation=f"agent produced no valid plan: {error}",
                    blocked_reason="invalid_plan", calls=calls, tokens=tokens,
                    latency=latency, valid=valid, raws=raws, turns=turn,
                )

            action = parsed["action"]

            if action == "satisfied":
                satisfied, _status, _obs = postcheck(goal, world, before)
                if satisfied:
                    return self._result(
                        goal, world, before, history,
                        success=True, status="ok", observation="agent: goal already satisfied",
                        satisfied=True, calls=calls, tokens=tokens, latency=latency,
                        valid=valid, raws=raws, turns=turn,
                    )
                return self._result(
                    goal, world, before, history,
                    success=False, status="partial",
                    observation="agent claimed done, but the world disagrees",
                    blocked_reason="llm_misjudged", calls=calls, tokens=tokens,
                    latency=latency, valid=valid, raws=raws, turns=turn,
                )

            if action == "refuse":
                return self._result(
                    goal, world, before, history,
                    success=False, status="blocked",
                    observation=f"agent refused: {parsed.get('reason')}",
                    blocked_reason=str(parsed.get("reason") or "llm_refused"),
                    calls=calls, tokens=tokens, latency=latency, valid=valid,
                    raws=raws, turns=turn,
                )

            if action == "clarify":
                return self._result(
                    goal, world, before, history,
                    success=False, status="blocked",
                    observation=f"agent needs clarification: {parsed.get('question')}",
                    blocked_reason="needs_clarification",
                    calls=calls, tokens=tokens, latency=latency, valid=valid,
                    raws=raws, turns=turn,
                )

            executed = await execute_plan(parsed["steps"], ctx, world)
            for step in executed:
                history.append(summarize_step(step["skill"], step["result"]))
            turns = turn

        satisfied, status, observation = postcheck(goal, world, before)
        return self._result(
            goal, world, before, history,
            success=satisfied or status == "partial",
            status=status, observation=observation, satisfied=satisfied,
            calls=calls, tokens=tokens, latency=latency, valid=valid,
            raws=raws, turns=turns or self.max_turns,
        )

    # ── result shaping ───────────────────────────────────────────────────

    def _result(
        self,
        goal: dict[str, Any],
        world: SimWorld,
        before: dict[str, Any],
        history: list[dict[str, Any]],
        *,
        success: bool,
        status: str,
        observation: str,
        satisfied: bool = False,
        blocked_reason: str | None = None,
        calls: int = 0,
        tokens: int = 0,
        latency: int = 0,
        valid: bool = True,
        raws: list[str] | None = None,
        turns: int = 0,
    ) -> dict[str, Any]:
        return result_payload(
            goal_type=str(goal.get("type") or ""),
            success=success,
            status=status,
            observation=observation,
            plan={"turns": turns, "history": history},
            steps=history,
            before=before,
            after=world.snapshot(),
            satisfied=satisfied,
            blocked_reason=blocked_reason,
            extra={
                "plan_source": "agent",
                "llm": {
                    "model": self.config.model,
                    "calls": calls,
                    "tokens": tokens,
                    "latency_ms": latency,
                    "valid": valid,
                    "turns": turns,
                    "raw": raws or [],
                },
            },
        )
