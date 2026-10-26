from __future__ import annotations

from typing import Any

from agent.execution.tool_base import ToolBase


class NavigateToTool(ToolBase):
    name = "navigate_to"
    description = "移动到指定地点"
    parameters = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "target_location": {"type": "string"},
        },
        "required": ["x", "y"],
    }

    def build_command(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        command = {
            "request_id": request_id,
            "action": self.name,
            "x": int(arguments["x"]),
            "y": int(arguments["y"]),
        }
        if arguments.get("target_location"):
            command["target_location"] = arguments["target_location"]
        return command
