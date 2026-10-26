from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.memory.emotion import EmotionTracker, detect_emotion
from agent.memory.episodic import EpisodicMemory
from agent.memory.long_term import LongTermMemory
from agent.memory.retriever import MemoryRetriever
from agent.memory.rules import LearnedRules
from agent.memory.short_term import ShortTermMemory
from agent.memory.working import TurnRecord, WorkingMemory


class MemoryManager:
    def __init__(self, store_dir: Path, limit: int = 100, recent_limit: int = 10) -> None:
        self.limit = limit
        self.recent_limit = recent_limit
        self.short_term = ShortTermMemory(store_dir / "short_term.json", limit)
        self.working = WorkingMemory(capacity=recent_limit)
        self.rules = LearnedRules(store_dir / "learned_rules.json")
        self.episodes = EpisodicMemory(store_dir / "episodes.md")
        self.long_term = LongTermMemory(store_dir / "long_term.json")
        self.retriever = MemoryRetriever()
        self.emotion = EmotionTracker(store_dir / "emotion.json")
        self._context_cache: dict[str, Any] = {}
        self._cache_day_key: str = ""
        self._hydrate_working()

    def working_recent(self, n: int = 10) -> list[dict[str, Any]]:
        return self.working.recent(n)

    def short_term_recent(
        self,
        limit: int | None = None,
        categories: list[str] | None = None,
        day_key: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.short_term.recent(limit=limit, categories=categories, day_key=day_key)

    def long_term_day(self, day_key: str) -> dict[str, Any]:
        return self.long_term.get_day(day_key)

    def planner_memory(self, day_key: str) -> dict[str, Any]:
        if self._cache_day_key == day_key and self._context_cache:
            return self._context_cache

        short_records = self.short_term_recent(
            limit=48,
            day_key=day_key,
            categories=["player_dialogue", "special_event", "action_result", "decision_trace", "emotion_response"],
        )
        short_term = self.retriever.categorize(
            short_records,
            {
                "player_dialogue": 8,
                "special_event": 8,
                "action_result": 4,
                "decision_trace": 3,
                "emotion_response": 5,
            },
        )
        long_term = self.retriever.digest_daily_summary(
            self.long_term_day(day_key),
            {
                "player_dialogue": 6,
                "special_event": 6,
                "action_result": 3,
                "emotion_response": 4,
            },
        )
        past_days = self._recent_days_highlights(day_key)
        emotion_context = self._build_emotion_context(short_records)
        result = {
            "short_term": short_term,
            "long_term": long_term,
            "past_days": past_days,
            "emotion": emotion_context,
        }
        self._context_cache = result
        self._cache_day_key = day_key
        return result

    def _build_emotion_context(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        current_mood = self.emotion.get_mood()
        self.working.set_emotion(current_mood)
        emotion_rules = self.emotion.get_emotion_rules()
        emotion_records = self.retriever.retrieve_emotion_context(
            records, emotion_rules, current_mood,
        )
        return {
            "current_mood": current_mood,
            "recent_emotions": self.emotion.recent_emotions(),
            "behavior_patterns": emotion_rules[:5],
            "emotion_memories": [
                {"summary": r.get("summary"), "game_time": r.get("game_time")}
                for r in emotion_records
            ],
        }

    def _recent_days_highlights(self, current_day_key: str, lookback: int = 3) -> dict[str, list[dict[str, Any]]]:
        all_days = self.long_term.all_days()
        keys = sorted(all_days.keys())
        try:
            idx = keys.index(current_day_key)
        except ValueError:
            idx = len(keys)
        past_keys = keys[max(0, idx - lookback):idx]
        dialogues: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for dk in past_keys:
            day = all_days[dk]
            for d in (day.get("player_dialogues") or [])[:3]:
                d_copy = dict(d)
                d_copy["from_day"] = dk
                dialogues.append(d_copy)
            for e in (day.get("special_events") or [])[:2]:
                e_copy = dict(e)
                e_copy["from_day"] = dk
                events.append(e_copy)
        return {"past_dialogues": dialogues[-6:], "past_events": events[-4:]}

    def matched_rules(self, context_tags: list[str]) -> list[dict[str, Any]]:
        return self.rules.match(context_tags)

    def push_turn(
        self,
        action: str,
        arguments: dict[str, Any],
        expectation: str,
        result: Any,
        outcome: str = "",
        lesson: str = "",
        context_summary: str = "",
        game_time: int = 0,
        day_key: str = "",
        turn_id: str = "",
        request_id: str = "",
        decision_reason: str = "",
    ) -> None:
        record = TurnRecord(
            action=action,
            arguments=arguments,
            expectation=expectation,
            result=result,
            outcome=outcome,
            lesson=lesson,
        )
        self.working.push(record)
        records = [
            {
                "category": "decision_trace",
                "priority": 0,
                "day_key": day_key,
                "game_time": game_time,
                "turn_id": turn_id,
                "request_id": request_id,
                "summary": f"{action} -> {expectation or action}",
                "details": {
                    "action": action,
                    "arguments": arguments,
                    "expectation": expectation,
                    "decision_reason": decision_reason,
                    "context_summary": context_summary,
                },
            },
            {
                "category": "action_result",
                "priority": 1,
                "day_key": day_key,
                "game_time": game_time,
                "turn_id": turn_id,
                "request_id": request_id,
                "summary": self._build_action_summary(action, outcome, result, lesson),
                "details": {
                    "action": action,
                    "arguments": arguments,
                    "expectation": expectation,
                    "result": result,
                    "outcome": outcome,
                    "lesson": lesson,
                    "context_summary": context_summary,
                },
            },
        ]
        dialogue_record = self._build_dialogue_record(action, result, day_key, game_time, turn_id, request_id)
        if dialogue_record is not None:
            records.append(dialogue_record)
        self.short_term.append_many(records)
        self._invalidate_cache()
        if day_key:
            self.refresh_long_term(day_key)

    def record_episode(
        self,
        action: str,
        context_summary: str,
        result: Any,
        lesson: str,
        game_time: int = 0,
    ) -> None:
        self.episodes.record(action, context_summary, result, lesson, game_time)

    def add_rule(self, condition: str, action: str, confidence: float = 0.7) -> None:
        self.rules.add(condition, action, confidence)

    def daily_decay(self) -> None:
        self.rules.decay()

    def record_event(
        self,
        category: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        day_key: str = "",
        game_time: int = 0,
        turn_id: str = "",
        priority: int | None = None,
    ) -> None:
        self.short_term.append(
            {
                "category": category,
                "priority": priority if priority is not None else self._priority_for_category(category),
                "day_key": day_key,
                "game_time": game_time,
                "turn_id": turn_id,
                "summary": summary,
                "details": payload or {},
            }
        )
        self._invalidate_cache()
        if day_key:
            self.refresh_long_term(day_key)

    def record_emotion_event(
        self,
        emotion: str,
        trigger_action: str,
        player_text: str,
        context_tags: list[str],
        npc_name: str = "",
        day_key: str = "",
        game_time: int = 0,
        turn_id: str = "",
    ) -> None:
        if emotion == "neutral":
            return
        self.emotion.record(
            emotion=emotion,
            trigger_action=trigger_action,
            context_tags=context_tags,
            player_text=player_text,
            npc_name=npc_name,
            day_key=day_key,
            game_time=game_time,
        )
        self.working.set_emotion(emotion)
        self.short_term.append({
            "category": "emotion_response",
            "priority": 3,
            "day_key": day_key,
            "game_time": game_time,
            "turn_id": turn_id,
            "summary": f"emotion:{emotion} | trigger={trigger_action} | {player_text[:60]}",
            "details": {
                "emotion": emotion,
                "trigger_action": trigger_action,
                "player_text": player_text[:200],
                "npc_name": npc_name,
            },
        })
        self._invalidate_cache()

    def detect_and_record_emotion(
        self,
        text: str,
        last_action: str,
        context_tags: list[str],
        npc_name: str = "",
        day_key: str = "",
        game_time: int = 0,
        turn_id: str = "",
    ) -> str:
        emotion = detect_emotion(text)
        if emotion != "neutral":
            self.record_emotion_event(
                emotion=emotion,
                trigger_action=last_action,
                player_text=text,
                context_tags=context_tags,
                npc_name=npc_name,
                day_key=day_key,
                game_time=game_time,
                turn_id=turn_id,
            )
        return emotion

    def refresh_long_term(self, day_key: str) -> dict[str, Any]:
        records = self.short_term_recent(limit=self.limit, day_key=day_key)
        return self.long_term.summarize_day(day_key, records)

    def build_day_key(self, raw_state: dict[str, Any]) -> str:
        world = raw_state.get("world") or {}
        year = int(world.get("year") or 1)
        season = str(world.get("season") or "unknown")
        day = int(world.get("day") or 0)
        return f"year{year}_{season}_day{day}"

    def _invalidate_cache(self) -> None:
        self._context_cache = {}

    def _hydrate_working(self) -> None:
        records = self.short_term_recent(limit=self.recent_limit * 4)
        for item in records:
            if item.get("category") != "action_result":
                continue
            details = item.get("details") or {}
            action = details.get("action")
            arguments = details.get("arguments")
            expectation = details.get("expectation")
            if not isinstance(action, str) or not isinstance(arguments, dict):
                continue
            self.working.push(
                TurnRecord(
                    action=action,
                    arguments=arguments,
                    expectation=expectation if isinstance(expectation, str) else action,
                    result=details.get("result"),
                    outcome=str(details.get("outcome") or ""),
                    lesson=str(details.get("lesson") or ""),
                )
            )

    def _build_action_summary(self, action: str, outcome: str, result: Any, lesson: str) -> str:
        parts = [action]
        if outcome:
            parts.append(outcome)
        message = self._extract_result_message(result)
        if message:
            parts.append(message)
        if lesson:
            parts.append(lesson)
        return " | ".join(parts)

    def _extract_result_message(self, result: Any) -> str:
        if isinstance(result, list):
            messages = []
            for item in result[:3]:
                if isinstance(item, dict) and item.get("message"):
                    messages.append(str(item["message"]))
            return "; ".join(messages)
        if isinstance(result, dict) and result.get("message"):
            return str(result["message"])
        return ""

    def _priority_for_category(self, category: str) -> int:
        if category in ("player_dialogue", "emotion_response"):
            return 3
        if category == "special_event":
            return 2
        if category == "action_result":
            return 1
        return 0

    def _build_dialogue_record(
        self,
        action: str,
        result: Any,
        day_key: str,
        game_time: int,
        turn_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        if action not in {"talk_to_npc", "give_gift"}:
            return None
        summary = self._extract_result_message(result)
        if not summary:
            summary = action
        return {
            "category": "player_dialogue",
            "priority": 3,
            "day_key": day_key,
            "game_time": game_time,
            "turn_id": turn_id,
            "request_id": request_id,
            "summary": summary,
            "details": {
                "action": action,
                "result": result,
            },
        }

    @property
    def force_switch(self) -> bool:
        return self.working.force_switch

    def reset_force_switch(self) -> None:
        self.working.force_switch = False
