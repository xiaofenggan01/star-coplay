from pathlib import Path


def test_bootstrap_installs_say_delivery_hook_without_final_text_forwarding():
    bootstrap = Path(__file__).parents[1] / "app" / "bootstrap.py"
    source = bootstrap.read_text(encoding="utf-8")

    assert "turn_completion_hook=hosted_turn_completion_relay" in source
    assert "background_jobs=hosted_composition.projection" in source
    assert "hosted_turn_completion_relay.bind(hosted_runtime_input_dispatcher)" in source
    assert "HostedTurnDelivery" not in source
