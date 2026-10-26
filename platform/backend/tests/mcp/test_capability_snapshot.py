"""M3.2 capability snapshot and contract-catalog tests."""
from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest

from modules.mcp.contracts.models import ExecutionContext, SideEffect
from modules.mcp.capabilities.contracts import build_capability_contract_catalog
from modules.mcp.capabilities.models import (
    CapabilityContract,
    CapabilityErrorCode,
    CapabilityRuntimeError,
    CapabilitySnapshot,
    ProtocolCapabilityEntry,
)
from modules.mcp.capabilities.snapshot import (
    build_capability_snapshot,
    compute_capability_snapshot_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(*, digest="sha256:deadbeef", **overrides):
    defaults = dict(
        profile="fake", principal_id="principal-a",
        runtime_ref="runtime-principal-a", generation=1,
        binding_ref="binding-principal-a", binding_version=1,
        game_session_ref="game-a", game_instance_nonce="nonce-a",
        lease_epoch=1,
        scopes=frozenset({"game:read", "game:write"}),
        allowed_tools=frozenset({"say"}),
        capability_digest=digest,
        issued_at=time.time(),
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)


def _digest_from_entries(entries):
    """Compute digest from a list of dict/tuple entries for assertions."""
    return compute_capability_snapshot_digest(entries)


# ===================================================================
# Contract catalog
# ===================================================================

def test_contract_catalog_has_proto_and_v2_production_entries():
    catalog = build_capability_contract_catalog(REPO_ROOT)
    assert len(catalog.contracts) == 35

    skill_eligible = [c for c in catalog.contracts if c.skill_eligible]
    assert len(skill_eligible) == 34

    tp = catalog.find_exact("tp", "2.4.5")
    assert tp is not None
    assert tp.internal_only is True
    assert tp.skill_eligible is False
    assert tp.adapter is None
    assert tp.required_scope == "game:admin"


def test_contract_catalog_all_skill_eligible_have_adapter():
    catalog = build_capability_contract_catalog(REPO_ROOT)
    for c in catalog.contracts:
        if c.skill_eligible:
            assert c.adapter is not None, f"{c.capability_id} is skill_eligible but has no adapter"
            assert c.internal_only is False


def test_contract_catalog_exposes_registered_v2_compound_capability():
    catalog = build_capability_contract_catalog(REPO_ROOT)

    water = catalog.find_exact("water_crops", "2.4.5")

    assert water is not None
    assert water.source_surface == "v2-compat"
    assert water.required_scope == "game:write"
    assert water.cancellable is True
    assert water.skill_eligible is True
    assert water.adapter is not None


def test_contract_catalog_read_only_scopes():
    catalog = build_capability_contract_catalog(REPO_ROOT)
    read_only = {
        "query_menu", "query_runtime_snapshot", "query_world_region",
        "query_inventory_snapshot", "query_ui_snapshot", "query_inspect_refs",
        "observe_current_view",
    }
    for c in catalog.contracts:
        if c.capability_id in read_only:
            assert c.required_scope == "game:read", f"{c.capability_id} should be game:read"
            assert c.side_effect is SideEffect.READ_ONLY
        elif c.capability_id != "tp":
            assert c.required_scope == "game:write", f"{c.capability_id} should be game:write"
            assert c.side_effect is SideEffect.MUTATING


def test_contract_catalog_no_legacy_query_specs():
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ids = {c.capability_id for c in catalog.contracts}
    assert "query_inventory" not in ids
    assert "query_location" not in ids
    assert "query_map_exits" not in ids


def test_say_input_schema_lacks_emote_field():
    catalog = build_capability_contract_catalog(REPO_ROOT)
    say = catalog.find_exact("say", "2.4.5")
    assert say is not None
    assert "emote" not in say.input_schema.get("properties", {})


# ===================================================================
# Digest
# ===================================================================

def test_digest_reproduces_canonical_digest_vector():
    import json
    contract = json.loads(
        (REPO_ROOT / "protocol/schemas/hosted-route-contract.json").read_text()
    )
    vector = contract["canonical_digest_vector"]
    entries = [tuple(e) for e in vector["entries"]]
    assert compute_capability_snapshot_digest(entries) == vector["expected"]


def test_digest_accepts_protocol_capability_entries():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "face", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    assert digest == "sha256:f9daca8a6fd4072c95bcd36cec6c8c0c67cab546c3fc1ceaba0df0987db29778"


def test_digest_accepts_mapping_entries():
    entries = [
        {"source_surface": "protocol-2.4.5", "capability_id": "face", "capability_contract_version": "2.4.5"},
        {"source_surface": "protocol-2.4.5", "capability_id": "say", "capability_contract_version": "2.4.5"},
    ]
    digest = compute_capability_snapshot_digest(entries)
    assert digest == "sha256:f9daca8a6fd4072c95bcd36cec6c8c0c67cab546c3fc1ceaba0df0987db29778"


def test_digest_accepts_tuple_entries():
    entries = [
        ("protocol-2.4.5", "face", "2.4.5"),
        ("protocol-2.4.5", "say", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    assert digest == "sha256:f9daca8a6fd4072c95bcd36cec6c8c0c67cab546c3fc1ceaba0df0987db29778"


def test_digest_empty_input_raises_value_error():
    with pytest.raises(ValueError):
        compute_capability_snapshot_digest([])


def test_digest_malformed_mapping_raises_value_error():
    with pytest.raises(ValueError):
        compute_capability_snapshot_digest([{"source_surface": "", "capability_id": "say", "capability_contract_version": "2.4.5"}])


def test_digest_malformed_tuple_raises_value_error():
    with pytest.raises(ValueError):
        compute_capability_snapshot_digest([("", "say", "2.4.5")])


def test_digest_deterministic_ordering():
    entries_a = [
        ("protocol-2.4.5", "say", "2.4.5"),
        ("protocol-2.4.5", "face", "2.4.5"),
    ]
    entries_b = [
        ("protocol-2.4.5", "face", "2.4.5"),
        ("protocol-2.4.5", "say", "2.4.5"),
    ]
    assert compute_capability_snapshot_digest(entries_a) == compute_capability_snapshot_digest(entries_b)


# ===================================================================
# Snapshot algorithm
# ===================================================================

def test_snapshot_known_entries_produce_known_contracts():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "face", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)
    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" in known_ids
    assert "face" in known_ids
    assert len(snap.diagnostics) == 0


def test_snapshot_accepts_versioned_v2_compat_contracts():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("v2-compat", "water_crops", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)

    snap = build_capability_snapshot(
        entries, catalog.contracts, _make_context(digest=digest),
    )

    assert [
        (contract.source_surface, contract.capability_id)
        for contract in snap.known_contracts
    ] == [
        ("protocol-2.4.5", "say"),
        ("v2-compat", "water_crops"),
    ]
    assert snap.diagnostics == ()


def test_snapshot_unknown_source_surface_emits_diagnostic():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-9.0", "future_action", "9.0"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    unknown = [d for d in snap.diagnostics if d.code == "unknown_source_surface"]
    assert len(unknown) == 1
    assert unknown[0].capability_id == "future_action"

    # Only the known surface entry enters known_contracts
    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" in known_ids
    assert "future_action" not in known_ids

    # Both entries are in raw_entries
    raw_ids = {e.capability_id for e in snap.raw_entries}
    assert "say" in raw_ids
    assert "future_action" in raw_ids


def test_snapshot_unsupported_version_emits_diagnostic():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "face", "3.0.0"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    unsupported = [d for d in snap.diagnostics if d.code == "unsupported_version"]
    assert len(unsupported) == 1
    assert unsupported[0].capability_id == "face"
    assert unsupported[0].advertised_version == "3.0.0"

    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" in known_ids
    assert "face" not in known_ids


def test_snapshot_malformed_entry_emits_diagnostic():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        {"source_surface": "", "capability_id": "bad", "capability_contract_version": ""},
    ]
    digest = compute_capability_snapshot_digest(
        [ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5")]
    )
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    malformed = [d for d in snap.diagnostics if d.code == "malformed_entry"]
    assert len(malformed) == 1
    # Malformed entries remain in raw_entries for audit/digest, but are not executable.
    raw_ids = {e.capability_id for e in snap.raw_entries}
    assert "bad" in raw_ids
    assert "bad" not in {e.capability_id for e in snap.known_contracts}


def test_snapshot_duplicate_conflict_emits_diagnostic():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.4"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    conflicts = [d for d in snap.diagnostics if d.code == "duplicate_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].capability_id == "say"

    # Conflicting entries excluded from known_contracts
    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" not in known_ids


def test_snapshot_conflict_is_scoped_to_source_surface_and_capability_pair():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-9", "say", "8"),
        ProtocolCapabilityEntry("protocol-9", "say", "9"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    snap = build_capability_snapshot(entries, catalog.contracts, _make_context(digest=digest))

    conflicts = [d for d in snap.diagnostics if d.code == "duplicate_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].capability_id == "say"
    assert "protocol-9" in conflicts[0].detail
    assert "say" in {contract.capability_id for contract in snap.known_contracts}


def test_snapshot_duplicate_exact_triples_collapse():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(
        [ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5")]
    )
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    # Exactly one entry in raw_entries after collapse
    say_entries = [e for e in snap.raw_entries if e.capability_id == "say"]
    assert len(say_entries) == 1


def test_snapshot_digest_mismatch_raises_snapshot_conflict():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
    ]
    catalog = build_capability_contract_catalog(REPO_ROOT)
    # Deliberately wrong digest
    ctx = _make_context(digest="sha256:0000000000000000000000000000000000000000000000000000000000000000")
    with pytest.raises(CapabilityRuntimeError) as exc:
        build_capability_snapshot(entries, catalog.contracts, ctx)
    assert exc.value.code is CapabilityErrorCode.SNAPSHOT_CONFLICT


def test_snapshot_empty_valid_raises_snapshot_conflict():
    entries: list[ProtocolCapabilityEntry] = []
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest="any")
    with pytest.raises(CapabilityRuntimeError) as exc:
        build_capability_snapshot(entries, catalog.contracts, ctx)
    assert exc.value.code is CapabilityErrorCode.SNAPSHOT_CONFLICT


def test_snapshot_no_v2_contracts_in_known():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)
    for c in snap.known_contracts:
        assert c.source_surface == "protocol-2.4.5", (
            f"{c.capability_id} has non-Proto source_surface: {c.source_surface}"
        )


def test_snapshot_known_contracts_sorted_by_capability_id():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "go_to", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "face", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)
    known_ids = [c.capability_id for c in snap.known_contracts]
    assert known_ids == sorted(known_ids)


