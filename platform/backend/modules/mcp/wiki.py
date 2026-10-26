"""Bounded, read-only access to the repository Stardew Valley Wiki."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from agent.knowledge import game_knowledge
from agent.knowledge.game_knowledge import GameKnowledge


SEARCH_MAX_CHARS = 4096
READ_MAX_CHARS = 6000


@lru_cache(maxsize=1)
def _knowledge() -> GameKnowledge:
    wiki_dir = Path(game_knowledge.__file__).resolve().parent / "wiki"
    knowledge = GameKnowledge(wiki_dir)
    knowledge.load()
    return knowledge


def _bounded(content: str, max_chars: int) -> tuple[str, bool]:
    if len(content) <= max_chars:
        return content, False
    suffix = "\n\n[Result truncated. Narrow the query or request one section.]"
    return content[:max(0, max_chars - len(suffix))] + suffix, True


def search_wiki(query: str, limit: int = 8) -> dict[str, object]:
    result_limit = max(1, min(int(limit), 8))
    content, truncated = _bounded(
        _knowledge().search(query, limit=result_limit), SEARCH_MAX_CHARS,
    )
    return {
        "status": "completed",
        "content": content,
        "result_limit": result_limit,
        "truncated": truncated,
    }


def read_wiki(
    page: str,
    section: str | None = None,
    max_chars: int = READ_MAX_CHARS,
) -> dict[str, object]:
    result_max_chars = max(1000, min(int(max_chars), READ_MAX_CHARS))
    content, truncated = _bounded(
        _knowledge().read_page(page, section), result_max_chars,
    )
    return {
        "status": "completed",
        "page": page,
        "section": section or "",
        "content": content,
        "max_chars": result_max_chars,
        "truncated": truncated,
    }
