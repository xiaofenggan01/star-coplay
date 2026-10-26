"""L3 find_interactions skill entrypoint."""

from __future__ import annotations

from typing import Any

from l3_query.interactions import find_interactions as find_current_interactions


async def find_interactions(ctx: Any, radius: int = 8, limit: int = 20, offset: int = 0) -> dict:
    result = await find_current_interactions(ctx, radius=radius, limit=limit, offset=offset)
    return {
        "success": bool(result.get("success", True)),
        "status": "ok" if result.get("success", True) else "failed",
        "observation": result["description"],
        "data": {
            **result["data"],
            "items": result["data"].get("candidates", []),
        },
    }
