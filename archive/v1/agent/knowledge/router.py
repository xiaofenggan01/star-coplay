from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.interface.perceiver import GameContext


@dataclass(slots=True)
class KnowledgeCard:
    card_id: str
    content: str


class KnowledgeRouter:
    def __init__(self, knowledge_dir: Path) -> None:
        self.knowledge_dir = knowledge_dir

    def suggest_intents(self, context: GameContext, player_mood: str = "neutral") -> list[str]:
        intents: list[str] = []
        if context.to_water:
            intents.append("watering")
        if context.harvest_tiles:
            intents.append("harvesting")
        if context.time_of_day >= 2200:
            intents.append("sleeping")
        if context.is_indoor and 600 <= context.time_of_day < 2200:
            intents.append("indoor_morning")
        world = context.world or {}
        if bool(world.get("is_festival_day")):
            intents.append("festival")
        farm = context.farm_summary or {}
        animals = farm.get("animals") or {}
        if int(animals.get("unfed", 0) or 0) > 0 or int(animals.get("product_ready", 0) or 0) > 0:
            intents.append("animals_care")
        machines = farm.get("machines") or {}
        if int(machines.get("ready_to_collect", 0) or 0) > 0 or int(machines.get("processing", 0) or 0) > 0:
            intents.append("machines")
        loc = context.location_name
        if any(x in loc for x in ("Shop", "Mart", "Saloon", "Blacksmith")):
            intents.append("errands_shopping")
        if player_mood == "unhappy":
            intents.insert(0, "player_comfort")
        elif player_mood in ("happy", "excited"):
            intents.append("social_interaction")
        return intents

    def get_cards(self, intents: list[str]) -> list[KnowledgeCard]:
        cards: list[KnowledgeCard] = []
        for name in ("weather", "energy"):
            card = self._read_card(self.knowledge_dir / "core" / f"{name}.md", f"core/{name}")
            if card:
                cards.append(card)
        for intent in intents:
            card = self._read_card(self.knowledge_dir / "intents" / f"{intent}.md", f"intent/{intent}")
            if card:
                cards.append(card)
        return cards

    def context_tags(self, context: GameContext) -> list[str]:
        tags: list[str] = []
        if context.block_water:
            tags.append("weather=rain")
        if context.to_water:
            tags.append("has_water_tiles")
        if context.harvest_tiles:
            tags.append("has_harvest_tiles")
        if context.time_of_day >= 2200:
            tags.append("late_night")
        if context.time_of_day < 800:
            tags.append("early_morning")
        world = context.world or {}
        if bool(world.get("is_festival_day")):
            tags.append("festival_day")
        farm = context.farm_summary or {}
        animals = farm.get("animals") or {}
        if int(animals.get("unfed", 0) or 0) > 0:
            tags.append("animals_need_care")
        if int(animals.get("product_ready", 0) or 0) > 0:
            tags.append("animal_products_ready")
        machines = farm.get("machines") or {}
        if int(machines.get("ready_to_collect", 0) or 0) > 0:
            tags.append("machines_ready")
        if int(machines.get("processing", 0) or 0) > 0:
            tags.append("machines_processing")
        crops = farm.get("crops") or {}
        if int(crops.get("unwatered_count", 0) or 0) > 8:
            tags.append("many_unwatered")
        if int(context.energy_max) > 0 and context.energy_current / max(context.energy_max, 1) < 0.35:
            tags.append("low_energy")
        loc = context.location_name
        if any(x in loc for x in ("Shop", "Mart", "Saloon", "Blacksmith")):
            tags.append("commercial_area")
        tags.append(f"location={context.location_name}")
        return tags

    def build_prompt_budget(self, context: GameContext, player_mood: str = "neutral") -> dict[str, int]:
        budget = {
            "player_dialogues": 8,
            "special_events": 8,
            "action_results": 4,
            "decision_trace": 3,
            "emotion_learnings": 5,
        }
        if player_mood in ("unhappy", "sad"):
            budget["emotion_learnings"] = max(budget["emotion_learnings"], 8)
            budget["player_dialogues"] = max(budget["player_dialogues"], 10)
        elif player_mood in ("happy", "excited"):
            budget["emotion_learnings"] = max(budget["emotion_learnings"], 6)
        world = context.world or {}
        if bool(world.get("is_festival_day")):
            budget["special_events"] = max(budget["special_events"], 12)
            budget["player_dialogues"] = max(budget["player_dialogues"], 10)
        farm = context.farm_summary or {}
        animals = farm.get("animals") or {}
        machines = farm.get("machines") or {}
        if int(animals.get("unfed", 0) or 0) > 0 or int(animals.get("product_ready", 0) or 0) > 0:
            budget["special_events"] = max(budget["special_events"], 10)
        if int(machines.get("ready_to_collect", 0) or 0) > 0:
            budget["action_results"] = max(budget["action_results"], 6)
        if int(context.energy_max) > 0 and context.energy_current / max(context.energy_max, 1) < 0.35:
            budget["special_events"] = max(budget["special_events"], 10)
        if any(x in context.location_name for x in ("Shop", "Mart")):
            budget["player_dialogues"] = max(budget["player_dialogues"], 10)
        return budget

    def _read_card(self, path: Path, card_id: str) -> KnowledgeCard | None:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        return KnowledgeCard(card_id=card_id, content=text)
