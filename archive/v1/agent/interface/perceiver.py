from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GameContext:
    """从 game_state.json 解析出的结构化游戏上下文。

    字段命名和结构与 bridge-protocol-spec.md v1.0 对齐。
    """
    raw_state: dict[str, Any]
    status: str
    game_day_started: bool
    world: dict[str, Any]
    player: dict[str, Any]
    inventory: dict[str, Any]
    farm_summary: dict[str, Any]
    surroundings: dict[str, Any]
    # 派生字段
    to_water: list[dict[str, int]]
    harvest_tiles: list[dict[str, int]]
    block_water: bool
    location_name: str
    time_of_day: int
    day_of_month: int = 0
    season: str = ""
    year: int = 1
    nearby_npcs: list[dict[str, Any]] = field(default_factory=list)
    energy_current: int = 0
    energy_max: int = 0
    equipped_item_name: str = ""
    water_level: int = -1
    water_capacity: int = -1
    is_indoor: bool = False
    tool_items: list[dict[str, Any]] = field(default_factory=list)

    def state_summary(self) -> str:
        parts = [
            f"location={self.location_name}",
            f"time={self.time_of_day}",
            f"water_targets={len(self.to_water)}",
            f"harvest_targets={len(self.harvest_tiles)}",
            f"block_water={self.block_water}",
            f"energy={self.energy_current}/{self.energy_max}",
            f"equipped={self.equipped_item_name or 'none'}",
        ]
        if self.water_level >= 0:
            parts.append(f"water={self.water_level}/{self.water_capacity}")
        if self.is_indoor:
            parts.append("indoor=True")
        return ",".join(parts)


