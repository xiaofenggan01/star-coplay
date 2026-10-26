from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ShortTermMemory:
    def __init__(self, path: Path, limit: int) -> None:
        self.path = path
        self.limit = limit

    def load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            with self.path.open(encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                return [self._normalize_record(item) for item in data if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            return []
        return []

    def append(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        items = self.load()
        normalized = self._normalize_record(record)
        if items and self._is_duplicate(items[-1], normalized):
            return items
        items.append(normalized)
        items = self._trim(items)
        self._save(items)
        return items

    def append_many(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = self.load()
        for record in records:
            normalized = self._normalize_record(record)
            if items and self._is_duplicate(items[-1], normalized):
                continue
            items.append(normalized)
        items = self._trim(items)
        self._save(items)
        return items

    def recent(
        self,
        limit: int | None = None,
        categories: list[str] | None = None,
        day_key: str | None = None,
    ) -> list[dict[str, Any]]:
        items = self.load()
        if categories:
            allow = set(categories)
            items = [item for item in items if item.get("category") in allow]
        if day_key:
            items = [item for item in items if item.get("day_key") == day_key]
        if limit is not None:
            items = items[-limit:]
        return items

    _PROTECT_BUDGET: dict[int, int] = {3: 12, 2: 8}
    _COMPACT_AGE_THRESHOLD = 30

    def _trim(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(items) <= self.limit:
            return items
        items = self._microcompact(items)
        if len(items) <= self.limit:
            return items
        protected: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        counts: dict[int, int] = {}
        for item in reversed(items):
            p = item.get("priority", 0)
            budget = self._PROTECT_BUDGET.get(p, 0)
            if budget and counts.get(p, 0) < budget:
                protected.append(item)
                counts[p] = counts.get(p, 0) + 1
            else:
                rest.append(item)
        protected.reverse()
        rest.reverse()
        remaining = self.limit - len(protected)
        if remaining < 0:
            protected = protected[-self.limit:]
            return protected
        return rest[-remaining:] + protected

    def _microcompact(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(items) <= self._COMPACT_AGE_THRESHOLD:
            return items
        old = items[:-self._COMPACT_AGE_THRESHOLD]
        recent = items[-self._COMPACT_AGE_THRESHOLD:]
        merged: dict[str, dict[str, Any]] = {}
        for item in old:
            cat = item.get("category", "misc")
            day = item.get("day_key", "")
            key = f"{cat}|{day}"
            if key not in merged:
                merged[key] = {
                    "category": cat,
                    "priority": item.get("priority", 0),
                    "day_key": day,
                    "game_time": item.get("game_time", 0),
                    "turn_id": "",
                    "request_id": "",
                    "summary": item.get("summary", ""),
                    "details": {},
                    "timestamp": item.get("timestamp", ""),
                    "_count": 1,
                }
            else:
                m = merged[key]
                m["_count"] += 1
                m["game_time"] = max(m["game_time"], item.get("game_time", 0))
                m["priority"] = max(m["priority"], item.get("priority", 0))
                if item.get("category") == "emotion_response":
                    m["summary"] = item.get("summary", m["summary"])
                    m["details"] = item.get("details", m["details"])
        compacted = []
        for m in merged.values():
            cnt = m.pop("_count", 1)
            if cnt > 1:
                m["summary"] = f"[x{cnt}] {m['summary']}"
            compacted.append(m)
        compacted.sort(key=lambda x: x.get("game_time", 0))
        return compacted + recent

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(items, file, ensure_ascii=False, indent=2)

    def _normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        category = record.get("category")
        if not isinstance(category, str) or not category.strip():
            category = "action_result" if record.get("action") else "misc"
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = self._infer_summary(record)
        return {
            "category": category,
            "priority": int(record.get("priority", self._default_priority(category))),
            "day_key": str(record.get("day_key") or ""),
            "game_time": int(record.get("game_time") or 0),
            "turn_id": str(record.get("turn_id") or ""),
            "request_id": str(record.get("request_id") or ""),
            "summary": summary,
            "details": record.get("details") if isinstance(record.get("details"), dict) else self._legacy_details(record),
            "timestamp": str(record.get("timestamp") or datetime.now(timezone.utc).isoformat()),
        }

    def _legacy_details(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": record.get("action"),
            "arguments": record.get("args") or record.get("arguments"),
            "expectation": record.get("expectation"),
            "result": record.get("result"),
            "outcome": record.get("outcome"),
            "lesson": record.get("lesson"),
        }

    def _infer_summary(self, record: dict[str, Any]) -> str:
        action = record.get("action")
        outcome = record.get("outcome")
        lesson = record.get("lesson")
        if isinstance(action, str) and action:
            parts = [action]
            if isinstance(outcome, str) and outcome:
                parts.append(outcome)
            if isinstance(lesson, str) and lesson:
                parts.append(lesson)
            return " | ".join(parts)
        return "memory_record"

    def _default_priority(self, category: str) -> int:
        if category == "player_dialogue":
            return 3
        if category == "special_event":
            return 2
        if category == "action_result":
            return 1
        return 0

    def _is_duplicate(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (
            left.get("category") == right.get("category")
            and left.get("day_key") == right.get("day_key")
            and left.get("summary") == right.get("summary")
            and left.get("details") == right.get("details")
        )
