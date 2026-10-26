from llm_client import _normalize_temperature


def test_kimi_models_force_temperature_to_one() -> None:
    assert _normalize_temperature("kimi-k2.6", 0.7) == 1.0
    assert _normalize_temperature("KIMI-K2.7-CODE", 0.2) == 1.0


def test_non_kimi_models_keep_requested_temperature() -> None:
    assert _normalize_temperature("deepseek-v4-pro", 0.7) == 0.7

