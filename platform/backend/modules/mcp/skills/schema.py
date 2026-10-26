"""M3.3 Skill YAML parser, schema subset validator, version-range engine.

No network, no subprocess, no skill execution.  Only PyYAML + stdlib.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml
from yaml.events import AliasEvent

from modules.mcp.skills.models import (
    CapabilityRequirement,
    RISK_LABELS,
    SkillPackage,
    SkillPackageError,
    SkillPackageErrorCode,
    SkillPackageManifest,
    SkillSource,
)

# ---------------------------------------------------------------------------
# YAML StrictSafeLoader — alias, duplicate-key, tag, and multi-doc rejection
# ---------------------------------------------------------------------------

_DUPLICATE_KEY_ERR = "duplicate mapping key in strict mode"
_ALIAS_ERR = "aliases are forbidden in strict mode"


class StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects aliases and duplicate mapping keys.

    Aliases are rejected during composition.  Duplicate mapping keys are
    detected during construction by overriding ``construct_mapping`` so we
    never depend on a specific PyYAML minor version's default behaviour.
    Non-standard tags and multi-document input are rejected by the standard
    ``SafeLoader`` / ``get_single_node`` machinery.
    """

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise yaml.composer.ComposerError(
                None,
                None,
                _ALIAS_ERR,
                event.start_mark,
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        """Rejects duplicate mapping keys and unhashable keys at every depth."""
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None,
                "expected a mapping node, but found %s" % node.id,
                node.start_mark,
            )
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                )
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    _DUPLICATE_KEY_ERR,
                    key_node.start_mark,
                )
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


# ---------------------------------------------------------------------------
# YAML node counting helpers (applied post-compose, pre-construct)
# ---------------------------------------------------------------------------


def _count_yaml_nodes(node: Any) -> int:
    """Count MappingNode, SequenceNode, ScalarNode, keys, and values once each."""
    count = 1  # this node
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            count += _count_yaml_nodes(key_node)
            count += _count_yaml_nodes(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            count += _count_yaml_nodes(item)
    # ScalarNode contributes only itself (already counted)
    return count


def _yaml_depth(node: Any) -> int:
    """Maximum node depth of the YAML tree (root has depth 1)."""
    if isinstance(node, yaml.MappingNode):
        child_max = 0
        for key_node, value_node in node.value:
            child_max = max(child_max, _yaml_depth(key_node), _yaml_depth(value_node))
        return 1 + child_max
    elif isinstance(node, yaml.SequenceNode):
        child_max = 0
        for item in node.value:
            child_max = max(child_max, _yaml_depth(item))
        return 1 + child_max
    return 1  # ScalarNode


# ---------------------------------------------------------------------------
# Internal YAML load — byte / depth / node limits, then construction
# ---------------------------------------------------------------------------

_MAX_YAML_BYTES = 262144
_MAX_NODE_COUNT = 10000
_MAX_NODE_DEPTH = 32


def _load_skill_yaml_raw(package_root: Path) -> dict[str, Any]:
    """Load skill.yaml subject to all YAML-level security gates.

    Raises ``SkillPackageError`` on any violation so the caller never touches
    raw YAML exception text.
    """
    manifest_path = package_root / "skill.yaml"

    # --- byte read -----------------------------------------------------------
    try:
        raw_bytes = manifest_path.read_bytes()
    except (PermissionError, OSError):
        raise SkillPackageError(SkillPackageErrorCode.IO_ERROR)

    if len(raw_bytes) > _MAX_YAML_BYTES:
        raise SkillPackageError(SkillPackageErrorCode.YAML_LIMIT_EXCEEDED)

    # --- UTF-8 check ---------------------------------------------------------
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillPackageError(SkillPackageErrorCode.YAML_INVALID)

    # --- compose (alias rejection happens here) ------------------------------
    loader = StrictSafeLoader(text)
    try:
        try:
            node = loader.get_single_node()
        except yaml.error.YAMLError:
            raise SkillPackageError(SkillPackageErrorCode.YAML_INVALID)

        if node is None or not isinstance(node, yaml.MappingNode):
            raise SkillPackageError(SkillPackageErrorCode.YAML_INVALID)

        # --- node / depth limits -------------------------------------------------
        if _count_yaml_nodes(node) > _MAX_NODE_COUNT:
            raise SkillPackageError(SkillPackageErrorCode.YAML_LIMIT_EXCEEDED)
        if _yaml_depth(node) > _MAX_NODE_DEPTH:
            raise SkillPackageError(SkillPackageErrorCode.YAML_LIMIT_EXCEEDED)

        # --- construct (duplicate-key rejection happens here) --------------------
        try:
            data = loader.construct_document(node)
        except yaml.error.YAMLError:
            raise SkillPackageError(SkillPackageErrorCode.YAML_INVALID)

        if not isinstance(data, dict):
            raise SkillPackageError(SkillPackageErrorCode.YAML_INVALID)

        return data
    finally:
        loader.dispose()


# ---------------------------------------------------------------------------
# Regex patterns (contract-mandated)
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*){2,}$")
_VERSION_CORE_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DESCRIPTION_MAX_LEN = 500
_GAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FAILURE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Sentinel for fields that must exactly match (not regex-tested)
_SENTINEL = object()


