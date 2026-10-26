"""LLM planner policy: the model proposes a plan, the harness judges the outcome.

The model only chooses *what to do* (a short list of skill calls, or a refusal).
Execution and post-conditions stay deterministic and are shared with the router,
so an LLM plan is scored by the resulting world state, not by its own claims.

Live use needs an OpenAI-compatible endpoint:
    LLM_RELAY_BASE_URL / LLM_RELAY_API_KEY  (platform relay)
    or OPENAI_BASE_URL / OPENAI_API_KEY
    plus --llm-model (a concrete model name; tier names are not resolved here).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from slow_loop.goal_router import normalize_item

from ..sim_world import SimWorld
from .executor import execute_plan, postcheck, result_payload


# Skills the planner may call, with the parameters it may set.
SKILL_CATALOG: dict[str, dict[str, str]] = {
    "chop_trees": {"target_wood": "int 1-99, how much wood to collect"},
    "water_crops": {},
    "go_bed": {},
    "harvest_crops": {"max_count": "int 1-999", "replant_seed": "optional item name"},
    "ship_items": {"dry_run": "optional bool"},
}

MAX_STEPS = 4

SYSTEM_PROMPT = """You are the planning brain of a Stardew Valley companion.
Given a goal and a world snapshot, decide what to do. Reply with ONE JSON object.

Rules a normal player follows:
- Past midnight (time >= 2400) do not start work; refuse with reason "late_night".
- If stamina < 20, do not start stamina-heavy work; refuse with reason "stamina_low".
- If it is raining, do not water crops; refuse with reason "raining".
- Going to bed before 20:00 (time < 1200) is too early; refuse with reason "too_early".
- If the goal is already satisfied by the inventory/world, reply satisfied.
- If the goal cannot be done with the available skills, refuse with reason "unsupported_item".

The goal may be a composite of several subgoals; plan the steps in order.
If the goal is ambiguous (for example no quantity), ask instead of guessing.

Reply shapes:
{"action":"plan","steps":[{"skill":"chop_trees","params":{"target_wood":10},"reason":"..."}]}
{"action":"satisfied","note":"..."}
{"action":"refuse","reason":"late_night|stamina_low|raining|too_early|unsupported_item","note":"..."}
{"action":"clarify","question":"how many wood do you need?"}

Available skills and parameters:
- chop_trees: target_wood (int, required)
- water_crops: no parameters
- go_bed: no parameters
- harvest_crops: max_count (optional int), replant_seed (optional string)
- ship_items: dry_run (optional bool)

Output JSON only. No prose, no markdown fences."""


@dataclass
class LlmConfig:
    model: str
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 600

    @classmethod
    def from_env(cls, model: str = "") -> "LlmConfig":
        return cls(
            model=model or os.environ.get("LLM_MODEL", ""),
            base_url=os.environ.get("LLM_RELAY_BASE_URL") or os.environ.get("OPENAI_BASE_URL", ""),
            api_key=os.environ.get("LLM_RELAY_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
        )


class PlannerClient(Protocol):
    def complete(self, messages: list[dict[str, str]], config: LlmConfig) -> dict[str, Any]:
        """Return {"text": str, "usage": {...}, "latency_ms": int}."""


class OpenAIPlannerClient:
    """Thin OpenAI-compatible client; imported lazily so eval tests stay offline."""

    def __init__(self, client: Any | None = None):
        self._client = client

    def _ensure(self, config: LlmConfig):
        if self._client is not None:
            return self._client
        from openai import OpenAI  # imported only for live runs

        kwargs: dict[str, Any] = {}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.api_key:
            kwargs["api_key"] = config.api_key
        self._client = OpenAI(**kwargs)
        return self._client

    def complete(self, messages: list[dict[str, str]], config: LlmConfig) -> dict[str, Any]:
        client = self._ensure(config)
        started = time.monotonic()
        request: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        try:
            response = client.chat.completions.create(**request, response_format={"type": "json_object"})
        except Exception:
            # Some relays do not implement response_format; retry plain.
            response = client.chat.completions.create(**request)
        latency_ms = int((time.monotonic() - started) * 1000)
        text = response.choices[0].message.content or ""
        usage = {}
        if getattr(response, "usage", None) is not None:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
        return {"text": text, "usage": usage, "latency_ms": latency_ms}


class ScriptedPlannerClient:
    """Deterministic client for tests: replays a list of raw responses."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], config: LlmConfig) -> dict[str, Any]:
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("scripted planner ran out of responses")
        return {"text": self._responses.pop(0), "usage": {"total_tokens": 42}, "latency_ms": 1}


