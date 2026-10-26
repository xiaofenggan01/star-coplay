from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.debug.runtime import TurnTrace
from agent.llm.client import LLMClient
from agent.llm.prompt_builder import build_distill_prompt
from agent.memory.manager import MemoryManager

if TYPE_CHECKING:
    from agent.evomap.integration import EvoMapIntegration

logger = logging.getLogger(__name__)


class KnowledgeEvolver:
    def __init__(self, llm_client: LLMClient, memory: MemoryManager, knowledge_dir: Path) -> None:
        self.llm = llm_client
        self.memory = memory
        self.knowledge_dir = knowledge_dir
        self._card_meta_path = knowledge_dir / "card_meta.json"
        self.evomap: "EvoMapIntegration | None" = None

    def distill_from_day(self, day_key: str, trace: TurnTrace | None = None) -> int:
        daily_summary = self.memory.refresh_long_term(day_key)
        if not daily_summary:
            return 0
        prompt = build_distill_prompt(daily_summary)
        response = self.llm.distill(prompt, trace=trace)
        if not response.ok or not isinstance(response.value, list):
            if trace is not None:
                trace.record("distill_skipped", response.as_dict())
            return 0
        new_rules = response.value
        count = 0
        for rule in new_rules:
            cond = rule.get("condition", "")
            act = rule.get("action", "")
            conf = float(rule.get("confidence", 0.7))
            if cond and act:
                self.memory.add_rule(cond, act, conf)
                count += 1
        if trace is not None:
            trace.record("distill_rules", {"day_key": day_key, "count": count})
        return count

    def score_card(self, card_id: str, hit: bool) -> None:
        meta = self._load_meta()
        entry = meta.get(card_id, {"hit_count": 0, "miss_count": 0})
        if hit:
            entry["hit_count"] = entry.get("hit_count", 0) + 1
        else:
            entry["miss_count"] = entry.get("miss_count", 0) + 1
        meta[card_id] = entry
        self._save_meta(meta)

    def prune_cards(self) -> list[str]:
        meta = self._load_meta()
        pruned = []
        for card_id, entry in list(meta.items()):
            miss = entry.get("miss_count", 0)
            hit = entry.get("hit_count", 0)
            if miss > hit * 2 and miss >= 3:
                pruned.append(card_id)
                del meta[card_id]
        if pruned:
            self._save_meta(meta)
        return pruned

    def distill_emotion_rules(self, day_key: str, trace: TurnTrace | None = None) -> int:
        emotion_rules = self.memory.emotion.get_emotion_rules(limit=10)
        if not emotion_rules:
            return 0
        count = 0
        for rule in emotion_rules:
            if rule.get("total", 0) < 3:
                continue
            pos_rate = rule.get("positive_rate", 0)
            trigger = rule.get("trigger", "")
            ctx = rule.get("context", [])
            ctx_str = ",".join(ctx[:3]) if ctx else "any"
            if pos_rate >= 0.7:
                self.memory.add_rule(
                    condition=f"player_emotion+{ctx_str}",
                    action=f"do:{trigger}(让玩家开心)",
                    confidence=min(0.9, pos_rate),
                )
                count += 1
            elif pos_rate <= 0.3:
                self.memory.add_rule(
                    condition=f"player_emotion+{ctx_str}",
                    action=f"avoid:{trigger}(玩家不喜欢)",
                    confidence=min(0.9, 1.0 - pos_rate),
                )
                count += 1
        if trace is not None:
            trace.record("emotion_rules_distilled", {"count": count})
        return count

    def daily_maintenance(self, day_key: str, trace: TurnTrace | None = None) -> dict[str, Any]:
        self.memory.daily_decay()
        distilled = self.distill_from_day(day_key, trace=trace)
        emotion_distilled = self.distill_emotion_rules(day_key, trace=trace)
        pruned = self.prune_cards()
        evomap_published = 0
        evomap_fetched = 0
        if self.evomap is not None:
            try:
                evomap_published = self.evomap.publish_mature_rules()
                evomap_fetched = self.evomap.fetch_community_rules()
            except Exception as e:
                logger.warning("evomap daily sync error: %s", e)
        return {
            "day_key": day_key,
            "distilled_rule_count": distilled,
            "emotion_rule_count": emotion_distilled,
            "pruned_cards": pruned,
            "evomap_published": evomap_published,
            "evomap_fetched": evomap_fetched,
        }

    def _load_meta(self) -> dict[str, Any]:
        if not self._card_meta_path.is_file():
            return {}
        try:
            with self._card_meta_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_meta(self, meta: dict[str, Any]) -> None:
        self._card_meta_path.parent.mkdir(parents=True, exist_ok=True)
        with self._card_meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
