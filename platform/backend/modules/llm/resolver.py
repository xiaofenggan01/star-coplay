from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ModelResolutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResolvedModel:
    model_tier: str
    model: str
    source: str


def resolve_model(
    *,
    model_tier: str,
    preferred_models: Mapping[str, str] | None,
    tier_mapping: Mapping[str, Any] | None,
) -> ResolvedModel:
    tier = _normalize_key(model_tier)
    if not tier:
        raise ModelResolutionError("MODEL_TIER_REQUIRED", "model_tier is required")

    mapping = _normalize_mapping(tier_mapping or {})
    if tier not in mapping:
        raise ModelResolutionError("MODEL_TIER_NOT_ALLOWED", f"model_tier is not allowed: {tier}")

    allowed_models = mapping[tier]
    preferred = _normalize_model((preferred_models or {}).get(tier))
    if preferred:
        if preferred not in allowed_models:
            raise ModelResolutionError(
                "MODEL_NOT_ALLOWED",
                f"preferred model is not allowed for tier {tier}",
            )
        return ResolvedModel(model_tier=tier, model=preferred, source="user_preference")

    default_model = allowed_models[0] if allowed_models else ""
    if not default_model:
        raise ModelResolutionError("MODEL_NOT_CONFIGURED", f"no model configured for tier {tier}")
    return ResolvedModel(model_tier=tier, model=default_model, source="system_config")


def _normalize_mapping(raw: Mapping[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in raw.items():
        tier = _normalize_key(key)
        if not tier:
            continue
        models = _extract_models(value)
        if not models:
            raise ModelResolutionError(
                "MODEL_TIER_CONFIG_INVALID",
                f"model_tier_mapping.{tier} must include at least one model",
            )
        result[tier] = models
    return result


def _extract_models(value: Any) -> list[str]:
    if isinstance(value, str):
        model = _normalize_model(value)
        return [model] if model else []
    if isinstance(value, list):
        return _dedupe(_normalize_model(item) for item in value if isinstance(item, str))
    if isinstance(value, Mapping):
        raw_default = value.get("default")
        raw_models = value.get("models")
        models: list[str] = []
        default_model = _normalize_model(raw_default if isinstance(raw_default, str) else None)
        if default_model:
            models.append(default_model)
        if isinstance(raw_models, list):
            models.extend(_normalize_model(item) for item in raw_models if isinstance(item, str))
        elif isinstance(raw_models, str):
            models.append(_normalize_model(raw_models))
        return _dedupe(models)
    raise ModelResolutionError(
        "MODEL_TIER_CONFIG_INVALID",
        "model_tier_mapping values must be strings, arrays, or objects",
    )


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = _normalize_model(item)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalize_key(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_model(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
