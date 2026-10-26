from __future__ import annotations

import json
from pathlib import Path

from modules.runtime.telemetry_writer import should_persist_event_type

POLICY = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "telemetry-event-policy.json").read_text(encoding="utf-8")
)


def test_telemetry_writer_persists_business_events() -> None:
    for event_type in POLICY["persisted_examples"]:
        assert should_persist_event_type(event_type) is True, event_type
    assert should_persist_event_type("external_text.agent_visible_output") is True


def test_telemetry_writer_drops_runtime_events() -> None:
    for event_type in POLICY["dropped_examples"]:
        assert should_persist_event_type(event_type) is False, event_type
