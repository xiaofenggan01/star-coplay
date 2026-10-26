from __future__ import annotations

import json
from pathlib import Path

from report.telemetry import (
    Telemetry,
    clear_current,
    reset_session_counters,
    session_end_payload,
    set_current,
    should_send_remote_event,
    track,
)
from report.telemetry_backends import TelemetryAuthError, TelemetryBackend

POLICY = json.loads(
    (Path(__file__).resolve().parents[2] / "platform" / "shared" / "telemetry-event-policy.json").read_text(
        encoding="utf-8"
    )
)


class AuthFailBackend(TelemetryBackend):
    def __init__(self) -> None:
        self.calls = 0

    def write(self, events: list[dict], *, token: str) -> None:
        self.calls += 1
        raise TelemetryAuthError("invalid token / cannot resolve user_id")


class CaptureBackend(TelemetryBackend):
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def write(self, events: list[dict], *, token: str) -> None:
        self.batches.append(events)


def test_invalid_token_switches_to_spill_only(tmp_path):
    backend = AuthFailBackend()
    tel = Telemetry(platform_token="bad", backend=backend, spill_dir=tmp_path)

    tel.track("player.message", {"n": 1})
    tel.flush()
    tel.track("player.message", {"n": 2})
    tel.flush()

    assert backend.calls == 1
    assert list(tmp_path.glob("telemetry_*.jsonl"))


def test_runtime_diagnostics_are_not_sent_to_remote_events(tmp_path):
    backend = CaptureBackend()
    tel = Telemetry(platform_token="ok", backend=backend, spill_dir=tmp_path)

    tel.track("ai.llm_call", {"tokens_in": 3, "tokens_out": 5})
    tel.track("error.llm_timeout", {"model": "balanced"})
    tel.track("ai.error", {"message": "boom"})
    tel.track("ai.action", {"action_type": "go_to"})
    tel.track("player.message", {"length": 2})
    tel.flush()

    assert len(backend.batches) == 1
    assert [event["type"] for event in backend.batches[0]] == ["player.message"]
    payload = tel._heartbeat_snapshot_fn  # keep test local to this module without exposing internals
    assert payload is None


def test_remote_event_policy_matches_shared_contract():
    for event_type in POLICY["persisted_examples"]:
        assert should_send_remote_event(event_type) is True, event_type
    for event_type in POLICY["dropped_examples"]:
        assert should_send_remote_event(event_type) is False, event_type


def test_suppressed_runtime_diagnostics_still_update_session_counters(tmp_path):
    backend = CaptureBackend()
    tel = Telemetry(platform_token="ok", backend=backend, spill_dir=tmp_path)
    reset_session_counters()
    set_current(tel)

    try:
        track("ai.llm_call", {"tokens_in": 3, "tokens_out": 5})
        track("ai.action", {"action_type": "go_to"})
    finally:
        clear_current()

    payload = session_end_payload()
    assert payload["total_llm_calls"] == 1
    assert payload["total_actions"] == 1
