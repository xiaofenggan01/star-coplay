from __future__ import annotations

import json
import shutil
from pathlib import Path

from modules.mcp.manifest import baseline_document


REPO_ROOT = Path(__file__).resolve().parents[4]
BASELINE = Path(__file__).parent / "fixtures/capability-baseline.json"


def test_capability_baseline_matches_mod_sources():
    assert json.loads(BASELINE.read_text()) == baseline_document(REPO_ROOT)


def test_manifest_truthfully_separates_declared_registered_and_product_classification():
    document = json.loads(BASELINE.read_text())
    rows = {row["capability_id"]: row for row in document["manifest"]}
    for name in ("query_inventory", "query_location", "query_map_exits"):
        row = rows[f"proto:{name}"]
        assert row["declared"] is True and row["registered"] is False
        assert row["product_classification"] == "production"
    assert rows["proto:query_menu"]["registered"] is True
    assert rows["proto:tp"]["product_classification"] == "internal-control"
    assert rows["v2:dev_eval"]["product_classification"] == "not-exposed"
    assert rows["v2:water_crops"]["product_classification"] == "production"


def test_canonical_surface_reaches_truthful_profile_evidence_tiers():
    document = json.loads(BASELINE.read_text())
    entries = document["catalog"]
    assert len(entries) <= len(document["manifest"])
    public = [entry for entry in entries if entry["exposure_kind"] == "tool"]
    assert public
    assert all(entry["parity_status"] == "schema-mapper-verified" for entry in public)
    assert not any(entry["parity_status"] in {"planned", "unmapped"} for entry in public)
    local_adapter_verified = {"go_to", "interact", "query_world_region", "say"}
    assert {
        entry["canonical_name"]
        for entry in public
        if entry["profile_support"]["local_direct"] == "adapter-contract-verified"
    } == local_adapter_verified
    assert all(
        entry["profile_support"]["local_direct"] == "schema-mapper-verified"
        for entry in public
        if entry["canonical_name"] not in local_adapter_verified
    )
    assert all(entry["profile_support"]["hosted"] == "product-transport-verified" for entry in public)
    assert not any(
        support == "real-smapi-verified"
        for entry in public for support in entry["profile_support"].values()
    )
    covered = {
        source
        for entry in entries
        for source in entry["source_manifest_ids"]
    }
    assert covered == {row["capability_id"] for row in document["manifest"]}


def _mutable_source(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in (
        "protocol/proto",
        "mod/StarCoPlay/ProtocolCore/Handlers",
        "mod/StarCoPlay/Legacy/AdapterRuntime",
        "mod/StarCoPlay/Legacy/AtomicHandlers",
        "mod/StarCoPlay/Legacy/CompoundActions/Handlers/Compound",
        "mod/StarCoPlay/Legacy/Observation",
        "agent/protocol",
    ):
        source = REPO_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    target = root / "mod/StarCoPlay/ModEntry.cs"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "mod/StarCoPlay/ModEntry.cs", target)
    return root


def test_deleting_real_register_statement_changes_baseline(tmp_path):
    root = _mutable_source(tmp_path)
    entry = root / "mod/StarCoPlay/ModEntry.cs"
    entry.write_text(entry.read_text().replace(
        "_protoRegistry.Register(new QueryMenuProtoHandler", "RemovedRegister(new QueryMenuProtoHandler", 1
    ))
    document = baseline_document(root)
    row = next(item for item in document["manifest"] if item["capability_id"] == "proto:query_menu")
    assert row["registered"] is False
    assert document != json.loads(BASELINE.read_text())


def test_changing_real_registration_condition_changes_baseline(tmp_path):
    root = _mutable_source(tmp_path)
    entry = root / "mod/StarCoPlay/ModEntry.cs"
    entry.write_text(entry.read_text().replace("if (IsDevMode)", "if (FeatureFlag)", 1))
    document = baseline_document(root)
    row = next(item for item in document["manifest"] if item["capability_id"] == "v2:dev_eval")
    assert row["registration_condition"] == "FeatureFlag"
    assert document != json.loads(BASELINE.read_text())


def test_changing_protocol_version_changes_baseline(tmp_path):
    root = _mutable_source(tmp_path)
    proto = root / "protocol/proto/capability.proto"
    proto.write_text(proto.read_text().replace(
        "package starcoplay.protocol.v245;", "package starcoplay.protocol.v246;", 1
    ))
    assert baseline_document(root)["source"]["mod_protocol"] == "2.4.6"
    assert baseline_document(root) != json.loads(BASELINE.read_text())


def test_changing_protocol_schema_changes_baseline(tmp_path):
    root = _mutable_source(tmp_path)
    proto = root / "protocol/proto/capability_params.proto"
    proto.write_text(proto.read_text() + "\n// schema mutation\n")
    assert baseline_document(root)["source"]["protocol_schema"] != (
        json.loads(BASELINE.read_text())["source"]["protocol_schema"]
    )


def test_changing_v2_handler_result_contract_changes_baseline(tmp_path):
    root = _mutable_source(tmp_path)
    handler = root / "mod/StarCoPlay/Legacy/CompoundActions/Handlers/Compound/ShipItemsHandler.cs"
    handler.write_text(handler.read_text() + "\n// output contract mutation\n")
    assert baseline_document(root) != json.loads(BASELINE.read_text())
