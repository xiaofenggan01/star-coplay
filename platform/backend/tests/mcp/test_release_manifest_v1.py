from pathlib import Path

import pytest

from modules.mcp.release_manifest import (
    BUILTIN_COMPOSITES,
    BUILTIN_SKILL_RISKS,
    PROTO_CAPABILITIES,
    V2_CAPABILITIES,
    ReleaseManifestError,
    build_release_manifest,
    load_release_manifest,
    verify_release_manifest,
)


def test_builtin_risk_authority_covers_exact_release_surface():
    expected = {f"stardew.proto.{capability_id}" for capability_id in PROTO_CAPABILITIES}
    expected.update(BUILTIN_COMPOSITES)
    assert set(BUILTIN_SKILL_RISKS) == expected
    assert BUILTIN_SKILL_RISKS["stardew.proto.go_to_bed"] == {
        "advances_time", "changes_save",
    }
    assert BUILTIN_SKILL_RISKS["stardew.proto.menu_click"] == {
        "changes_save", "consumes_item", "spends_money",
    }


ROOT = Path(__file__).resolve().parents[2]
BUILTIN = ROOT / "modules/mcp/skills/builtin"
CONTROLLED = ROOT / "modules/mcp/skills/controlled-m4"
ARTIFACT = ROOT / "modules/mcp/release-manifest.yaml"


def test_release_manifest_v1_artifact_is_reproducible():
    artifact = load_release_manifest(ARTIFACT, builtin_root=BUILTIN, controlled_root=CONTROLLED)
    rebuilt = build_release_manifest(BUILTIN, CONTROLLED)
    assert verify_release_manifest(artifact, expected=rebuilt) == artifact["release_digest"]
    assert len(artifact["capabilities"]) == len(PROTO_CAPABILITIES) + len(V2_CAPABILITIES) == 34
    assert len(artifact["skills"]) == 38


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(release_digest="sha256:bad"),
    lambda d: d["skills"].append(dict(d["skills"][0])),
    lambda d: d["skills"][0].update(package_digest="sha256:drift"),
])
def test_release_manifest_fails_closed_on_drift(mutation):
    artifact = load_release_manifest(ARTIFACT, builtin_root=BUILTIN, controlled_root=CONTROLLED)
    mutation(artifact)
    with pytest.raises(ReleaseManifestError):
        verify_release_manifest(artifact, builtin_root=BUILTIN, controlled_root=CONTROLLED)


def test_release_manifest_requires_audited_root_for_loading():
    with pytest.raises(ReleaseManifestError, match="audited_root_required"):
        load_release_manifest(ARTIFACT)


def test_release_manifest_rebuild_detects_package_set_drift():
    artifact = load_release_manifest(ARTIFACT, builtin_root=BUILTIN, controlled_root=CONTROLLED)
    artifact["skills"][0]["skill_id"] = "stardew.proto.injected"
    with pytest.raises(ReleaseManifestError):
        verify_release_manifest(artifact, builtin_root=BUILTIN, controlled_root=CONTROLLED)


@pytest.mark.parametrize("bad_id", ["proto:tp", "proto:unknown_capability"])
def test_release_manifest_rejects_unknown_or_internal_capability(bad_id):
    artifact = load_release_manifest(ARTIFACT, builtin_root=BUILTIN, controlled_root=CONTROLLED)
    artifact["capabilities"][0]["id"] = bad_id
    with pytest.raises(ReleaseManifestError):
        verify_release_manifest(artifact, builtin_root=BUILTIN, controlled_root=CONTROLLED)
