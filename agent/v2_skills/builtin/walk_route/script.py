from __future__ import annotations


async def walk_route(ctx, waypoints: list[dict], stop_on_failure: bool = True) -> dict:
    normalized: list[dict] = []
    warnings: list[dict] = []
    for index, waypoint in enumerate(waypoints or []):
        if not isinstance(waypoint, dict):
            return _result(
                success=False,
                status="blocked",
                observation=f"walk_route blocked: waypoint[{index}] is not an object.",
                stopped_reason="invalid_waypoint",
                completed=[],
                failed_waypoint=None,
                warnings=warnings,
                error_code="INVALID_WAYPOINT",
            )
        try:
            map_id = str(waypoint["map_id"]).strip()
            x = int(waypoint["x"])
            y = int(waypoint["y"])
        except Exception:
            return _result(
                success=False,
                status="blocked",
                observation=f"walk_route blocked: waypoint[{index}] is missing map_id/x/y.",
                stopped_reason="invalid_waypoint",
                completed=[],
                failed_waypoint=None,
                warnings=warnings,
                error_code="INVALID_WAYPOINT",
            )
        if not map_id:
            return _result(
                success=False,
                status="blocked",
                observation=f"walk_route blocked: waypoint[{index}] has an empty map_id.",
                stopped_reason="invalid_waypoint",
                completed=[],
                failed_waypoint=None,
                warnings=warnings,
                error_code="INVALID_WAYPOINT",
            )
        normalized.append({"map_id": map_id, "x": x, "y": y})

    if not normalized:
        return _result(
            success=False,
            status="blocked",
            observation="walk_route blocked: no waypoints were provided.",
            stopped_reason="empty_route",
            completed=[],
            failed_waypoint=None,
            warnings=warnings,
            error_code="EMPTY_ROUTE",
        )

    completed: list[dict] = []
    failed_waypoint: dict | None = None

    for waypoint in normalized:
        ctx.check_cancelled()
        result = await ctx.move_to(waypoint["x"], waypoint["y"], location=waypoint["map_id"])
        if result.get("success"):
            completed.append(waypoint)
            continue
        failed_waypoint = {
            **waypoint,
            "error_code": (result.get("error") or {}).get("code"),
            "message": result.get("observation") or (result.get("error") or {}).get("message"),
        }
        if stop_on_failure:
            break
        warnings.append(
            {
                "code": "WAYPOINT_FAILED",
                "message": "A waypoint failed but the route continued because stop_on_failure=false.",
                "waypoint": failed_waypoint,
            }
        )

    if failed_waypoint is None:
        return _result(
            success=True,
            status="ok",
            observation=f"walk_route completed: visited {len(completed)}/{len(normalized)} waypoints.",
            stopped_reason="all_done",
            completed=completed,
            failed_waypoint=None,
            warnings=warnings,
        )

    success = len(completed) > 0
    status = "partial" if success else "failed"
    return _result(
        success=success,
        status=status,
        observation=(
            f"walk_route {status}: visited {len(completed)}/{len(normalized)} waypoints "
            f"before failing at {failed_waypoint['map_id']}:{failed_waypoint['x']},{failed_waypoint['y']}."
        ),
        stopped_reason="waypoint_failed",
        completed=completed,
        failed_waypoint=failed_waypoint,
        warnings=warnings,
        error_code=None if success else "WAYPOINT_FAILED",
    )


def _result(
    *,
    success: bool,
    status: str,
    observation: str,
    stopped_reason: str,
    completed: list[dict],
    failed_waypoint: dict | None,
    warnings: list[dict],
    error_code: str | None = None,
) -> dict:
    payload = {
        "success": success,
        "status": status,
        "observation": observation,
        "data": {
            "completed": completed,
            "completed_count": len(completed),
            "failed_waypoint": failed_waypoint,
            "stopped_reason": stopped_reason,
            "warnings": warnings,
        },
    }
    if error_code:
        payload["error"] = {"code": error_code}
    return payload
