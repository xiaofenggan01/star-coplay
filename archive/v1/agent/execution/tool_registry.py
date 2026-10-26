from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.execution.tool_base import ToolBase, MacroTool, DynamicTool, ToolLayer
from agent.execution.tools.macro.water_all import WaterAllCropsTool
from agent.execution.tools.macro.harvest_all import HarvestAllCropsTool

logger = logging.getLogger("agent.tools")


def _convert_params_to_json_schema(params: dict[str, Any]) -> dict[str, Any]:
    """将 tools.json 中的 params 定义转换为 OpenAI function calling 的 JSON Schema。"""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param_def in params.items():
        prop: dict[str, Any] = {}
        type_map = {"string": "string", "int": "integer", "bool": "boolean", "float": "number"}
        prop["type"] = type_map.get(param_def.get("type", "string"), "string")
        if "description" in param_def:
            prop["description"] = param_def["description"]
        if "enum" in param_def:
            prop["enum"] = param_def["enum"]
        if "default" in param_def:
            prop["default"] = param_def["default"]
        properties[param_name] = prop
        if param_def.get("required", False):
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _load_tools_from_json(tools_json_path: Path) -> list[DynamicTool]:
    """从 bridge 目录的 tools.json 动态加载工具定义。"""
    if not tools_json_path.exists():
        logger.warning("tools.json not found: %s", tools_json_path)
        return []

    try:
        data = json.loads(tools_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load tools.json: %s", exc)
        return []

    tools: list[DynamicTool] = []
    for tool_def in data.get("tools", []):
        name = tool_def.get("name")
        description = tool_def.get("description", "")
        params = tool_def.get("params", {})
        if not name:
            continue
        schema = _convert_params_to_json_schema(params)
        tools.append(DynamicTool(name=name, description=description, parameters=schema))
        logger.info("Loaded tool from tools.json: %s", name)

    logger.info("Loaded %d tools from tools.json", len(tools))
    return tools


class ToolRegistry:
    def __init__(self, bridge_dir: Path | None = None) -> None:
        self._tools: dict[str, ToolBase] = {}

        # 从 bridge 目录的 tools.json 动态加载 Mod 支持的工具
        for macro in (WaterAllCropsTool(), HarvestAllCropsTool()):
            self._tools[macro.name] = macro

        if bridge_dir is not None:
            tools_json = bridge_dir / "tools.json"
            for tool in _load_tools_from_json(tools_json):
                self._tools[tool.name] = tool


    def all_schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def schemas_for_planner(self) -> list[dict]:
        return [t.schema() for t in self._tools.values() if t.planner_visible]

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolBase:
        return self._tools[name]

    def is_macro(self, name: str) -> bool:
        return isinstance(self._tools.get(name), MacroTool)

    def get_macro(self, name: str) -> MacroTool | None:
        t = self._tools.get(name)
        return t if isinstance(t, MacroTool) else None

    def get_atomic(self, name: str) -> ToolBase | None:
        t = self._tools.get(name)
        return t if t and t.layer == ToolLayer.ATOMIC else None
