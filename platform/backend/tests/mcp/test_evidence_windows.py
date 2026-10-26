import hashlib
import json

from modules.mcp.evidence import load_ledger


def test_text_evidence_accepts_windows_crlf_for_canonical_lf(tmp_path):
    artifact = tmp_path / "proof.py"
    artifact.write_bytes(b"proof line\r\nsecond line\r\n")
    canonical = hashlib.sha256(b"proof line\nsecond line\n").hexdigest()
    ledger = {
        "schema_version": "starcoplay.mcp-evidence.v1",
        "records": [{
            "evidence_id": "windows-crlf-proof",
            "profile": "hosted",
            "capabilities": ["say"],
            "tier": "schema-mapper-verified",
            "evidence_kind": "automated_test",
            "artifact_refs": ["proof.py"],
            "catalog_digest": "sha256:catalog",
            "protocol_version": "2.4.5",
            "protocol_schema": "sha256:schema",
            "verified_at": "2026-07-21T00:00:00Z",
            "verified_by": "pytest",
            "environment": {"game": "absent", "smapi": "absent"},
            "artifact_hashes": {"proof.py": f"sha256:{canonical}"},
        }],
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")

    assert load_ledger(path, artifact_root=tmp_path)[0].evidence_id == "windows-crlf-proof"
