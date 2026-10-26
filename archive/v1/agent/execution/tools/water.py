from __future__ import annotations

from typing import Any

from agent.execution.tool_base import ToolBase


class WaterCropsTool(ToolBase):
    name = "water_crops"
    description = "给指定地块浇水"
    parameters = {
        "type": "object",
        "properties": {
            "tiles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                },
            },
            "max_count": {"type": "integer"},
        },
        "required": ["tiles"],
    }

    def build_command(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        max_count = int(arguments.get("max_count") or len(arguments.get("tiles") or []))
        return {
            "request_id": request_id,
            "action": self.name,
            "tiles": arguments.get("tiles") or [],
            "max_count": max_count,
        }
