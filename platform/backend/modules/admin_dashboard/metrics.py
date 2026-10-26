from __future__ import annotations

import gzip
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _lines(path: Path) -> Iterable[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
            yield from stream
    except OSError:
        return


def _rows(log_root: Path) -> Iterable[dict[str, Any]]:
    sources = (
        (log_root / "gateway", "*.jsonl*"),
        (log_root / "agent-runtime", "*.jsonl*"),
        (log_root / "operations", "gateway-*.jsonl*"),
        (log_root / "operations", "agent-runtime-*.jsonl*"),
    )
    seen: set[Path] = set()
    for directory, pattern in sources:
        if not directory.exists():
            continue
        for path in sorted(directory.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            for line in _lines(path):
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict):
                    yield row


def _retention(active_days: dict[str, set[str]], today: datetime) -> list[dict[str, Any]]:
    first_days = {user_id: min(days) for user_id, days in active_days.items() if days}
    cohorts: dict[str, set[str]] = defaultdict(set)
    for user_id, first_day in first_days.items():
        cohorts[first_day].add(user_id)
    result = []
    today_date = today.date()
    for cohort_date in sorted(cohorts):
        users = cohorts[cohort_date]
        cohort_day = datetime.fromisoformat(cohort_date).date()
        item: dict[str, Any] = {"cohort_date": cohort_date, "cohort_size": len(users)}
        for offset in (1, 3, 7):
            key = f"d{offset}"
            if today_date <= cohort_day + timedelta(days=offset):
                item[key] = None
            else:
                target = (cohort_day + timedelta(days=offset)).isoformat()
                retained = sum(1 for user_id in users if target in active_days[user_id])
                item[key] = round(retained / len(users) * 100, 1) if users else 0.0
        result.append(item)
    return result[-30:]


def build_dashboard_metrics(log_root: Path, *, days: int, now: str | None = None) -> dict[str, Any]:
    current = _timestamp(now) if now else datetime.now(timezone.utc)
    current = current or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max(1, days))
    active_days: dict[str, set[str]] = defaultdict(set)
    sessions: dict[str, dict[str, Any]] = {}
    latest: datetime | None = None
    event_count = 0

    for row in _rows(log_root):
        ts = _timestamp(row.get("ts"))
        event = row.get("event")
        if ts is None or ts < cutoff or ts > current or not isinstance(event, str):
            continue
        if event not in {"gateway.auth.ok", "agent.session.started", "agent.session.stopped"}:
            continue
        latest = ts if latest is None or ts > latest else latest
        event_count += 1
        user_id = row.get("user_id")
        if event == "gateway.auth.ok" and isinstance(user_id, str) and user_id:
            active_days[user_id].add(ts.date().isoformat())
        instance_id = row.get("agent_instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            continue
        session = sessions.setdefault(instance_id, {})
        if event == "agent.session.started":
            session["started_at"] = ts
            session["user_id"] = user_id
            if isinstance(user_id, str) and user_id:
                active_days[user_id].add(ts.date().isoformat())
        elif event == "agent.session.stopped":
            session["stopped_at"] = ts

    dau = [
        {"day": day, "dau": sum(1 for user_days in active_days.values() if day in user_days)}
        for day in sorted({day for user_days in active_days.values() for day in user_days})
    ]
    durations: dict[str, list[float]] = defaultdict(list)
    for session in sessions.values():
        started = session.get("started_at")
        if not isinstance(started, datetime):
            continue
        stopped = session.get("stopped_at")
        minutes = max(1.0, (stopped - started).total_seconds() / 60.0) if isinstance(stopped, datetime) else 1.0
        durations[started.date().isoformat()].append(minutes)
    avg_duration = [
        {"day": day, "avg_duration_min": round(sum(values) / len(values), 1), "total_sessions": len(values)}
        for day, values in sorted(durations.items())
    ]
    return {
        "dau": dau,
        "avg_session_duration": avg_duration,
        "retention": _retention(active_days, current),
        "freshness": {
            "status": "available",
            "latest_event_at": latest.isoformat() if latest else None,
            "latest_event_type": "ecs.runtime_activity" if latest else None,
            "events_in_window": event_count,
            "window_days": days,
            "server_now": current.isoformat(),
            "error_code": None,
            "source": "ecs_structured_logs",
        },
    }
