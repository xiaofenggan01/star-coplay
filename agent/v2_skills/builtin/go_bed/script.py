"""go_bed - walk home and sleep without teleporting."""

from __future__ import annotations

from typing import Any


async def go_bed(ctx) -> dict[str, Any]:
    """Use the protocol-native go_to_bed L2 flow.

    The Mod handler owns the real game operations: scan sleepable beds, navigate
    with go_to/pathfinding, trigger the bed interaction, confirm the sleep prompt,
    and drain the end-of-day UI. This L3 wrapper intentionally does not use the
    legacy go_bed compound handler because that path teleports/repositions.
    """
    result = await ctx.go_to_bed(timeout_frames=9000)

    success = bool(result.get("success", False))
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    protocol_data = data.get("protocol_data") if isinstance(data, dict) else {}
    if not isinstance(protocol_data, dict):
        protocol_data = {}

    used_tp = bool(protocol_data.get("used_tp", False))
    day_advanced = bool(protocol_data.get("day_advanced", False))
    sleep_confirmed = bool(protocol_data.get("sleep_confirmed", False))
    sleep_prompt_seen = bool(protocol_data.get("sleep_prompt_seen", False))
    decision_required = bool(protocol_data.get("decision_required", False))
    phase = str(protocol_data.get("phase") or "")
    bed_position = protocol_data.get("bed_position")
    if not isinstance(bed_position, dict):
        bed_position = {}

    normalized_data = {
        "used_tp": used_tp,
        "day_advanced": day_advanced,
        "sleep_confirmed": sleep_confirmed,
        "sleep_prompt_seen": sleep_prompt_seen,
        "decision_required": decision_required,
        "phase": phase,
        "bed_position": bed_position,
        "protocol": data,
    }

    if success and not used_tp:
        if decision_required:
            return {
                "success": True,
                "status": "partial",
                "observation": "已走到床边并确认睡觉，日终菜单需要继续选择",
                "data": normalized_data,
            }
        return {
            "success": True,
            "status": "ok",
            "observation": "已通过寻路回到床边并进入睡觉流程",
            "data": normalized_data,
        }

    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    message = (
        str(error.get("message") or "")
        or result.get("observation")
        or str(result.get("message") or "")
        or "go_to_bed failed"
    )
    if used_tp:
        message = "go_to_bed used teleport unexpectedly"
    return {
        "success": False,
        "status": "failed",
        "observation": f"回床睡觉失败：{message}",
        "data": normalized_data,
        "error": {
            "code": "USED_TP" if used_tp else error.get("code", "GO_TO_BED_FAILED"),
            "message": message,
        },
    }
