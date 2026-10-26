from __future__ import annotations

from typing import Any

from agent.execution.tool_base import ToolBase


class HarvestCropsTool(ToolBase):
    name = "harvest_crops"
    description = "收割成熟作物"
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
            }
        },
        "required": ["tiles"],
    }

    def build_command(self, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "action": self.name,
            "tiles": arguments.get("tiles") or [],
        }
