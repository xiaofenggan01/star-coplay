"""Release Manifest v1: immutable, deterministic inventory for M4.

The release manifest is a build-time artifact.  It is deliberately separate
from the live Mod capability snapshot: unknown runtime entries never alter it.
No package discovery or loading happens here; callers provide the audited
package roots and the verifier compares the resulting inventory exactly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from modules.mcp.skills.loader import _compute_implementation_digest
from modules.mcp.skills.models import SkillRef, SkillSource
from modules.mcp.skills.schema import parse_skill_package
from modules.mcp.skills.registry import SkillRegistry

SCHEMA_VERSION = "1"
PROTO_CAPABILITIES: tuple[str, ...] = (
    "emote", "equip", "face", "go_to", "go_to_bed", "interact",
    "menu_click", "menu_close", "move_to", "open_menu",
    "query_inspect_refs", "query_inventory_snapshot", "query_menu",
    "query_runtime_snapshot", "query_ui_snapshot", "query_world_region",
    "say", "use_tool",
)
V2_CAPABILITIES: tuple[str, ...] = (
    "act_on_target", "buy_items", "chop_trees", "equip_matching_item",
    "execute_on_targets", "harvest_crops", "menu_interact", "mine_rocks",
    "move_to_target", "navigate_to_target", "observe_current_view",
    "plant_crops", "ship_items", "sleep_for_night", "use_tool_at_target",
    "water_crops",
)
BUILTIN_COMPOSITES = (
    "stardew.basic.observe_current_view",
    "stardew.basic.navigate_and_interact",
    "stardew.knowledge.search_wiki",
    "stardew.knowledge.read_wiki",
    "stardew.advanced.chop_trees",
    "stardew.advanced.clear_farm",
    "stardew.advanced.find_interactions",
    "stardew.advanced.go_bed",
    "stardew.advanced.go_shopping",
    "stardew.advanced.harvest_crops",
    "stardew.advanced.inspect",
    "stardew.advanced.observe",
    "stardew.advanced.pet_animals",
    "stardew.advanced.search_facts",
    "stardew.advanced.ship_items",
    "stardew.advanced.walk_route",
    "stardew.advanced.water_crops",
    "stardew.navigation.go_to_place",
)
CONTROLLED_COMPOSITES = (
    "stardew.daily.water_crops",
    "stardew.daily.harvest_crops",
)

# Release-time authority for the risk surface of every builtin Skill.  Package
# YAML remains human-readable, but a drift from this table fails the release
# build instead of silently weakening Registry policy or MCP annotations.
BUILTIN_SKILL_RISKS: Mapping[str, frozenset[str]] = {
    **{f"stardew.proto.{capability_id}": frozenset()
       for capability_id in PROTO_CAPABILITIES},
    "stardew.proto.go_to_bed": frozenset({"advances_time", "changes_save"}),
    "stardew.proto.interact": frozenset({"changes_save"}),
    "stardew.proto.menu_click": frozenset({
        "changes_save", "consumes_item", "spends_money",
    }),
    "stardew.proto.use_tool": frozenset({"changes_save", "consumes_energy"}),
    "stardew.basic.observe_current_view": frozenset(),
    "stardew.basic.navigate_and_interact": frozenset({"changes_save"}),
    "stardew.knowledge.search_wiki": frozenset(),
    "stardew.knowledge.read_wiki": frozenset(),
    "stardew.advanced.chop_trees": frozenset({"changes_save", "consumes_energy"}),
    "stardew.advanced.clear_farm": frozenset({"changes_save", "consumes_energy"}),
    "stardew.advanced.find_interactions": frozenset(),
    "stardew.advanced.go_bed": frozenset({"advances_time", "changes_save"}),
    "stardew.advanced.go_shopping": frozenset({"changes_save", "spends_money"}),
    "stardew.advanced.harvest_crops": frozenset({"changes_save"}),
    "stardew.advanced.inspect": frozenset(),
    "stardew.advanced.observe": frozenset(),
    "stardew.advanced.pet_animals": frozenset({"changes_save"}),
    "stardew.advanced.search_facts": frozenset(),
    "stardew.advanced.ship_items": frozenset({"changes_save"}),
    "stardew.advanced.walk_route": frozenset(),
    "stardew.advanced.water_crops": frozenset({"changes_save", "consumes_energy"}),
    "stardew.navigation.go_to_place": frozenset(),
}


def default_mounted_skill_refs(
    builtin_root: Path,
    controlled_root: Path | None = None,
) -> frozenset[SkillRef]:
    """Return the exact release package refs mounted by a profile default.

    This is a process-start, audited-root operation.  It never discovers
    packages outside the supplied roots and does not alter permission/risk
    decisions; Registry resolution still applies those gates per session.
    """
    registry = SkillRegistry.index(builtin_root, controlled_root)
    return frozenset(
        SkillRef(p.manifest.skill_id, p.manifest.version)
        for p in registry.packages
    )


class ReleaseManifestError(ValueError):
    """Safe, deterministic release-manifest validation failure."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ReleaseManifestError("non_string_key")
        return {k: _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ReleaseManifestError("non_finite_number")
        return value
    raise ReleaseManifestError("unsupported_value")


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    """UTF-8 compact sorted-key JSON used for every release digest."""
    return json.dumps(_jsonable(document), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def release_digest(document: Mapping[str, Any]) -> str:
    body = dict(document)
    body.pop("release_digest", None)
    return "sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest()


def _schema_digest(schema: Mapping[str, Any] | None) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(schema or {})).hexdigest()


def _package_record(package: Any) -> dict[str, Any]:
    m = package.manifest
    return {
        "skill_id": m.skill_id,
        "version": m.version,
        "source": package.source.value,
        "package_digest": package.manifest_digest,
        "implementation_digest": _compute_implementation_digest(
            package.implementation_path.read_bytes()),
        "game": m.game,
        "entrypoint": f"{m.entrypoint_module}:{m.entrypoint_callable}",
        "requires": [{"id": r.capability_id, "version_range": r.version_range}
                     for r in m.requires],
        "input_schema_digest": _schema_digest(m.input_schema),
        "output_schema_digest": _schema_digest(m.output_schema),
        "risk": sorted(m.risk),
        "timeout_seconds": m.timeout_seconds,
        "cancellable": m.cancellable,
        "retry": m.retry,
    }


def _capability_record(source_surface: str, capability_id: str) -> dict[str, Any]:
    # Bind schema digests to the canonical contract source used at runtime.
    # Building the live snapshot remains the Capability Runtime's job.
    try:
        from agent.protocol.canonical_surface import canonical_specs
        spec = canonical_specs().get(capability_id)
    except Exception as exc:
        raise ReleaseManifestError("canonical_capability_source_unavailable") from exc
    if spec is None or spec.source_surface != source_surface:
        raise ReleaseManifestError("canonical_capability_source_mismatch")
    input_schema = spec.input_schema
    output_schema = spec.output_schema
    cancellable = spec.cancellable
    prefix = "proto" if source_surface == "protocol-2.4.5" else "v2-compat"
    return {
        "id": prefix + ":" + capability_id,
        "contract_version": "2.4.5",
        "input_schema_digest": _schema_digest(input_schema),
        "output_schema_digest": _schema_digest(output_schema),
        "error_codes": [],
        "lifecycle": {"cancellable": cancellable},
        "source": "builtin",
    }


def build_release_manifest(builtin_root: Path, controlled_root: Path | None = None,
                           *, include_controlled: bool = True) -> dict[str, Any]:
    """Build the exact v1 inventory from real package parser/indexer output."""
    if not builtin_root.is_dir():
        raise ReleaseManifestError("builtin_root_invalid")
    packages = list(SkillRegistry.index(
        builtin_root,
        controlled_root if include_controlled else None,
    ).packages)
    records = [_package_record(p) for p in packages]
    for package in packages:
        if package.source is not SkillSource.BUILTIN:
            continue
        expected_risk = BUILTIN_SKILL_RISKS.get(package.manifest.skill_id)
        if expected_risk is None or package.manifest.risk != expected_risk:
            raise ReleaseManifestError("builtin_skill_risk_mismatch")
    expected_atomic = {f"stardew.proto.{cid}@1.0.0" for cid in PROTO_CAPABILITIES}
    refs = {f"{r['skill_id']}@{r['version']}" for r in records}
    expected = expected_atomic | {x + "@1.0.0" for x in BUILTIN_COMPOSITES}
    if include_controlled:
        expected |= {x + "@1.0.0" for x in CONTROLLED_COMPOSITES}
    if refs != expected:
        raise ReleaseManifestError("package_set_mismatch")
    document: dict[str, Any] = {
        "manifest_version": SCHEMA_VERSION,
        "capabilities": (
            [_capability_record("protocol-2.4.5", x) for x in PROTO_CAPABILITIES]
            + [_capability_record("v2-compat", x) for x in V2_CAPABILITIES]
        ),
        "skills": sorted(records, key=lambda x: (x["skill_id"], x["version"])),
    }
    document["release_digest"] = release_digest(document)
    return document


def verify_release_manifest(document: Mapping[str, Any], *, builtin_root: Path | None = None,
                            controlled_root: Path | None = None,
                            expected: Mapping[str, Any] | None = None) -> str:
    """Fail closed on schema, duplicate refs, digest, or inventory drift."""
    if not isinstance(document, Mapping) or document.get("manifest_version") != SCHEMA_VERSION:
        raise ReleaseManifestError("manifest_version_invalid")
    if set(document) != {"manifest_version", "capabilities", "skills", "release_digest"}:
        raise ReleaseManifestError("manifest_fields_invalid")
    caps, skills = document["capabilities"], document["skills"]
    if not isinstance(caps, list) or not isinstance(skills, list):
        raise ReleaseManifestError("manifest_arrays_invalid")
    if len(caps) != len(PROTO_CAPABILITIES) + len(V2_CAPABILITIES) or len(skills) == 0:
        raise ReleaseManifestError("manifest_cardinality_invalid")
    cap_ids: list[str] = []
    for cap in caps:
        if not isinstance(cap, Mapping):
            raise ReleaseManifestError("capability_entry_invalid")
        required = {"id", "contract_version", "input_schema_digest",
                    "output_schema_digest", "error_codes", "lifecycle", "source"}
        if set(cap) != required or cap.get("source") != "builtin":
            raise ReleaseManifestError("capability_entry_invalid")
        if not isinstance(cap["id"], str) or not cap["id"].startswith(("proto:", "v2-compat:")):
            raise ReleaseManifestError("capability_entry_invalid")
        for key in ("input_schema_digest", "output_schema_digest"):
            if not isinstance(cap[key], str) or not cap[key].startswith("sha256:"):
                raise ReleaseManifestError("digest_missing")
        if not isinstance(cap["error_codes"], list) or not isinstance(cap["lifecycle"], Mapping):
            raise ReleaseManifestError("capability_entry_invalid")
        cap_ids.append(cap["id"])
    expected_cap_ids = {"proto:" + x for x in PROTO_CAPABILITIES} | {
        "v2-compat:" + x for x in V2_CAPABILITIES
    }
    if set(cap_ids) != expected_cap_ids:
        raise ReleaseManifestError("capability_set_mismatch")
    skill_refs: list[tuple[str, str]] = []
    builtin_refs: set[tuple[str, str]] = set()
    controlled_refs: set[tuple[str, str]] = set()
    for skill in skills:
        if not isinstance(skill, Mapping):
            raise ReleaseManifestError("skill_entry_invalid")
        required = {"skill_id", "version", "source", "package_digest",
                    "implementation_digest", "game", "entrypoint", "requires",
                    "input_schema_digest", "output_schema_digest", "risk",
                    "timeout_seconds", "cancellable", "retry"}
        if set(skill) != required:
            raise ReleaseManifestError("skill_entry_invalid")
        if skill.get("source") not in {"builtin", "controlled"}:
            raise ReleaseManifestError("skill_entry_invalid")
        for key in ("package_digest", "implementation_digest", "input_schema_digest", "output_schema_digest"):
            if not isinstance(skill[key], str) or not skill[key].startswith("sha256:"):
                raise ReleaseManifestError("digest_missing")
        if not isinstance(skill["skill_id"], str) or not isinstance(skill["version"], str):
            raise ReleaseManifestError("skill_entry_invalid")
        skill_refs.append((skill["skill_id"], skill["version"]))
        (controlled_refs if skill["source"] == "controlled" else builtin_refs).add(
            (skill["skill_id"], skill["version"])
        )
    if len(cap_ids) != len(set(cap_ids)) or len(skill_refs) != len(set(skill_refs)):
        raise ReleaseManifestError("duplicate_entry")
    expected_builtin_refs = {
        (f"stardew.proto.{cid}", "1.0.0") for cid in PROTO_CAPABILITIES
    } | {(x, "1.0.0") for x in BUILTIN_COMPOSITES}
    expected_controlled_refs = {(x, "1.0.0") for x in CONTROLLED_COMPOSITES}
    if builtin_refs != expected_builtin_refs or controlled_refs != expected_controlled_refs:
        raise ReleaseManifestError("skill_set_mismatch")
    actual = release_digest(document)
    if document.get("release_digest") != actual:
        raise ReleaseManifestError("release_digest_mismatch")
    if builtin_root is not None:
        rebuilt = build_release_manifest(
            builtin_root,
            controlled_root,
            include_controlled=controlled_root is not None,
        )
        if canonical_bytes(document) != canonical_bytes(rebuilt):
            raise ReleaseManifestError("release_drift")
    if expected is not None and canonical_bytes(document) != canonical_bytes(expected):
        raise ReleaseManifestError("release_drift")
    return actual


def load_release_manifest(path: Path, *, builtin_root: Path | None = None,
                          controlled_root: Path | None = None) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseManifestError("manifest_read_error") from exc
    if not isinstance(raw, dict):
        raise ReleaseManifestError("manifest_fields_invalid")
    if builtin_root is None:
        raise ReleaseManifestError("audited_root_required")
    verify_release_manifest(raw, builtin_root=builtin_root, controlled_root=controlled_root)
    return raw


def write_release_manifest(path: Path, document: Mapping[str, Any]) -> None:
    verify_release_manifest(document)
    path.write_text(yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=False), encoding="utf-8")
