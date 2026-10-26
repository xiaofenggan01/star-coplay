from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from modules.mcp.evidence import catalog_digest, load_ledger, reduce_profile_support
from modules.mcp.mapping_authority import (
    expected_mapping, load_mapping_vectors, mapper_source_digest, semantic_vector_digest,
)
from agent.protocol.canonical_surface import map_canonical_command


def _write(tmp_path: Path, records: list[dict]) -> Path:
    artifact = tmp_path / "tests/example.py"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"evidence fixture\n")
    digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    for record in records:
        record.setdefault("artifact_hashes", {
            reference: digest for reference in record.get("artifact_refs", ())
        })
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({
        "schema_version": "starcoplay.mcp-evidence.v1", "records": records,
    }))
    return path


def _evidence(tier: str, *, evidence_id: str | None = None, profile: str = "local_direct") -> dict:
    return {
        "evidence_id": evidence_id or tier,
        "profile": profile,
        "capabilities": ["say"],
        "tier": tier,
        "evidence_kind": "automated_test",
        "artifact_refs": ["tests/example.py::test_case"],
        "catalog_digest": catalog_digest({"say": {"scope": "game:write"}}),
        "protocol_version": "2.4.5",
        "protocol_schema": "proto-sha256:test",
        "verified_at": "2026-07-14T00:00:00Z",
        "verified_by": "ci",
        "environment": {"peer": "synthetic", "game": "absent", "smapi": "absent"},
    }


def test_reducer_requires_an_unbroken_tier_chain(tmp_path):
    path = _write(tmp_path, [_evidence("product-transport-verified")])
    status = reduce_profile_support(
        {"say": {"scope": "game:write"}}, "2.4.5", "proto-sha256:test",
        ledger_path=path, artifact_root=tmp_path,
    )
    assert status["local_direct"]["say"] == "not-yet-adapter-verified"

    records = [
        _evidence("schema-mapper-verified"),
        _evidence("adapter-contract-verified"),
        _evidence("product-transport-verified"),
    ]
    for index, record in enumerate(records):
        record["evidence_id"] = f"evidence-{index}"
    path = _write(tmp_path, records)
    status = reduce_profile_support(
        {"say": {"scope": "game:write"}}, "2.4.5", "proto-sha256:test",
        ledger_path=path, artifact_root=tmp_path,
    )
    assert status["local_direct"]["say"] == "product-transport-verified"


def test_stale_catalog_or_protocol_evidence_is_not_carried_forward(tmp_path):
    record = _evidence("schema-mapper-verified")
    record["catalog_digest"] = "stale"
    path = _write(tmp_path, [record])
    assert reduce_profile_support(
        {"say": {"scope": "game:write"}}, "2.4.5", "proto-sha256:test",
        ledger_path=path, artifact_root=tmp_path,
    )["local_direct"]["say"] == (
        "not-yet-adapter-verified"
    )
    record["catalog_digest"] = catalog_digest({"say": {"scope": "game:write"}})
    path = _write(tmp_path, [record])
    assert reduce_profile_support(
        {"say": {"scope": "game:write"}}, "9.9.9", "proto-sha256:test",
        ledger_path=path, artifact_root=tmp_path,
    )["local_direct"]["say"] == (
        "not-yet-adapter-verified"
    )
    assert reduce_profile_support(
        {"say": {"scope": "game:write"}}, "2.4.5", "proto-sha256:changed",
        ledger_path=path, artifact_root=tmp_path,
    )["local_direct"]["say"] == "not-yet-adapter-verified"


def test_automated_or_synthetic_evidence_cannot_claim_real_smapi(tmp_path):
    record = _evidence("real-smapi-verified")
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="real SMAPI evidence_kind"):
        load_ledger(path, artifact_root=tmp_path)


def test_real_smapi_claim_requires_environment_and_case_metadata(tmp_path):
    record = _evidence("real-smapi-verified")
    record["evidence_kind"] = "real_smapi_manual"
    record["environment"] = {"game": "present", "smapi": "present"}
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="version, build, and case"):
        load_ledger(path, artifact_root=tmp_path)


def test_real_smapi_claim_requires_hash_for_every_artifact(tmp_path):
    record = _evidence("real-smapi-verified")
    record["evidence_kind"] = "real_smapi_manual"
    record["environment"] = {
        "game": "present", "smapi": "present", "game_version": "1.6",
        "smapi_version": "4", "mod_commit": "abc", "case_id": "local-say-1",
    }
    record["artifact_hashes"] = {}
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="exact sha256 for every artifact"):
        load_ledger(path, artifact_root=tmp_path)


@pytest.mark.parametrize("forged", ["sha256:abc", "sha256:" + "A" * 64, "md5:" + "a" * 64])
def test_artifact_hash_must_have_exact_lowercase_sha256_shape(tmp_path, forged):
    record = _evidence("schema-mapper-verified")
    record["artifact_hashes"] = {record["artifact_refs"][0]: forged}
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="exact sha256"):
        load_ledger(path, artifact_root=tmp_path)


def test_forged_hash_and_content_drift_are_rejected_for_lower_tiers_too(tmp_path):
    record = _evidence("schema-mapper-verified")
    reference = record["artifact_refs"][0]
    record["artifact_hashes"] = {reference: "sha256:" + "0" * 64}
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="sha256 mismatch"):
        load_ledger(path, artifact_root=tmp_path)

    record.pop("artifact_hashes")
    path = _write(tmp_path, [record])
    (tmp_path / "tests/example.py").write_bytes(b"changed after verification\n")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        load_ledger(path, artifact_root=tmp_path)


