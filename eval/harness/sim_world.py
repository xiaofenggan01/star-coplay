"""Deterministic Stardew Valley simulator used by the evaluation harness.

The simulator implements just enough game mechanics for the scenario corpus:
trees (health / felling / stumps / drops), loose items, a tile grid, player
energy and the in-game clock.  It is intentionally small but *physical*: an
action only succeeds when the game would allow it, so skills are evaluated
against something closer to the real mod than a canned response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Item metadata mirrors the fact payloads the mod emits.
ITEM_META: dict[str, dict[str, Any]] = {
    "Wood": {"item_id": "388", "internal_name": "Wood", "display_name": "Wood", "category": -16},
    "Sap": {"item_id": "92", "internal_name": "Sap", "display_name": "Sap", "category": -16},
    "Acorn": {"item_id": "309", "internal_name": "Acorn", "display_name": "Acorn", "category": -74},
    "Axe": {"item_id": "Axe", "internal_name": "Axe", "display_name": "Axe", "category": -99},
    "Watering Can": {"item_id": "WateringCan", "internal_name": "WateringCan", "display_name": "Watering Can", "category": -99},
}

# Strikes to fell a mature tree / clear a stump, by axe upgrade level.
TREE_STRIKES = {0: 10, 1: 8, 2: 6, 3: 4, 4: 2}
STUMP_STRIKES = {0: 5, 1: 4, 2: 3, 3: 2, 4: 1}

SIDE_OFFSETS = {
    "left": (-1, 0),
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
}


@dataclass
class Player:
    x: int = 10
    y: int = 10
    stamina: float = 270.0
    max_stamina: float = 270.0
    axe_level: int = 0
    tool: str | None = None
    inventory: list[dict[str, Any]] = field(default_factory=list)

    def add_item(self, name: str, qty: int) -> None:
        for item in self.inventory:
            if item.get("internal_name") == name:
                item["stack"] = int(item.get("stack") or 0) + qty
                return
        meta = ITEM_META.get(name, {"item_id": name, "internal_name": name, "display_name": name, "category": 0})
        slot = len(self.inventory) + 1
        self.inventory.append(
            {
                "slot": slot,
                "item_id": meta["item_id"],
                "qualified_item_id": f"(O){meta['item_id']}" if meta["item_id"].isdigit() else meta["item_id"],
                "internal_name": meta["internal_name"],
                "display_name": meta["display_name"],
                "stack": qty,
                "category": meta["category"],
            }
        )

    def count(self, name: str) -> int:
        return sum(int(i.get("stack") or 0) for i in self.inventory if i.get("internal_name") == name)

    def item_by_slot(self, slot: int) -> dict[str, Any] | None:
        for item in self.inventory:
            if item.get("slot") == slot:
                return item
        return None

    def has_axe(self) -> bool:
        return any(str(i.get("internal_name", "")).lower().endswith("axe") for i in self.inventory)


@dataclass
class Tree:
    x: int
    y: int
    growth_stage: int = 5
    health: float = 10.0
    stump: bool = False
    tree_type: str = "1"
    display_name: str = "Oak Tree"
    drops: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mature(self) -> bool:
        return self.growth_stage >= 5 and not self.stump


@dataclass
class LooseItem:
    x: int
    y: int
    name: str
    qty: int = 1


@dataclass
class Crop:
    x: int
    y: int
    seed: str = "Parsnip"
    watered: bool = False
    ready: bool = False
    dead: bool = False


class SimWorld:
    """A tiny tile world with deterministic mechanics."""

    def __init__(
        self,
        *,
        player: Player,
        trees: list[Tree],
        crops: list[Crop] | None = None,
        loose_items: list[LooseItem] | None = None,
        blocked: set[tuple[int, int]] | None = None,
        map_name: str = "Farm",
        weather: str = "sunny",
        day: int = 1,
        minutes_per_action: int = 10,
        stamina_per_strike: float = 3.0,
        start_minutes: int = 900,
    ):
        self.player = player
        self.trees = {(t.x, t.y): t for t in trees}
        self.crops = {(c.x, c.y): c for c in (crops or [])}
        self.loose_items = {(i.x, i.y): i for i in (loose_items or [])}
        self.blocked = set(blocked or set())
        self.map_name = map_name
        self.weather = weather
        self.day = day
        self.minutes = start_minutes
        self.minutes_per_action = minutes_per_action
        self.stamina_per_strike = stamina_per_strike

        # Ledger: what the world gave the player during the run.
        self.energy_spent = 0.0
        self.minutes_spent = 0
        self.trees_felled = 0
        self.stumps_cleared = 0
        self.items_collected: dict[str, int] = {}
        self.drops_spawned: dict[str, int] = {}

    # ── geometry ─────────────────────────────────────────────────────────

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < 80 and 0 <= y < 80

    def is_passable(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return False
        if (x, y) in self.blocked:
            return False
        if (x, y) in self.trees:
            return False
        return True

    def tree_at(self, x: int, y: int) -> Tree | None:
        return self.trees.get((x, y))

    # ── capability dispatch ──────────────────────────────────────────────

    def apply(self, capability: str, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        """Execute one capability. Returns (ok, protocol_data, error_code, observation)."""
        if capability == "move_to":
            return self._move_to(params)
        if capability == "equip":
            return self._equip(params)
        if capability == "use_tool":
            return self._use_tool(params)
        if capability == "interact":
            return self._interact(params)
        if capability == "water_crops":
            return self._water_crops(params)
        if capability == "go_to_bed":
            return self._go_to_bed(params)
        # Unknown action: accept as a no-op so scripts that use extra atoms are not
        # silently blocked; the command log still records it.
        return True, {"ok": True}, None, "ok"

    def _move_to(self, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        if params.get("mode") == "adjacent":
            side = str(params.get("adjacent_side") or "down")
            dx, dy = SIDE_OFFSETS.get(side, (0, 1))
            tx, ty = int(params["x"]) + dx, int(params["y"]) + dy
        else:
            tx, ty = int(params["x"]), int(params["y"])

        if not self.is_passable(tx, ty):
            return False, {}, "NO_PATH", f"tile ({tx},{ty}) is not passable"

        self.player.x, self.player.y = tx, ty
        self._tick()
        return True, {"player_tile": {"x": tx, "y": ty}}, None, "moved"

    def _equip(self, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        slot = params.get("slot")
        if slot is None:
            return False, {}, "INVALID_PARAMS", "equip requires slot"
        item = self.player.item_by_slot(int(slot))
        if item is None:
            return False, {}, "ITEM_NOT_FOUND", f"no item in slot {slot}"
        self.player.tool = str(item.get("internal_name"))
        return True, {"equipped": item.get("internal_name")}, None, "equipped"

    def _use_tool(self, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        x, y = int(params["x"]), int(params["y"])
        tree = self.tree_at(x, y)
        if tree is None:
            return False, {}, "NO_TARGET", f"no tree at ({x},{y})"
        if not str(self.player.tool or "").lower().endswith("axe"):
            return False, {}, "NO_TOOL", "no axe equipped"

        level = max(0, min(4, self.player.axe_level))
        damage = TREE_STRIKES[0] / TREE_STRIKES[level]
        self.player.stamina = max(0.0, self.player.stamina - self.stamina_per_strike)
        self.energy_spent += self.stamina_per_strike
        self._tick()

        if tree.stump:
            tree.health -= damage
            if tree.health <= 0:
                del self.trees[(x, y)]
                self.stumps_cleared += 1
                return True, {"effect": {"target_tile": {"x": x, "y": y}}}, None, "stump cleared"
            return True, {"effect": {"target_tile": {"x": x, "y": y}}}, None, "stump hit"

        tree.health -= damage
        if tree.health <= 0:
            tree.stump = True
            tree.health = float(STUMP_STRIKES[level])
            self.trees_felled += 1
            self._spawn_drops(tree)
            return True, {"effect": {"target_tile": {"x": x, "y": y}}}, None, "tree felled"
        return True, {"effect": {"target_tile": {"x": x, "y": y}}}, None, "tree hit"

    def _interact(self, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        x, y = int(params["x"]), int(params["y"])
        item = self.loose_items.pop((x, y), None)
        if item is None:
            return False, {}, "NOTHING_TO_INTERACT", f"no loose item at ({x},{y})"
        self.player.add_item(item.name, item.qty)
        self.items_collected[item.name] = self.items_collected.get(item.name, 0) + item.qty
        return True, {"picked": item.qty}, None, f"picked {item.qty} {item.name}"

    # ── helpers ──────────────────────────────────────────────────────────

    def _water_crops(self, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        limit = int(params.get("max_count") or 0)
        candidates = [
            crop
            for crop in self.crops.values()
            if not crop.dead and not crop.watered
        ]
        candidates.sort(key=lambda c: (abs(c.x - self.player.x) + abs(c.y - self.player.y), c.x, c.y))
        if limit > 0:
            candidates = candidates[:limit]

        watered = 0
        stopped = "all_done"
        for crop in candidates:
            if self.player.stamina < 2:
                stopped = "stamina_low"
                break
            self.player.stamina -= 2
            self.energy_spent += 2
            self._tick()
            crop.watered = True
            watered += 1

        remaining = len([c for c in self.crops.values() if not c.dead and not c.watered])
        if remaining and stopped == "all_done":
            stopped = "remaining_unwatered"
        return (
            True,
            {"watered": watered, "skipped": 0, "refilled": 0, "stopped_reason": stopped},
            None,
            f"watered {watered} crops",
        )

    def _go_to_bed(self, params: dict[str, Any]) -> tuple[bool, dict[str, Any], str | None, str]:
        self.day += 1
        self.minutes = 600
        self.player.stamina = self.player.max_stamina
        return (
            True,
            {
                "used_tp": False,
                "day_advanced": True,
                "sleep_confirmed": True,
                "sleep_prompt_seen": True,
                "decision_required": False,
                "phase": "done",
                "bed_position": {"x": self.player.x, "y": self.player.y},
            },
            None,
            "slept",
        )

    def _tick(self) -> None:
        self.minutes += self.minutes_per_action
        self.minutes_spent += self.minutes_per_action

    def _spawn_drops(self, tree: Tree) -> None:
        drops = tree.drops or [{"item": "Wood", "qty": 5}]
        # Real drops scatter 1-5 tiles away.  Prefer a ring at distance 2 so the
        # skill's "stand next to the drop" approach tile is never the stump tile
        # itself (which is not walkable).
        candidates = [
            (tree.x + 2, tree.y),
            (tree.x - 2, tree.y),
            (tree.x, tree.y + 2),
            (tree.x, tree.y - 2),
            (tree.x + 1, tree.y + 1),
            (tree.x - 1, tree.y - 1),
            (tree.x + 1, tree.y - 1),
            (tree.x - 1, tree.y + 1),
            (tree.x + 1, tree.y),
            (tree.x - 1, tree.y),
            (tree.x, tree.y + 1),
            (tree.x, tree.y - 1),
        ]
        for spec in drops:
            name = str(spec.get("item") or "Wood")
            qty = int(spec.get("qty") or 1)
            placed = False
            for px, py in candidates:
                if (px, py) == (self.player.x, self.player.y):
                    continue
                if (px, py) not in self.loose_items and self.is_passable(px, py):
                    self.loose_items[(px, py)] = LooseItem(px, py, name, qty)
                    placed = True
                    break
            if not placed:
                # Fall back to the tree tile itself (still collectable once the
                # skill moves adjacent).
                self.loose_items[(tree.x, tree.y)] = LooseItem(tree.x, tree.y, name, qty)
            self.drops_spawned[name] = self.drops_spawned.get(name, 0) + qty

    # ── fact queries ─────────────────────────────────────────────────────

    def runtime_fact(self) -> dict[str, Any]:
        return {
            "fact": {
                "player_location": {"map_token": self.map_name, "x": self.player.x, "y": self.player.y},
                "stamina": self.player.stamina,
                "max_stamina": self.player.max_stamina,
                "time_of_day": self.minutes,
                "day": self.day,
                "season": "spring",
                "day_of_week": "Mon",
                "weather": self.weather,
                "is_currently_raining": self.weather in ("rain", "rainy", "storm"),
            }
        }

    def inventory_fact(self) -> dict[str, Any]:
        return {
            "fact": {
                "container_kind": "player",
                "items": [dict(item) for item in self.player.inventory],
                "slots_used": len(self.player.inventory),
            }
        }

    def world_region_fact(self, params: dict[str, Any]) -> dict[str, Any]:
        center = params.get("center") or {"x": self.player.x, "y": self.player.y}
        cx, cy = int(center.get("x", self.player.x)), int(center.get("y", self.player.y))
        radius = int(params.get("radius") or 8)
        types = set(params.get("world_entity_types") or [])

        entities: list[dict[str, Any]] = []
        if not types or "tree" in types:
            for (x, y), tree in self.trees.items():
                if abs(x - cx) <= radius and abs(y - cy) <= radius:
                    entities.append(
                        {
                            "ref": {"value": f"world_entity:tree:{self.map_name.lower()}:{x}:{y}#tree"},
                            "type": "tree",
                            "fact_kind": "tree",
                            "position": {"map_id": self.map_name, "x": x, "y": y},
                            "summary": f"{tree.display_name} (stage {tree.growth_stage})",
                            "tree": {
                                "tree_type": tree.tree_type,
                                "growth_stage": tree.growth_stage,
                                "is_stump": tree.stump,
                                "health": tree.health,
                                "display_name": tree.display_name,
                            },
                        }
                    )
        if not types or "loose_item" in types:
            for (x, y), item in self.loose_items.items():
                if abs(x - cx) <= radius and abs(y - cy) <= radius:
                    entities.append(
                        {
                            "ref": {"value": f"world_entity:loose_item:{self.map_name.lower()}:{x}:{y}#loot"},
                            "type": "loose_item",
                            "fact_kind": "loose_item",
                            "position": {"map_id": self.map_name, "x": x, "y": y},
                            "loose_item": {"can_be_grabbed": True, "display_name": item.name, "stack": item.qty},
                        }
                    )
        if not types or "crop" in types:
            for (x, y), crop in self.crops.items():
                if abs(x - cx) <= radius and abs(y - cy) <= radius:
                    entities.append(
                        {
                            "ref": {"value": f"world_entity:crop:{self.map_name.lower()}:{x}:{y}#crop"},
                            "type": "crop",
                            "fact_kind": "crop",
                            "position": {"map_id": self.map_name, "x": x, "y": y},
                            "summary": f"{crop.seed} crop",
                            "crop": {
                                "seed": crop.seed,
                                "watered": crop.watered,
                                "ready": crop.ready,
                                "dead": crop.dead,
                            },
                        }
                    )

        tiles: list[dict[str, Any]] = []
        if params.get("include_tiles"):
            for x in range(cx - radius, cx + radius + 1):
                for y in range(cy - radius, cy + radius + 1):
                    if not self.in_bounds(x, y):
                        continue
                    tiles.append(
                        {
                            "position": {"map_id": self.map_name, "x": x, "y": y},
                            "passable": self.is_passable(x, y),
                            "occupied": (x, y) in self.trees,
                        }
                    )

        return {
            "fact": {
                "map_token": self.map_name,
                "map_id": self.map_name,
                "tiles": tiles,
                "entities": entities,
            }
        }

    # ── reporting helpers ────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "player": {"x": self.player.x, "y": self.player.y, "stamina": self.player.stamina},
            "trees_total": len([t for t in self.trees.values()]),
            "mature_trees_left": len([t for t in self.trees.values() if t.mature]),
            "stumps": len([t for t in self.trees.values() if t.stump]),
            "stumps_cleared": self.stumps_cleared,
            "trees_felled": self.trees_felled,
            "loose_items_left": len(self.loose_items),
            "inventory": {item["internal_name"]: item["stack"] for item in self.player.inventory},
            "energy_spent": self.energy_spent,
            "game_minutes": self.minutes_spent,
            "drops_spawned": dict(self.drops_spawned),
            "items_collected": dict(self.items_collected),
            "crops_total": len([c for c in self.crops.values() if not c.dead]),
            "watered_crops": len([c for c in self.crops.values() if c.watered and not c.dead]),
            "unwatered_crops": len([c for c in self.crops.values() if not c.watered and not c.dead]),
            "day": self.day,
            "weather": self.weather,
        }


def build_world(config: dict[str, Any]) -> SimWorld:
    player_cfg = config.get("player") or {}
    player = Player(
        x=int(player_cfg.get("x", 10)),
        y=int(player_cfg.get("y", 10)),
        stamina=float(player_cfg.get("stamina", 270)),
        max_stamina=float(player_cfg.get("max_stamina", 270)),
        axe_level=int(player_cfg.get("axe_level", 0)),
    )
    for item in config.get("inventory") or []:
        name = str(item.get("item") or item.get("name") or "")
        qty = int(item.get("qty", 1))
        slot = int(item.get("slot", len(player.inventory) + 1))
        meta = ITEM_META.get(name, {"item_id": name, "internal_name": name, "display_name": name, "category": 0})
        player.inventory.append(
            {
                "slot": slot,
                "item_id": meta["item_id"],
                "qualified_item_id": f"(O){meta['item_id']}" if meta["item_id"].isdigit() else meta["item_id"],
                "internal_name": meta["internal_name"],
                "display_name": meta["display_name"],
                "stack": qty,
                "category": meta["category"],
            }
        )

    trees = [
        Tree(
            x=int(t.get("x", 0)),
            y=int(t.get("y", 0)),
            growth_stage=int(t.get("growth_stage", 5)),
            health=float(t.get("health", TREE_STRIKES[0])),
            stump=bool(t.get("stump", False)),
            tree_type=str(t.get("tree_type", "1")),
            display_name=str(t.get("display_name", "Oak Tree")),
            drops=list(t.get("drops") or [{"item": "Wood", "qty": 5}]),
        )
        for t in config.get("trees") or []
    ]
    loose = [
        LooseItem(int(i.get("x", 0)), int(i.get("y", 0)), str(i.get("item", "Wood")), int(i.get("qty", 1)))
        for i in config.get("loose_items") or []
    ]
    crops = [
        Crop(
            x=int(c.get("x", 0)),
            y=int(c.get("y", 0)),
            seed=str(c.get("seed", "Parsnip")),
            watered=bool(c.get("watered", False)),
            ready=bool(c.get("ready", False)),
            dead=bool(c.get("dead", False)),
        )
        for c in config.get("crops") or []
    ]
    blocked = {(int(p[0]), int(p[1])) for p in config.get("blocked") or []}

    return SimWorld(
        player=player,
        trees=trees,
        crops=crops,
        loose_items=loose,
        blocked=blocked,
        map_name=str(config.get("map", "Farm")),
        weather=str(config.get("weather", "sunny")),
        day=int(config.get("day", 1)),
        minutes_per_action=int(config.get("minutes_per_action", 10)),
        stamina_per_strike=float(config.get("stamina_per_strike", 3)),
        start_minutes=int(config.get("start_minutes", 900)),
    )