# ── prompt & parsing ─────────────────────────────────────────────────────


def summarize_world(world: SimWorld) -> dict[str, Any]:
    inventory = {item["internal_name"]: item["stack"] for item in world.player.inventory}
    return {
        "time": world.minutes,
        "day": world.day,
        "weather": world.weather,
        "stamina": world.player.stamina,
        "max_stamina": world.player.max_stamina,
        "inventory": inventory,
        "crops": {
            "total": len([c for c in world.crops.values() if not c.dead]),
            "unwatered": len([c for c in world.crops.values() if not c.dead and not c.watered]),
        },
        "trees": {
            "mature": len([t for t in world.trees.values() if t.mature]),
            "stumps": len([t for t in world.trees.values() if t.stump]),
        },
    }


def build_messages(goal: dict[str, Any], world: SimWorld) -> list[dict[str, str]]:
    user = json.dumps(
        {"goal": goal, "world": summarize_world(world)},
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_plan(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate the model's JSON. Returns (plan, error)."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"
    if not isinstance(payload, dict):
        return None, "top-level value must be an object"

    action = str(payload.get("action") or "").strip().lower()
    if action == "satisfied":
        return {"action": "satisfied", "note": str(payload.get("note") or "")}, None
    if action == "refuse":
        reason = str(payload.get("reason") or "llm_refused")
        return {"action": "refuse", "reason": reason, "note": str(payload.get("note") or "")}, None
    if action == "clarify":
        question = str(payload.get("question") or payload.get("note") or "").strip()
        return {"action": "clarify", "question": question}, None
    if action != "plan":
        return None, f"unknown action {action!r}"

    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "plan.steps must be a non-empty list"
    if len(steps) > MAX_STEPS:
        return None, f"too many steps ({len(steps)} > {MAX_STEPS})"

    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return None, f"step {index} is not an object"
        skill = str(step.get("skill") or "")
        if skill not in SKILL_CATALOG:
            return None, f"step {index}: unknown skill {skill!r}"
        params = step.get("params") or {}
        if not isinstance(params, dict):
            return None, f"step {index}: params must be an object"
        error = _validate_params(skill, params)
        if error:
            return None, f"step {index}: {error}"
        normalized.append({"skill": skill, "params": params, "reason": str(step.get("reason") or "")})
    return {"action": "plan", "steps": normalized}, None


def _validate_params(skill: str, params: dict[str, Any]) -> str | None:
    allowed = SKILL_CATALOG[skill]
    unknown = [key for key in params if key not in allowed]
    if unknown:
        return f"unexpected params {unknown} for {skill}"
    if skill == "chop_trees":
        target = params.get("target_wood")
        if not isinstance(target, int) or isinstance(target, bool) or not 1 <= target <= 99:
            return "chop_trees.target_wood must be an int in 1..99"
    if skill == "harvest_crops" and "max_count" in params:
        value = params["max_count"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 999:
            return "harvest_crops.max_count must be an int in 1..999"
    if skill == "ship_items" and "dry_run" in params and not isinstance(params["dry_run"], bool):
        return "ship_items.dry_run must be a bool"
    return None


# ── policy ───────────────────────────────────────────────────────────────


class LlmPolicy:
    name = "llm"

    def __init__(self, client: PlannerClient | None = None, config: LlmConfig | None = None):
        self.client = client or OpenAIPlannerClient()
        self.config = config or LlmConfig.from_env()

    async def run(self, goal: dict[str, Any], ctx: Any, world: SimWorld) -> dict[str, Any]:
        before = world.snapshot()
        messages = build_messages(goal, world)
        calls = 0
        tokens = 0
        latency = 0
        raw_text = ""
        parsed: dict[str, Any] | None = None
        error: str | None = None

        for attempt in range(2):
            response = self.client.complete(messages, self.config)
            calls += 1
            latency += int(response.get("latency_ms") or 0)
            tokens += int((response.get("usage") or {}).get("total_tokens") or 0)
            raw_text = str(response.get("text") or "")
            parsed, error = parse_plan(raw_text)
            if parsed is not None:
                break
            # One repair round: tell the model exactly what was wrong.
            messages = messages + [
                {"role": "assistant", "content": raw_text},
                {"role": "user", "content": f"Invalid plan: {error}. Reply again with valid JSON only."},
            ]

        llm_meta = {
            "model": self.config.model,
            "calls": calls,
            "tokens": tokens,
            "latency_ms": latency,
            "valid": parsed is not None,
            "raw": raw_text,
            "messages": messages,
        }

        if parsed is None:
            return result_payload(
                goal_type=str(goal.get("type") or ""),
                success=False,
                status="error",
                observation=f"llm produced no valid plan: {error}",
                plan={"raw": raw_text},
                steps=[],
                before=before,
                after=world.snapshot(),
                satisfied=False,
                blocked_reason="invalid_plan",
                extra={"llm": llm_meta, "plan_source": "llm"},
            )

        action = parsed["action"]

        if action == "satisfied":
            satisfied, _status, _observation = postcheck(goal, world, before)
            if satisfied:
                return result_payload(
                    goal_type=str(goal.get("type") or ""),
                    success=True,
                    status="ok",
                    observation=f"llm: already satisfied ({parsed.get('note', '')})",
                    plan=parsed,
                    steps=[],
                    before=before,
                    after=world.snapshot(),
                    satisfied=True,
                    extra={"llm": llm_meta, "plan_source": "llm"},
                )
            return result_payload(
                goal_type=str(goal.get("type") or ""),
                success=False,
                status="partial",
                observation="llm claimed the goal was satisfied, but the world disagrees",
                plan=parsed,
                steps=[],
                before=before,
                after=world.snapshot(),
                satisfied=False,
                blocked_reason="llm_misjudged",
                extra={"llm": llm_meta, "plan_source": "llm"},
            )

        if action == "clarify":
            return result_payload(
                goal_type=str(goal.get("type") or ""),
                success=False,
                status="blocked",
                observation=f"llm needs clarification: {parsed.get('question')}",
                plan=parsed,
                steps=[],
                before=before,
                after=world.snapshot(),
                satisfied=False,
                blocked_reason="needs_clarification",
                extra={"llm": llm_meta, "plan_source": "llm"},
            )

        if action == "refuse":
            reason = str(parsed.get("reason") or "llm_refused")
            return result_payload(
                goal_type=str(goal.get("type") or ""),
                success=False,
                status="blocked",
                observation=f"llm refused: {reason}",
                plan=parsed,
                steps=[],
                before=before,
                after=world.snapshot(),
                satisfied=False,
                blocked_reason=reason,
                extra={"llm": llm_meta, "plan_source": "llm"},
            )

        step_results = await execute_plan(parsed["steps"], ctx, world)
        satisfied, status, observation = postcheck(goal, world, before)
        return result_payload(
            goal_type=str(goal.get("type") or ""),
            success=satisfied or status == "partial",
            status=status,
            observation=observation,
            plan=parsed,
            steps=step_results,
            before=before,
            after=world.snapshot(),
            satisfied=satisfied,
            extra={"llm": llm_meta, "plan_source": "llm"},
        )
