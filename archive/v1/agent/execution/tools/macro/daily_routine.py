from __future__ import annotations
from typing import Any
from agent.execution.tool_base import MacroTool, ToolLayer


class DailyFarmRoutineTool(MacroTool):
    name = "daily_farm_routine"
    description = "执行每日农场例行：先浇水再收割，体力不足时停止"
    layer = ToolLayer.MACRO
    planner_visible = True
    parameters = {"type": "object", "properties": {}}

    def expand(self, arguments: dict[str, Any], context: Any) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        to_water = getattr(context, "to_water", None) or []
        harvest = getattr(context, "harvest_tiles", None) or []
        block_water = getattr(context, "block_water", False)
        if to_water and not block_water:
            commands.append({"action": "equip_item", "params": {"item_name": "Watering Can"}})
            for t in to_water[:30]:
                commands.append({"action": "use_tool_on_tile", "params": {"x": t["x"], "y": t["y"]}})
        for t in harvest[:20]:
            commands.append({"action": "interact_with_tile", "params": {"x": t["x"], "y": t["y"]}})
        return commands
