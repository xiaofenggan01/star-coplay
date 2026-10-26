from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / "platform/backend/modules/mcp/skills/builtin"


def _program(skill_id: str):
    path = SKILLS / skill_id / "implementation.py"
    spec = importlib.util.spec_from_file_location(f"{skill_id}_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


@pytest.mark.asyncio
async def test_search_wiki_returns_bounded_results_without_game_capabilities():
    result = await _program("stardew.knowledge.search_wiki")(
        None, {"query": "spring crops", "limit": 3},
    )

    assert result["status"] == "completed"
    assert "Search results" in result["content"]
    assert result["result_limit"] == 3
    assert len(result["content"]) <= 4096


@pytest.mark.asyncio
async def test_read_wiki_honors_per_call_character_limit():
    result = await _program("stardew.knowledge.read_wiki")(
        None, {"page": "Villagers", "max_chars": 1200},
    )

    assert result["status"] == "completed"
    assert result["page"] == "Villagers"
    assert result["max_chars"] == 1200
    assert len(result["content"]) <= 1200
