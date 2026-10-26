from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LongTermMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = self._load()

    def get_day(self, day_key: str) -> dict[str, Any]:
        return dict(self._data.get(day_key) or {})

    def summarize_day(self, day_key: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        grouped = {
            "player_dialogues": self._collect(records, "player_dialogue"),
            "special_events": self._collect(records, "special_event"),
            "action_results": self._collect(records, "action_result"),
            "emotion_responses": self._collect(records, "emotion_response"),
        }
        summary = {
            "day_key": day_key,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "player_dialogues": grouped["player_dialogues"],
            "special_events": grouped["special_events"],
            "action_results": grouped["action_results"],
            "emotion_responses": grouped["emotion_responses"],
            "counts": {
                "player_dialogues": len(grouped["player_dialogues"]),
                "special_events": len(grouped["special_events"]),
                "action_results": len(grouped["action_results"]),
                "emotion_responses": len(grouped["emotion_responses"]),
            },
        }
        self._data[day_key] = summary
        self._save()
        return summary

    def all_days(self) -> dict[str, Any]:
        return dict(self._data)

    def _collect(self, records: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for record in records:
            if record.get("category") != category:
                continue
            summary = str(record.get("summary") or "").strip()
            if not summary:
                continue
            item = seen.setdefault(
                summary,
                {
                    "summary": summary,
                    "count": 0,
                    "latest_time": 0,
                    "latest_turn_id": "",
                },
            )
            item["count"] += 1
            item["latest_time"] = max(item["latest_time"], int(record.get("game_time") or 0))
            item["latest_turn_id"] = str(record.get("turn_id") or item["latest_turn_id"])
        items = list(seen.values())
        items.sort(key=lambda item: (-item["count"], -item["latest_time"], item["summary"]))
        return items[:20]

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            with self.path.open(encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(self._data, file, ensure_ascii=False, indent=2)
