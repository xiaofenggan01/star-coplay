#!/usr/bin/env python3
"""
Bridge CLI — 文件桥人工测试工具
模拟 LLM Agent 通过 4 个桥文件与 StarCoPlay Mod 交互。

用法:
  python bridge_cli.py [bridge_dir]
  bridge_dir 默认为当前目录下的 bridge/ 或 %APPDATA%/StardewValley/Mods/StarCoPlay/bridge/
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# ─── 颜色 ───────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


# ─── 文件桥 I/O ─────────────────────────────────────────

def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    results = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        results.append(obj)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return results


def append_jsonl(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ─── 格式化输出 ─────────────────────────────────────────

def fmt_time(unix_ts: int | float) -> str:
    """Unix 时间戳 → 可读时间。"""
    try:
        return datetime.fromtimestamp(int(unix_ts)).strftime("%H:%M:%S")
    except (ValueError, OSError):
        return str(unix_ts)


def fmt_json(data: Any, indent: int = 2) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent)


def print_header(title: str) -> None:
    width = 60
    print(f"\n{c('━' * width, DIM)}")
    print(f"  {c(title, BOLD + CYAN)}")
    print(f"{c('━' * width, DIM)}")


def print_kv(key: str, value: Any, indent: int = 0) -> None:
    prefix = "  " * indent
    print(f"{prefix}{c(key, YELLOW)}: {value}")


# ─── 游戏状态展示 ────────────────────────────────────────

def show_game_state(state: dict) -> None:
    print_header("🎮 游戏状态 (game_state.json)")

    # 状态
    status = state.get("status", "?")
    status_color = GREEN if status == "running" else RED
    print_kv("状态", c(status, status_color))
    print_kv("游戏日已开始", state.get("game_day_started", "?"))

    # 世界
    world = state.get("world")
    if world:
        print(f"\n  {c('【世界】', BOLD)}")
        season_map = {"spring": "🌸春", "summer": "☀️夏", "fall": "🍂秋", "winter": "❄️冬"}
        season = world.get("season", "?")
        print_kv("时间", f"{world.get('time_display', '?')} ({world.get('time', '?')})", 1)
        print_kv("日期", f"{season_map.get(season, season)} {world.get('day', '?')}日 "
                 f"第{world.get('year', '?')}年 ({world.get('day_of_week', '?')})", 1)
        weather = world.get("weather", {})
        if isinstance(weather, dict):
            w_str = weather.get("current", "?")
            flags = []
            if weather.get("is_raining"):
                flags.append("🌧️雨")
            if weather.get("is_snowing"):
                flags.append("🌨️雪")
            if weather.get("is_lightning"):
                flags.append("⚡雷")
            if flags:
                w_str += " " + " ".join(flags)
            print_kv("天气", w_str, 1)
        if world.get("is_festival_day"):
            print_kv("节日", c(world.get("festival_name", "未知节日"), MAGENTA), 1)

    # 玩家
    player = state.get("player")
    if player:
        print(f"\n  {c('【玩家】', BOLD)}")
        tile = player.get("tile", {})
        print_kv("名称", player.get("name", "?"), 1)
        print_kv("位置", f"{player.get('location', '?')} ({tile.get('x', '?')},{tile.get('y', '?')})", 1)
        print_kv("朝向", player.get("facing_direction", "?"), 1)

        energy = player.get("energy", {})
        health = player.get("health", {})
        if isinstance(energy, dict):
            e_cur, e_max = energy.get("current", 0), energy.get("max", 1)
            e_pct = int(e_cur / e_max * 100) if e_max > 0 else 0
            e_bar = bar_graph(e_pct)
            print_kv("体力", f"{e_cur}/{e_max} {e_bar} {e_pct}%", 1)
        if isinstance(health, dict):
            h_cur, h_max = health.get("current", 0), health.get("max", 1)
            h_pct = int(h_cur / h_max * 100) if h_max > 0 else 0
            h_bar = bar_graph(h_pct, color=RED)
            print_kv("生命", f"{h_cur}/{h_max} {h_bar} {h_pct}%", 1)

        print_kv("金币", f"{player.get('gold', 0)}g", 1)

        equipped = player.get("equipped_item")
        if equipped:
            eq_str = f"{equipped.get('name', '?')} [{equipped.get('type', '?')}]"
            if equipped.get("water_level") is not None:
                eq_str += f" 💧{equipped['water_level']}/{equipped.get('water_capacity', '?')}"
            print_kv("装备", eq_str, 1)

        effects = player.get("status_effects", {})
        if isinstance(effects, dict):
            flags = []
            if effects.get("is_exhausted"):
                flags.append(c("疲劳", RED))
            if effects.get("is_swimming"):
                flags.append("游泳")
            if effects.get("is_riding_horse"):
                flags.append("骑马")
            if not effects.get("can_move"):
                flags.append(c("无法移动", RED))
            if flags:
                print_kv("状态", " | ".join(flags), 1)

        skills = player.get("skills", {})
        if isinstance(skills, dict) and any(skills.values()):
            s = skills
            print_kv("技能", f"农{s.get('farming',0)} 矿{s.get('mining',0)} "
                     f"采{s.get('foraging',0)} 钓{s.get('fishing',0)} 战{s.get('combat',0)}", 1)

    # 背包
    inv = state.get("inventory")
    if inv:
        print(f"\n  {c('【背包】', BOLD)} ({inv.get('slots_used', 0)}/{inv.get('slots_total', 0)})")
        items = inv.get("items", [])
        for item in items[:20]:  # 最多显示 20 个
            q_str = ""
            qn = item.get("quality_name")
            if qn:
                q_colors = {"Silver": WHITE, "Gold": YELLOW, "Iridium": MAGENTA}
                q_str = f" {c(qn, q_colors.get(qn, WHITE))}"
            print(f"    [{item.get('slot', '?'):2d}] {item.get('name', '?')} "
                  f"x{item.get('stack', 1)}{q_str} "
                  f"{c(item.get('type', ''), DIM)}")
        if len(items) > 20:
            print(f"    ... 还有 {len(items) - 20} 个物品")

    # 农场
    farm = state.get("farm_summary")
    if farm:
        crops = farm.get("crops", {})
        animals = farm.get("animals", {})
        machines = farm.get("machines", {})
        if any(v for v in [crops.get("total_planted"), animals.get("total"), machines.get("ready_to_collect")]):
            print(f"\n  {c('【农场】', BOLD)}")
            if crops.get("total_planted"):
                print_kv("作物", f"共{crops['total_planted']} "
                         f"未浇水:{crops.get('unwatered_count', 0)} "
                         f"可收获:{crops.get('harvestable_count', 0)}", 1)
            if animals.get("total"):
                print_kv("动物", f"共{animals['total']} 未抚摸:{animals.get('unpetted', 0)} "
                         f"产品就绪:{animals.get('product_ready', 0)}", 1)
            if machines.get("ready_to_collect") or machines.get("processing"):
                print_kv("机器", f"待收:{machines.get('ready_to_collect', 0)} "
                         f"加工中:{machines.get('processing', 0)} "
                         f"空闲:{machines.get('empty', 0)}", 1)

    # 周围环境
    surr = state.get("surroundings")
    if surr:
        npcs = surr.get("npcs", [])
        if npcs:
            print(f"\n  {c('【附近 NPC】', BOLD)} (半径 {surr.get('radius', 8)})")
            for npc in npcs:
                tile = npc.get("tile", {})
                print(f"    {c(npc.get('name', '?'), GREEN)} "
                      f"({tile.get('x','?')},{tile.get('y','?')}) "
                      f"♥{npc.get('hearts', 0)} "
                      f"距离:{npc.get('distance', '?'):.1f}")

        objects = surr.get("objects", [])
        if objects:
            print(f"\n  {c('【附近物品】', BOLD)} ({len(objects)})")
            for obj in objects[:10]:
                tile = obj.get("tile", {})
                print(f"    ({tile.get('x','?')},{tile.get('y','?')}) {obj.get('name', '?')} "
                      f"{c(obj.get('type', ''), DIM)}")
            if len(objects) > 10:
                print(f"    ... 还有 {len(objects) - 10} 个")

        crops = surr.get("crops", [])
        if crops:
            print(f"\n  {c('【附近作物】', BOLD)} ({len(crops)})")
            for crop in crops[:10]:
                tile = crop.get("tile", {})
                w = c("💧", BLUE) if crop.get("watered") else c("🏜", YELLOW)
                h = c("✓可收", GREEN) if crop.get("harvestable") else ""
                print(f"    ({tile.get('x','?')},{tile.get('y','?')}) {crop.get('crop_name', '?')} {w} {h}")

        terrain = surr.get("terrain", [])
        if terrain:
            print(f"\n  {c('【附近地形】', BOLD)} ({len(terrain)})")
            for t in terrain[:10]:
                tile = t.get("tile", {})
                print(f"    ({tile.get('x','?')},{tile.get('y','?')}) {t.get('type', '?')} {c(t.get('state', ''), DIM)}")


def bar_graph(pct: int, width: int = 15, color: str = GREEN) -> str:
    filled = int(pct / 100 * width)
    empty = width - filled
    return f"[{c('█' * filled, color)}{c('░' * empty, DIM)}]"


# ─── 事件展示 ────────────────────────────────────────────

EVENT_LABELS = {
    "day_started": ("🌅 新一天", GREEN),
    "day_ending": ("🌙 一天结束", YELLOW),
    "time_changed": ("⏰ 时间变化", DIM),
    "inventory_changed": ("🎒 背包变化", CYAN),
    "npc_nearby": ("👤 NPC 附近", GREEN),
    "player_warped": ("🚪 切换地图", BLUE),
    "cmd_result": ("📋 命令结果", MAGENTA),
    "energy_low": ("⚠️ 体力低", RED),
    "season_changed": ("🔄 季节变更", CYAN),
    "chat_received": ("💬 收到聊天", GREEN),
    "user_directive": ("🎯 用户指令", RED),
}


def show_event(evt: dict) -> None:
    etype = evt.get("type", "unknown")
    label, color = EVENT_LABELS.get(etype, (f"❓ {etype}", WHITE))
    seq = evt.get("seq", "?")
    ts = fmt_time(evt.get("ts", 0))
    data = evt.get("data", {})

    print(f"  {c(f'#{seq}', DIM)} {c(ts, DIM)} {c(label, color)}")

    # 按类型做简洁展示
    if etype == "day_started":
        print(f"       {data.get('season','?')} {data.get('day','?')}日 第{data.get('year','?')}年 "
              f"天气:{data.get('weather','?')} 未浇水:{data.get('unwatered_crops',0)} 可收获:{data.get('harvestable_crops',0)}")
    elif etype == "time_changed":
        print(f"       {data.get('time_display', data.get('time', '?'))}")
    elif etype == "inventory_changed":
        added = data.get("added", [])
        removed = data.get("removed", [])
        if added:
            items_str = ", ".join(f"{i.get('name','?')}x{i.get('stack',1)}" for i in added[:5])
            print(f"       {c('+', GREEN)} {items_str}")
        if removed:
            items_str = ", ".join(f"{i.get('name','?')}x{i.get('stack',1)}" for i in removed[:5])
            print(f"       {c('-', RED)} {items_str}")
        hv = data.get("high_value_item")
        if hv:
            print(f"       {c(f'💎 高价值: {hv}', YELLOW)}")
    elif etype == "npc_nearby":
        print(f"       {data.get('name','?')} @ {data.get('location','?')} "
              f"♥{data.get('hearts',0)} 距离:{data.get('distance','?')} "
              f"{'已对话' if data.get('talked_today') else '未对话'} "
              f"{'已送礼' if data.get('gifted_today') else '未送礼'}")
    elif etype == "player_warped":
        tile = data.get("tile", {})
        print(f"       {data.get('from_location','?')} → {data.get('to_location','?')} "
              f"({tile.get('x','?')},{tile.get('y','?')})")
    elif etype == "cmd_result":
        success = data.get("success", False)
        icon = c("✓", GREEN) if success else c("✗", RED)
        print(f"       {icon} [{data.get('cmd_id','?')}] {data.get('message','')}")
        if data.get("error_code"):
            print(f"       错误码: {c(data['error_code'], RED)}")
        # 显示查询返回的实际数据
        result = data.get("result")
        if result is not None:
            print(f"       {c('── 查询结果 ──', BOLD)}")
            if isinstance(result, dict):
                for k, v in result.items():
                    if isinstance(v, (dict, list)):
                        print(f"         {c(k, YELLOW)}: {fmt_json(v)}")
                    else:
                        print(f"         {c(k, YELLOW)}: {v}")
            else:
                print(f"         {fmt_json(result)}")
    elif etype == "energy_low":
        print(f"       {data.get('current',0)}/{data.get('max',0)} ({data.get('percent',0)}%)")
    elif etype == "season_changed":
        print(f"       {data.get('from','?')} → {data.get('to','?')} (第{data.get('year','?')}年)")
    elif etype == "chat_received":
        print(f"       {c(data.get('player_name','?'), GREEN)}: {data.get('message','')}")
    elif etype == "user_directive":
        print(f"       [{data.get('source','?')}] {c(data.get('directive',''), RED)}")
    else:
        # 未知事件类型，显示原始 data
        print(f"       {fmt_json(data)}")


# ─── 命令构建 ────────────────────────────────────────────

def gen_cmd_id() -> str:
    return f"cli-{uuid.uuid4().hex[:8]}"


COMMANDS = [
    # (编号, action, 描述, 参数提示列表)
    ("1", "navigate_to", "导航到坐标", [("x", "int"), ("y", "int")]),
    ("1w", "navigate_to", "传送到地图", [("location", "str"), ("x", "int", "64"), ("y", "int", "15")]),
    ("2", "use_tool_on_tile", "在坐标使用工具", [("x", "int"), ("y", "int")]),
    ("3", "equip_item", "装备物品", [("item_name", "str")]),
    ("4", "interact_with_tile", "与坐标交互(X键)", [("x", "int"), ("y", "int")]),
    ("5", "send_chat", "发送聊天(多人可见)", [("message", "str")]),
    ("6", "drop_item", "丢弃物品", [("item_name", "str"), ("count", "int", "-1")]),
    ("7", "ship_item", "出货物品", [("item_name", "str"), ("count", "int", "-1")]),
    ("8", "water_crops", "批量浇水", [("max_count", "int", "999")]),
    ("9", "harvest_crops", "批量收获", []),
    ("10", "go_to_bed", "回家睡觉", []),
    ("11", "talk_to_npc", "与 NPC 对话", [("npc_name", "str")]),
    ("12", "give_gift", "送礼给 NPC", [("npc_name", "str"), ("item_name", "str")]),
    ("13", "cancel", "取消当前动作", []),
    ("14", "query", "查询信息", [("tool", "str")]),
    ("15", "do_emote", "播放表情", [("emote", "str")]),
    ("16", "show_hud", "HUD 通知(本地)", [("message", "str"), ("type", "int", "0")]),
    ("17", "peek_chest", "查看箱子内容", [("x", "int"), ("y", "int")]),
    ("18", "store_in_chest", "存入箱子", [("x", "int"), ("y", "int"), ("item_name", "str"), ("count", "int", "-1")]),
    ("19", "take_from_chest", "从箱子取出", [("x", "int"), ("y", "int"), ("item_name", "str"), ("count", "int", "-1")]),
    ("20", "get_active_dialog", "读取当前对话框", []),
    ("21", "answer_dialog", "回答/关闭对话框", [("choice", "int")]),
]

QUERY_TOOLS = [
    "get_world_state",
    "get_player_state",
    "get_inventory",
    "get_farm_summary",
    "get_available_actions",
    "get_location_info",
    "get_map_objects",
    "get_walkable_area",
    "get_map_exits",
]


def show_command_menu() -> None:
    print_header("📋 命令列表")
    print(f"  {c('编号', BOLD)}   {c('命令', BOLD)}                 {c('说明', BOLD)}")
    print(f"  {c('─' * 50, DIM)}")
    for cmd in COMMANDS:
        num, action, desc, _ = cmd[0], cmd[1], cmd[2], cmd[3]
        params_hint = ""
        if len(cmd) > 3 and cmd[3]:
            params_hint = " " + c("(" + ", ".join(p[0] for p in cmd[3]) + ")", DIM)
        print(f"  {c(num, CYAN):>6s}  {action:<24s} {desc}{params_hint}")
    print(f"\n  {c('快捷键', BOLD)}")
    print(f"  {c('s', CYAN):>6s}  查看游戏状态 (game_state.json)")
    print(f"  {c('e', CYAN):>6s}  查看新事件 (events.jsonl)")
    print(f"  {c('ea', CYAN):>6s}  查看全部事件")
    print(f"  {c('a', CYAN):>6s}  设置 Agent 状态")
    print(f"  {c('w', CYAN):>6s}  监听模式 (实时显示状态+事件)")
    print(f"  {c('r', CYAN):>6s}  原始 JSON 查看")
    print(f"  {c('h', CYAN):>6s}  显示此菜单")
    print(f"  {c('q', CYAN):>6s}  退出")


def prompt_params(param_defs: list) -> dict:
    """根据参数定义交互式收集参数。"""
    params = {}
    for pdef in param_defs:
        name = pdef[0]
        ptype = pdef[1]
        default = pdef[2] if len(pdef) > 2 else None

        prompt_str = f"    {name}"
        if default is not None:
            prompt_str += f" [{default}]"
        prompt_str += ": "

        val = input(prompt_str).strip()
        if not val and default is not None:
            val = default

        if ptype == "int":
            try:
                params[name] = int(val)
            except ValueError:
                print(c(f"    ⚠ 无效整数，使用 0", YELLOW))
                params[name] = 0
        else:
            params[name] = val

    return params


# ─── 主循环 ──────────────────────────────────────────────

class BridgeCLI:
    def __init__(self, bridge_dir: Path):
        self.bridge_dir = bridge_dir
        self.state_path = bridge_dir / "game_state.json"
        self.events_path = bridge_dir / "events.jsonl"
        self.commands_path = bridge_dir / "commands.jsonl"
        self.agent_status_path = bridge_dir / "agent_status.json"
        self._last_seq = -1
        self._cmd_seq = 0

    def ensure_dir(self) -> None:
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def read_state(self) -> dict | None:
        return read_json(self.state_path)

    def read_all_events(self) -> list[dict]:
        return read_jsonl(self.events_path)

    def read_new_events(self) -> list[dict]:
        all_events = read_jsonl(self.events_path)

        # 检测 seq 重置（游戏重新加载存档时 Mod 会清空 events.jsonl 并重置 seq）
        if all_events:
            max_seq = max(int(e.get("seq", 0)) for e in all_events if e.get("seq") is not None)
            if max_seq < self._last_seq:
                print(c(f"  ⟳ 检测到事件序列重置 (文件 max_seq={max_seq}, 本地 last_seq={self._last_seq})，重新同步", YELLOW))
                self._last_seq = -1
        elif self._last_seq > 0:
            # 文件为空但 _last_seq > 0 → 文件被清空
            print(c(f"  ⟳ 事件文件已清空，重置序列游标", YELLOW))
            self._last_seq = -1

        new = []
        for evt in all_events:
            seq = evt.get("seq")
            if seq is not None and int(seq) > self._last_seq:
                new.append(evt)
        if new:
            new.sort(key=lambda e: int(e.get("seq", 0)))
            self._last_seq = int(new[-1]["seq"])
        return new

    def send_command(self, action: str, params: dict) -> str:
        cmd_id = gen_cmd_id()
        cmd = {
            "cmd_id": cmd_id,
            "action": action,
            "params": params,
        }
        append_jsonl(self.commands_path, cmd)
        return cmd_id

    def write_agent_status(self, status: str, current_goal: str = "") -> None:
        data = {
            "status": status,
            "current_goal": current_goal,
            "last_update": int(time.time()),
        }
        write_json_atomic(self.agent_status_path, data)

    def read_agent_status(self) -> dict | None:
        return read_json(self.agent_status_path)

    # ─── 交互命令 ────────────────────────────────────

    def cmd_show_state(self) -> None:
        state = self.read_state()
        if not state:
            print(c("  ⚠ game_state.json 不存在或为空", YELLOW))
            return
        show_game_state(state)

    def cmd_show_new_events(self) -> None:
        events = self.read_new_events()
        if not events:
            print(c("  (无新事件)", DIM))
            return
        print_header(f"📨 新事件 ({len(events)} 条)")
        for evt in events:
            show_event(evt)

    def cmd_show_all_events(self) -> None:
        all_events = self.read_all_events()
        if not all_events:
            print(c("  (无事件)", DIM))
            return

        # 检测 seq 重置
        if all_events:
            max_seq = max(int(e.get("seq", 0)) for e in all_events if e.get("seq") is not None)
            if max_seq < self._last_seq:
                print(c(f"  ⟳ 检测到事件序列重置，重新同步", YELLOW))
                self._last_seq = -1

        print_header(f"📨 全部事件 ({len(all_events)} 条)")
        display = all_events
        if len(all_events) > 30:
            print(c(f"  (仅显示最后 30 条，共 {len(all_events)} 条)", DIM))
            display = all_events[-30:]
        for evt in display:
            show_event(evt)
        # 更新 seq 游标（用全部事件，不是截断后的）
        for evt in all_events:
            seq = evt.get("seq")
            if seq is not None and int(seq) > self._last_seq:
                self._last_seq = int(seq)

    def cmd_agent_status(self) -> None:
        print(f"\n  当前 Agent 状态:")
        current = self.read_agent_status()
        if current:
            print(f"    {fmt_json(current)}")
        else:
            print(c("    (无 agent_status.json)", DIM))

        status = input(f"\n  新状态 [idle/thinking/acting/waiting]: ").strip()
        if not status:
            return
        goal = input(f"  当前目标 (可选): ").strip()
        self.write_agent_status(status, goal)
        print(c(f"  ✓ 已写入 agent_status.json", GREEN))

    def cmd_raw_view(self) -> None:
        print(f"\n  查看原始文件:")
        print(f"    {c('1', CYAN)} game_state.json")
        print(f"    {c('2', CYAN)} events.jsonl (最后 10 行)")
        print(f"    {c('3', CYAN)} commands.jsonl (最后 10 行)")
        print(f"    {c('4', CYAN)} agent_status.json")

        choice = input(f"  选择: ").strip()
        if choice == "1":
            data = self.read_state()
            if data:
                print(fmt_json(data))
            else:
                print(c("  (不存在)", DIM))
        elif choice == "2":
            events = self.read_all_events()
            for evt in events[-10:]:
                print(json.dumps(evt, ensure_ascii=False))
        elif choice == "3":
            cmds = read_jsonl(self.commands_path)
            for cmd in cmds[-10:]:
                print(json.dumps(cmd, ensure_ascii=False))
        elif choice == "4":
            data = self.read_agent_status()
            if data:
                print(fmt_json(data))
            else:
                print(c("  (不存在)", DIM))

    def cmd_watch(self) -> None:
        """监听模式：每秒刷新状态 + 显示新事件。Ctrl+C 退出。"""
        print(c("\n  进入监听模式 (Ctrl+C 退出)...\n", CYAN))
        try:
            while True:
                # 检查新事件
                events = self.read_new_events()
                if events:
                    for evt in events:
                        show_event(evt)

                # 简要状态行
                state = self.read_state()
                if state:
                    world = state.get("world", {})
                    player = state.get("player", {})
                    energy = player.get("energy", {})
                    tile = player.get("tile", {})
                    status_line = (
                        f"\r  {c('[监听]', CYAN)} "
                        f"{world.get('time_display', '?')} | "
                        f"{player.get('location', '?')} "
                        f"({tile.get('x','?')},{tile.get('y','?')}) | "
                        f"⚡{energy.get('current',0)}/{energy.get('max',0)} | "
                        f"💰{player.get('gold',0)}g"
                    )
                    sys.stdout.write(status_line + "   ")
                    sys.stdout.flush()

                time.sleep(1)
        except KeyboardInterrupt:
            print(c("\n  已退出监听模式", DIM))

    def cmd_send_action(self, cmd_def: tuple) -> None:
        num, action, desc, param_defs = cmd_def[0], cmd_def[1], cmd_def[2], cmd_def[3]

        print(f"\n  {c(desc, BOLD)} ({action})")

        # query 命令的特殊处理：显示可用 tool 列表
        if action == "query":
            print(f"  可用查询工具:")
            for i, tool in enumerate(QUERY_TOOLS):
                print(f"    {c(str(i + 1), CYAN)} {tool}")
            choice = input(f"  选择编号或输入名称: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(QUERY_TOOLS):
                    params = {"tool": QUERY_TOOLS[idx]}
                else:
                    params = {"tool": choice}
            except ValueError:
                params = {"tool": choice}
        elif action == "do_emote":
            emotes = ["happy", "sad", "heart", "exclamation", "angry", "question",
                      "sleep", "sick", "laugh", "note", "blush"]
            print(f"  可用表情: {', '.join(emotes)}")
            params = prompt_params(param_defs)
        else:
            params = prompt_params(param_defs)

        # 确认
        print(f"\n  即将发送: {c(action, CYAN)} {fmt_json(params)}")
        confirm = input(f"  确认? [Y/n]: ").strip().lower()
        if confirm == "n":
            print(c("  已取消", DIM))
            return

        cmd_id = self.send_command(action, params)
        print(c(f"  ✓ 已发送命令 [{cmd_id}]", GREEN))

        # 等待结果
        wait = input(f"  等待结果? [Y/n] (默认等待 5 秒): ").strip().lower()
        if wait != "n":
            print(c(f"  等待命令结果... (cmd_id={cmd_id}, last_seq={self._last_seq})", DIM))
            deadline = time.time() + 5
            found_any_result = False
            while time.time() < deadline:
                events = self.read_new_events()
                for evt in events:
                    if evt.get("type") == "cmd_result":
                        data = evt.get("data", {})
                        evt_cmd_id = data.get("cmd_id", "?")
                        if evt_cmd_id == cmd_id:
                            show_event(evt)
                            return
                        else:
                            # 有 cmd_result 但 cmd_id 不匹配
                            print(c(f"  ⚠ 收到其他命令的结果: {evt_cmd_id}", DIM))
                            found_any_result = True
                            show_event(evt)
                    else:
                        show_event(evt)
                time.sleep(0.3)
            # 超时诊断
            all_evts = self.read_all_events()
            result_evts = [e for e in all_evts if e.get("type") == "cmd_result"]
            print(c(f"  ⏰ 等待超时 (5秒)，命令可能仍在执行", YELLOW))
            print(c(f"  📊 诊断: events.jsonl 共 {len(all_evts)} 条事件, "
                     f"其中 cmd_result {len(result_evts)} 条, "
                     f"last_seq={self._last_seq}", DIM))

    def run(self) -> None:
        self.ensure_dir()

        print(f"\n{c('╔══════════════════════════════════════════════╗', CYAN)}")
        print(f"{c('║', CYAN)}  {c('Bridge CLI', BOLD)} — StarCoPlay 文件桥测试工具   {c('║', CYAN)}")
        print(f"{c('╚══════════════════════════════════════════════╝', CYAN)}")
        print(f"  桥目录: {c(str(self.bridge_dir), DIM)}")

        # 检查文件状态
        files = {
            "game_state.json": self.state_path.exists(),
            "events.jsonl": self.events_path.exists(),
            "commands.jsonl": self.commands_path.exists(),
            "agent_status.json": self.agent_status_path.exists(),
        }
        for name, exists in files.items():
            icon = c("✓", GREEN) if exists else c("·", DIM)
            print(f"    {icon} {name}")

        show_command_menu()

        # 命令索引
        cmd_map = {cmd[0]: cmd for cmd in COMMANDS}

        while True:
            try:
                raw = input(f"\n{c('>', CYAN)} ").strip()
                if not raw:
                    continue

                if raw.lower() == "q":
                    print(c("  再见！", DIM))
                    break
                elif raw.lower() == "h":
                    show_command_menu()
                elif raw.lower() == "s":
                    self.cmd_show_state()
                elif raw.lower() == "e":
                    self.cmd_show_new_events()
                elif raw.lower() == "ea":
                    self.cmd_show_all_events()
                elif raw.lower() == "a":
                    self.cmd_agent_status()
                elif raw.lower() == "r":
                    self.cmd_raw_view()
                elif raw.lower() == "w":
                    self.cmd_watch()
                elif raw in cmd_map:
                    self.cmd_send_action(cmd_map[raw])
                else:
                    # 尝试作为自定义 JSON 命令
                    print(c(f"  未知命令: {raw}，输入 h 查看帮助", YELLOW))

            except KeyboardInterrupt:
                print(c("\n  (Ctrl+C) 输入 q 退出", DIM))
            except EOFError:
                break


# ─── 入口 ────────────────────────────────────────────────

def find_bridge_dir(arg: str | None) -> Path:
    """确定桥目录位置。扫描各操作系统的常见游戏安装路径。"""
    if arg:
        return Path(arg)

    home = Path.home()
    username = home.name  # 当前用户名

    # Mod 在自身目录下创建 bridge/，路径为 <game>/Mods/StarCoPlay/bridge/
    mod_subpath = Path("Mods") / "StarCoPlay" / "bridge"

    candidates: list[Path] = [
        # 相对路径（从项目目录运行时）
        Path.cwd() / "bridge",
        Path.cwd() / "mod" / "bridge",
    ]

    # ── macOS ──
    # Steam 默认安装
    candidates.append(
        home / "Library" / "Application Support" / "Steam" / "steamapps" / "common"
        / "Stardew Valley" / "Contents" / "MacOS" / mod_subpath
    )
    # GOG / 直接安装
    candidates.append(
        Path("/Applications") / "Stardew Valley.app" / "Contents" / "MacOS" / mod_subpath
    )

    # ── Windows ──
    # Steam 常见路径
    for drive in ["C", "D", "E"]:
        candidates.append(
            Path(f"{drive}:/Program Files (x86)/Steam/steamapps/common/Stardew Valley") / mod_subpath
        )
        candidates.append(
            Path(f"{drive}:/Program Files/Steam/steamapps/common/Stardew Valley") / mod_subpath
        )
        candidates.append(
            Path(f"{drive}:/SteamLibrary/steamapps/common/Stardew Valley") / mod_subpath
        )
    # GOG 默认
    candidates.append(Path("C:/GOG Games/Stardew Valley") / mod_subpath)
    # %APPDATA% 备选
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "StardewValley" / mod_subpath)

    # ── Linux ──
    candidates.append(
        home / ".steam" / "steam" / "steamapps" / "common" / "Stardew Valley" / mod_subpath
    )
    candidates.append(
        home / ".local" / "share" / "Steam" / "steamapps" / "common" / "Stardew Valley" / mod_subpath
    )

    # 扫描找到的第一个存在的目录
    for p in candidates:
        if p.is_dir():
            return p

    # 未找到，列出已扫描路径供调试
    print(c("⚠ 未自动检测到 bridge 目录，已扫描以下路径：", YELLOW))
    for p in candidates[:8]:  # 只显示前几个
        print(f"  {c('·', DIM)} {p}")
    print(f"  ... 共扫描 {len(candidates)} 个路径")
    print(f"\n  请手动指定: python bridge_cli.py <bridge目录路径>")
    print(f"  例如: python bridge_cli.py ~/Library/Application\\ Support/Steam/.../Mods/StarCoPlay/bridge/\n")

    # 默认创建在当前目录
    return Path.cwd() / "bridge"


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    bridge_dir = find_bridge_dir(arg)

    cli = BridgeCLI(bridge_dir)
    cli.run()


if __name__ == "__main__":
    main()
