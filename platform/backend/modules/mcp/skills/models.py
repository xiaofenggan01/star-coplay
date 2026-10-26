"""Frozen M3.3 Skill package models — no transport, MCP, or Gateway types."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


# ===================================================================
# Enums
# ===================================================================


class SkillSource(str, Enum):
    BUILTIN = "builtin"
    CONTROLLED = "controlled"


class RiskDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class SkillUnavailableReason(str, Enum):
    NOT_MOUNTED = "not_mounted"
    GAME_MISMATCH = "game_mismatch"
    CAPABILITY_MISSING = "capability_missing"
    CAPABILITY_VERSION_UNSUPPORTED = "capability_version_unsupported"
    RISK_DENIED = "risk_denied"
    CONFIRMATION_UNAVAILABLE = "confirmation_unavailable"


class SkillPackageErrorCode(str, Enum):
    ROOT_INVALID = "root_invalid"
    PATH_ESCAPE = "path_escape"
    SYMLINK_FORBIDDEN = "symlink_forbidden"
    MANIFEST_MISSING = "manifest_missing"
    IMPLEMENTATION_MISSING = "implementation_missing"
    README_MISSING = "readme_missing"
    IO_ERROR = "io_error"
    YAML_INVALID = "yaml_invalid"
    YAML_LIMIT_EXCEEDED = "yaml_limit_exceeded"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"
    FIELD_TYPE_INVALID = "field_type_invalid"
    ID_INVALID = "id_invalid"
    VERSION_INVALID = "version_invalid"
    DESCRIPTION_INVALID = "description_invalid"
    GAME_INVALID = "game_invalid"
    ENTRYPOINT_INVALID = "entrypoint_invalid"
    SCHEMA_INVALID = "schema_invalid"
    REQUIREMENT_INVALID = "requirement_invalid"
    RISK_INVALID = "risk_invalid"
    TIMEOUT_INVALID = "timeout_invalid"
    RETRY_INVALID = "retry_invalid"
    FAILURE_CODE_INVALID = "failure_code_invalid"
    EXECUTION_MODE_INVALID = "execution_mode_invalid"
    PACKAGE_CONFLICT = "package_conflict"


class SkillRegistryErrorCode(str, Enum):
    SKILL_NOT_INDEXED = "skill_not_indexed"
    MOUNT_CONFLICT = "mount_conflict"
    EXPOSURE_CONTEXT_INVALID = "exposure_context_invalid"
    CAPABILITY_CONFLICT = "capability_conflict"


# ===================================================================
# Frozen risk label set
# ===================================================================

RISK_LABELS: frozenset[str] = frozenset({
    "changes_save",
    "advances_time",
    "consumes_item",
    "consumes_energy",
    "spends_money",
    "changes_relationship",
})


# ===================================================================
# Recursive freeze helpers
# ===================================================================


def _deep_freeze(value: Any) -> Any:
    """Recursively freeze a value: Mapping→MappingProxyType, list→tuple, set→frozenset.

    Every ``collections.abc.Mapping`` (including ``MappingProxyType`` and custom
    Mapping subclasses) is defensively copied into a privately-owned ``dict``
    before being wrapped in ``MappingProxyType`` so the original cannot affect
    the frozen result.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({
            k: _deep_freeze(v) for k, v in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(v) for v in value)
    return value


def _freeze_dataclass(instance: object, skip: frozenset[str] = frozenset()) -> None:
    """Apply _deep_freeze to every field of a frozen dataclass instance."""
    for f in fields(instance):
        if f.name in skip:
            continue
        value = getattr(instance, f.name)
        frozen_value = _deep_freeze(value)
        if frozen_value is not value:
            object.__setattr__(instance, f.name, frozen_value)


# ===================================================================
# Exception classes
# ===================================================================


class SkillPackageError(Exception):
    """Neutral package-validation error — never exposes paths or raw content."""

    def __init__(
        self,
        code: SkillPackageErrorCode,
        package_path: str | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.safe_message: str = code.value
        self.package_path: str | None = package_path


class SkillRegistryError(Exception):
    """Neutral registry error — safe_message is always exactly code.value."""

    def __init__(self, code: SkillRegistryErrorCode) -> None:
        super().__init__(code.value)
        self.code = code
        self.safe_message: str = code.value


# ===================================================================
# Immutable dataclasses
# ===================================================================


@dataclass(frozen=True)
class CapabilityRequirement:
    capability_id: str
    version_range: str

    def __post_init__(self) -> None:
        _freeze_dataclass(self)


@dataclass(frozen=True)
class SkillRef:
    skill_id: str
    version: str

    def __post_init__(self) -> None:
        _freeze_dataclass(self)


@dataclass(frozen=True)
class DependencyBinding:
    requirement: CapabilityRequirement
    capability_ref: CapabilityRef | None  # noqa: F821 — defined below

    def __post_init__(self) -> None:
        _freeze_dataclass(self)


@dataclass(frozen=True)
class SkillPackageManifest:
    """Complete, validated, immutable manifest backed by a package skill.yaml."""

    skill_id: str
    version: str
    description: str
    game: str
    entrypoint_module: str
    entrypoint_callable: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    requires: tuple[CapabilityRequirement, ...]
    risk: frozenset[str]
    timeout_seconds: float
    cancellable: bool
    retry: str
    failure_codes: tuple[str, ...]
    execution_mode: str = "synchronous"

    def __post_init__(self) -> None:
        _freeze_dataclass(self)


@dataclass(frozen=True)
class SkillPackage:
    manifest: SkillPackageManifest
    package_root: Path
    implementation_path: Path
    source: SkillSource
    manifest_digest: str

    def __post_init__(self) -> None:
        _freeze_dataclass(self, skip=frozenset({"package_root", "implementation_path"}))


@dataclass(frozen=True)
class ExposureContext:
    game: str
    mounted_skill_refs: frozenset[SkillRef]
    risk_policy: Mapping[str, RiskDecision]
    confirmation_available: bool
    mount_revision: str
    policy_revision: str
    binding_ref: str
    binding_version: int
    game_session_ref: str

    def __post_init__(self) -> None:
        _freeze_dataclass(self, skip=frozenset({"binding_version"}))


@dataclass(frozen=True)
class EffectiveSkillPolicy:
    decisions: Mapping[str, RiskDecision]
    requires_confirmation: bool

    def __post_init__(self) -> None:
        _freeze_dataclass(self)


@dataclass(frozen=True)
class ResolvedSkill:
    package: SkillPackage
    available: bool
    reasons: tuple[SkillUnavailableReason, ...]
    dependency_bindings: tuple[DependencyBinding, ...]
    effective_policy: EffectiveSkillPolicy

    def __post_init__(self) -> None:
        _freeze_dataclass(self)


@dataclass(frozen=True)
class ResolvedSkillCatalog:
    skills: tuple[ResolvedSkill, ...]
    mount_revision: str
    policy_revision: str
    capability_digest: str
    binding_ref: str
    binding_version: int
    game_session_ref: str

    def __post_init__(self) -> None:
        _freeze_dataclass(self, skip=frozenset({"binding_version"}))


# Late import to resolve forward reference in DependencyBinding
from modules.mcp.capabilities.models import CapabilityRef  # noqa: E402
