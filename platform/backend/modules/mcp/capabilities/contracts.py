"""Schema validation, contract catalog builder, and Proto capability adapter."""
from __future__ import annotations

import copy
import re as _re
from pathlib import Path
from typing import Any, Mapping

from modules.mcp.contracts.models import CommandState, GameCommandResult, SideEffect
from modules.mcp.capabilities.models import (
    CapabilityAdapter,
    CapabilityContract,
    CapabilityContractCatalog,
    CapabilityErrorCode,
    CapabilityInvocationHandle,
    CapabilityRuntimeError,
    PreparedCapabilityCall,
)

# ---------------------------------------------------------------------------
# Private schema-validation exception; no protocol-layer error dependency.
# ---------------------------------------------------------------------------

class JsonSchemaValidationError(Exception):
    """Public neutral JSON Schema validation error — no protocol-layer dependency."""


# ---------------------------------------------------------------------------
# Neutral recursive JSON Schema validator — subset only
# ---------------------------------------------------------------------------

def validate_json_schema(schema: Mapping[str, Any], value: Any) -> None:
    expected = schema.get("type")
    valid = expected is None or (
        (expected == "object" and isinstance(value, Mapping))
        or (expected == "array" and isinstance(value, list))
        or (expected == "string" and isinstance(value, str))
        or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (expected == "boolean" and isinstance(value, bool))
    )
    if not valid:
        raise JsonSchemaValidationError("value does not match schema type")
    if "enum" in schema and value not in schema["enum"]:
        raise JsonSchemaValidationError("value not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise JsonSchemaValidationError("string below minLength")
        pattern = schema.get("pattern")
        if pattern is not None and _re.fullmatch(str(pattern), value) is None:
            raise JsonSchemaValidationError("string does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise JsonSchemaValidationError("value below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise JsonSchemaValidationError("value above maximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise JsonSchemaValidationError("array below minItems")
        items = schema.get("items")
        if items is not None:
            for item in value:
                validate_json_schema(items, item)
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise JsonSchemaValidationError(f"missing required property: {key}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    raise JsonSchemaValidationError(f"unknown property: {key}")
        for key, nested in value.items():
            if key in properties:
                validate_json_schema(properties[key], nested)


# ---------------------------------------------------------------------------
# Proto capability classification (frozen, per contract)
# ---------------------------------------------------------------------------

_READ_ONLY: frozenset[str] = frozenset({
    "query_menu", "query_runtime_snapshot", "query_world_region",
    "query_inventory_snapshot", "query_ui_snapshot", "query_inspect_refs",
})

_PROTO_ASYNC: frozenset[str] = frozenset({
    "face", "move_to", "go_to", "go_to_bed", "interact", "use_tool", "equip",
    "open_menu", "menu_click", "menu_close",
})

_INTERNAL_ONLY: frozenset[str] = frozenset({"tp"})


# ---------------------------------------------------------------------------
# Canonical capability adapters
# ---------------------------------------------------------------------------

class CanonicalCapabilityAdapter:
    """Bound adapter for exactly one versioned canonical capability.

    Prepares arguments through the canonical mapper and normalizes terminal
    results through the shared result adapter. The source surface fixes the
    expected wire format; there is no runtime fallback between transports.
    """

    def __init__(
        self,
        capability_id: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any] | None,
        source_surface: str,
    ) -> None:
        self._capability_id = capability_id
        self._input_schema = input_schema
        self._output_schema = output_schema
        self._source_surface = source_surface

    def prepare(self, arguments: Mapping[str, Any], command_id: str) -> PreparedCapabilityCall:
        # 1. Neutral schema validation
        try:
            validate_json_schema(self._input_schema, arguments)
        except JsonSchemaValidationError as exc:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INVALID_ARGUMENTS, str(exc), False,
            )

        # 2. Canonical mapping (lazy import to keep module-level dependency clean)
        from agent.protocol.canonical_surface import map_canonical_command
        from agent.protocol.tool_mapper import ProtocolMappingError

        try:
            mapped = map_canonical_command(
                self._capability_id, arguments, command_id=command_id,
            )
        except ProtocolMappingError as exc:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INVALID_ARGUMENTS, str(exc), False,
            )

        # 3. Contract invariants
        expected_wire_format = (
            "protocol-2.4.5" if self._source_surface == "protocol-2.4.5"
            else "v2-json" if self._source_surface == "v2-compat"
            else None
        )
        if expected_wire_format is None or mapped.wire_format != expected_wire_format:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INTERNAL,
                f"unexpected wire format: {mapped.wire_format}", False,
            )
        if mapped.capability != self._capability_id:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INTERNAL, "mapped capability mismatch", False,
            )
        if mapped.command_id != command_id:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.INTERNAL, "mapped command id mismatch", False,
            )

        return PreparedCapabilityCall(
            capability_id=self._capability_id,
            contract_version="2.4.5",
            validated_arguments=dict(arguments),
        )

    def normalize(
        self, handle: CapabilityInvocationHandle, result: GameCommandResult,
    ) -> GameCommandResult:
        from agent.protocol.result_adapter import normalize_canonical_result
        from agent.protocol.tool_mapper import ProtocolMappingError

        try:
            normalized = normalize_canonical_result(
                self._capability_id,
                handle.validated_arguments,
                expected_command_id=handle.command_id,
                lifecycle_state=result.state.value,
                raw_output=result.output,
                error_code=result.error_code,
            )
        except ProtocolMappingError as exc:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.UPSTREAM_CONTRACT_VIOLATION, str(exc), False,
            )

        if normalized is None:
            raise CapabilityRuntimeError(
                CapabilityErrorCode.UPSTREAM_CONTRACT_VIOLATION,
                "normalizer returned None for terminal result", False,
            )

        # Output schema gate
        if self._output_schema is not None:
            try:
                validate_json_schema(self._output_schema, normalized)
            except JsonSchemaValidationError as exc:
                raise CapabilityRuntimeError(
                    CapabilityErrorCode.UPSTREAM_CONTRACT_VIOLATION, str(exc), False,
                )

        return GameCommandResult(
            result.command_id, result.state, normalized, result.error_code,
        )


