"""Scenario runner.

Runs one scenario end-to-end: build the simulated world, wire a SimBridge into
the production ``SkillContext``, call the real L3 skill, then evaluate the
expectations.  Nothing here depends on a live game, a real model, or network
access.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from .assertions import Check, evaluate
from .bootstrap import bootstrap as _bootstrap

_bootstrap()

from v2_skills.context import SkillContext  # noqa: E402

from .sim_bridge import SimBridge  # noqa: E402
from .sim_world import build_world  # noqa: E402


SKILL_TIMEOUT_S = 20.0


def load_skill(name: str):
    module = importlib.import_module(f"v2_skills.builtin.{name}.script")
    func = getattr(module, name, None)
    if func is None:
        raise AttributeError(f"skill module {module.__name__} has no callable {name!r}")
    return func


def _game_state_stub(world) -> Path:
    path = Path(tempfile.mkstemp(prefix="eval-state-", suffix=".json")[1])
    path.write_text(
        json.dumps(
            {
                "player": {
                    "energy": {"current": world.player.stamina, "max": world.player.max_stamina},
                    "gold": 0,
                    "location": world.map_name,
                    "tile": {"x": world.player.x, "y": world.player.y},
                    "equipped_item": {"name": world.player.tool or ""},
                },
                "world": {"time": world.minutes, "day": 1, "season": "spring", "day_of_week": "Mon"},
            }
        ),
        encoding="utf-8",
    )
    return path


def build_metrics(world, bridge: SimBridge, result: dict[str, Any]) -> dict[str, Any]:
    snap = world.snapshot()
    counts = bridge.capability_counts()
    metrics: dict[str, Any] = {
        "stumps": snap["stumps"],
        "stumps_cleared": snap["stumps_cleared"],
        "trees_felled": snap["trees_felled"],
        "mature_trees_left": snap["mature_trees_left"],
        "loose_items_left": snap["loose_items_left"],
        "inventory": snap["inventory"],
        "energy_spent": snap["energy_spent"],
        "game_minutes": snap["game_minutes"],
        "player_x": snap["player"]["x"],
        "player_y": snap["player"]["y"],
        "commands_total": len(bridge.commands),
        "failed_commands": len(bridge.failed_commands()),
        "use_tool_calls": counts.get("use_tool", 0),
        "move_calls": counts.get("move_to", 0),
        "command_counts": counts,
        "result_status": result.get("status"),
        "result_success": result.get("success"),
    }
    return metrics


def run_scenario(spec: dict[str, Any], *, verbose: bool = False) -> dict[str, Any]:
    scenario_id = str(spec.get("id") or "<unnamed>")
    status = str(spec.get("status") or "runnable")
    started = time.monotonic()

    if status != "runnable":
        return {
            "id": scenario_id,
            "title": spec.get("title", ""),
            "status": "skipped",
            "skill": spec.get("skill"),
            "tags": spec.get("tags") or [],
            "reason": f"scenario status is {status!r}",
            "checks": [],
            "metrics": {},
            "duration_ms": 0,
        }

    world = build_world(spec.get("world") or {})
    bridge = SimBridge(world)
    ctx = SkillContext(bridge, _game_state_stub(world))
    state_path = ctx._game_state_path

    error: str | None = None
    trace: str | None = None
    try:
        skill = load_skill(str(spec["skill"]))
        result = asyncio.run(asyncio.wait_for(skill(ctx, **(spec.get("params") or {})), timeout=SKILL_TIMEOUT_S))
        if not isinstance(result, dict):
            result = {"success": False, "status": "error", "data": {"raw": repr(result)}}
    except asyncio.TimeoutError:
        result = {"success": False, "status": "timeout", "data": {}}
        error = f"skill timed out after {SKILL_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001 - eval must capture everything
        result = {"success": False, "status": "error", "data": {}}
        error = f"{type(exc).__name__}: {exc}"
        trace = traceback.format_exc() if verbose else None

    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass

    metrics = build_metrics(world, bridge, result)
    checks: list[Check] = evaluate(
        spec.get("expect") or {},
        result=result,
        metrics=metrics,
        commands=bridge.commands,
    )

    failed = [c for c in checks if not c.ok]
    known_gap = bool((spec.get("expect") or {}).get("known_gap"))

    if error:
        outcome = "error"
    elif not checks:
        outcome = "passed"
    elif not failed:
        outcome = "passed"
    elif known_gap:
        outcome = "gap"
    else:
        outcome = "failed"

    return {
        "id": scenario_id,
        "title": spec.get("title", ""),
        "status": outcome,
        "skill": spec.get("skill"),
        "tags": spec.get("tags") or [],
        "checks": [c.to_dict() for c in checks],
        "metrics": metrics,
        "result": {
            "success": result.get("success"),
            "status": result.get("status"),
            "observation": result.get("observation"),
            "data": result.get("data"),
        },
        "commands": bridge.commands,
        "error": error,
        "trace": trace,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
