"""M3.3 SkillRegistry — atomic process-local Skill package index and resolver.

No network, no subprocess, no skill execution, no MCP DTOs.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from modules.mcp.capabilities.models import (
    CapabilityDiagnostic,
    CapabilityRef,
    CapabilitySnapshot,
)
from modules.mcp.skills.models import (
    CapabilityRequirement,
    DependencyBinding,
    EffectiveSkillPolicy,
    ExposureContext,
    ResolvedSkill,
    ResolvedSkillCatalog,
    RiskDecision,
    RISK_LABELS,
    SkillPackage,
    SkillPackageError,
    SkillPackageErrorCode,
    SkillRef,
    SkillRegistryError,
    SkillRegistryErrorCode,
    SkillSource,
    SkillUnavailableReason,
)
from modules.mcp.skills.schema import _parse_semver, parse_skill_package, version_satisfies


# ---------------------------------------------------------------------------
# Index construction helpers
# ---------------------------------------------------------------------------


def _validate_scan_root(root: Path, *, label: str) -> Path:
    """Raise ROOT_INVALID or return the resolved absolute directory."""
    if not isinstance(root, Path):
        raise SkillPackageError(SkillPackageErrorCode.ROOT_INVALID)
    if not root.is_absolute():
        raise SkillPackageError(SkillPackageErrorCode.ROOT_INVALID)
    if root.is_symlink():
        raise SkillPackageError(SkillPackageErrorCode.ROOT_INVALID)
    if not root.is_dir():
        raise SkillPackageError(SkillPackageErrorCode.ROOT_INVALID)
    return root.resolve()


def _natural_path_key(p: Path) -> str:
    """Return a Unicode code-point sort key for *p.name*."""
    return p.name


def _scan_candidates(scan_root: Path, source: SkillSource) -> list[SkillPackage]:
    """Scan immediate children in Unicode code-point path-name order.

    Ignores children without ``skill.yaml`` (without inspecting their contents).
    Any child containing ``skill.yaml`` that fails validation aborts the full
    index build.
    """
    try:
        children = sorted(
            [c for c in scan_root.iterdir() if c.is_dir()],
            key=_natural_path_key,
        )
    except (PermissionError, OSError) as e:
        raise SkillPackageError(SkillPackageErrorCode.IO_ERROR) from e

    packages: list[SkillPackage] = []
    for child in children:
        # A candidate is a direct child whose skill.yaml exists or is a
        # symlink (including dangling symlinks, so they reach SYMLINK_FORBIDDEN).
        skill_yaml_path = child / "skill.yaml"
        if not (skill_yaml_path.exists() or skill_yaml_path.is_symlink()):
            continue
        pkg = parse_skill_package(child, source, scan_root)
        packages.append(pkg)
    return packages


def _check_conflicts(packages: list[SkillPackage]) -> None:
    """Any repeated skill_id at any version rejects the full index with
    PACKAGE_CONFLICT.  No best-version selection; controlled never overrides builtin."""
    seen: dict[str, SkillPackage] = {}
    for pkg in packages:
        skill_id = pkg.manifest.skill_id
        if skill_id in seen:
            raise SkillPackageError(SkillPackageErrorCode.PACKAGE_CONFLICT)
        seen[skill_id] = pkg


# ---------------------------------------------------------------------------
# ExposureContext validation
# ---------------------------------------------------------------------------

_GAME_RE_CTX = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

_REASON_ORDER: dict[SkillUnavailableReason, int] = {
    SkillUnavailableReason.NOT_MOUNTED: 0,
    SkillUnavailableReason.GAME_MISMATCH: 1,
    SkillUnavailableReason.CAPABILITY_MISSING: 2,
    SkillUnavailableReason.CAPABILITY_VERSION_UNSUPPORTED: 3,
    SkillUnavailableReason.RISK_DENIED: 4,
    SkillUnavailableReason.CONFIRMATION_UNAVAILABLE: 5,
}


def _validate_exposure_context(ctx: ExposureContext) -> None:
    """Raise EXPOSURE_CONTEXT_INVALID when *ctx* violates any contract rule."""
    # game
    if not _GAME_RE_CTX.match(ctx.game):
        raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)

    # mount_revision, policy_revision, binding_ref, game_session_ref — non-empty str
    for name, val in (
        ("mount_revision", ctx.mount_revision),
        ("policy_revision", ctx.policy_revision),
        ("binding_ref", ctx.binding_ref),
        ("game_session_ref", ctx.game_session_ref),
    ):
        if not isinstance(val, str) or len(val) == 0:
            raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)

    # binding_version: integer excluding bool, >= 1
    if isinstance(ctx.binding_version, bool) or not isinstance(ctx.binding_version, int):
        raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)
    if ctx.binding_version < 1:
        raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)

    # confirmation_available: strictly bool
    if not isinstance(ctx.confirmation_available, bool):
        raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)

    # risk_policy keys/values
    if not isinstance(ctx.risk_policy, Mapping):
        raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)
    for key, val in ctx.risk_policy.items():
        if key not in RISK_LABELS:
            raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)
        if not isinstance(val, RiskDecision):
            raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)

    # mounted_skill_refs
    if not isinstance(ctx.mounted_skill_refs, (frozenset, set, tuple, list)):
        raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)
    for ref in ctx.mounted_skill_refs:
        if not isinstance(ref, SkillRef):
            raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)
        # id must be package-valid and version canonical three-component
        from modules.mcp.skills.schema import _ID_RE, _VERSION_CORE_RE as _VER_RE
        if not isinstance(ref.skill_id, str) or not _ID_RE.match(ref.skill_id):
            raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)
        if not isinstance(ref.version, str) or not _VER_RE.match(ref.version):
            raise SkillRegistryError(SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID)

    # mounted_skill_refs: at most one version per skill_id
    seen_ids: dict[str, str] = {}
    for ref in ctx.mounted_skill_refs:
        if ref.skill_id in seen_ids and seen_ids[ref.skill_id] != ref.version:
            raise SkillRegistryError(SkillRegistryErrorCode.MOUNT_CONFLICT)
        seen_ids[ref.skill_id] = ref.version


# ---------------------------------------------------------------------------
# Dependency binding
# ---------------------------------------------------------------------------


def _bind_dependency(
    req: CapabilityRequirement,
    snapshot: CapabilitySnapshot,
) -> tuple[CapabilityRef | None, SkillUnavailableReason | None]:
    """Try to bind a single ``CapabilityRequirement`` against *snapshot*.

    Returns ``(ref, None)`` on success or ``(None, reason)`` on failure.
    May raise ``CAPABILITY_CONFLICT``.
    """
    # Collect eligible contracts: same id, skill_eligible=true, internal_only=false
    eligible = [
        c for c in snapshot.known_contracts
        if c.capability_id == req.capability_id
        and c.skill_eligible
        and not c.internal_only
    ]

    # Which eligible contracts satisfy the version range?
    satisfying = [
        c for c in eligible
        if version_satisfies(c.contract_version, req.version_range)
    ]

    if satisfying:
        # Pick highest parsed SemVer
        best = max(satisfying, key=lambda c: _parse_semver(c.contract_version))
        best_version = best.contract_version
        # Conflict detection: same highest version from different surfaces
        same_ver = [c for c in satisfying if c.contract_version == best_version]
        surfaces = {c.source_surface for c in same_ver}
        if len(surfaces) > 1:
            raise SkillRegistryError(SkillRegistryErrorCode.CAPABILITY_CONFLICT)
        return (
            CapabilityRef(
                capability_id=best.capability_id,
                contract_version=best.contract_version,
            ),
            None,
        )

    # No satisfying contract → distinguish VERSION_UNSUPPORTED from MISSING
    has_eligible = len(eligible) > 0
    has_unsupported_diag = any(
        isinstance(d, CapabilityDiagnostic)
        and d.code == "unsupported_version"
        and d.capability_id == req.capability_id
        for d in snapshot.diagnostics
    )

    if has_eligible or has_unsupported_diag:
        return (None, SkillUnavailableReason.CAPABILITY_VERSION_UNSUPPORTED)

    return (None, SkillUnavailableReason.CAPABILITY_MISSING)


# ---------------------------------------------------------------------------
# Risk evaluation
# ---------------------------------------------------------------------------


def _evaluate_risk(
    manifest_risk: frozenset[str],
    risk_policy: Mapping[str, RiskDecision],
    confirmation_available: bool,
) -> tuple[EffectiveSkillPolicy, tuple[SkillUnavailableReason, ...]]:
    """Evaluate every risk label without short-circuit.

    Returns ``(policy, reasons)`` where *reasons* may contain at most one
    ``RISK_DENIED`` and at most one ``CONFIRMATION_UNAVAILABLE``.
    """
    decisions: dict[str, RiskDecision] = {}
    reasons: list[SkillUnavailableReason] = []
    any_deny = False
    any_unavail = False

    for label in sorted(manifest_risk):
        decision = risk_policy.get(label, RiskDecision.DENY)
        decisions[label] = decision
        if decision == RiskDecision.DENY and not any_deny:
            reasons.append(SkillUnavailableReason.RISK_DENIED)
            any_deny = True
        elif decision == RiskDecision.REQUIRE_CONFIRMATION:
            if not confirmation_available and not any_unavail:
                reasons.append(SkillUnavailableReason.CONFIRMATION_UNAVAILABLE)
                any_unavail = True

    requires_confirmation = (
        any(
            risk_policy.get(label, RiskDecision.DENY) == RiskDecision.REQUIRE_CONFIRMATION
            for label in manifest_risk
        )
        and confirmation_available
    )

    return (
        EffectiveSkillPolicy(
            decisions=decisions,
            requires_confirmation=requires_confirmation,
        ),
        tuple(reasons),
    )


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """Immutable, process-local Skill package index.

    Constructed atomically via ``SkillRegistry.index()``.  No partial index
    is ever returned — any invalid candidate rejects the entire construction.
    """

    def __init__(self, packages: tuple[SkillPackage, ...]) -> None:
        self._packages: tuple[SkillPackage, ...] = packages
        self._by_skill_ref: dict[tuple[str, str], SkillPackage] = {
            (p.manifest.skill_id, p.manifest.version): p for p in packages
        }

    # -- construction ----------------------------------------------------------

    @classmethod
    def index(
        cls,
        builtin_root: Path,
        controlled_root: Path | None = None,
    ) -> "SkillRegistry":
        """Complete atomic construction.  Any invalid candidate aborts fully."""
        # Validate roots
        resolved_builtin = _validate_scan_root(builtin_root, label="builtin_root")

        resolved_controlled: Path | None = None
        if controlled_root is not None:
            resolved_controlled = _validate_scan_root(
                controlled_root, label="controlled_root"
            )

        # Mutual nesting check
        if resolved_controlled is not None:
            try:
                resolved_controlled.relative_to(resolved_builtin)
                raise SkillPackageError(SkillPackageErrorCode.ROOT_INVALID)
            except ValueError:
                pass
            try:
                resolved_builtin.relative_to(resolved_controlled)
                raise SkillPackageError(SkillPackageErrorCode.ROOT_INVALID)
            except ValueError:
                pass

        # Scan builtin packages first, then controlled
        builtin_packages = _scan_candidates(resolved_builtin, SkillSource.BUILTIN)
        controlled_packages: list[SkillPackage] = []
        if resolved_controlled is not None:
            controlled_packages = _scan_candidates(
                resolved_controlled, SkillSource.CONTROLLED
            )

        all_packages = builtin_packages + controlled_packages

        # Conflict detection (across and within sources)
        _check_conflicts(all_packages)

        # Deterministic sort: (skill_id, semver_major, semver_minor, semver_patch)
        sorted_packages = sorted(
            all_packages,
            key=lambda p: (
                p.manifest.skill_id,
                *_parse_semver(p.manifest.version),
            ),
        )

        return cls(tuple(sorted_packages))

    # -- public properties -----------------------------------------------------

    @property
    def packages(self) -> tuple[SkillPackage, ...]:
        """All indexed packages sorted ascending by
        ``(skill_id, semver_major, semver_minor, semver_patch)``."""
        return self._packages

    # -- resolution ------------------------------------------------------------

    def resolve(
        self,
        skill_ref: SkillRef,
        exposure_context: ExposureContext,
        capability_snapshot: CapabilitySnapshot,
    ) -> ResolvedSkill:
        """Resolve a single ``SkillRef`` against the current exposure context
        and capability snapshot.

        Returns a ``ResolvedSkill`` with ``available=True`` and empty
        ``reasons`` when fully available, or ``available=False`` with one or
        more ``SkillUnavailableReason`` entries.

        Raises ``SkillRegistryError(SKILL_NOT_INDEXED)`` when *skill_ref*
        is not present in the index.
        """
        _validate_exposure_context(exposure_context)

        # Lookup by exact (skill_id, version)
        key = (skill_ref.skill_id, skill_ref.version)
        pkg = self._by_skill_ref.get(key)
        if pkg is None:
            raise SkillRegistryError(SkillRegistryErrorCode.SKILL_NOT_INDEXED)

        return self._resolve_package(pkg, exposure_context, capability_snapshot)

    def resolve_catalog(
        self,
        exposure_context: ExposureContext,
        capability_snapshot: CapabilitySnapshot,
    ) -> ResolvedSkillCatalog:
        """Resolve every indexed package (including unmounted / unavailable).

        Preserves deterministic package ordering and pins every revision /
        digest field from *exposure_context* and *capability_snapshot*.
        """
        _validate_exposure_context(exposure_context)

        resolved = tuple(
            self._resolve_package(pkg, exposure_context, capability_snapshot)
            for pkg in self._packages
        )

        return ResolvedSkillCatalog(
            skills=resolved,
            mount_revision=exposure_context.mount_revision,
            policy_revision=exposure_context.policy_revision,
            capability_digest=capability_snapshot.capability_digest,
            binding_ref=exposure_context.binding_ref,
            binding_version=exposure_context.binding_version,
            game_session_ref=exposure_context.game_session_ref,
        )

    # -- internal resolution ---------------------------------------------------

    def _resolve_package(
        self,
        pkg: SkillPackage,
        ctx: ExposureContext,
        snapshot: CapabilitySnapshot,
    ) -> ResolvedSkill:
        """Evaluate all dimensions without short-circuit; collect reasons in
        fixed evaluation order."""
        reasons: list[SkillUnavailableReason] = []

        # 1) mount
        mounted_versions = {
            ref.skill_id: ref.version
            for ref in ctx.mounted_skill_refs
        }
        if pkg.manifest.skill_id not in mounted_versions:
            reasons.append(SkillUnavailableReason.NOT_MOUNTED)
        elif mounted_versions[pkg.manifest.skill_id] != pkg.manifest.version:
            reasons.append(SkillUnavailableReason.NOT_MOUNTED)

        # 2) game
        if pkg.manifest.game != ctx.game:
            reasons.append(SkillUnavailableReason.GAME_MISMATCH)

        # 3) dependencies (manifest order)
        bindings: list[DependencyBinding] = []
        for req in pkg.manifest.requires:
            try:
                bound_ref, reason = _bind_dependency(req, snapshot)
            except SkillRegistryError:
                raise  # CAPABILITY_CONFLICT propagates
            bindings.append(DependencyBinding(
                requirement=req,
                capability_ref=bound_ref,
            ))
            if reason is not None:
                reasons.append(reason)

        # 4) risk (lexical label order)
        effective_policy, risk_reasons = _evaluate_risk(
            pkg.manifest.risk,
            ctx.risk_policy,
            ctx.confirmation_available,
        )
        reasons.extend(risk_reasons)

        # Deduplicate and sort by fixed enum order independent of evaluation
        # or risk-label order.
        unique_reasons = sorted(set(reasons), key=lambda r: _REASON_ORDER[r])

        return ResolvedSkill(
            package=pkg,
            available=len(unique_reasons) == 0,
            reasons=tuple(unique_reasons),
            dependency_bindings=tuple(bindings),
            effective_policy=effective_policy,
        )
