"""Tests for the evaluation harness itself."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EVAL_DIR.parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from harness.assertions import evaluate  # noqa: E402
from harness.runner import run_scenario  # noqa: E402
from harness.sim_world import Player, SimWorld, Tree, build_world  # noqa: E402


def _axe_world(**kwargs) -> SimWorld:
    config = {
        "player": {"x": 10, "y": 10, "stamina": 270, "axe_level": 0},
        "inventory": [{"item": "Axe", "qty": 1, "slot": 1}],
        "trees": [{"x": 12, "y": 10, "drops": [{"item": "Wood", "qty": 5}]}],
    }
    config.update(kwargs)
    return build_world(config)


def _equip_axe(world: SimWorld) -> None:
    ok, data, error, _ = world.apply("equip", {"slot": 1})
    assert ok, (data, error)


def _stand_next_to_tree(world: SimWorld) -> None:
    ok, data, error, _ = world.apply("move_to", {"mode": "adjacent", "x": 12, "y": 10, "adjacent_side": "left"})
    assert ok, (data, error)


def test_tree_needs_ten_hits_and_leaves_a_stump():
    world = _axe_world()
    _equip_axe(world)
    _stand_next_to_tree(world)

    for _ in range(9):
        ok, _, error, _ = world.apply("use_tool", {"x": 12, "y": 10, "charge_level": 0})
        assert ok, error
        assert not world.tree_at(12, 10).stump

    ok, _, error, _ = world.apply("use_tool", {"x": 12, "y": 10, "charge_level": 0})
    assert ok, error
    tree = world.tree_at(12, 10)
    assert tree is not None and tree.stump
    assert world.trees_felled == 1
    assert world.loose_items, "felling a tree must spawn drops"


def test_use_tool_requires_equipped_axe():
    world = _axe_world()
    ok, _, error, _ = world.apply("use_tool", {"x": 12, "y": 10})
    assert not ok
    assert error == "NO_TOOL"


def test_drops_are_picked_up_on_interact():
    world = _axe_world()
    _equip_axe(world)
    _stand_next_to_tree(world)
    for _ in range(10):
        world.apply("use_tool", {"x": 12, "y": 10})

    item_pos = next(iter(world.loose_items))
    world.player.x, world.player.y = item_pos[0] - 1, item_pos[1]
    ok, _, error, _ = world.apply("interact", {"x": item_pos[0], "y": item_pos[1]})
    assert ok, error
    assert world.player.count("Wood") >= 1
    assert item_pos not in world.loose_items


def test_assertion_operators():
    result = {"success": True, "status": "ok", "data": {"wood_gained": 5}}
    metrics = {"stumps": 1, "inventory": {"Wood": 5}}
    checks = evaluate(
        {
            "success": True,
            "status": "ok",
            "data": {"wood_gained": {"min": 3, "max": 6}},
            "world": {"stumps": {"equals": 1}, "inventory.Wood": {"min": 5}},
        },
        result=result,
        metrics=metrics,
        commands=[],
    )
    assert all(c.ok for c in checks), [c.to_dict() for c in checks if not c.ok]


def test_basic_scenario_passes():
    spec = json.loads((EVAL_DIR / "scenarios" / "chop-001-basic.json").read_text(encoding="utf-8"))
    out = run_scenario(spec)
    assert out["status"] == "passed", out["checks"]
    assert out["metrics"]["inventory"]["Wood"] >= 5


def test_former_gaps_are_now_plain_passes():
    # chop-011 used to be a known gap; the skill now short-circuits when the
    # inventory already holds the requested wood.
    spec = json.loads((EVAL_DIR / "scenarios" / "chop-011-wood-already-enough.json").read_text(encoding="utf-8"))
    out = run_scenario(spec)
    assert out["status"] == "passed", out["checks"]
    assert out["metrics"]["use_tool_calls"] == 0


def test_planned_scenario_is_skipped():
    spec = json.loads((EVAL_DIR / "scenarios" / "planned-001-evening-watering.json").read_text(encoding="utf-8"))
    out = run_scenario(spec)
    assert out["status"] == "skipped"


def test_empty_scenario_dir_fails_loudly(tmp_path):
    """A CI run that evaluates nothing must not look green."""
    import run_eval

    assert run_eval.main(["--scenarios", str(tmp_path)]) == 2
