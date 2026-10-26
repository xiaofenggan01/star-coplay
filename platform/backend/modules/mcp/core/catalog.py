from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from modules.mcp.contracts.models import SideEffect
from modules.mcp.core.errors import McpError


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    capability_id: str
    capability_version: str
    required_scope: str
    side_effect: SideEffect
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None
    exposure_kind: str = "tool"
    timeout_seconds: float = 30.0


class ToolCatalog:
    def __init__(self, tools: list[ToolDefinition]):
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("duplicate tool name")
        self._tools = {tool.name: tool for tool in tools}
        canonical = json.dumps(
            [
                {
                    "name": tool.name,
                    "capability": tool.capability_id,
                    "version": tool.capability_version,
                    "scope": tool.required_scope,
                    "side_effect": tool.side_effect.value,
                    "input": tool.input_schema,
                    "output": tool.output_schema,
                    "exposure": tool.exposure_kind,
                }
                for tool in tools
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._digest = hashlib.sha256(canonical).hexdigest()

    @property
    def digest(self) -> str:
        return self._digest

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise McpError("capability_denied", "tool is not available") from exc

    def list_for(self, allowed_tools: frozenset[str]) -> tuple[ToolDefinition, ...]:
        return tuple(tool for name, tool in self._tools.items() if name in allowed_tools)


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    _validate_value(schema, arguments)
    return dict(arguments)


def _validate_value(schema: Mapping[str, Any], value: Any) -> None:
    expected = schema.get("type")
    valid = expected is None or (
        (expected == "string" and isinstance(value, str))
        or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (expected == "boolean" and isinstance(value, bool))
        or (expected == "array" and isinstance(value, list))
        or (expected == "object" and isinstance(value, Mapping))
    )
    if not valid:
        raise McpError("invalid_arguments", "arguments do not match tool schema")
    if "enum" in schema and value not in schema["enum"]:
        raise McpError("invalid_arguments", "arguments do not match tool schema")
    if isinstance(value, str):
        import re
        if len(value) < schema.get("minLength", 0):
            raise McpError("invalid_arguments", "arguments do not match tool schema")
        if schema.get("pattern") and re.fullmatch(str(schema["pattern"]), value) is None:
            raise McpError("invalid_arguments", "arguments do not match tool schema")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise McpError("invalid_arguments", "arguments do not match tool schema")
        if "maximum" in schema and value > schema["maximum"]:
            raise McpError("invalid_arguments", "arguments do not match tool schema")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise McpError("invalid_arguments", "arguments do not match tool schema")
        item_schema = schema.get("items")
        if item_schema:
            for item in value:
                _validate_value(item_schema, item)
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if any(name not in value for name in schema.get("required", [])):
            raise McpError("invalid_arguments", "arguments do not match tool schema")
        if schema.get("additionalProperties") is False and any(name not in properties for name in value):
            raise McpError("invalid_arguments", "arguments do not match tool schema")
        for name, nested in value.items():
            if name in properties:
                _validate_value(properties[name], nested)
