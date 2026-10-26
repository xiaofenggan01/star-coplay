from __future__ import annotations

from typing import Any

from agent.execution.tool_base import ToolBase


class GoToBedTool(ToolBase):
    name = "go_to_bed"
    description = "结束当天并回床睡觉"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def build_command(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "action": self.name,
        }