class Perceiver:
    """从 Mod 端 game_state.json 解析游戏上下文。

    协议规格 (bridge-protocol-spec.md v1.0):
      - world.weather: {current, is_raining, is_snowing, is_lightning, tomorrow_forecast}
      - player.tile: {x, y}
      - player.energy: {current, max}
      - player.health: {current, max}
      - player.facing_direction: "up"/"down"/"left"/"right"
      - player.equipped_item: {name, type, item_id, water_level, water_capacity}
      - player.status_effects: {is_exhausted, is_swimming, is_riding_horse, can_move}
      - inventory.items[]: {slot, name, item_id, type, stack, quality, quality_name?}
      - farm_summary: {crops: {total_planted, unwatered_count, harvestable_count, empty_tilled_tiles},
                        animals: {total, unfed, product_ready}, machines: {ready_to_collect, processing, empty}}
      - surroundings: {radius, objects[], crops[], npcs[], terrain[]}
    """

    def perceive(self, state: dict[str, Any]) -> GameContext:
        status = str(state.get("status") or "running")
        game_day_started = bool(state.get("game_day_started", False))
        world = state.get("world") or {}
        player = state.get("player") or {}
        inventory = state.get("inventory") or {}
        farm_summary = state.get("farm_summary") or {}
        surroundings = state.get("surroundings") or {}

        location_name = str(player.get("location") or "")
        time_of_day = int(world.get("time") or 0)
        day_of_month = int(world.get("day") or 0)
        season = str(world.get("season") or "")
        year = int(world.get("year") or 1)

        # energy 嵌套: {current, max}
        energy_obj = player.get("energy") or {}
        if isinstance(energy_obj, dict):
            energy_current = int(energy_obj.get("current") or 0)
            energy_max = int(energy_obj.get("max") or 0)
        else:
            # 兼容旧格式（直接整数）
            energy_current = int(energy_obj or 0)
            energy_max = int(player.get("max_energy") or energy_current)

        # weather 嵌套: {current, is_raining, ...}
        weather = world.get("weather") or {}
        block_water = self._should_block_water(weather, location_name)

        # 从 surroundings 收集浇水/收割目标
        to_water = self._collect_water_targets(surroundings, farm_summary)
        harvest_tiles = self._collect_harvest_targets(surroundings)
        nearby_npcs = surroundings.get("npcs") or []

        equipped = player.get("equipped_item") or {}
        equipped_item_name = str(equipped.get("name") or "")
        water_level = int(equipped.get("water_level") or -1)
        water_capacity = int(equipped.get("water_capacity") or -1)

        _INDOOR = {"FarmHouse", "SeedShop", "Saloon", "Hospital", "JoshHouse",
                    "HaleyHouse", "SamHouse", "Blacksmith", "JojaMart", "AnimalShop",
                    "LeahHouse", "WizardHouse", "BathHouse_Entry", "Mine"}
        is_indoor = location_name in _INDOOR or location_name.endswith("House")

        inv_items = inventory.get("items") or []
        tool_items = [
            {"name": i.get("name"), "slot": i.get("slot")}
            for i in inv_items if i.get("type") == "tool" and i.get("name")
        ]

        return GameContext(
            raw_state=state,
            status=status,
            game_day_started=game_day_started,
            world=world,
            player=player,
            inventory=inventory,
            farm_summary=farm_summary,
            surroundings=surroundings,
            to_water=to_water,
            harvest_tiles=harvest_tiles,
            block_water=block_water,
            location_name=location_name,
            time_of_day=time_of_day,
            day_of_month=day_of_month,
            season=season,
            year=year,
            nearby_npcs=nearby_npcs if isinstance(nearby_npcs, list) else [],
            energy_current=energy_current,
            energy_max=energy_max,
            equipped_item_name=equipped_item_name,
            water_level=water_level,
            water_capacity=water_capacity,
            is_indoor=is_indoor,
            tool_items=tool_items,
        )

    def _should_block_water(self, weather: dict[str, Any], location_name: str) -> bool:
        """雨天农场不需要浇水（姜岛除外）。weather 是嵌套对象 {current, is_raining, ...}。"""
        if isinstance(weather, str):
            # 兼容旧格式
            is_raining = weather.lower() in {"rainy", "stormy", "rain", "storm"}
        else:
            is_raining = bool(weather.get("is_raining"))
            if not is_raining:
                current = str(weather.get("current") or "").lower()
                is_raining = current in {"rainy", "stormy"}
        if not is_raining:
            return False
        on_island = location_name.startswith("Island") or location_name in {"Caldera", "VolcanoDungeon"}
        return not on_island

    def _collect_water_targets(self, surroundings: dict[str, Any], farm_summary: dict[str, Any]) -> list[dict[str, int]]:
        """从 surroundings.crops 收集未浇水的 tile。"""
        crops = surroundings.get("crops") or []
        tiles: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        for crop in crops:
            if crop.get("watered") or crop.get("harvestable"):
                continue
            tile = crop.get("tile") or {}
            if "x" in tile and "y" in tile:
                key = (int(tile["x"]), int(tile["y"]))
                if key not in seen:
                    seen.add(key)
                    tiles.append({"x": key[0], "y": key[1]})
        # 也检查 terrain 中的 dry hoe_dirt
        for item in surroundings.get("terrain") or []:
            if item.get("type") != "hoe_dirt" or item.get("state") != "dry":
                continue
            if item.get("has_crop"):
                continue  # 有作物的在 crops 里已处理
            tile = item.get("tile") or {}
            if "x" in tile and "y" in tile:
                key = (int(tile["x"]), int(tile["y"]))
                if key not in seen:
                    seen.add(key)
                    tiles.append({"x": key[0], "y": key[1]})
        return tiles

    def _collect_harvest_targets(self, surroundings: dict[str, Any]) -> list[dict[str, int]]:
        """从 surroundings.crops 收集 harvestable 的 tile。"""
        crops = surroundings.get("crops") or []
        results: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        for crop in crops:
            if not crop.get("harvestable"):
                continue
            tile = crop.get("tile") or {}
            if "x" not in tile or "y" not in tile:
                continue
            key = (int(tile["x"]), int(tile["y"]))
            if key in seen:
                continue
            seen.add(key)
            results.append({"x": key[0], "y": key[1]})
        return results
