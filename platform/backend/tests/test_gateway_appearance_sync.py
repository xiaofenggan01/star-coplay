from __future__ import annotations

from modules.gateway.server import _coerce_profile_config, _normalize_appearance_sync_payload


def valid_mod_payload() -> dict[str, object]:
    return {
        "gender": "Female",
        "skin": 3,
        "hairstyle": 12,
        "hair_color": {"h": 10, "s": 60, "b": 70},
        "eye_color": {"h": 30, "s": 40, "b": 50},
        "accessory": -1,
        "shirt_index": 20,
        "pants_index": 2,
        "pants_color": {"h": 5, "s": 30, "b": 80},
        "hat_index": None,
        "boots_index": 42,
        "facing_direction": 2,
    }


def test_normalize_appearance_sync_payload_converts_mod_snake_case_to_web_schema() -> None:
    normalized = _normalize_appearance_sync_payload(valid_mod_payload())

    assert normalized is not None
    assert normalized["hairColor"] == {"h": 10, "s": 60, "b": 70}
    assert normalized["eyeColor"] == {"h": 30, "s": 40, "b": 50}
    assert normalized["shirtIndex"] == 20
    assert normalized["pantsIndex"] == 2
    assert normalized["hatIndex"] is None
    assert normalized["bootsIndex"] == 42
    assert normalized["facingDirection"] == 2
    assert "hair_color" not in normalized
    assert "updated_at" in normalized


def test_normalize_appearance_sync_payload_rejects_invalid_values() -> None:
    invalid = valid_mod_payload()
    invalid["shirt_index"] = 1.5

    assert _normalize_appearance_sync_payload(invalid) is None


def test_normalize_appearance_sync_payload_accepts_netint_like_values() -> None:
    payload = valid_mod_payload()
    payload["skin"] = {"value": 4}
    payload["accessory"] = {"Value": "-1"}
    payload["facing_direction"] = "2"

    normalized = _normalize_appearance_sync_payload(payload)

    assert normalized is not None
    assert normalized["skin"] == 4
    assert normalized["accessory"] == -1
    assert normalized["facingDirection"] == 2


def test_normalize_appearance_sync_payload_accepts_netcode_array_values_and_unset_clothes() -> None:
    payload = valid_mod_payload()
    payload["skin"] = [0]
    payload["accessory"] = [-1]
    payload["facing_direction"] = [2]
    payload["shirt_index"] = "-1"
    payload["pants_index"] = "-1"

    normalized = _normalize_appearance_sync_payload(payload)

    assert normalized is not None
    assert normalized["skin"] == 0
    assert normalized["accessory"] == -1
    assert normalized["facingDirection"] == 2
    assert normalized["shirtIndex"] == -1
    assert normalized["pantsIndex"] == -1


def test_normalize_appearance_sync_payload_does_not_persist_live_facing_direction() -> None:
    payload = valid_mod_payload()
    payload["facing_direction"] = 1

    normalized = _normalize_appearance_sync_payload(payload)

    assert normalized is not None
    assert normalized["facingDirection"] == 2


def test_normalize_appearance_sync_payload_accepts_missing_facing_direction_and_clothes() -> None:
    payload = valid_mod_payload()
    del payload["facing_direction"]
    del payload["shirt_index"]
    del payload["pants_index"]

    normalized = _normalize_appearance_sync_payload(payload)

    assert normalized is not None
    assert normalized["facingDirection"] == 2
    assert normalized["shirtIndex"] == -1
    assert normalized["pantsIndex"] == -1


def test_normalize_appearance_sync_payload_rejects_unknown_fields() -> None:
    invalid = valid_mod_payload()
    invalid["debug_dump"] = "nope"

    assert _normalize_appearance_sync_payload(invalid) is None


def test_coerce_profile_config_accepts_asyncpg_json_string() -> None:
    config = _coerce_profile_config('{"appearance":{"skin":0},"persona":{"name":"A"}}')

    assert config == {"appearance": {"skin": 0}, "persona": {"name": "A"}}


def test_coerce_profile_config_discards_invalid_or_non_object_values() -> None:
    assert _coerce_profile_config(None) == {}
    assert _coerce_profile_config("{bad") == {}
    assert _coerce_profile_config('["not", "object"]') == {}