def test_snapshot_unknown_capability_in_kwown_surface_emits_diagnostic():
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "nonexistent_cap", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    unknown = [d for d in snap.diagnostics if d.code == "unknown_capability"]
    assert len(unknown) == 1
    assert unknown[0].capability_id == "nonexistent_cap"

    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" in known_ids
    assert "nonexistent_cap" not in known_ids


# ===================================================================
# Diagnostic classification — static-catalog lookup precedes version gate
# ===================================================================

def test_unknown_id_at_exact_version_is_unknown_capability_only():
    """An id absent from the static catalog emits unknown_capability,
    never unsupported_version, even when advertised at 2.4.5."""
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "not_a_cap", "2.4.5"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    unknown = [d for d in snap.diagnostics if d.code == "unknown_capability"]
    unsupported = [d for d in snap.diagnostics if d.code == "unsupported_version"]
    assert len(unknown) == 1
    assert unknown[0].capability_id == "not_a_cap"
    assert len(unsupported) == 0

    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "not_a_cap" not in known_ids


def test_unknown_id_at_wrong_version_is_still_unknown_capability_only():
    """An id absent from the static catalog emits unknown_capability
    regardless of its advertised version — never unsupported_version."""
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "not_a_cap", "9.9.9"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    unknown = [d for d in snap.diagnostics if d.code == "unknown_capability"]
    unsupported = [d for d in snap.diagnostics if d.code == "unsupported_version"]
    assert len(unknown) == 1
    assert unknown[0].capability_id == "not_a_cap"
    assert unknown[0].advertised_version == "9.9.9"
    assert len(unsupported) == 0

    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "not_a_cap" not in known_ids


