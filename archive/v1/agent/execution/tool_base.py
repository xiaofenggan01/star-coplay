from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolLayer(Enum):
    ATOMIC = "atomic"
    MACRO = "macro"


@dataclass(slots=True)
class ToolDecision:
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    expectation: str
    knowledge_ids: list[str] = field(default_factory=list)


class ToolBase(ABC):
    name: str
    description: str
    parameters: dict[str, Any]
    layer: ToolLayer = ToolLayer.ATOMIC
    planner_visible: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    def build_command(self, arguments: dict[str, Any], cmd_id: str) -> dict[str, Any]:
        """构建命令，格式: {cmd_id, action, params: {...}}"""
        raise NotImplementedError


class DynamicTool(ToolBase):
    """从 tools.json 动态生成的工具，直接透传命令给 Mod。"""

    layer = ToolLayer.ATOMIC
    planner_visible = True

    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters

    def build_command(self, arguments: dict[str, Any], cmd_id: str) -> dict[str, Any]:
        return {"cmd_id": cmd_id, "action": self.name, "params": arguments}


class MacroTool(ToolBase):
    layer = ToolLayer.MACRO
    planner_visible = True

    @abstractmethod
    def expand(self, arguments: dict[str, Any], context: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def build_command(self, arguments: dict[str, Any], cmd_id: str) -> dict[str, Any]:
        raise NotImplementedError("MacroTool uses expand(), not build_command()")
