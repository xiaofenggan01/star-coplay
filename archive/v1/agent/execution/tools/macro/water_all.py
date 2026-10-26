from __future__ import annotations
from typing import Any
from agent.execution.tool_base import MacroTool, ToolLayer


class WaterAllCropsTool(MacroTool):
    name = "water_all_crops"
    description = "自动装备水壶并浇灌当前视野内所有未浇水的作物，内部处理水量不足时自动装水"
    layer = ToolLayer.MACRO
    planner_visible = True
    parameters = {
        "type": "object",
        "properties": {
            "max_count": {"type": "integer", "description": "本轮最多浇几格，默认30"},
        },
    }

    def expand(self, arguments: dict[str, Any], context: Any) -> list[dict[str, Any]]:
        tiles = getattr(context, "to_water", None) or []
        max_count = min(int(arguments.get("max_count") or 30), 30)
        commands: list[dict[str, Any]] = []
        commands.append({"action": "equip_item", "params": {"item_name": "Watering Can"}})
        for tile in tiles[:max_count]:
            commands.append({"action": "use_tool_on_tile", "params": {"x": tile["x"], "y": tile["y"]}})
        return commands
