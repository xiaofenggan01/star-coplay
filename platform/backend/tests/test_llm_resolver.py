from __future__ import annotations

import pytest

from modules.llm.resolver import ModelResolutionError, resolve_model


def test_resolve_model_uses_user_preference_when_allowed():
    resolved = resolve_model(
        model_tier="balanced",
        preferred_models={"balanced": "gpt-4o-mini"},
        tier_mapping={
            "balanced": {
                "default": "gpt-4o",
                "models": ["gpt-4o", "gpt-4o-mini"],
            }
        },
    )

    assert resolved.model == "gpt-4o-mini"
    assert resolved.source == "user_preference"


def test_resolve_model_falls_back_to_tier_default():
    resolved = resolve_model(
        model_tier="fast",
        preferred_models={},
        tier_mapping={"fast": ["gpt-4o-mini", "gpt-4.1-mini"]},
    )

    assert resolved.model == "gpt-4o-mini"
    assert resolved.source == "system_config"


def test_resolve_model_rejects_unconfigured_tier():
    with pytest.raises(ModelResolutionError) as exc:
        resolve_model(
            model_tier="reasoning",
            preferred_models={},
            tier_mapping={"fast": "gpt-4o-mini"},
        )

    assert exc.value.code == "MODEL_TIER_NOT_ALLOWED"


def test_resolve_model_rejects_preference_outside_allowed_models():
    with pytest.raises(ModelResolutionError) as exc:
        resolve_model(
            model_tier="balanced",
            preferred_models={"balanced": "gpt-5"},
            tier_mapping={"balanced": {"default": "gpt-4o", "models": ["gpt-4o"]}},
        )

    assert exc.value.code == "MODEL_NOT_ALLOWED"