# ---------------------------------------------------------------------------
# Field type enforcer
# ---------------------------------------------------------------------------


def _check_type(value: Any, expected: type | tuple[type, ...], *,
                forbid_bool: bool = False) -> None:
    """Raise FIELD_TYPE_INVALID when *value* does not match *expected*."""
    ok: bool
    if forbid_bool:
        # bool is a subclass of int — reject it for numeric fields that forbid it
        if isinstance(value, bool):
            ok = False
        else:
            ok = isinstance(value, expected)
    else:
        ok = isinstance(value, expected)
    if not ok:
        raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)


# ---------------------------------------------------------------------------
# Per-field semantic validators
# ---------------------------------------------------------------------------


def _validate_id(skill_id: str, package_root: Path) -> None:
    if not _ID_RE.match(skill_id) or not (3 <= len(skill_id) <= 128):
        raise SkillPackageError(SkillPackageErrorCode.ID_INVALID)
    if package_root.name != skill_id:
        raise SkillPackageError(SkillPackageErrorCode.ID_INVALID)


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a strict MAJOR.MINOR.PATCH string; raise ValueError on failure."""
    m = _VERSION_CORE_RE.match(version)
    if m is None:
        raise ValueError(f"invalid SemVer core: {version!r}")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _validate_version(version: str) -> None:
    try:
        _parse_semver(version)
    except ValueError:
        raise SkillPackageError(SkillPackageErrorCode.VERSION_INVALID)


def _validate_description(raw_description: str) -> str:
    normalized = raw_description.strip()
    if not (1 <= len(normalized) <= _DESCRIPTION_MAX_LEN):
        raise SkillPackageError(SkillPackageErrorCode.DESCRIPTION_INVALID)
    return normalized


def _validate_game(game: str) -> None:
    if not _GAME_RE.match(game):
        raise SkillPackageError(SkillPackageErrorCode.GAME_INVALID)


def _validate_entrypoint(entrypoint: str) -> tuple[str, str]:
    """M3 enforces exactly ``implementation:run``."""
    if entrypoint != "implementation:run":
        raise SkillPackageError(SkillPackageErrorCode.ENTRYPOINT_INVALID)
    return ("implementation", "run")


# ---------------------------------------------------------------------------
# JSON Schema subset validator (contract skill_yaml.schema_subset)
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMA_KEYWORDS: frozenset[str] = frozenset({
    "type", "properties", "required", "additionalProperties",
    "items", "enum", "minimum", "maximum",
    "minLength", "maxLength", "pattern",
    "minItems", "maxItems", "description", "x-open-output",
})

_ALLOWED_TYPES: frozenset[str] = frozenset({
    "object", "array", "string", "integer", "number", "boolean",
})

# Per-type keyword allowance (always includes "type", "enum", "description")
_TYPE_KEYWORDS: dict[str, frozenset[str]] = {
    "object": frozenset({"properties", "required", "additionalProperties", "x-open-output"}),
    "array": frozenset({"items", "minItems", "maxItems"}),
    "string": frozenset({"minLength", "maxLength", "pattern"}),
    "integer": frozenset({"minimum", "maximum"}),
    "number": frozenset({"minimum", "maximum"}),
    "boolean": frozenset(),
}

_COMMON_KEYWORDS: frozenset[str] = frozenset({"type", "enum", "description"})


def _allowed_keywords_for(schema_type: str) -> frozenset[str]:
    return _COMMON_KEYWORDS | _TYPE_KEYWORDS.get(schema_type, frozenset())


def _validate_schema_root(schema: Any, label: str) -> Mapping[str, Any]:
    """Entry point: validate a JSON Schema M3-subset document."""
    if not isinstance(schema, dict):
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    allow_open_output = label == "output_schema"
    _validate_schema_node(
        schema,
        f"{label}#",
        allow_open_output=allow_open_output,
    )
    # Contract: root type must be object and additionalProperties explicitly false
    if schema.get("type") != "object":
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if schema.get("additionalProperties") is not False and not (
        allow_open_output and schema.get("x-open-output") is True
    ):
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    return schema


def _validate_schema_node(
    schema: dict[str, Any],
    path: str,
    *,
    allow_open_output: bool = False,
) -> None:
    """Recursive schema-subset enforcement."""
    # --- type keyword required -------------------------------------------------
    if "type" not in schema:
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    schema_type = schema["type"]
    if not isinstance(schema_type, str):
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if schema_type not in _ALLOWED_TYPES:
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)

    allowed = _allowed_keywords_for(schema_type)

    # ``x-open-output`` is a narrowly-scoped escape hatch for an output
    # document whose *root* is intentionally an open object.  Accepting it on
    # input or below the root would let a package bypass the closed-schema
    # security boundary.
    if "x-open-output" in schema:
        if not allow_open_output or schema.get("x-open-output") is not True:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)

    # --- unknown keyword check -------------------------------------------------
    for key in schema:
        if key not in allowed or key not in _ALLOWED_SCHEMA_KEYWORDS:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        if key not in allowed:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)

    # --- description -----------------------------------------------------------
    if "description" in schema and not isinstance(schema["description"], str):
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)

    # --- enum ------------------------------------------------------------------
    if "enum" in schema:
        _validate_enum(schema["enum"], schema_type, path)

    # --- type-specific validation ----------------------------------------------
    if schema_type == "object":
        _validate_object_schema(
            schema,
            path,
            allow_open_output=allow_open_output,
        )
    elif schema_type == "array":
        _validate_array_schema(schema, path)
    elif schema_type == "string":
        _validate_string_schema(schema, path)
    elif schema_type in ("integer", "number"):
        _validate_numeric_schema(schema, path)
    # boolean: no additional keywords


def _validate_enum_json_deep(value: Any, path: str) -> None:
    """Recursively reject non-JSON-serializable values and non-string keys.

    JSON allows: str, int (arbitrary magnitude), float (finite), bool, list,
    dict (with str keys).  This validator rejects None, NaN, infinity,
    non-string dict keys, and any non-JSON type such as bytes, set, or tuple.
    """
    if isinstance(value, str):
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        return
    # None is rejected — JSON null is not a valid enum member in M3.
    if isinstance(value, list):
        for item in value:
            _validate_enum_json_deep(item, f"{path}[]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
            _validate_enum_json_deep(v, f"{path}.{k}")
        return
    raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)


def _validate_enum(enum_values: Any, schema_type: str, path: str) -> None:
    if not isinstance(enum_values, list) or len(enum_values) == 0:
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    for i, val in enumerate(enum_values):
        item_path = f"{path}/enum/{i}"
        # type check
        if schema_type == "string":
            if not isinstance(val, str):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        elif schema_type == "integer":
            if not isinstance(val, int) or isinstance(val, bool):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        elif schema_type == "number":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
            if isinstance(val, float) and not math.isfinite(val):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        elif schema_type == "boolean":
            if not isinstance(val, bool):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        elif schema_type == "array":
            if not isinstance(val, list):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
            _validate_enum_json_deep(val, item_path)
        elif schema_type == "object":
            if not isinstance(val, dict):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
            _validate_enum_json_deep(val, item_path)


def _validate_object_schema(
    schema: dict[str, Any],
    path: str,
    *,
    allow_open_output: bool = False,
) -> None:
    # additionalProperties must be explicitly false at every depth
    if "additionalProperties" not in schema:
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if schema["additionalProperties"] is not False and not (
        allow_open_output and schema.get("x-open-output") is True
    ):
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)

    # properties: string-keyed mapping to schemas
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        for prop_name, prop_schema in props.items():
            if not isinstance(prop_schema, dict):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
            _validate_schema_node(prop_schema, f"{path}properties/{prop_name}")

    # required: unique string list subset of properties
    if "required" in schema:
        req = schema["required"]
        if not isinstance(req, list):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        if len(req) != len(set(req)):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        for r in req:
            if not isinstance(r, str):
                raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        prop_keys = set(schema.get("properties", {}).keys()) if "properties" in schema else set()
        if not set(req).issubset(prop_keys):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)


def _validate_array_schema(schema: dict[str, Any], path: str) -> None:
    # items is a schema (not a list of schemas)
    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, dict):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        _validate_schema_node(items, f"{path}items")

    # minItems / maxItems: non-negative integers, min <= max
    if "minItems" in schema:
        v = schema["minItems"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if "maxItems" in schema:
        v = schema["maxItems"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if "minItems" in schema and "maxItems" in schema:
        if schema["minItems"] > schema["maxItems"]:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)


def _validate_string_schema(schema: dict[str, Any], path: str) -> None:
    if "minLength" in schema:
        v = schema["minLength"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if "maxLength" in schema:
        v = schema["maxLength"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if "minLength" in schema and "maxLength" in schema:
        if schema["minLength"] > schema["maxLength"]:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if "pattern" in schema:
        pat = schema["pattern"]
        if not isinstance(pat, str):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        try:
            re.compile(pat)
        except re.error:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)


def _validate_numeric_schema(schema: dict[str, Any], path: str) -> None:
    for key in ("minimum", "maximum"):
        if key not in schema:
            continue
        v = schema[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
        # Arbitrary-magnitude int is always finite; only float needs the check.
        if isinstance(v, float) and not math.isfinite(v):
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    if "minimum" in schema and "maximum" in schema:
        if schema["minimum"] > schema["maximum"]:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)


# ---------------------------------------------------------------------------
# Capability requires validation
# ---------------------------------------------------------------------------


def _validate_requires(requires: Any) -> tuple[CapabilityRequirement, ...]:
    """Validate *requires* block and return frozen tuple of CapabilityRequirement."""
    if not isinstance(requires, dict):
        raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)

    # exactly one key: "capabilities"
    unknown = set(requires.keys()) - {"capabilities"}
    if unknown:
        raise SkillPackageError(SkillPackageErrorCode.UNKNOWN_FIELD)
    if "capabilities" not in requires:
        raise SkillPackageError(SkillPackageErrorCode.MISSING_FIELD)

    caps = requires["capabilities"]
    if not isinstance(caps, list):
        raise SkillPackageError(SkillPackageErrorCode.REQUIREMENT_INVALID)

    seen_ids: set[str] = set()
    result: list[CapabilityRequirement] = []
    for item in caps:
        if not isinstance(item, dict):
            raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
        # unknown fields
        unknown_item = set(item.keys()) - {"id", "version"}
        if unknown_item:
            raise SkillPackageError(SkillPackageErrorCode.UNKNOWN_FIELD)
        # missing fields
        for field in ("id", "version"):
            if field not in item:
                raise SkillPackageError(SkillPackageErrorCode.MISSING_FIELD)
        # type checks
        if not isinstance(item["id"], str):
            raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
        if not isinstance(item["version"], str):
            raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
        # capability id regex
        if not _CAPABILITY_ID_RE.match(item["id"]):
            raise SkillPackageError(SkillPackageErrorCode.REQUIREMENT_INVALID)
        # version range parse + satisfiability check
        _check_version_range(item["version"])
        # duplicate id
        if item["id"] in seen_ids:
            raise SkillPackageError(SkillPackageErrorCode.REQUIREMENT_INVALID)
        seen_ids.add(item["id"])
        result.append(CapabilityRequirement(
            capability_id=item["id"],
            version_range=item["version"],
        ))
    return tuple(result)


# ---------------------------------------------------------------------------
# Risk validation
# ---------------------------------------------------------------------------


def _validate_risk(risk: Any) -> frozenset[str]:
    if not isinstance(risk, list):
        raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
    seen: set[str] = set()
    result: list[str] = []
    for label in risk:
        if not isinstance(label, str):
            raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
        if label not in RISK_LABELS:
            raise SkillPackageError(SkillPackageErrorCode.RISK_INVALID)
        if label in seen:
            raise SkillPackageError(SkillPackageErrorCode.RISK_INVALID)
        seen.add(label)
        result.append(label)
    return frozenset(result)


# ---------------------------------------------------------------------------
# Scalar field validators
# ---------------------------------------------------------------------------


def _validate_timeout(value: Any) -> float:
    """timeout_seconds: number excluding bool, finite, 1..300.

    Arbitrary-magnitude ints are range-checked directly so float() never
    raises OverflowError.  Only float values are checked for NaN / infinity.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
    if isinstance(value, int):
        if not (1 <= value <= 300):
            raise SkillPackageError(SkillPackageErrorCode.TIMEOUT_INVALID)
        return float(value)
    # float path
    if not math.isfinite(value):
        raise SkillPackageError(SkillPackageErrorCode.TIMEOUT_INVALID)
    if not (1 <= value <= 300):
        raise SkillPackageError(SkillPackageErrorCode.TIMEOUT_INVALID)
    return value