@pytest.mark.parametrize("reference", ["/tmp/outside.log", "../outside.log"])
def test_absolute_and_traversal_artifact_paths_are_rejected(tmp_path, reference):
    record = _evidence("schema-mapper-verified")
    record["artifact_refs"] = [reference]
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="unsafe evidence artifact path"):
        load_ledger(path, artifact_root=tmp_path)


def test_missing_artifact_and_symlink_escape_are_rejected(tmp_path):
    record = _evidence("schema-mapper-verified")
    record["artifact_refs"] = ["missing.log"]
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="does not exist"):
        load_ledger(path, artifact_root=tmp_path)

    outside = tmp_path.parent / "outside-evidence.log"
    outside.write_bytes(b"outside\n")
    link = tmp_path / "escape.log"
    link.symlink_to(outside)
    record["artifact_refs"] = ["escape.log"]
    record.pop("artifact_hashes", None)
    path = _write(tmp_path, [record])
    with pytest.raises(ValueError, match="escapes root"):
        load_ledger(path, artifact_root=tmp_path)


@pytest.mark.parametrize("field,new_value", [
    ("input_schema", {"type": "string"}),
    ("output_schema", {"type": "integer"}),
    ("required_scope", "game:read"),
    ("side_effect", "read_only"),
    ("capability_version", "canonical.v2"),
    ("mapping_type", "renamed-adapter"),
    ("mapper_authority", {"semantic_vector_digest": "sha256:changed"}),
])
def test_same_name_contract_drift_invalidates_evidence(tmp_path, field, new_value):
    original = {"say": {
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "required_scope": "game:write", "side_effect": "mutating",
        "capability_version": "canonical.v1", "mapping_type": "direct",
    }}
    record = _evidence("schema-mapper-verified")
    record["catalog_digest"] = catalog_digest(original)
    path = _write(tmp_path, [record])
    changed = json.loads(json.dumps(original))
    changed["say"][field] = new_value
    status = reduce_profile_support(
        changed, "2.4.5", "proto-sha256:test", ledger_path=path,
        artifact_root=tmp_path,
    )
    assert status["local_direct"]["say"] == "not-yet-adapter-verified"


def test_frozen_vectors_are_independent_oracles_for_the_entire_public_catalog():
    root = Path(__file__).resolve().parents[4]
    from modules.mcp.manifest import build_catalog, build_manifest

    public = {
        entry.canonical_name for entry in build_catalog(build_manifest(root), root)
        if entry.exposure_kind == "tool"
    }
    vectors = load_mapping_vectors()
    assert set(vectors) == public
    for name, vector in vectors.items():
        mapped = map_canonical_command(name, vector["input"], command_id="golden-command")
        actual = {
            "command_id": mapped.command_id, "capability": mapped.capability,
            "timeout_ms": mapped.timeout_ms, "execution_model": mapped.execution_model,
            "cancellable": mapped.cancellable, "wire_format": mapped.wire_format,
            "payload": mapped.payload,
        }
        assert actual == expected_mapping(name, "golden-command")


def test_mapper_source_digest_ignores_formatting_but_detects_semantic_change(tmp_path):
    relative = Path("agent/protocol/canonical_surface.py")
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_text("def mapping(value):\n    return value + 1\n")
    first = mapper_source_digest(tmp_path, (relative,))
    source.write_text("# comment\n\ndef mapping( value ):\n    return value + 1\n")
    assert mapper_source_digest(tmp_path, (relative,)) == first
    source.write_text("def mapping(value):\n    return value + 2\n")
    assert mapper_source_digest(tmp_path, (relative,)) != first


def test_semantic_vector_digest_ignores_json_format_but_detects_vector_change(tmp_path):
    original = json.loads(
        (Path(__file__).resolve().parents[2] / "modules/mcp/evidence/canonical-mapping-vectors.json").read_text()
    )
    compact = tmp_path / "vectors.json"
    compact.write_text(json.dumps(original, separators=(",", ":")))
    assert semantic_vector_digest(compact) == semantic_vector_digest()
    original["vectors"]["say"]["expected"]["payload"]["say"]["content"] = "wrong"
    compact.write_text(json.dumps(original))
    assert semantic_vector_digest(compact) != semantic_vector_digest()


def test_duplicate_ids_unknown_profile_and_unknown_capability_are_rejected(tmp_path):
    first = _evidence("schema-mapper-verified", evidence_id="duplicate")
    path = _write(tmp_path, [first, first])
    with pytest.raises(ValueError, match="duplicate"):
        load_ledger(path, artifact_root=tmp_path)

    invalid = _evidence("schema-mapper-verified", profile="local_gateway")
    path = _write(tmp_path, [invalid])
    with pytest.raises(ValueError, match="invalid evidence"):
        load_ledger(path, artifact_root=tmp_path)

    unknown = _evidence("schema-mapper-verified")
    unknown["capabilities"] = ["unknown"]
    path = _write(tmp_path, [unknown])
    with pytest.raises(ValueError, match="unknown capabilities"):
        reduce_profile_support(
            {"say": {"scope": "game:write"}}, "2.4.5", "proto-sha256:test",
            ledger_path=path, artifact_root=tmp_path,
        )
