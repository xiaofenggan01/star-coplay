from __future__ import annotations

import json
from pathlib import Path

import pytest

from protocol.canonical_surface import canonical_specs, _validate_json
from protocol.result_adapter import normalize_canonical_result
from protocol.tool_mapper import ProtocolMappingError


VECTORS = json.loads(
    (Path(__file__).parent / "fixtures/canonical-result-vectors.json").read_text()
)["vectors"]

# Vectors whose expected value is a dict (success canonical output).
_SUCCESS_VECTORS = {
    vid: v for vid, v in VECTORS.items()
    if isinstance(v.get("expected"), dict)
}
# Vectors whose expected value is null (UNKNOWN non-terminal).
_NULL_VECTORS = {
    vid: v for vid, v in VECTORS.items()
    if v.get("expected") is None
}
# Vectors whose expected value is a ProtocolMappingError string.
_ERROR_VECTORS = {
    vid: v for vid, v in VECTORS.items()
    if isinstance(v.get("expected"), str) and v["expected"].startswith("ProtocolMappingError")
}


@pytest.mark.parametrize("vector_id", sorted(_SUCCESS_VECTORS))
def test_frozen_source_result_vectors_normalize_to_canonical_output(vector_id):
    vector = _SUCCESS_VECTORS[vector_id]
    source = vector["result"]
    actual = normalize_canonical_result(
        vector["canonical_name"],
        vector["arguments"],
        expected_command_id=source["command_id"],
        lifecycle_state=source["state"],
        raw_output=source["output"],
        error_code=source.get("error_code"),
    )
    assert actual == vector["expected"], (
        f"{vector_id}: normalized result does not match frozen expected output"
    )
    _validate_json(canonical_specs()[vector["canonical_name"]].output_schema, actual)


@pytest.mark.parametrize("vector_id", sorted(_NULL_VECTORS))
def test_unknown_lifecycle_state_returns_none(vector_id):
    vector = _NULL_VECTORS[vector_id]
    source = vector["result"]
    actual = normalize_canonical_result(
        vector["canonical_name"],
        vector["arguments"],
        expected_command_id=source["command_id"],
        lifecycle_state=source["state"],
        raw_output=source["output"],
        error_code=source.get("error_code"),
    )
    assert actual is None


@pytest.mark.parametrize("vector_id", sorted(_ERROR_VECTORS))
def test_frozen_error_vectors_raise_protocol_mapping_error(vector_id):
    vector = _ERROR_VECTORS[vector_id]
    source = vector["result"]
    expected_error = vector["expected"]
    # Parse the expected error string: "ProtocolMappingError: <match substring>"
    match_substring = expected_error.split(": ", 1)[1]
    with pytest.raises(ProtocolMappingError, match=match_substring):
        normalize_canonical_result(
            vector["canonical_name"],
            vector["arguments"],
            expected_command_id=source["command_id"],
            lifecycle_state=source["state"],
            raw_output=source["output"],
            error_code=source.get("error_code"),
        )


def test_result_correlation_and_capability_mismatch_are_rejected():
    """Quick inline smoke tests that don't depend on oracle vectors."""
    say = VECTORS["proto_say_completed"]
    source = say["result"]
    with pytest.raises(ProtocolMappingError, match="command_id"):
        normalize_canonical_result(
            "say", say["arguments"], expected_command_id="other",
            lifecycle_state=source["state"], raw_output=source["output"],
        )
    with pytest.raises(ProtocolMappingError, match="capability"):
        normalize_canonical_result(
            "say", say["arguments"], expected_command_id=source["command_id"],
            lifecycle_state=source["state"],
            raw_output={**source["output"], "capability": "emote"},
        )


def test_terminal_result_without_source_payload_is_rejected():
    with pytest.raises(ProtocolMappingError, match="missing"):
        normalize_canonical_result(
            "say", {"message": "hi"}, expected_command_id="cmd",
            lifecycle_state="SUCCEEDED", raw_output={},
        )


def test_act_on_target_result_schema_rejects_unregistered_examine_fields():
    schema = canonical_specs()["act_on_target"].output_schema
    valid = {
        "command_id": "cmd", "capability": "do", "success": True,
        "status": "COMPLETED", "data": {
            "intent": "examine", "target": {"x": 3, "y": 7},
            "object_type": "chest",
            "items": [{"name": "Stone", "stack": 1, "quality": 0}],
        },
    }
    _validate_json(schema, valid)
    with pytest.raises(ProtocolMappingError):
        _validate_json(schema, {**valid, "data": {**valid["data"], "fabricated": True}})
    with pytest.raises(ProtocolMappingError):
        _validate_json(schema, {**valid, "data": {
            **valid["data"],
            "items": [{"name": "Stone", "stack": 1, "quality": 0, "fabricated": True}],
        }})


def test_every_canonical_spec_has_at_least_one_success_vector():
    """Contract: every public canonical capability has a frozen success vector."""
    specs = canonical_specs()
    covered = {VECTORS[vid]["canonical_name"] for vid in _SUCCESS_VECTORS}
    missing = set(specs) - covered
    assert not missing, (
        f"canonical specs without a frozen success vector: {sorted(missing)}"
    )
