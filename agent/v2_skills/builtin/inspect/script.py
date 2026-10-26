"""L3 inspect skill entrypoint."""

from __future__ import annotations

from typing import Any

from l3_query.inspect import inspect_refs


async def inspect(
    ctx: Any,
    refs: list[str] | None = None,
    ref_id: str = "",
    include_debug: bool = False,
) -> dict:
    """Inspect opaque refs by delegating to query_inspect_refs."""
    ref_list = [ref for ref in (refs or []) if isinstance(ref, str) and ref.strip()]
    if not ref_list and ref_id.strip():
        ref_list = [ref_id.strip()]
    if not ref_list:
        return {
            "success": False,
            "status": "failed",
            "observation": "inspect 需要 refs 或 ref_id，且必须来自前序 observe/query 结果。",
            "error": {
                "code": "INVALID_PARAMS",
                "message": "refs/ref_id is required",
            },
        }

    result = await inspect_refs(ctx, ref_list, include_debug=include_debug)
    return {
        "success": bool(result.get("success")),
        "status": result.get("status") or ("ok" if result.get("success") else "failed"),
        "observation": result["description"],
        "data": result["data"],
        **({"error": result["error"]} if result.get("error") else {}),
    }
