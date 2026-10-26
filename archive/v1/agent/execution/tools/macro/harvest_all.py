from __future__ import annotations
from typing import Any
from agent.execution.tool_base import MacroTool, ToolLayer


class HarvestAllCropsTool(MacroTool):
    name = "harvest_all_crops"
    description = "收割当前视野内所有成熟作物"
    layer = ToolLayer.MACRO
    planner_visible = True
    parameters = {"type": "object", "properties": {}}

    def expand(self, arguments: dict[str, Any], context: Any) -> list[dict[str, Any]]:
        tiles = getattr(context, "harvest_tiles", None) or []
        return [{"action": "interact_with_tile", "params": {"x": t["x"], "y": t["y"]}} for t in tiles[:20]]