def _validate_cancellable(value: Any) -> bool:
    if not isinstance(value, bool):
        raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
    return value


def _validate_retry(value: Any) -> str:
    if value != "never":
        raise SkillPackageError(SkillPackageErrorCode.RETRY_INVALID)
    return "never"


def _validate_failure_codes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) == 0:
        raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
    seen: set[str] = set()
    result: list[str] = []
    for code in value:
        if not isinstance(code, str):
            raise SkillPackageError(SkillPackageErrorCode.FIELD_TYPE_INVALID)
        if not _FAILURE_CODE_RE.match(code):
            raise SkillPackageError(SkillPackageErrorCode.FAILURE_CODE_INVALID)
        if code in seen:
            raise SkillPackageError(SkillPackageErrorCode.FAILURE_CODE_INVALID)
        seen.add(code)
        result.append(code)
    return tuple(result)


# ---------------------------------------------------------------------------
# Manifest construction (contract error-precedence order)
# ---------------------------------------------------------------------------

_REQUIRED_TOP_LEVEL: frozenset[str] = frozenset({
    "id", "version", "description", "game", "entrypoint",
    "input_schema", "output_schema", "requires", "risk",
    "timeout_seconds", "cancellable", "retry", "failure_codes",
})
_OPTIONAL_TOP_LEVEL: frozenset[str] = frozenset({"execution_mode"})