def test_known_id_at_wrong_version_is_unsupported_version_only():
    """A known id (face) at 9.9.9 emits unsupported_version, never
    unknown_capability, because the id is in the static catalog."""
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-2.4.5", "face", "9.9.9"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    unknown = [d for d in snap.diagnostics if d.code == "unknown_capability"]
    unsupported = [d for d in snap.diagnostics if d.code == "unsupported_version"]
    assert len(unknown) == 0
    assert len(unsupported) == 1
    assert unsupported[0].capability_id == "face"
    assert unsupported[0].advertised_version == "9.9.9"

    known_ids = {c.capability_id for c in snap.known_contracts}
    assert "say" in known_ids
    assert "face" not in known_ids


def test_unknown_source_surface_behavior_unchanged():
    """An unknown source surface never becomes unknown_capability or
    unsupported_version — even when the id is unknown in the static catalog."""
    entries = [
        ProtocolCapabilityEntry("protocol-2.4.5", "say", "2.4.5"),
        ProtocolCapabilityEntry("protocol-9.0", "not_a_cap", "9.0"),
    ]
    digest = compute_capability_snapshot_digest(entries)
    catalog = build_capability_contract_catalog(REPO_ROOT)
    ctx = _make_context(digest=digest)
    snap = build_capability_snapshot(entries, catalog.contracts, ctx)

    surface_diags = [d for d in snap.diagnostics if d.code == "unknown_source_surface"]
    unknown = [d for d in snap.diagnostics if d.code == "unknown_capability"]
    unsupported = [d for d in snap.diagnostics if d.code == "unsupported_version"]

    assert len(surface_diags) == 1
    assert surface_diags[0].capability_id == "not_a_cap"
    assert len(unknown) == 0
    assert len(unsupported) == 0

    # The unknown-surface entry is still in raw_entries
    raw_ids = {e.capability_id for e in snap.raw_entries}
    assert "not_a_cap" in raw_ids