class ProtoCapabilityAdapter(CanonicalCapabilityAdapter):
    """Compatibility constructor for the existing Protocol 2.4.5 API."""

    def __init__(
        self,
        capability_id: str,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any] | None,
    ) -> None:
        super().__init__(
            capability_id, input_schema, output_schema, "protocol-2.4.5",
        )


# ===================================================================
# Contract catalog builder
# ===================================================================

def build_capability_contract_catalog(repo_root: Path) -> CapabilityContractCatalog:
    """Build the production contract catalog for Proto and V2 surfaces.

    Intersects ``build_proto_capability_entries(repo_root)`` with
    ``canonical_specs()``.  The 18 production entries each bind a
    ``ProtoCapabilityAdapter``; ``tp`` is added as an internal-only
    non-executable contract. Versioned ``v2-compat`` canonical specs bind the
    same deployment-neutral adapter without changing the Proto registrations.
    """
    from agent.protocol.capability_registry import SPECS_BY_CAPABILITY
    from agent.protocol.canonical_surface import canonical_specs
    from modules.mcp.manifest import build_proto_capability_entries

    entries = build_proto_capability_entries(repo_root)
    specs = canonical_specs()

    contracts: list[CapabilityContract] = []

    for entry in entries:
        cid: str = entry["capability_id"]

        if cid in _INTERNAL_ONLY:
            contracts.append(CapabilityContract(
                capability_id=cid,
                contract_version="2.4.5",
                source_surface="protocol-2.4.5",
                input_schema={},
                output_schema=None,
                required_scope="game:admin",
                side_effect=SideEffect.MUTATING,
                timeout_seconds=30.0,
                cancellable=False,
                skill_eligible=False,
                internal_only=True,
                adapter=None,
            ))
            continue

        # Production entry — must exist in canonical_specs and SPECS_BY_CAPABILITY
        spec = specs.get(cid)
        if spec is None:
            raise ValueError(
                f"production capability {cid} has no canonical spec"
            )

        cap_spec = SPECS_BY_CAPABILITY.get(cid)
        if cap_spec is None:
            raise ValueError(
                f"production capability {cid} has no CapabilitySpec"
            )

        read_only = cid in _READ_ONLY
        cancellable = cid in _PROTO_ASYNC

        contracts.append(CapabilityContract(
            capability_id=cid,
            contract_version="2.4.5",
            source_surface="protocol-2.4.5",
            input_schema=copy.deepcopy(spec.input_schema),
            output_schema=copy.deepcopy(spec.output_schema),
            required_scope="game:read" if read_only else "game:write",
            side_effect=SideEffect.READ_ONLY if read_only else SideEffect.MUTATING,
            timeout_seconds=cap_spec.timeout_ms / 1000,
            cancellable=cancellable,
            skill_eligible=True,
            internal_only=False,
            adapter=ProtoCapabilityAdapter(
                cid,
                copy.deepcopy(spec.input_schema),
                copy.deepcopy(spec.output_schema),
            ),
        ))

    for cid, spec in specs.items():
        if spec.source_surface != "v2-compat":
            continue
        read_only = cid == "observe_current_view"
        contracts.append(CapabilityContract(
            capability_id=cid,
            contract_version="2.4.5",
            source_surface="v2-compat",
            input_schema=copy.deepcopy(spec.input_schema),
            output_schema=copy.deepcopy(spec.output_schema),
            required_scope="game:read" if read_only else "game:write",
            side_effect=SideEffect.READ_ONLY if read_only else SideEffect.MUTATING,
            timeout_seconds=spec.timeout_ms / 1000,
            cancellable=spec.cancellable,
            skill_eligible=True,
            internal_only=False,
            adapter=CanonicalCapabilityAdapter(
                cid,
                copy.deepcopy(spec.input_schema),
                copy.deepcopy(spec.output_schema),
                "v2-compat",
            ),
        ))

    return CapabilityContractCatalog(contracts)
