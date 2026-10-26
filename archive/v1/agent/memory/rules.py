from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LearnedRules:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._rules: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self._rules, f, ensure_ascii=False, indent=2)

    def all(self) -> list[dict[str, Any]]:
        return list(self._rules)

    def match(self, context_tags: list[str]) -> list[dict[str, Any]]:
        matched = []
        for rule in self._rules:
            cond = rule.get("condition", "")
            if any(tag in cond for tag in context_tags):
                matched.append(rule)
        return matched

    def add(self, condition: str, action: str, confidence: float = 0.7) -> None:
        for r in self._rules:
            if r["condition"] == condition and r["action"] == action:
                r["confidence"] = min(1.0, r["confidence"] + 0.1)
                r["hit_count"] = r.get("hit_count", 0) + 1
                r["last_hit"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return
        self._rules.append({
            "rule_id": f"rule_{len(self._rules)}",
            "condition": condition,
            "action": action,
            "confidence": confidence,
            "hit_count": 1,
            "last_hit": datetime.now(timezone.utc).isoformat(),
        })
        self._save()

    def decay(self, factor: float = 0.99) -> None:
        pruned = []
        for r in self._rules:
            r["confidence"] = round(r["confidence"] * factor, 4)
            if r["confidence"] >= 0.3:
                pruned.append(r)
        self._rules = pruned
        self._save()

    def hit(self, rule_id: str) -> None:
        for r in self._rules:
            if r.get("rule_id") == rule_id:
                r["hit_count"] = r.get("hit_count", 0) + 1
                r["confidence"] = min(1.0, r["confidence"] + 0.05)
                r["last_hit"] = datetime.now(timezone.utc).isoformat()
                break
        self._save()
