import json
from pathlib import Path

from modules.admin_dashboard.metrics import build_dashboard_metrics


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_build_dashboard_metrics_from_structured_ecs_logs(tmp_path: Path) -> None:
    _write(tmp_path / "gateway" / "gateway.jsonl", [
        {"ts": "2026-07-10T08:00:00Z", "event": "gateway.auth.ok", "user_id": "u1"},
        {"ts": "2026-07-10T09:00:00Z", "event": "gateway.auth.ok", "user_id": "u1"},
        {"ts": "2026-07-11T08:00:00Z", "event": "gateway.auth.ok", "user_id": "u2"},
    ])
    _write(tmp_path / "agent-runtime" / "agent-runtime.jsonl", [
        {"ts": "2026-07-10T08:01:00Z", "event": "agent.session.started", "user_id": "u1", "agent_instance_id": "a1"},
        {"ts": "2026-07-10T08:16:00Z", "event": "agent.session.stopped", "agent_instance_id": "a1"},
        {"ts": "2026-07-11T08:01:00Z", "event": "agent.session.started", "user_id": "u2", "agent_instance_id": "a2"},
    ])

    result = build_dashboard_metrics(tmp_path, days=30, now="2026-07-12T00:00:00Z")

    assert result["dau"] == [
        {"day": "2026-07-10", "dau": 1},
        {"day": "2026-07-11", "dau": 1},
    ]
    assert result["avg_session_duration"] == [
        {"day": "2026-07-10", "avg_duration_min": 15.0, "total_sessions": 1},
        {"day": "2026-07-11", "avg_duration_min": 1.0, "total_sessions": 1},
    ]
    assert result["retention"] == [
        {"cohort_date": "2026-07-10", "cohort_size": 1, "d1": 0.0, "d3": None, "d7": None},
        {"cohort_date": "2026-07-11", "cohort_size": 1, "d1": None, "d3": None, "d7": None},
    ]
    assert result["freshness"]["latest_event_at"] == "2026-07-11T08:01:00+00:00"


def test_build_dashboard_metrics_skips_malformed_and_out_of_window_rows(tmp_path: Path) -> None:
    _write(tmp_path / "gateway" / "gateway.jsonl", [
        {"ts": "2026-06-01T00:00:00Z", "event": "gateway.auth.ok", "user_id": "old"},
        {"ts": "bad", "event": "gateway.auth.ok", "user_id": "bad"},
        {"ts": "2026-07-11T00:00:00Z", "event": "gateway.http.request_completed"},
    ])

    result = build_dashboard_metrics(tmp_path, days=7, now="2026-07-12T00:00:00Z")
    assert result["dau"] == []
    assert result["avg_session_duration"] == []
    assert result["retention"] == []


def test_build_dashboard_metrics_reads_current_operations_layout(tmp_path: Path) -> None:
    _write(tmp_path / "operations" / "gateway-info.jsonl", [
        {"ts": "2026-07-11T08:00:00Z", "event": "gateway.auth.ok", "user_id": "u1"},
    ])
    _write(tmp_path / "operations" / "agent-runtime-info.jsonl.1", [
        {
            "ts": "2026-07-11T08:01:00Z",
            "event": "agent.session.started",
            "user_id": "u1",
            "agent_instance_id": "a1",
        },
        {
            "ts": "2026-07-11T08:16:00Z",
            "event": "agent.session.stopped",
            "agent_instance_id": "a1",
        },
    ])

    result = build_dashboard_metrics(tmp_path, days=30, now="2026-07-12T00:00:00Z")

    assert result["dau"] == [{"day": "2026-07-11", "dau": 1}]
    assert result["avg_session_duration"] == [
        {"day": "2026-07-11", "avg_duration_min": 15.0, "total_sessions": 1},
    ]
    assert result["freshness"]["events_in_window"] == 3
