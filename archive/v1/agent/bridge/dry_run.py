"""
dry_run 模式：完全脱离 Mod，在终端模拟游戏推进。

功能：
- 时间自动递增（每 turn +10 分钟游戏时间）
- 模拟事件产生（day_started / inventory_changed / npc_nearby / energy_low）
- 命令输出打印到终端
- 状态输出打印到终端（替代 NoOpStatusWriter）
"""
from __future__ import annotations

import json
import random
import time
from typing import Any

from agent.bridge.state_watcher import StateSnapshot

# ── 初始游戏状态 ──────────────────────────────────────────────


def _minimal_game_state() -> dict[str, Any]:
    """模拟 Mod 端 game_state.json，完整对齐 bridge-protocol-spec.md v1.0。"""
    return {
        "status": "running",
        "game_day_started": True,
        "world": {
            "time": 600,
            "time_display": "6:00 AM",
            "day": 1,
            "season": "spring",
            "year": 1,
            "day_of_week": "Monday",
            "weather": {
                "current": "sunny",
                "is_raining": False,
                "is_snowing": False,
                "is_lightning": False,
                "tomorrow_forecast": "rainy",
            },
            "is_festival_day": False,
            "festival_name": None,
        },
        "player": {
            "name": "Player2",
            "location": "Farm",
            "tile": {"x": 64, "y": 15},
            "facing_direction": "down",
            "energy": {"current": 270, "max": 270},
            "health": {"current": 100, "max": 100},
            "gold": 500,
            "equipped_item": {
                "name": "Watering Can",
                "type": "tool",
                "item_id": "(T)WateringCan",
                "water_level": 40,
                "water_capacity": 40,
            },
            "current_tool_index": 0,
            "skills": {
                "farming": 2,
                "mining": 0,
                "foraging": 0,
                "fishing": 0,
                "combat": 0,
            },
            "status_effects": {
                "is_exhausted": False,
                "is_swimming": False,
                "is_riding_horse": False,
                "can_move": True,
            },
        },
        "inventory": {
            "slots_used": 3,
            "slots_total": 12,
            "items": [
                {"slot": 0, "name": "Watering Can", "item_id": "(T)WateringCan", "type": "tool", "stack": 1, "quality": 0},
                {"slot": 1, "name": "Parsnip Seeds", "item_id": "(O)472", "type": "seed", "stack": 15, "quality": 0},
                {"slot": 2, "name": "Pickaxe", "item_id": "(T)Pickaxe", "type": "tool", "stack": 1, "quality": 0},
            ],
        },
        "farm_summary": {
            "crops": {
                "total_planted": 15,
                "unwatered_count": 10,
                "harvestable_count": 3,
                "empty_tilled_tiles": 5,
            },
            "animals": {"total": 0, "unfed": 0, "product_ready": 0},
            "machines": {"ready_to_collect": 0, "processing": 0, "empty": 0},
        },
        "surroundings": {
            "radius": 8,
            "objects": [],
            "crops": [
                {"tile": {"x": 63, "y": 14}, "crop_name": "Parsnip", "watered": False, "harvestable": False, "days_until_harvest": 2},
                {"tile": {"x": 64, "y": 14}, "crop_name": "Parsnip", "watered": False, "harvestable": False, "days_until_harvest": 2},
                {"tile": {"x": 65, "y": 14}, "crop_name": "Cauliflower", "watered": True, "harvestable": True, "days_until_harvest": 0},
            ],
            "npcs": [],
            "terrain": [
                {"tile": {"x": 66, "y": 14}, "type": "hoe_dirt", "state": "dry", "has_crop": False},
            ],
        },
    }


# ── 预设事件场景 ──────────────────────────────────────────────

