"""Strict, source-controlled evidence reducer for MCP profile support claims."""
from __future__ import annotations

import hashlib
import json
import hmac
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping


TIERS = (
    "schema-mapper-verified",
    "adapter-contract-verified",
    "product-transport-verified",
    "real-smapi-verified",
)
PROFILES = frozenset({"local_direct", "hosted"})
DEFAULT_LEDGER = Path(__file__).with_name("evidence") / "capability-evidence.json"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TEXT_ARTIFACT_SUFFIXES = frozenset({
    ".cs", ".json", ".md", ".proto", ".py", ".toml", ".txt", ".yaml", ".yml",
})


def _artifact_digest(path: Path) -> str:
    data = path.read_bytes()
    # Evidence hashes are authored from Git's canonical LF blobs. A Windows
    # checkout may materialize CRLF without changing the reviewed source.
    # Binary artifacts remain byte-exact.
    if path.suffix.lower() in _TEXT_ARTIFACT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    profile: str
    capabilities: tuple[str, ...]
    tier: str
    evidence_kind: str
    artifact_refs: tuple[str, ...]
    catalog_digest: str
    protocol_version: str
    protocol_schema: str
    verified_at: str
    verified_by: str
    environment: Mapping[str, str]
    artifact_hashes: Mapping[str, str]


def catalog_digest(contracts: Mapping[str, Mapping[str, Any]]) -> str:
    """Digest the complete callable contract, not merely capability names."""
    payload = json.dumps(contracts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _record(value: Mapping[str, Any]) -> EvidenceRecord:
    required = {
        "evidence_id", "profile", "capabilities", "tier", "evidence_kind",
        "artifact_refs", "catalog_digest", "protocol_version", "verified_at",
        "protocol_schema", "verified_by", "environment",
    }
    missing = required - value.keys()
    if missing:
        raise ValueError(f"evidence record missing fields: {sorted(missing)}")
    unknown = value.keys() - required - {"artifact_hashes"}
    if unknown:
        raise ValueError(f"evidence record has unknown fields: {sorted(unknown)}")
    if not isinstance(value["capabilities"], list) or not all(
        isinstance(item, str) and item for item in value["capabilities"]
    ):
        raise ValueError("evidence capabilities must be a non-empty string list")
    if not isinstance(value["artifact_refs"], list) or not all(
        isinstance(item, str) and item for item in value["artifact_refs"]
    ):
        raise ValueError("evidence artifact_refs must be a non-empty string list")
    if not isinstance(value["environment"], dict):
        raise ValueError("evidence environment must be an object")
    record = EvidenceRecord(
        evidence_id=str(value["evidence_id"]), profile=str(value["profile"]),
        capabilities=tuple(value["capabilities"]), tier=str(value["tier"]),
        evidence_kind=str(value["evidence_kind"]),
        artifact_refs=tuple(value["artifact_refs"]),
        catalog_digest=str(value["catalog_digest"]),
        protocol_version=str(value["protocol_version"]),
        protocol_schema=str(value["protocol_schema"]),
        verified_at=str(value["verified_at"]), verified_by=str(value["verified_by"]),
        environment=dict(value["environment"]),
        artifact_hashes=dict(value.get("artifact_hashes", {})),
    )
    if not record.evidence_id or record.profile not in PROFILES or record.tier not in TIERS:
        raise ValueError(f"invalid evidence identity/profile/tier: {record.evidence_id}")
    if not record.capabilities or not record.artifact_refs:
        raise ValueError(f"evidence must name capabilities and artifacts: {record.evidence_id}")
    if record.tier == "real-smapi-verified":
        if record.evidence_kind not in {"real_smapi_manual", "real_smapi_harness"}:
            raise ValueError("real SMAPI claims require real SMAPI evidence_kind")
        required_environment = {"game_version", "smapi_version", "mod_commit", "case_id"}
        if record.environment.get("game") != "present" or record.environment.get("smapi") != "present":
            raise ValueError("real SMAPI claims require game and SMAPI present")
        if not all(record.environment.get(key) for key in required_environment):
            raise ValueError("real SMAPI claims require version, build, and case metadata")
    elif record.environment.get("game") == "present" or record.environment.get("smapi") == "present":
        raise ValueError("automated evidence must not imply a real game environment")
    if set(record.artifact_hashes) != set(record.artifact_refs) or not all(
        _SHA256.fullmatch(value) for value in record.artifact_hashes.values()
    ):
        raise ValueError("evidence requires an exact sha256 for every artifact")
    return record


def _verify_artifacts(record: EvidenceRecord, artifact_root: Path) -> None:
    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("evidence artifact_root must be a directory")
    for reference in record.artifact_refs:
        path_text = reference.split("::", 1)[0]
        relative = Path(path_text)
        if (relative.is_absolute() or PurePosixPath(path_text).is_absolute()
                or PureWindowsPath(path_text).is_absolute() or ".." in relative.parts):
            raise ValueError(f"unsafe evidence artifact path: {reference}")
        try:
            resolved = (root / relative).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"evidence artifact does not exist: {reference}") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError(f"evidence artifact escapes root or is not a file: {reference}")
        actual = _artifact_digest(resolved)
        if not hmac.compare_digest(actual, record.artifact_hashes[reference]):
            raise ValueError(f"evidence artifact sha256 mismatch: {reference}")


def load_ledger(
    path: Path = DEFAULT_LEDGER, *, artifact_root: Path | None = None,
) -> tuple[EvidenceRecord, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "starcoplay.mcp-evidence.v1":
        raise ValueError("unsupported MCP evidence ledger schema")
    records = tuple(_record(value) for value in payload.get("records", ()))
    ids = [record.evidence_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate MCP evidence_id")
    root = artifact_root or Path(__file__).resolve().parents[4]
    for record in records:
        _verify_artifacts(record, root)
    return records


def reduce_profile_support(
    contracts: Mapping[str, Mapping[str, Any]], protocol_version: str, protocol_schema: str,
    *, ledger_path: Path = DEFAULT_LEDGER,
    artifact_root: Path | None = None,
) -> dict[str, dict[str, str]]:
    names = frozenset(contracts)
    digest = catalog_digest(contracts)
    reached: dict[tuple[str, str], set[int]] = {
        (profile, name): set() for profile in PROFILES for name in names
    }
    for record in load_ledger(ledger_path, artifact_root=artifact_root):
        # Stale evidence is deliberately ignored rather than carried across a
        # catalog or protocol change.
        if (
            record.catalog_digest != digest or record.protocol_version != protocol_version
            or record.protocol_schema != protocol_schema
        ):
            continue
        selected = names if record.capabilities == ("*public-canonical*",) else frozenset(record.capabilities)
        if not selected <= names:
            raise ValueError(f"evidence names unknown capabilities: {record.evidence_id}")
        tier_index = TIERS.index(record.tier)
        for name in selected:
            reached[(record.profile, name)].add(tier_index)

    result: dict[str, dict[str, str]] = {}
    for profile in PROFILES:
        result[profile] = {}
        for name in names:
            levels = reached[(profile, name)]
            highest = -1
            for index in range(len(TIERS)):
                if index not in levels:
                    break
                highest = index
            result[profile][name] = TIERS[highest] if highest >= 0 else "not-yet-adapter-verified"
    return result
