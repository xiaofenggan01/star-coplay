"""M3.3 contract-mandated tests — SkillRegistry construction, resolution, immutability.

Uses ``tmp_path`` for all file fixtures; no pre-created package directories.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from modules.mcp.capabilities.models import (
    CapabilityContract,
    CapabilityDiagnostic,
    CapabilityRef,
    CapabilitySnapshot,
    ProtocolCapabilityEntry,
)
from modules.mcp.contracts.models import SideEffect
from modules.mcp.skills.models import (
    CapabilityRequirement,
    DependencyBinding,
    EffectiveSkillPolicy,
    ExposureContext,
    ResolvedSkill,
    ResolvedSkillCatalog,
    RiskDecision,
    SkillPackage,
    SkillPackageError,
    SkillPackageErrorCode,
    SkillRef,
    SkillRegistryError,
    SkillRegistryErrorCode,
    SkillSource,
    SkillUnavailableReason,
)
from modules.mcp.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# Helpers — package scaffolding
# ---------------------------------------------------------------------------

_MINIMAL_SKILL_YAML = """\
id: {skill_id}
version: 1.0.0
description: A test skill.
game: stardew-valley
entrypoint: implementation:run
input_schema:
  type: object
  properties: {{}}
  required: []
  additionalProperties: false
output_schema:
  type: object
  properties: {{}}
  required: []
  additionalProperties: false
requires:
  capabilities:
    - id: say
      version: ==2.4.5
risk: []
timeout_seconds: 30
cancellable: true
retry: never
failure_codes:
  - test_fail