# 按 turn 序号触发（不再用游戏时间，消除空转）
# turn 0 = 初始状态，turn 1 = 第一次 wait_for_next 之后
_SCRIPTED_EVENTS: list[dict[str, Any]] = [
    # Turn 0: 新一天开始（立即触发）
    {
        "trigger_turn": 0,
        "event": {"seq": 1, "type": "day_started", "data": {"day": 1, "season": "spring", "year": 1, "weather": "sunny"}},
    },
    # Turn 2: 浇完水后 NPC 路过
    {
        "trigger_turn": 2,
        "event": {"seq": 2, "type": "npc_nearby", "data": {"name": "Abigail", "distance": 5, "hearts": 2, "talked_today": False, "gifted_today": False}},
    },
    # Turn 4: 挖到钻石！
    {
        "trigger_turn": 4,
        "event": {"seq": 3, "type": "inventory_changed", "data": {
            "source": "mining",
            "added": [{"name": "Diamond", "count": 1, "sell_price": 750, "item_id": "(O)72"}],
            "removed": [],
        }},
    },
    # Turn 6: 体力低
    {
        "trigger_turn": 6,
        "event": {"seq": 4, "type": "energy_low", "data": {"current": 30, "max": 270, "percentage": 11}},
    },
    # Turn 8: 玩家换了地图
    {
        "trigger_turn": 8,
        "event": {"seq": 5, "type": "player_warped", "data": {"from_location": "Farm", "to_location": "Town"}},
    },
    # Turn 10: 又遇到 NPC
    {
        "trigger_turn": 10,
        "event": {"seq": 6, "type": "npc_nearby", "data": {"name": "Sebastian", "distance": 3, "hearts": 0, "talked_today": False, "gifted_today": False}},
    },
]


# ── 终端打印命令发送器 ────────────────────────────────────────


class PrintCommandSender:
    """将 Agent 发出的命令打印到终端，并模拟命令对游戏状态的副作用。"""

    def __init__(self, state_ref: dict[str, Any] | None = None) -> None:
        self._state_ref = state_ref  # 指向 FakeStateWatcher 的 _state

    def send(self, commands: list[dict]) -> float:
        print(f"\033[36m[CMD →]\033[0m {json.dumps(commands, ensure_ascii=False)}")
        # 模拟副作用
        if self._state_ref is not None:
            for cmd in commands:
                self._apply_side_effect(cmd)
        return time.time()

    def _apply_side_effect(self, cmd: dict) -> None:
        action = cmd.get("action", "")
        params = cmd.get("params") or {}

        if action == "use_tool_on_tile":
            # 模拟浇水：标记 crop 为 watered
            tx, ty = params.get("x"), params.get("y")
            if tx is not None and ty is not None:
                for crop in self._state_ref.get("surroundings", {}).get("crops", []):
                    tile = crop.get("tile", {})
                    if tile.get("x") == tx and tile.get("y") == ty:
                        crop["watered"] = True
                # 也标记 terrain dry → watered
                for t in self._state_ref.get("surroundings", {}).get("terrain", []):
                    tile = t.get("tile", {})
                    if tile.get("x") == tx and tile.get("y") == ty and t.get("state") == "dry":
                        t["state"] = "watered"
                # 更新 farm_summary
                crops_summary = self._state_ref.get("farm_summary", {}).get("crops", {})
                uw = crops_summary.get("unwatered_count", 0)
                if uw > 0:
                    crops_summary["unwatered_count"] = uw - 1

        elif action == "interact_with_tile":
            # 模拟收割
            tx, ty = params.get("x"), params.get("y")
            if tx is not None and ty is not None:
                crops = self._state_ref.get("surroundings", {}).get("crops", [])
                self._state_ref["surroundings"]["crops"] = [
                    c for c in crops
                    if not (c.get("tile", {}).get("x") == tx and c.get("tile", {}).get("y") == ty and c.get("harvestable"))
                ]
                crops_summary = self._state_ref.get("farm_summary", {}).get("crops", {})
                hc = crops_summary.get("harvestable_count", 0)
                if hc > 0:
                    crops_summary["harvestable_count"] = hc - 1

        elif action == "equip_item":
            item_name = params.get("item_name", "")
            inv_items = self._state_ref.get("inventory", {}).get("items", [])
            found = any(i.get("name") == item_name for i in inv_items)
            if found:
                equipped = self._state_ref.get("player", {}).get("equipped_item", {})
                equipped["name"] = item_name
                equipped["type"] = "tool"
                if item_name == "Watering Can":
                    equipped["item_id"] = "(T)WateringCan"
                else:
                    equipped.pop("water_level", None)
                    equipped.pop("water_capacity", None)

        elif action == "refill_watering_can":
            equipped = self._state_ref.get("player", {}).get("equipped_item", {})
            cap = equipped.get("water_capacity", 40)
            equipped["water_level"] = cap

        elif action == "send_chat":
            msg = params.get("message", "")
            print(f"\033[34m[CHAT]\033[0m {msg}")

        elif action == "navigate_to":
            loc = params.get("location") or params.get("target_location")
            if loc:
                self._state_ref.get("player", {})["location"] = loc

        elif action == "go_to_bed":
            self._state_ref["world"]["time"] = 2600


