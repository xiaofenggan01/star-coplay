from __future__ import annotations

from types import SimpleNamespace

import session_runner


def test_session_runner_requires_llm_relay(monkeypatch):
    monkeypatch.delenv("LLM_RELAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_RELAY_API_KEY", raising=False)
    monkeypatch.setattr(
        session_runner,
        "_parse_args",
        lambda: SimpleNamespace(
            agent_instance_id="agent-1",
            platform_token="platform-token",
            user_id="user-1",
            target_player_id="",
            platform_url="http://platform.local",
        ),
    )

    assert session_runner.main() == 2


def test_session_runner_passes_only_llm_relay_overrides(monkeypatch):
    captured: dict = {}
    config: dict = {}

    class FakeBridge:
        def __init__(self, **_kwargs):
            pass

        def handle_message(self, _msg):
            pass

    class FakeHandle:
        def stop(self):
            pass

    monkeypatch.setenv("LLM_RELAY_BASE_URL", "http://router.local/api/v1/system")
    monkeypatch.setenv("LLM_RELAY_API_KEY", "relay-token")
    monkeypatch.setattr(
        session_runner,
        "_parse_args",
        lambda: SimpleNamespace(
            agent_instance_id="agent-1",
            platform_token="platform-token",
            user_id="",
            target_player_id="",
            platform_url="http://platform.local",
        ),
    )
    monkeypatch.setattr("bridge.ws_bridge.WSBridge", FakeBridge)
    monkeypatch.setattr("main.load_config", lambda: config)

    def fake_build_agent(_bridge, _config, *, llm_overrides, start_telemetry):
        captured["llm_overrides"] = dict(llm_overrides)
        captured["start_telemetry"] = start_telemetry
        return FakeHandle()

    monkeypatch.setattr("main.build_agent", fake_build_agent)
    monkeypatch.setattr(session_runner.sys, "stdin", [])

    assert session_runner.main() == 0
    assert captured["llm_overrides"] == {
        "api_base": "http://router.local/api/v1/system",
        "api_key": "relay-token",
    }
    assert captured["start_telemetry"] is False
    assert config["telemetry"]["platform_token"] == "platform-token"