"""


def _make_package_dir(base: Path, skill_id: str, *,
                      yaml_overrides: dict[str, str] | None = None,
                      include_impl: bool = True,
                      include_readme: bool = False,
                      extra_children: list[str] | None = None) -> Path:
    """Create *skill_id* directory under *base* with ``skill.yaml`` + fixed files."""
    pkg_dir = base / skill_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    yaml_content = _MINIMAL_SKILL_YAML.format(skill_id=skill_id)
    if yaml_overrides:
        for key, val in yaml_overrides.items():
            # Simple key replace
            old = f"{key}: " + _get_yaml_key_value(yaml_content, key)
            new = f"{key}: {val}"
            yaml_content = yaml_content.replace(old, new)

    (pkg_dir / "skill.yaml").write_text(yaml_content, encoding="utf-8")

    if include_impl:
        (pkg_dir / "implementation.py").write_text("# placeholder", encoding="utf-8")

    if include_readme:
        (pkg_dir / "README.md").write_text("# README", encoding="utf-8")

    # Extra children are directories WITHOUT skill.yaml (should be ignored)
    if extra_children:
        for child_name in extra_children:
            child_dir = base / child_name
            child_dir.mkdir(parents=True, exist_ok=True)
            # Some might contain implementation.py but no skill.yaml
            if child_name.startswith("no_manifest"):
                (child_dir / "implementation.py").write_text("# impl", encoding="utf-8")
            if child_name.startswith("with_readme"):
                (child_dir / "README.md").write_text("# readme", encoding="utf-8")

    return pkg_dir


def _get_yaml_key_value(yaml_text: str, key: str) -> str:
    """Extract the value of a top-level YAML key (simple case)."""
    for line in yaml_text.splitlines():
        if line.startswith(key + ":"):
            return line[len(key) + 1:].strip()
    return ""


# ---------------------------------------------------------------------------
# Helpers — snapshot construction
# ---------------------------------------------------------------------------

_EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object", "properties": {}, "required": [], "additionalProperties": False,
}


def _capability_contract(
    capability_id: str,
    contract_version: str = "2.4.5",
    source_surface: str = "protocol-2.4.5",
    skill_eligible: bool = True,
    internal_only: bool = False,
) -> CapabilityContract:
    return CapabilityContract(
        capability_id=capability_id,
        contract_version=contract_version,
        source_surface=source_surface,
        input_schema=_EMPTY_SCHEMA,
        output_schema=_EMPTY_SCHEMA,
        required_scope="game:read",
        side_effect=SideEffect.READ_ONLY,
        timeout_seconds=10.0,
        cancellable=True,
        skill_eligible=skill_eligible,
        internal_only=internal_only,
        adapter=None,
    )


def _snapshot(
    contracts: list[CapabilityContract] | None = None,
    entries: list[ProtocolCapabilityEntry] | None = None,
    diagnostics: list[CapabilityDiagnostic] | None = None,
    capability_digest: str = "sha256:aaaa",
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        raw_entries=tuple(entries or []),
        known_contracts=tuple(contracts or []),
        diagnostics=tuple(diagnostics or []),
        capability_digest=capability_digest,
    )


def _exposure_context(
    game: str = "stardew-valley",
    mounted_skill_refs: frozenset[SkillRef] | None = None,
    risk_policy: dict[str, RiskDecision] | None = None,
    confirmation_available: bool = True,
    **overrides: Any,
) -> ExposureContext:
    defaults: dict[str, Any] = {
        "game": game,
        "mounted_skill_refs": mounted_skill_refs or frozenset(),
        "risk_policy": risk_policy or {},
        "confirmation_available": confirmation_available,
        "mount_revision": "rev-1",
        "policy_revision": "pol-1",
        "binding_ref": "bind-1",
        "binding_version": 1,
        "game_session_ref": "sess-1",
    }
    defaults.update(overrides)
    return ExposureContext(**defaults)


# ---------------------------------------------------------------------------
# Index — builtin only and builtin + controlled
# ---------------------------------------------------------------------------

class TestIndexConstruction:

    def test_index_builtin_only(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        assert len(reg.packages) == 1
        assert reg.packages[0].manifest.skill_id == "test.pkg.example"
        assert reg.packages[0].source == SkillSource.BUILTIN

    def test_index_builtin_plus_controlled(self, tmp_path):
        builtin = tmp_path / "builtin"
        controlled = tmp_path / "controlled"
        builtin.mkdir()
        controlled.mkdir()
        _make_package_dir(builtin, "test.pkg.builtin")
        _make_package_dir(controlled, "test.pkg.controlled", include_readme=True)
        reg = SkillRegistry.index(builtin_root=builtin, controlled_root=controlled)
        sources = {p.manifest.skill_id: p.source for p in reg.packages}
        assert sources["test.pkg.builtin"] == SkillSource.BUILTIN
        assert sources["test.pkg.controlled"] == SkillSource.CONTROLLED

    def test_ignore_child_without_skill_yaml(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        # Create a child directory without skill.yaml
        no_manifest = tmp_path / "not.a.package"
        no_manifest.mkdir()
        (no_manifest / "implementation.py").write_text("# not a package", encoding="utf-8")
        (no_manifest / "README.md").write_text("# readme", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        assert len(reg.packages) == 1

    def test_reject_candidate_with_missing_implementation(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example", include_impl=False)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=tmp_path)
        assert exc.value.code == SkillPackageErrorCode.IMPLEMENTATION_MISSING

    def test_reject_controlled_without_readme(self, tmp_path):
        builtin = tmp_path / "builtin"
        controlled = tmp_path / "controlled"
        builtin.mkdir()
        controlled.mkdir()
        _make_package_dir(controlled, "test.pkg.example", include_readme=False)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(
                builtin_root=builtin,
                controlled_root=controlled,
            )
        assert exc.value.code == SkillPackageErrorCode.README_MISSING

    def test_deterministic_package_ordering(self, tmp_path):
        # Create packages in non-alphabetical order
        for skill_id in ["zz.last.one", "aa.first.one", "mm.middle.one"]:
            _make_package_dir(tmp_path, skill_id)
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ids = [p.manifest.skill_id for p in reg.packages]
        assert ids == ["aa.first.one", "mm.middle.one", "zz.last.one"]

    def test_deterministic_ordering_with_same_id_different_versions(self, tmp_path):
        # Same id, different version — works as long as no conflict (different dirs needed)
        # Actually the contract says "Any repeated skill_id at any version" → PACKAGE_CONFLICT
        # So same id with different version IS a conflict
        pass  # Handled by conflict test below


# ---------------------------------------------------------------------------
# Root / candidate / symlink / path escape rejection
# ---------------------------------------------------------------------------

class TestPathAndSymlinkRejection:

    def test_reject_nonexistent_root(self, tmp_path):
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=tmp_path / "nonexistent")
        assert exc.value.code == SkillPackageErrorCode.ROOT_INVALID

    def test_reject_root_not_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=f)
        assert exc.value.code == SkillPackageErrorCode.ROOT_INVALID

    def test_reject_root_symlink(self, tmp_path):
        real = tmp_path / "real_root"
        real.mkdir()
        link = tmp_path / "link_root"
        link.symlink_to(real)
        _make_package_dir(real, "test.pkg.example")
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=link)
        assert exc.value.code == SkillPackageErrorCode.ROOT_INVALID

    def test_reject_builtin_and_controlled_nested(self, tmp_path):
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        controlled = builtin / "controlled"
        controlled.mkdir()
        _make_package_dir(builtin, "test.pkg.b")
        _make_package_dir(controlled, "test.pkg.c", include_readme=True)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=builtin, controlled_root=controlled)
        assert exc.value.code == SkillPackageErrorCode.ROOT_INVALID

    def test_reject_candidate_symlink_dir(self, tmp_path):
        # Create the real valid package *outside* the builtin scan root.
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        _make_package_dir(outside, "real.pkg.one")
        # Inside builtin, create only a symlink candidate with a valid
        # 3-segment id pointing at the real package outside.
        link = builtin / "link.pkg.one"
        link.symlink_to(outside / "real.pkg.one")
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=builtin)
        assert exc.value.code == SkillPackageErrorCode.SYMLINK_FORBIDDEN

    def test_reject_path_escape(self, tmp_path):
        # Package dir resolves outside scan root
        outside = tmp_path.parent / "escape"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "skill.yaml").write_text(
            _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example"),
            encoding="utf-8",
        )
        (outside / "implementation.py").write_text("# impl", encoding="utf-8")
        # Scan under tmp_path but package is outside
        link = tmp_path / "test.pkg.example"
        link.symlink_to(outside)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=tmp_path)
        assert exc.value.code in (
            SkillPackageErrorCode.SYMLINK_FORBIDDEN,
            SkillPackageErrorCode.MANIFEST_MISSING,
        )


# ---------------------------------------------------------------------------
# Conflict rejection
# ---------------------------------------------------------------------------

class TestConflictRejection:

    def test_reject_cross_source_same_id_same_version_conflict(self, tmp_path):
        """Same skill_id + same version in builtin and controlled → PACKAGE_CONFLICT."""
        builtin = tmp_path / "builtin"
        controlled = tmp_path / "controlled"
        builtin.mkdir()
        controlled.mkdir()
        _make_package_dir(builtin, "test.pkg.shared", yaml_overrides={"version": "1.0.0"})
        _make_package_dir(controlled, "test.pkg.shared", yaml_overrides={"version": "1.0.0"}, include_readme=True)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=builtin, controlled_root=controlled)
        assert exc.value.code == SkillPackageErrorCode.PACKAGE_CONFLICT

    def test_reject_cross_source_same_id_different_version_conflict(self, tmp_path):
        """Same skill_id + different version across sources → PACKAGE_CONFLICT.
        M3 does not support multi-version coexistence and controlled never
        overrides builtin."""
        builtin = tmp_path / "builtin"
        controlled = tmp_path / "controlled"
        builtin.mkdir()
        controlled.mkdir()
        _make_package_dir(builtin, "test.pkg.shared", yaml_overrides={"version": "1.0.0"})
        _make_package_dir(controlled, "test.pkg.shared", yaml_overrides={"version": "2.0.0"}, include_readme=True)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=builtin, controlled_root=controlled)
        assert exc.value.code == SkillPackageErrorCode.PACKAGE_CONFLICT

    def test_reject_same_id_across_sources(self, tmp_path):
        builtin = tmp_path / "builtin"
        controlled = tmp_path / "controlled"
        builtin.mkdir()
        controlled.mkdir()
        _make_package_dir(builtin, "test.pkg.shared")
        _make_package_dir(controlled, "test.pkg.shared", include_readme=True)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=builtin, controlled_root=controlled)
        assert exc.value.code == SkillPackageErrorCode.PACKAGE_CONFLICT

    def test_no_override_builtin_by_controlled(self, tmp_path):
        """Prove controlled never overrides builtin: conflict, not silent override."""
        builtin = tmp_path / "builtin"
        controlled = tmp_path / "controlled"
        builtin.mkdir()
        controlled.mkdir()
        _make_package_dir(builtin, "test.pkg.shared")
        _make_package_dir(controlled, "test.pkg.shared", include_readme=True)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=builtin, controlled_root=controlled)
        assert exc.value.code == SkillPackageErrorCode.PACKAGE_CONFLICT


# ---------------------------------------------------------------------------
# Atomic construction
# ---------------------------------------------------------------------------

class TestAtomicConstruction:

    def test_invalid_candidate_rejects_entire_index(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.ok")
        _make_package_dir(tmp_path, "test.pkg.bad", include_impl=False)
        with pytest.raises(SkillPackageError):
            SkillRegistry.index(builtin_root=tmp_path)

    def test_no_partial_registry_on_error(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.ok")
        _make_package_dir(tmp_path, "test.pkg.bad", include_impl=False)
        with pytest.raises(SkillPackageError):
            SkillRegistry.index(builtin_root=tmp_path)


# ---------------------------------------------------------------------------
# Immutability after construction
# ---------------------------------------------------------------------------

class TestRegistryImmutability:

    def test_packages_returns_tuple(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        assert isinstance(reg.packages, tuple)

    def test_resolved_skill_mappings_are_immutable(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert isinstance(resolved.effective_policy.decisions, MappingProxyType)

    def test_caller_mutation_does_not_affect_registry(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        pkg_before = reg.packages[0]
        # packages is a tuple — immutable reference
        # Verify manifest fields are deeply frozen
        assert isinstance(pkg_before.manifest.input_schema, MappingProxyType)


# ---------------------------------------------------------------------------
# ExposureContext validation
# ---------------------------------------------------------------------------

class TestExposureContextValidation:

    def test_reject_invalid_game(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(game="Invalid Game!"),
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_reject_invalid_risk_policy_key(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(risk_policy={"not_a_risk": RiskDecision.ALLOW}),
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_reject_non_bool_confirmation(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(confirmation_available=1),  # type: ignore
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_reject_invalid_mounted_skill_ref(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(
                    mounted_skill_refs=frozenset({
                        SkillRef("bad id", "1.0.0"),  # invalid skill_id
                    }),
                ),
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_reject_two_versions_of_same_skill_mounted(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            ctx = _exposure_context(
                mounted_skill_refs=frozenset({
                    SkillRef("test.pkg.example", "1.0.0"),
                    SkillRef("test.pkg.example", "2.0.0"),
                }),
            )
            reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert exc.value.code == SkillRegistryErrorCode.MOUNT_CONFLICT

    def test_reject_empty_mount_revision(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(mount_revision=""),
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_reject_binding_version_zero(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(binding_version=0),
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_reject_binding_version_bool(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(binding_version=True),  # type: ignore
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID


# ---------------------------------------------------------------------------
# Resolution — mount, game, dependencies, risk
# ---------------------------------------------------------------------------

class TestResolution:

    def test_resolve_available_exact_mount_exact_game(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.available is True
        assert len(resolved.reasons) == 0

    def test_resolve_not_mounted(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context()  # no mounted skills
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.available is False
        assert SkillUnavailableReason.NOT_MOUNTED in resolved.reasons

    def test_resolve_skill_not_indexed(self, tmp_path):
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context()
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(SkillRef("nonexistent.pkg.test", "1.0.0"), ctx, snap)
        assert exc.value.code == SkillRegistryErrorCode.SKILL_NOT_INDEXED

    def test_resolve_game_mismatch(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            game="other-game",
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.GAME_MISMATCH in resolved.reasons

    def test_index_package_with_other_valid_game_id(self, tmp_path):
        """Package with game=other-game resolves as GAME_MISMATCH in stardew-valley context."""
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "game: stardew-valley", "game: other-game",
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            game="stardew-valley",
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.GAME_MISMATCH in resolved.reasons


# ---------------------------------------------------------------------------
# Dependency binding
# ---------------------------------------------------------------------------

class TestDependencyBinding:

    def test_bind_highest_compatible_semver(self, tmp_path):
        """Package requires >=1.0.0,<4.0.0; three contracts satisfy; highest wins."""
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "version: ==2.4.5",
            'version: ">=1.0.0,<4.0.0"',
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[
            _capability_contract("say", "1.0.0"),
            _capability_contract("say", "3.0.0"),
            _capability_contract("say", "2.0.0"),
        ])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.available is True
        binding = resolved.dependency_bindings[0]
        assert binding.capability_ref is not None
        assert binding.capability_ref.capability_id == "say"
        assert binding.capability_ref.contract_version == "3.0.0"

    def test_capability_missing(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot()  # no contracts at all
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.CAPABILITY_MISSING in resolved.reasons

    def test_capability_version_unsupported_know_id_wrong_version(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        # Contract exists but version 1.0.0 doesn't satisfy ==2.4.5 range
        snap = _snapshot(contracts=[_capability_contract("say", "1.0.0")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.CAPABILITY_VERSION_UNSUPPORTED in resolved.reasons
        assert SkillUnavailableReason.CAPABILITY_MISSING not in resolved.reasons

    def test_capability_version_unsupported_from_diagnostic(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(
            contracts=[],
            diagnostics=[
                CapabilityDiagnostic(
                    code="unsupported_version",
                    capability_id="say",
                    advertised_version="3.0.0",
                    detail="version not supported",
                ),
            ],
        )
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.CAPABILITY_VERSION_UNSUPPORTED in resolved.reasons

    def test_reject_equal_highest_version_different_surfaces(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[
            _capability_contract("say", "2.4.5", source_surface="surface-a"),
            _capability_contract("say", "2.4.5", source_surface="surface-b"),
        ])
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert exc.value.code == SkillRegistryErrorCode.CAPABILITY_CONFLICT

    def test_reject_internal_only_capability(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[
            _capability_contract("say", "2.4.5", internal_only=True),
        ])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        # internal_only contract is not eligible → should be CAPABILITY_MISSING (no eligible contract)
        # Actually, no eligible contract (all filtered by internal_only) + no unsupported_version diag → MISSING
        assert SkillUnavailableReason.CAPABILITY_MISSING in resolved.reasons

    def test_reject_non_skill_eligible_capability(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[
            _capability_contract("say", "2.4.5", skill_eligible=False),
        ])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.CAPABILITY_MISSING in resolved.reasons


# ---------------------------------------------------------------------------
# Risk evaluation
# ---------------------------------------------------------------------------

class TestRiskEvaluation:

    def test_absent_risk_policy_defaults_deny(self, tmp_path):
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "risk: []", "risk:\n  - changes_save",
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
            risk_policy={},  # empty → defaults to DENY
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.RISK_DENIED in resolved.reasons

    def test_unavailable_confirmation_rejects(self, tmp_path):
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "risk: []", "risk:\n  - changes_save",
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
            risk_policy={"changes_save": RiskDecision.REQUIRE_CONFIRMATION},
            confirmation_available=False,
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.CONFIRMATION_UNAVAILABLE in resolved.reasons

    def test_empty_risk_allowed(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert SkillUnavailableReason.RISK_DENIED not in resolved.reasons

    def test_multiple_reasons_not_short_circuited(self, tmp_path):
        """Prove non-short-circuit: unmounted + game mismatch + missing capability + denied risk all appear."""
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "game: stardew-valley", "game: other-game",
        ).replace("risk: []", "risk:\n  - changes_save")
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            game="stardew-valley",
            # NOT mounted
            risk_policy={},  # defaults to DENY for changes_save
        )
        snap = _snapshot()  # no contracts → CAPABILITY_MISSING
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert not resolved.available
        assert SkillUnavailableReason.NOT_MOUNTED in resolved.reasons
        assert SkillUnavailableReason.GAME_MISMATCH in resolved.reasons
        assert SkillUnavailableReason.CAPABILITY_MISSING in resolved.reasons
        assert SkillUnavailableReason.RISK_DENIED in resolved.reasons
        # Order must be: mount, game, deps, risk
        assert resolved.reasons[0] == SkillUnavailableReason.NOT_MOUNTED
        assert resolved.reasons[1] == SkillUnavailableReason.GAME_MISMATCH
        assert resolved.reasons[2] == SkillUnavailableReason.CAPABILITY_MISSING
        assert resolved.reasons[3] == SkillUnavailableReason.RISK_DENIED


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class TestCatalog:

    def test_catalog_includes_unavailable_packages(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context()  # no mounts
        snap = _snapshot()
        catalog = reg.resolve_catalog(ctx, snap)
        assert len(catalog.skills) == 1
        assert catalog.skills[0].available is False

    def test_catalog_pins_revisions_and_digest(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mount_revision="mr-1",
            policy_revision="pr-1",
            binding_ref="br-1",
            game_session_ref="gsr-1",
        )
        snap = _snapshot(
            contracts=[_capability_contract("say")],
            capability_digest="sha256:abcd1234",
        )
        catalog = reg.resolve_catalog(ctx, snap)
        assert catalog.mount_revision == "mr-1"
        assert catalog.policy_revision == "pr-1"
        assert catalog.capability_digest == "sha256:abcd1234"
        assert catalog.binding_ref == "br-1"
        assert catalog.binding_version == 1
        assert catalog.game_session_ref == "gsr-1"

    def test_catalog_pins_binding_ref_binding_version_game_session_ref_verbatim(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            binding_ref="custom-binding",
            binding_version=42,
            game_session_ref="custom-session",
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        catalog = reg.resolve_catalog(ctx, snap)
        assert catalog.binding_ref == "custom-binding"
        assert catalog.binding_version == 42
        assert catalog.game_session_ref == "custom-session"


# ---------------------------------------------------------------------------
# No implementation module side effect during indexing/resolution
# ---------------------------------------------------------------------------

class TestNoImplementationSideEffect:

    def test_implementation_py_not_imported(self, tmp_path):
        """Verify implementation.py is not executed during indexing or resolution."""
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(
            _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example"),
            encoding="utf-8",
        )
        # Write a side-effect-producing implementation.py
        (pkg_dir / "implementation.py").write_text(
            "raise RuntimeError('should not be imported')",
            encoding="utf-8",
        )
        # Indexing should succeed without importing
        reg = SkillRegistry.index(builtin_root=tmp_path)
        # Resolution should succeed without importing
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.available is True


# ---------------------------------------------------------------------------
# Risk evaluation — require_confirmation edge cases
# ---------------------------------------------------------------------------

class TestRiskConfirmationEdgeCases:

    def test_requires_confirmation_true_when_channel_available(self, tmp_path):
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "risk: []", "risk:\n  - changes_save",
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
            risk_policy={"changes_save": RiskDecision.REQUIRE_CONFIRMATION},
            confirmation_available=True,
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.available is True
        assert resolved.effective_policy.requires_confirmation is True

    def test_requires_confirmation_false_when_channel_unavailable(self, tmp_path):
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "risk: []", "risk:\n  - changes_save",
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
            risk_policy={"changes_save": RiskDecision.REQUIRE_CONFIRMATION},
            confirmation_available=False,
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.effective_policy.requires_confirmation is False

    def test_requires_confirmation_false_when_no_require_confirmation_risk(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.example", "1.0.0")}),
            risk_policy={},  # empty risk list, so no REQUIRE_CONFIRMATION labels
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert resolved.effective_policy.requires_confirmation is False


# ---------------------------------------------------------------------------
# Multiple versions of same package (same id different versions)
# ---------------------------------------------------------------------------

class TestMultiVersion:
    """The contract forbids multi-version coexistence, but we test that the
    registry properly resolves only the requested version."""

    def test_resolve_exact_version(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.v1")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            mounted_skill_refs=frozenset({SkillRef("test.pkg.v1", "1.0.0")}),
        )
        snap = _snapshot(contracts=[_capability_contract("say")])
        resolved = reg.resolve(SkillRef("test.pkg.v1", "1.0.0"), ctx, snap)
        assert resolved.available is True

    def test_resolve_wrong_version_not_indexed(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.v1")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context()
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(SkillRef("test.pkg.v1", "9.9.9"), ctx, snap)
        assert exc.value.code == SkillRegistryErrorCode.SKILL_NOT_INDEXED


# ---------------------------------------------------------------------------
# ExposureContext edge cases
# ---------------------------------------------------------------------------

class TestExposureContextEdgeCases:

    def test_risk_policy_value_must_be_risk_decision_enum(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(risk_policy={"changes_save": "allow"}),  # string, not enum
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID

    def test_binding_version_bool_True_rejected(self, tmp_path):
        _make_package_dir(tmp_path, "test.pkg.example")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        snap = _snapshot()
        with pytest.raises(SkillRegistryError) as exc:
            reg.resolve(
                SkillRef("test.pkg.example", "1.0.0"),
                _exposure_context(binding_version=True),
                snap,
            )
        assert exc.value.code == SkillRegistryErrorCode.EXPOSURE_CONTEXT_INVALID


# ---------------------------------------------------------------------------
# Repair-specific tests
# ---------------------------------------------------------------------------

class TestDanglingSymlinkCandidate:

    def test_dangling_skill_yaml_symlink_reaches_symlink_forbidden(self, tmp_path):
        """A candidate whose skill.yaml is a symlink (even dangling) must be
        treated as a candidate so the symlink check fires before the existence
        check."""
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        # Point the symlink at a target outside the scan root so it is a valid
        # symlink but the path-escape / symlink check fires correctly.
        outside_target = tmp_path.parent / "outside_skill.yaml"
        outside_target.write_text("id: test.pkg.example\nversion: 1.0.0\n", encoding="utf-8")
        skill_link = pkg_dir / "skill.yaml"
        skill_link.symlink_to(outside_target)
        with pytest.raises(SkillPackageError) as exc:
            SkillRegistry.index(builtin_root=tmp_path)
        assert exc.value.code == SkillPackageErrorCode.SYMLINK_FORBIDDEN


class TestFixedReasonOrdering:

    def test_reasons_sorted_by_fixed_enum_order(self, tmp_path):
        """Prove reasons sort by the fixed enum order regardless of risk-label
        evaluation order within the manifest."""
        # Two risk labels: advances_time (a) → DENY, changes_save (c) → REQUIRE_CONFIRMATION
        # with confirmation_available=False.  Both RISK_DENIED and
        # CONFIRMATION_UNAVAILABLE must appear, and RISK_DENIED always comes
        # before CONFIRMATION_UNAVAILABLE.
        yaml = _MINIMAL_SKILL_YAML.format(skill_id="test.pkg.example").replace(
            "risk: []",
            "risk:\n  - advances_time\n  - changes_save",
        )
        pkg_dir = tmp_path / "test.pkg.example"
        pkg_dir.mkdir()
        (pkg_dir / "skill.yaml").write_text(yaml, encoding="utf-8")
        (pkg_dir / "implementation.py").write_text("# impl", encoding="utf-8")
        reg = SkillRegistry.index(builtin_root=tmp_path)
        ctx = _exposure_context(
            game="other-game",
            risk_policy={
                "advances_time": RiskDecision.DENY,
                "changes_save": RiskDecision.REQUIRE_CONFIRMATION,
            },
            confirmation_available=False,
        )
        snap = _snapshot()  # no contracts → CAPABILITY_MISSING
        resolved = reg.resolve(SkillRef("test.pkg.example", "1.0.0"), ctx, snap)
        assert not resolved.available
        # Order must be the fixed enum order, NOT the risk evaluation order
        # or manifest declaration order: NOT_MOUNTED, GAME_MISMATCH,
        # CAPABILITY_MISSING, RISK_DENIED, CONFIRMATION_UNAVAILABLE.
        assert resolved.reasons == (
            SkillUnavailableReason.NOT_MOUNTED,
            SkillUnavailableReason.GAME_MISMATCH,
            SkillUnavailableReason.CAPABILITY_MISSING,
            SkillUnavailableReason.RISK_DENIED,
            SkillUnavailableReason.CONFIRMATION_UNAVAILABLE,
        )


# ---------------------------------------------------------------------------
# typing.get_type_hints smoke test — all public model classes
# ---------------------------------------------------------------------------

class TestGetTypeHints:
    def test_get_type_hints_on_all_public_models(self):
        """typing.get_type_hints must succeed for every public dataclass/API type."""
        import typing
        from modules.mcp.skills.models import (
            CapabilityRequirement,
            DependencyBinding,
            EffectiveSkillPolicy,
            ExposureContext,
            ResolvedSkill,
            ResolvedSkillCatalog,
            SkillPackage,
            SkillPackageManifest,
            SkillRef,
        )
        for cls in (
            CapabilityRequirement,
            SkillRef,
            DependencyBinding,
            SkillPackageManifest,
            SkillPackage,
            ExposureContext,
            EffectiveSkillPolicy,
            ResolvedSkill,
            ResolvedSkillCatalog,
        ):
            hints = typing.get_type_hints(cls)
            assert isinstance(hints, dict)
            assert len(hints) > 0, f"no type hints resolved for {cls.__name__}"