# ── 终端打印状态写入器 ────────────────────────────────────────


class PrintStatusWriter:
    """将 Agent 状态变化打印到终端（替代 NoOpStatusWriter）。"""

    def update(self, status: str, **kwargs: Any) -> None:
        extras = {k: v for k, v in kwargs.items() if v is not None}
        extra_str = f" {extras}" if extras else ""
        print(f"\033[33m[STATUS]\033[0m {status}{extra_str}")

    def idle(self) -> None:
        self.update("idle")

    def thinking(self, about: str | None = None) -> None:
        self.update("thinking", about=about)

    def acting(self, task: str) -> None:
        self.update("acting", task=task)

    def chatting(self) -> None:
        self.update("chatting")

    def error(self, message: str) -> None:
        self.update("error", message=message)

    def shutdown(self) -> None:
        self.update("shutdown")


# ── 模拟事件读取器 ────────────────────────────────────────────


class FakeEventReader:
    """根据 turn 序号产生脚本事件，模拟 events.jsonl。"""

    def __init__(self) -> None:
        self._last_seq: int = 0
        self._current_turn: int = 0
        self._fired: set[int] = set()  # 已触发的事件 seq

    def set_turn(self, turn: int) -> None:
        """由 FakeStateWatcher 每 turn 调用。"""
        self._current_turn = turn

    def reset_cursor(self) -> None:
        self._last_seq = 0
        self._fired.clear()

    def read_new(self) -> list[dict[str, Any]]:
        """返回当前 turn 应触发但尚未返回的事件。"""
        events = []
        for scripted in _SCRIPTED_EVENTS:
            seq = scripted["event"]["seq"]
            if seq in self._fired:
                continue
            if scripted["trigger_turn"] <= self._current_turn:
                self._fired.add(seq)
                event = scripted["event"]
                events.append(event)
                print(f"\033[35m[EVT ←]\033[0m {json.dumps(event, ensure_ascii=False)}")
        if events:
            self._last_seq = max(e["seq"] for e in events)
        return events

    def wait_cmd_result(self, cmd_id: str, timeout: float, poll_interval: float = 0.3) -> dict[str, Any] | None:
        """立即返回模拟成功结果。"""
        return {
            "cmd_id": cmd_id,
            "success": True,
            "message": "dry_run_ok",
            "error_code": None,
            "result": {},
        }


# ── 模拟状态监视器 ────────────────────────────────────────────


_SEASONS = ["spring", "summer", "fall", "winter"]
_WEEK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEATHERS = ["sunny", "sunny", "sunny", "rainy", "windy"]  # 权重偏晴天


