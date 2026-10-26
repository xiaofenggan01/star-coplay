#!/usr/bin/env python3
"""Probe L3 current-view interactions through the local proto-native WS bridge.

Default mode is read-only: it calls the real L3 ``find_interactions`` helper
against /tmp/ws_protocol_cmd.json and prints the resulting JSON. Use
``--execute-first`` only for a single, deliberate E2E step after reviewing the
printed candidate in read-only mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "agent"
sys.path.insert(0, str(AGENT_ROOT))

from l3_query.interactions import find_interactions  # noqa: E402


CMD_FILE = Path("/tmp/ws_protocol_cmd.json")
RESULT_FILE = Path("/tmp/ws_protocol_result.json")


class ProtocolFileCtx:
    def __init__(self, *, query_timeout_ms: int) -> None:
        self.query_timeout_ms = query_timeout_ms

    async def query(self, capability: str, params: dict | None = None) -> dict:
        result = await send_protocol(capability, params or {}, timeout_ms=self.query_timeout_ms)
        if not result.get("success"):
            raise RuntimeError(result.get("error") or result)
        return {"protocol_data": result.get("data") or {}}


async def send_protocol(
    capability: str,
    params: dict[str, Any],
    *,
    timeout_ms: int = 30000,
) -> dict:
    if RESULT_FILE.exists():
        RESULT_FILE.unlink()
    payload = {
        "capability": capability,
        "params": params,
        "timeout_ms": timeout_ms,
        "source": "diagnostic",
    }
    CMD_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    deadline = time.monotonic() + (timeout_ms / 1000.0) + 5
    while time.monotonic() < deadline:
        if RESULT_FILE.exists() and RESULT_FILE.stat().st_size > 0:
            return json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        await asyncio.sleep(0.1)
    return {"success": False, "error": {"code": "TIMEOUT", "message": capability}}


def protocol_params_for_tool(tool: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if tool == "move_to":
        params = dict(args)
        mode = str(params.get("mode") or "")
        if mode == "exact":
            params["mode"] = "MOVE_TO_EXACT"
        elif mode == "adjacent":
            params["mode"] = "MOVE_TO_ADJACENT"
        elif mode == "edge_warp":
            params["mode"] = "MOVE_TO_EDGE_WARP"
        return tool, params
    if tool in {"interact", "use_tool"}:
        params = {
            "target_tile": {"x": args["x"], "y": args["y"]},
        }
        if tool == "use_tool":
            params["charge_level"] = args.get("charge_level", 0)
        return tool, params
    if tool == "menu_click":
        return tool, {
            "component_id": args["component_id"],
            "source": args["component_source"],
        }
    if tool == "equip":
        return tool, dict(args)
    raise ValueError(f"unsupported recommended step tool: {tool}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius", type=int, default=8)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--query-timeout-ms", type=int, default=5000)
    parser.add_argument(
        "--execute-first",
        action="store_true",
        help="execute the first candidate's recommended_steps after printing it",
    )
    args = parser.parse_args()

    result = await find_interactions(
        ProtocolFileCtx(query_timeout_ms=args.query_timeout_ms),
        radius=args.radius,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if not args.execute_first:
        return 0 if result.get("success", True) else 1

    items = result.get("data", {}).get("items") or []
    if not items:
        print("No interaction candidates to execute.", file=sys.stderr)
        return 2
    steps = items[0].get("recommended_steps") or []
    if not steps:
        print("First candidate has no recommended_steps.", file=sys.stderr)
        return 2

    for step in steps:
        tool = step.get("tool")
        step_args = step.get("args") or {}
        capability, params = protocol_params_for_tool(tool, step_args)
        action_result = await send_protocol(capability, params)
        print(json.dumps({
            "executed": capability,
            "params": params,
            "result": action_result,
        }, ensure_ascii=False, indent=2, default=str))
        if not action_result.get("success"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
