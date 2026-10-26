"""L3 search_facts skill entrypoint."""

from __future__ import annotations

from typing import Any

from l3_query.search import search_current_view


async def search_facts(
    ctx: Any,
    fact_type: str,
    filters: dict | None = None,
    sort: str = "",
    limit: int = 20,
    offset: int = 0,
    radius: int = 8,
) -> dict:
    result = await search_current_view(
        ctx,
        fact_type=fact_type,
        filters=filters or {},
        sort=sort or None,
        limit=limit,
        offset=offset,
        radius=radius,
    )
    return {
        "success": bool(result["success"]),
        "status": "ok" if result["success"] else "failed",
        "observation": result["description"],
        "data": result["data"],
    }
