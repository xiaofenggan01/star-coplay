from __future__ import annotations

from typing import Any


class MemoryRetriever:
    def recent(self, records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        return records[-limit:]

    def categorize(self, records: list[dict[str, Any]], budgets: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for category, limit in budgets.items():
            bucket = [record for record in records if record.get("category") == category]
            bucket = self._score_and_sort(bucket)
            grouped[category] = bucket[-limit:] if limit > 0 else []
        return grouped

    def digest_daily_summary(self, summary: dict[str, Any], budgets: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
        mapping = {
            "player_dialogue": "player_dialogues",
            "special_event": "special_events",
            "action_result": "action_results",
            "emotion_response": "emotion_responses",
        }
        out: dict[str, list[dict[str, Any]]] = {}
        for category, key in mapping.items():
            items = summary.get(key)
            if not isinstance(items, list):
                out[key] = []
                continue
            limit = budgets.get(category, 0)
            out[key] = items[:limit] if limit > 0 else []
        return out

    def retrieve_emotion_context(
        self,
        records: list[dict[str, Any]],
        emotion_rules: list[dict[str, Any]],
        current_emotion: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        emotion_records = [r for r in records if r.get("category") == "emotion_response"]
        if current_emotion and current_emotion != "neutral":
            matched = [
                r for r in emotion_records
                if r.get("details", {}).get("emotion") == current_emotion
            ]
            if matched:
                emotion_records = matched
        scored = []
        for r in emotion_records:
            score = r.get("priority", 0) * 2
            details = r.get("details") or {}
            if details.get("emotion") == current_emotion:
                score += 5
            if details.get("emotion") in ("happy", "excited"):
                score += 2
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def _score_and_sort(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(records) <= 1:
            return records
        scored = []
        for i, r in enumerate(records):
            recency = i
            priority = int(r.get("priority", 0))
            is_emotion = 3 if r.get("category") == "emotion_response" else 0
            is_dialogue = 2 if r.get("category") == "player_dialogue" else 0
            score = recency + priority * 2 + is_emotion + is_dialogue
            scored.append((score, i, r))
        scored.sort(key=lambda x: x[0])
        return [r for _, _, r in scored]