_EMPTY_CLOSED_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
}


def _resolve_published_skill_schemas(
    skill_id: str,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any],
    requires: tuple[CapabilityRequirement, ...],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Bind shipped packages to their code-owned schema authority.

    Atomic Skill packages intentionally carry only a closed empty placeholder
    in YAML.  Duplicating protobuf-derived schemas across 18 package files
    would create a second, manually maintained contract source.  At parse time
    the package is bound to ``canonical_specs()`` and any attempted inline
    override is rejected as drift.
    """
    from modules.mcp.skills.contracts import published_skill_schemas

    resolved = published_skill_schemas(skill_id)
    if resolved is None:
        return input_schema, output_schema

    if input_schema != _EMPTY_CLOSED_OBJECT_SCHEMA or output_schema != _EMPTY_CLOSED_OBJECT_SCHEMA:
        raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)

    prefix = "stardew.proto."
    if skill_id.startswith(prefix):
        capability_id = skill_id[len(prefix):]
        if len(requires) != 1 or requires[0].capability_id != capability_id:
            raise SkillPackageError(SkillPackageErrorCode.SCHEMA_INVALID)
    return deepcopy(resolved[0]), deepcopy(resolved[1])


def _build_manifest(raw: dict[str, Any], package_root: Path) -> SkillPackageManifest:
    """Apply contract error-precedence: unknown → missing → type → semantic."""
    # 1) unknown top-level fields
    unknown = set(raw.keys()) - _REQUIRED_TOP_LEVEL - _OPTIONAL_TOP_LEVEL
    if unknown:
        raise SkillPackageError(SkillPackageErrorCode.UNKNOWN_FIELD)

    # 2) missing required fields
    missing = _REQUIRED_TOP_LEVEL - set(raw.keys())
    if missing:
        raise SkillPackageError(SkillPackageErrorCode.MISSING_FIELD)

    # 3) top-level type checks (primitive / container before semantic)
    _check_type(raw["id"], str)
    _check_type(raw["version"], str)
    _check_type(raw["description"], str)
    _check_type(raw["game"], str)
    _check_type(raw["entrypoint"], str)
    _check_type(raw["requires"], dict)
    _check_type(raw["risk"], list)
    _check_type(raw["timeout_seconds"], (int, float), forbid_bool=True)
    _check_type(raw["cancellable"], bool)
    _check_type(raw["retry"], str)
    _check_type(raw["failure_codes"], list)
    if "execution_mode" in raw:
        _check_type(raw["execution_mode"], str)
    # input_schema / output_schema type checked inside _validate_schema_root

    # 4) semantic validators (any raises its specific code)
    _validate_id(raw["id"], package_root)
    _validate_version(raw["version"])
    description = _validate_description(raw["description"])
    _validate_game(raw["game"])
    entrypoint_module, entrypoint_callable = _validate_entrypoint(raw["entrypoint"])
    input_schema = _validate_schema_root(raw["input_schema"], "input_schema")
    output_schema = _validate_schema_root(raw["output_schema"], "output_schema")
    requires = _validate_requires(raw["requires"])
    input_schema, output_schema = _resolve_published_skill_schemas(
        raw["id"], input_schema, output_schema, requires,
    )
    risk = _validate_risk(raw["risk"])
    timeout_seconds = _validate_timeout(raw["timeout_seconds"])
    cancellable = _validate_cancellable(raw["cancellable"])
    retry = _validate_retry(raw["retry"])
    failure_codes = _validate_failure_codes(raw["failure_codes"])
    execution_mode = raw.get("execution_mode", "synchronous")
    if execution_mode not in {"synchronous", "background"}:
        raise SkillPackageError(SkillPackageErrorCode.EXECUTION_MODE_INVALID)

    return SkillPackageManifest(
        skill_id=raw["id"],
        version=raw["version"],
        description=description,
        game=raw["game"],
        entrypoint_module=entrypoint_module,
        entrypoint_callable=entrypoint_callable,
        input_schema=input_schema,
        output_schema=output_schema,
        requires=requires,
        risk=risk,
        timeout_seconds=timeout_seconds,
        cancellable=cancellable,
        retry=retry,
        failure_codes=failure_codes,
        execution_mode=execution_mode,
    )


# ---------------------------------------------------------------------------
# Deterministic manifest digest
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> Any:
    """Project manifest fragment to JSON primitives with sorted key order."""
    if isinstance(obj, Mapping):
        return {k: _canonical_json(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_canonical_json(v) for v in obj]
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("non-finite float in canonical JSON")
        return obj
    return obj


def compute_manifest_digest(manifest: SkillPackageManifest) -> str:
    """Compute ``sha256:<64-hex>`` over the canonical JSON projection."""
    payload: dict[str, Any] = {
        "id": manifest.skill_id,
        "version": manifest.version,
        "description": manifest.description,
        "game": manifest.game,
        "entrypoint": f"{manifest.entrypoint_module}:{manifest.entrypoint_callable}",
        "input_schema": _canonical_json(manifest.input_schema),
        "output_schema": _canonical_json(manifest.output_schema),
        "requires": [
            {"id": r.capability_id, "version": r.version_range}
            for r in manifest.requires
        ],
        "risk": sorted(manifest.risk),
        "timeout_seconds": float(manifest.timeout_seconds),
        "cancellable": manifest.cancellable,
        "retry": manifest.retry,
        "failure_codes": list(manifest.failure_codes),
    }
    if manifest.execution_mode != "synchronous":
        payload["execution_mode"] = manifest.execution_mode
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Public API — parse_skill_package
# ---------------------------------------------------------------------------


def parse_skill_package(package_root: Path, source: SkillSource,
                        scan_root: Path) -> SkillPackage:
    """Validate a candidate skill-package directory and return a frozen
    ``SkillPackage`` or raise ``SkillPackageError``.

    Contract error-precedence is enforced inside ``_validate_package_paths``.
    """
    _validate_package_paths(package_root, source, scan_root)
    raw = _load_skill_yaml_raw(package_root)
    manifest = _build_manifest(raw, package_root)
    digest = compute_manifest_digest(manifest)
    return SkillPackage(
        manifest=manifest,
        package_root=package_root.resolve(),
        implementation_path=(package_root / "implementation.py").resolve(),
        source=source,
        manifest_digest=digest,
    )


# ---------------------------------------------------------------------------
# Path validation (contract error-precedence order)
# ---------------------------------------------------------------------------


def _validate_package_paths(package_root: Path, source: SkillSource,
                            scan_root: Path) -> None:
    """Enforce root / candidate / fixed-file rules in error-precedence order."""
    resolved_root = package_root.resolve()
    resolved_scan = scan_root.resolve()

    # --- candidate symlink ----------------------------------------------------
    if package_root.is_symlink():
        raise SkillPackageError(SkillPackageErrorCode.SYMLINK_FORBIDDEN)

    # --- path escape (resolved candidate must be inside resolved scan) --------
    try:
        resolved_root.relative_to(resolved_scan)
    except ValueError:
        raise SkillPackageError(SkillPackageErrorCode.PATH_ESCAPE)

    # --- skill.yaml -----------------------------------------------------------
    manifest_path = package_root / "skill.yaml"
    if manifest_path.is_symlink():
        raise SkillPackageError(SkillPackageErrorCode.SYMLINK_FORBIDDEN)
    if not manifest_path.is_file():
        raise SkillPackageError(SkillPackageErrorCode.MANIFEST_MISSING)
    # verify skill.yaml resolved path descends from scan root
    _check_fixed_file_containment(manifest_path, resolved_scan, package_root)

    # --- implementation.py ----------------------------------------------------
    impl_path = package_root / "implementation.py"
    if impl_path.is_symlink():
        raise SkillPackageError(SkillPackageErrorCode.SYMLINK_FORBIDDEN)
    if not impl_path.is_file():
        raise SkillPackageError(SkillPackageErrorCode.IMPLEMENTATION_MISSING)
    _check_fixed_file_containment(impl_path, resolved_scan, package_root)

    # --- controlled README.md -------------------------------------------------
    if source == SkillSource.CONTROLLED:
        readme_path = package_root / "README.md"
        if readme_path.is_symlink():
            raise SkillPackageError(SkillPackageErrorCode.SYMLINK_FORBIDDEN)
        if not readme_path.is_file():
            raise SkillPackageError(SkillPackageErrorCode.README_MISSING)
        _check_fixed_file_containment(readme_path, resolved_scan, package_root)


def _check_fixed_file_containment(file_path: Path, scan_root: Path,
                                   package_root: Path) -> None:
    """Verify *file_path* resolves inside *scan_root* and is a direct child of
    *package_root*."""
    resolved_file = file_path.resolve()
    # Must descend from scan root
    try:
        resolved_file.relative_to(scan_root)
    except ValueError:
        raise SkillPackageError(SkillPackageErrorCode.PATH_ESCAPE)
    # Must be a direct child of package root
    if resolved_file.parent != package_root.resolve():
        raise SkillPackageError(SkillPackageErrorCode.PATH_ESCAPE)


# ---------------------------------------------------------------------------
# Public API — version_satisfies
# ---------------------------------------------------------------------------

# Comparator regex: op MAJOR.MINOR[.PATCH]
_COMPARATOR_RE = re.compile(
    r"^(==|>=|>|<=|<)(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$"
)
# Full range: 1-2 comma-separated comparators, no whitespace
_VERSION_RANGE_RE = re.compile(
    r"^(==|>=|>|<=|<)(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?"
    r"(?:,(==|>=|>|<=|<)(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?)?$"
)


def _check_version_range(version_range: str) -> tuple[tuple[str, int, int, int], ...]:
    """Parse *version_range*; raise REQUIREMENT_INVALID on parse failure or
    statically unsatisfiable conjunction across the non-negative SemVer domain.

    Returns a tuple of ``(op, major, minor, patch)`` for use by resolution.
    """
    if not _VERSION_RANGE_RE.fullmatch(version_range):
        raise SkillPackageError(SkillPackageErrorCode.REQUIREMENT_INVALID)

    parts = version_range.split(",")
    comparators: list[tuple[str, int, int, int]] = []
    for part in parts:
        m = _COMPARATOR_RE.fullmatch(part)
        if m is None:
            raise SkillPackageError(SkillPackageErrorCode.REQUIREMENT_INVALID)
        op = m.group(1)
        major = int(m.group(2))
        minor = int(m.group(3))
        patch = int(m.group(4)) if m.group(4) is not None else 0
        comparators.append((op, major, minor, patch))

    # Every comparator must be satisfiable within domain >=0.0.0 and every
    # conjunction must have a non-empty intersection.
    _check_satisfiable(comparators)

    return tuple(comparators)


def _check_satisfiable(
    comparators: list[tuple[str, int, int, int]],
) -> None:
    """Raise REQUIREMENT_INVALID when *comparators* cannot be satisfied by any
    non-negative ``(major, minor, patch)`` triple.

    SemVer is a discrete integer triple, not a continuous interval, so
    exclusive bounds are converted to their discrete successor / predecessor
    before intersection.
    """

    def _successor(ver: tuple[int, int, int]) -> tuple[int, int, int]:
        """Smallest discrete triple strictly greater than *ver*."""
        return (ver[0], ver[1], ver[2] + 1)

    def _predecessor(ver: tuple[int, int, int]
                     ) -> tuple[int, int, int] | None:
        """Largest discrete triple strictly less than *ver*, or None when
        no predecessor exists at this major.minor level (patch == 0)."""
        if ver[2] > 0:
            return (ver[0], ver[1], ver[2] - 1)
        return None

    # Intersect all comparators into a single effective inclusive range.
    lo: tuple[int, int, int] | None = None   # effective inclusive lower bound
    hi: tuple[int, int, int] | None = None   # effective inclusive upper bound

    for op, maj, min_, pat in comparators:
        ver = (maj, min_, pat)

        if op == "==":
            # exact point
            if lo is None or ver > lo:
                lo = ver
            if hi is None or ver < hi:
                hi = ver
        elif op == ">=":
            if lo is None or ver > lo:
                lo = ver
        elif op == ">":
            # discrete successor: >M.m.p  ≡  >=M.m.(p+1)
            suc = _successor(ver)
            if lo is None or suc > lo:
                lo = suc
        elif op == "<=":
            if hi is None or ver < hi:
                hi = ver
        elif op == "<":
            # discrete predecessor when patch > 0; when patch == 0 the
            # effective upper bound is (maj, min_-1, ∞) or similar —
            # any version with a smaller major or same major / smaller
            # minor satisfies, so we only need to check whether *lo*
            # already violates the constraint.
            pred = _predecessor(ver)
            if pred is not None:
                if hi is None or pred < hi:
                    hi = pred
            # else: no single predecessor — the constraint is lo < ver,
            # which we handle after the loop
        else:
            raise AssertionError(f"unexpected op: {op}")  # pragma: no cover

    # --- domain minimum --------------------------------------------------------
    zero = (0, 0, 0)
    if lo is None:
        lo = zero

    # --- check exclusive-upper bounds whose patch == 0 -------------------------
    for op, maj, min_, pat in comparators:
        if op == "<":
            ver = (maj, min_, pat)
            if _predecessor(ver) is None:
                # <M.m.0: lo must be strictly less than (M,m,0)
                if lo >= ver:
                    raise SkillPackageError(
                        SkillPackageErrorCode.REQUIREMENT_INVALID,
                    )

    # --- lo vs hi --------------------------------------------------------------
    if hi is not None and lo > hi:
        raise SkillPackageError(SkillPackageErrorCode.REQUIREMENT_INVALID)
    if hi is not None and lo == hi:
        # Equality only works when every comparator that produced lo and hi
        # was inclusive.  Walk back through the comparators to verify.
        ok = False
        for op, maj, min_, pat in comparators:
            ver = (maj, min_, pat)
            if op == "==" and ver == lo:
                ok = True
                break
            if op == ">=" and ver == lo:
                ok = True
                break
            if op == "<=" and ver == lo:
                ok = True
                break
            if op == ">" and _successor(ver) == lo:
                # >M.m.(p-1)  ≡  >=M.m.p  → version M.m.p included
                ok = True
                break
            if op == "<" and _predecessor(ver) == lo:
                # <M.m.(p+1)  ≡  <=M.m.p  → version M.m.p included
                ok = True
                break
        if not ok:
            raise SkillPackageError(
                SkillPackageErrorCode.REQUIREMENT_INVALID,
            )


def version_satisfies(version: str, version_range: str) -> bool:
    """Return ``True`` when *version* satisfies the restricted version range.

    Both arguments follow the contract grammars — no coercion, no PEP 440,
    no ``packaging`` library.
    """
    # This function is called at *resolution* time when version_range has
    # already passed _check_version_range validation.  Still, parse it fresh
    # and return False on any unexpected failure.
    try:
        comps = _check_version_range(version_range)
    except SkillPackageError:
        return False

    ver = _parse_semver(version)

    for op, maj, min_, pat in comps:
        target = (maj, min_, pat)
        if op == "==" and ver != target:
            return False
        elif op == ">=" and ver < target:
            return False
        elif op == ">" and ver <= target:
            return False
        elif op == "<=" and ver > target:
            return False
        elif op == "<" and ver >= target:
            return False

    return True
