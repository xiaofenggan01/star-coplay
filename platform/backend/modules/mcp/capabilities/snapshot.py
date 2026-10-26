"""Shared capability-snapshot digest and M3.2 snapshot algorithm."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from modules.mcp.contracts.models import ExecutionContext
from modules.mcp.capabilities.models import (
    CapabilityContract,
    CapabilityDiagnostic,
    CapabilityErrorCode,
    CapabilityRuntimeError,
    CapabilitySnapshot,
    ProtocolCapabilityEntry,
)

_CAPABILITY_SNAPSHOT_VERSION = "starcoplay.capability-snapshot.v1"


# ===================================================================
# Shared digest — authoritative single implementation
# ===================================================================

def compute_capability_snapshot_digest(
    entries: Sequence[ProtocolCapabilityEntry | Mapping[str, str] | tuple[str, str, str]],
) -> str:
    """Compute the ``starcoplay.capability-snapshot.v1`` digest.

    Accepts ``ProtocolCapabilityEntry``, ``Mapping`` triples, or 3-tuple
    triples.  Canonicalises to a sorted set of tab-separated lines.
    Raises ``ValueError`` for malformed or empty input.
    """
    lines: set[str] = set()
    for item in entries:
        if isinstance(item, ProtocolCapabilityEntry):
            if not item.source_surface or not item.capability_id or not item.capability_contract_version:
                raise ValueError("ProtocolCapabilityEntry has empty required field")
            lines.add(
                f"{item.source_surface}\t{item.capability_id}\t{item.capability_contract_version}\n"
            )
        elif isinstance(item, tuple):
            if len(item) != 3 or not all(isinstance(v, str) and v for v in item):
                raise ValueError("tuple entry must be three non-empty strings")
            lines.add(f"{item[0]}\t{item[1]}\t{item[2]}\n")
        elif isinstance(item, Mapping):
            surface = item.get("source_surface")
            cap_id = item.get("capability_id")
            version = item.get("capability_contract_version")
            if not all(isinstance(v, str) and v for v in (surface, cap_id, version)):
                raise ValueError("mapping entry has missing or empty required field")
            lines.add(f"{surface}\t{cap_id}\t{version}\n")
        else:
            raise ValueError(f"unsupported entry type: {type(item)}")

    if not lines:
        raise ValueError("capability snapshot is empty")

    payload = _CAPABILITY_SNAPSHOT_VERSION + "\n" + "".join(sorted(lines))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ===================================================================
# M3.2 snapshot algorithm
# ===================================================================

def build_capability_snapshot(
    raw_entries: Sequence[ProtocolCapabilityEntry | Mapping[str, str]],
    known_contracts: Sequence[CapabilityContract],
    context: ExecutionContext,
) -> CapabilitySnapshot:
    """Execute the frozen M3.2 snapshot algorithm.

    Normalises, canonicalises, and filters provider entries; computes the
    digest; cross-validates against *context.capability_digest*; and returns
    a ``CapabilitySnapshot``.
    """
    diagnostics: list[CapabilityDiagnostic] = []
    normalized: list[ProtocolCapabilityEntry] = []
    raw_preserved: list[ProtocolCapabilityEntry] = []

    # 1. Normalise each provider entry
    for item in raw_entries:
        if isinstance(item, ProtocolCapabilityEntry):
            raw_preserved.append(item)
            if (not item.source_surface or not item.capability_id
                    or not item.capability_contract_version):
                diagnostics.append(CapabilityDiagnostic(
                    code="malformed_entry",
                    capability_id=item.capability_id or "",
                    advertised_version=item.capability_contract_version or None,
                    detail="ProtocolCapabilityEntry has empty required field",
                ))
                continue
            normalized.append(item)
        elif isinstance(item, Mapping):
            surface = item.get("source_surface")
            cap_id = item.get("capability_id")
            version = item.get("capability_contract_version")
            if not all(isinstance(v, str) and v for v in (surface, cap_id, version)):
                raw_preserved.append(ProtocolCapabilityEntry(str(surface or ""), str(cap_id or ""), str(version or "")))
                diagnostics.append(CapabilityDiagnostic(
                    code="malformed_entry",
                    capability_id=str(cap_id or ""),
                    advertised_version=version if isinstance(version, str) else None,
                    detail="mapping entry has missing or empty required field",
                ))
                continue
            normalized.append(ProtocolCapabilityEntry(
                source_surface=str(surface),
                capability_id=str(cap_id),
                capability_contract_version=str(version),
            ))
            raw_preserved.append(normalized[-1])
        else:
            raw_preserved.append(ProtocolCapabilityEntry("", "", ""))
            diagnostics.append(CapabilityDiagnostic(
                code="malformed_entry",
                capability_id="",
                advertised_version=None,
                detail=f"unsupported entry type: {type(item)}",
            ))

    # 2. Canonicalise — deterministic lexical order, exact duplicate collapse
    canonical: list[ProtocolCapabilityEntry] = sorted(
        set(normalized),
        key=lambda e: (e.source_surface, e.capability_id, e.capability_contract_version),
    )

    # 3. Duplicate-conflict detection: same (surface, id) with multiple versions
    seen_pairs: dict[tuple[str, str], str] = {}
    conflict_pairs: set[tuple[str, str]] = set()
    for entry in canonical:
        pair = (entry.source_surface, entry.capability_id)
        prior = seen_pairs.get(pair)
        if prior is not None and prior != entry.capability_contract_version:
            conflict_pairs.add(pair)
        else:
            seen_pairs[pair] = entry.capability_contract_version

    # Emit exactly one duplicate_conflict per conflicting pair
    for surface, cid in sorted(conflict_pairs):
        diagnostics.append(CapabilityDiagnostic(
            code="duplicate_conflict",
            capability_id=cid,
            advertised_version=None,
            detail=(
                "same (source_surface, capability_id) advertises multiple versions: "
                f"{surface}"
            ),
        ))

    # 4. Digest computation and cross-validation
    digest = compute_capability_snapshot_digest(canonical) if canonical else (
        "sha256:" + hashlib.sha256((_CAPABILITY_SNAPSHOT_VERSION + "\n").encode()).hexdigest()
    )
    if digest != context.capability_digest:
        raise CapabilityRuntimeError(
            CapabilityErrorCode.SNAPSHOT_CONFLICT,
            "capability digest mismatch", False,
        )

    # 5. Known-contract filtering
    contract_by_surface_id: dict[tuple[str, str], CapabilityContract] = {
        (c.source_surface, c.capability_id): c for c in known_contracts
    }
    known_surfaces = {contract.source_surface for contract in known_contracts}
    known: list[CapabilityContract] = []

    for entry in canonical:
        cid = entry.capability_id

        # Unknown surface → diagnostic only
        if entry.source_surface not in known_surfaces:
            diagnostics.append(CapabilityDiagnostic(
                code="unknown_source_surface",
                capability_id=cid,
                advertised_version=entry.capability_contract_version,
                detail=f"source surface is not known: {entry.source_surface}",
            ))
            continue

        # Conflict → skip (diagnostic already emitted above)
        if (entry.source_surface, cid) in conflict_pairs:
            continue

        # Static catalog lookup — must come before the version gate so an
        # unknown id always emits unknown_capability regardless of version.
        contract = contract_by_surface_id.get((entry.source_surface, cid))
        if contract is None:
            diagnostics.append(CapabilityDiagnostic(
                code="unknown_capability",
                capability_id=cid,
                advertised_version=entry.capability_contract_version,
                detail="capability not in static contract catalog",
            ))
            continue

        # Exact version gate — only reached when the id *is* in the static
        # catalog, so a wrong version here is unambiguously unsupported_version.
        if entry.capability_contract_version != contract.contract_version:
            diagnostics.append(CapabilityDiagnostic(
                code="unsupported_version",
                capability_id=cid,
                advertised_version=entry.capability_contract_version,
                detail=(
                    f"version {entry.capability_contract_version} is not supported; "
                    f"only {contract.contract_version}"
                ),
            ))
            continue

        known.append(contract)

    known.sort(key=lambda c: c.capability_id)

    # Valid rows retain historical canonical de-duplication; malformed rows
    # remain inert audit records so they cannot become executable.
    malformed_raw = tuple(
        entry for entry in raw_preserved
        if not (entry.source_surface and entry.capability_id and entry.capability_contract_version)
    )
    return CapabilitySnapshot(
        raw_entries=tuple(canonical) + malformed_raw,
        known_contracts=tuple(known),
        diagnostics=tuple(diagnostics),
        capability_digest=digest,
    )