class FakeStateWatcher:
    """模拟游戏时间推进。

    dry_run 模式下零 sleep，事件按 turn 序号触发，极速跑完所有场景。
    每 turn 游戏时间 +50 分钟（可通过 time_step 调整）。
    到 2600（凌晨 2 点）自动切日。
    """

    def __init__(self, poll_interval: float, event_reader: FakeEventReader | None = None,
                 time_step: int = 50) -> None:
        self.poll_interval = poll_interval
        self._state = _minimal_game_state()
        self._turn = 0
        self._event_reader = event_reader
        self._time_step = time_step  # 每 turn 推进的游戏分钟数

    @property
    def state_ref(self) -> dict[str, Any]:
        """暴露状态引用给 PrintCommandSender 模拟副作用。"""
        return self._state

    def wait_until_ready(self) -> StateSnapshot:
        self._print_world_header()
        if self._event_reader:
            self._event_reader.set_turn(self._turn)
        return StateSnapshot(payload=self._state, mtime=time.time())

    def wait_for_next(self, last_mtime: float) -> StateSnapshot:
        # dry_run 不 sleep，极速推进
        self._turn += 1
        self._advance_time()
        self._simulate_energy_drain()
        self._maybe_add_npc()
        if self._event_reader:
            self._event_reader.set_turn(self._turn)
        m = time.time()
        while m <= last_mtime:
            m = time.time()
        return StateSnapshot(payload=self._state, mtime=m)

    def _advance_time(self) -> None:
        """每 turn 游戏时间 +time_step 分钟。"""
        world = self._state["world"]
        old_time = world["time"]
        new_time = old_time + self._time_step

        if new_time >= 2600:
            # 日切换
            self._next_day()
            return

        world["time"] = new_time
        hour = new_time // 100
        minute = new_time % 100
        ampm = "AM" if hour < 12 else "PM"
        display_hour = hour if hour <= 12 else hour - 12
        if display_hour == 0:
            display_hour = 12
        world["time_display"] = f"{display_hour}:{minute:02d} {ampm}"

    def _next_day(self) -> None:
        """切换到下一天。"""
        world = self._state["world"]
        old_day = world["day"]
        new_day = old_day + 1

        if new_day > 28:
            # 季节切换
            new_day = 1
            season_idx = _SEASONS.index(world["season"])
            if season_idx == 3:
                world["year"] = world.get("year", 1) + 1
                world["season"] = "spring"
            else:
                world["season"] = _SEASONS[season_idx + 1]

        world["day"] = new_day
        world["time"] = 600
        world["time_display"] = "6:00 AM"
        world["day_of_week"] = _WEEK_DAYS[(new_day - 1) % 7]
        world["weather"]["current"] = random.choice(_WEATHERS)
        world["weather"]["is_raining"] = world["weather"]["current"] == "rainy"
        world["weather"]["tomorrow_forecast"] = random.choice(_WEATHERS)
        self._state["game_day_started"] = True

        # 重置体力
        energy = self._state["player"]["energy"]
        energy["current"] = energy["max"]

        # 重置作物浇水状态
        for crop in self._state["surroundings"].get("crops", []):
            crop["watered"] = False
            if crop.get("days_until_harvest", 0) > 0:
                crop["days_until_harvest"] -= 1
            if crop.get("days_until_harvest", 1) == 0:
                crop["harvestable"] = True

        # 重置事件读取器
        if self._event_reader:
            self._event_reader._last_seq = 0
            self._event_reader._fired.clear()

        self._print_world_header()

    def _simulate_energy_drain(self) -> None:
        """模拟体力消耗。"""
        energy = self._state["player"]["energy"]
        # 每 turn 随机消耗 2-8 点
        drain = random.randint(2, 8)
        energy["current"] = max(0, energy["current"] - drain)

    def _maybe_add_npc(self) -> None:
        """随机在 surroundings 中添加/移除 NPC。"""
        npcs = self._state["surroundings"]["npcs"]
        game_time = self._state["world"]["time"]

        # 上午在农场不太会遇到 NPC；下午出门后概率高
        if game_time < 800:
            return

        if random.random() < 0.15 and len(npcs) < 2:
            npc_pool = ["Abigail", "Sebastian", "Sam", "Haley", "Emily", "Shane", "Leah", "Harvey"]
            existing = {n.get("name") for n in npcs}
            candidates = [n for n in npc_pool if n not in existing]
            if candidates:
                name = random.choice(candidates)
                npcs.append({
                    "name": name,
                    "distance": random.randint(2, 8),
                    "hearts": random.randint(0, 4),
                    "talked_today": False,
                    "gifted_today": False,
                })
        elif random.random() < 0.3 and npcs:
            npcs.pop(0)

    def _print_world_header(self) -> None:
        """打印当前游戏世界状态。"""
        w = self._state["world"]
        p = self._state["player"]
        e = p["energy"]
        loc = p["location"]
        weather = w["weather"]["current"]
        print(
            f"\n\033[32m{'='*60}\033[0m\n"
            f"\033[32m Year {w['year']} {w['season'].title()} Day {w['day']} ({w['day_of_week']}) "
            f"| {w['time_display']} | {weather} | {loc}\033[0m\n"
            f"\033[32m    Energy: {e['current']}/{e['max']} | Gold: {p['gold']}"
            f" | Turn #{self._turn}\033[0m\n"
            f"\033[32m{'='*60}\033[0m"
        )
