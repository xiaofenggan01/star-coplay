"""LLM FunctionSchema generation for Agent-side Protocol 2.4.5 tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from .capability_registry import CapabilitySpec, iter_llm_exposed_specs


TRANSPORT_FIELDS = frozenset(
    {
        "protocol_version",
        "message_id",
        "request_id",
        "session_id",
        "timestamp_ms",
        "command_id",
        "timeout_ms",
        "source",
        "capability",
    }
)


def visible_specs(enabled_capabilities: Iterable[str] | None) -> tuple[CapabilitySpec, ...]:
    """Return protocol specs visible to the LLM after runtime capability gating.

    Missing runtime capabilities are treated conservatively: expose no protocol tools.
    Phase 1 tests should pass a fake runtime list explicitly.
    """

    if enabled_capabilities is None:
        return ()
    enabled = set(enabled_capabilities)
    return tuple(spec for spec in iter_llm_exposed_specs() if spec.capability in enabled)


def get_protocol_tool_schemas(enabled_capabilities: Iterable[str] | None) -> list[dict]:
    return [_to_openai_tool_schema(spec) for spec in visible_specs(enabled_capabilities)]


def get_static_protocol_tool_schemas() -> list[dict]:
    """Return all statically LLM-exposed protocol tools, ignoring runtime gate.

    This is intended for tests and offline inspection only.
    """

    return [_to_openai_tool_schema(spec) for spec in iter_llm_exposed_specs()]


def _to_openai_tool_schema(spec: CapabilitySpec) -> dict:
    parameters = deepcopy(spec.parameters_schema)
    _assert_schema_boundary(parameters)
    return {
        "type": "function",
        "function": {
            "name": spec.tool_name,
            "description": spec.description,
            "parameters": parameters,
        },
    }


def _assert_schema_boundary(parameters: dict) -> None:
    if "$ref" in _walk_keys(parameters):
        raise ValueError("Protocol tool schema must not contain $ref")
    leaked = TRANSPORT_FIELDS.intersection(_walk_keys(parameters))
    if leaked:
        raise ValueError(f"Protocol tool schema leaked transport fields: {sorted(leaked)}")


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_walk_keys(nested))
    return keys
