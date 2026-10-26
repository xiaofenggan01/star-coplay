from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_EMOTION_PATTERNS: dict[str, list[str]] = {
    "happy": [
        "谢谢", "太棒了", "开心", "高兴", "喜欢", "爱你", "好耶", "不错",
        "厉害", "赞", "感谢", "哈哈", "真好", "太好了", "棒", "nice",
        "thanks", "thank", "love", "great", "awesome", "good", "wow",
        "yay", "cool", "amazing", "wonderful", "excellent",
    ],
    "unhappy": [
        "不要", "讨厌", "烦", "别", "差", "糟", "不满", "生气", "滚",
        "走开", "不喜欢", "够了", "停", "不行", "bad", "stop", "hate",
        "annoying", "no", "don't", "terrible", "awful", "worst",
    ],
    "surprised": [
        "哇", "天哪", "真的吗", "居然", "没想到", "惊", "omg", "what",
        "really", "seriously", "unbelievable",
    ],
    "sad": [
        "难过", "伤心", "可惜", "唉", "遗憾", "失望", "sad", "sorry",
        "unfortunately", "miss", "sigh",
    ],
    "excited": [
        "快", "冲", "走起", "出发", "来吧", "go", "let's", "hurry",
        "rush", "come on",
    ],
}

_EMOTION_REGEX: dict[str, re.Pattern[str]] = {}
for _emo, _words in _EMOTION_PATTERNS.items():
    _EMOTION_REGEX[_emo] = re.compile("|".join(re.escape(w) for w in _words), re.IGNORECASE)


def detect_emotion(text: str) -> str:
    if not text:
        return "neutral"
    best, best_count = "neutral", 0
    for emo, pattern in _EMOTION_REGEX.items():
        count = len(pattern.findall(text))
        if count > best_count:
            best, best_count = emo, count
    return best


class EmotionTracker:
    def __init__(self, path: Path, max_patterns: int = 200) -> None:
        self.path = path
        self.max_patterns = max_patterns
        self._data = self._load()

    def record(
        self,
        emotion: str,
        trigger_action: str,
        context_tags: list[str],
        player_text: str = "",
        npc_name: str = "",
        day_key: str = "",
        game_time: int = 0,
    ) -> None:
        if emotion == "neutral":
            return
        entry = {
            "emotion": emotion,
            "trigger_action": trigger_action,
            "context_tags": context_tags[:5],
            "player_text": player_text[:100],
            "npc_name": npc_name,
            "day_key": day_key,
            "game_time": game_time,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._data["events"].append(entry)
        self._update_patterns(entry)
        self._trim()
        self._save()

    def get_mood(self, lookback: int = 5) -> str:
        events = self._data["events"][-lookback:]
        if not events:
            return "neutral"
        scores = defaultdict(int)
        for i, e in enumerate(events):
            scores[e["emotion"]] += i + 1
        return max(scores, key=scores.get) if scores else "neutral"

    def get_behavior_hints(self, context_tags: list[str], limit: int = 5) -> list[dict[str, Any]]:
        patterns = self._data.get("patterns", {})
        hints: list[dict[str, Any]] = []
        tag_set = set(context_tags)
        for key, pat in patterns.items():
            overlap = tag_set & set(pat.get("context_tags", []))
            if overlap or pat.get("trigger_action", "") in str(context_tags):
                score = pat.get("positive_count", 0) - pat.get("negative_count", 0) * 2
                hints.append({
                    "action": pat.get("trigger_action", key),
                    "emotion_effect": "positive" if score > 0 else "negative",
                    "score": score,
                    "detail": pat.get("summary", ""),
                })
        hints.sort(key=lambda h: abs(h["score"]), reverse=True)
        return hints[:limit]

    def get_emotion_rules(self, limit: int = 8) -> list[dict[str, Any]]:
        patterns = self._data.get("patterns", {})
        rules: list[dict[str, Any]] = []
        for key, pat in patterns.items():
            pos = pat.get("positive_count", 0)
            neg = pat.get("negative_count", 0)
            total = pos + neg
            if total < 2:
                continue
            rules.append({
                "trigger": pat.get("trigger_action", key),
                "context": pat.get("context_tags", []),
                "positive_rate": round(pos / total, 2) if total else 0,
                "total": total,
                "summary": pat.get("summary", ""),
            })
        rules.sort(key=lambda r: r["total"], reverse=True)
        return rules[:limit]

    def recent_emotions(self, limit: int = 5) -> list[dict[str, Any]]:
        events = self._data["events"][-limit:]
        return [
            {
                "emotion": e["emotion"],
                "trigger": e.get("trigger_action", ""),
                "text": e.get("player_text", "")[:50],
                "time": e.get("game_time", 0),
            }
            for e in events
        ]

    def _update_patterns(self, entry: dict[str, Any]) -> None:
        action = entry["trigger_action"]
        emotion = entry["emotion"]
        key = f"{action}|{entry.get('npc_name', '')}".rstrip("|")
        patterns = self._data.setdefault("patterns", {})
        pat = patterns.setdefault(key, {
            "trigger_action": action,
            "context_tags": [],
            "positive_count": 0,
            "negative_count": 0,
            "summary": "",
        })
        is_positive = emotion in ("happy", "excited", "surprised")
        if is_positive:
            pat["positive_count"] = pat.get("positive_count", 0) + 1
        else:
            pat["negative_count"] = pat.get("negative_count", 0) + 1
        for tag in entry.get("context_tags", [])[:3]:
            if tag not in pat["context_tags"]:
                pat["context_tags"].append(tag)
                pat["context_tags"] = pat["context_tags"][-8:]
        pat["summary"] = (
            f"{action} → {emotion}(+{pat['positive_count']}/-{pat['negative_count']})"
        )

    def _trim(self) -> None:
        events = self._data["events"]
        if len(events) > self.max_patterns:
            self._data["events"] = events[-self.max_patterns:]

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"events": [], "patterns": {}}
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"events": [], "patterns": {}}
            data.setdefault("events", [])
            data.setdefault("patterns", {})
            return data
        except (OSError, json.JSONDecodeError):
            return {"events": [], "patterns": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
