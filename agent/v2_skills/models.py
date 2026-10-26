"""
v2_skills.models — L3 SkillRuntime 数据模型。

GameState 对应 Mod 侧 GameStateSnapshot 写出的 game_state.json 结构。
字段映射参考 v2/mod/StarCoPlay/Models/GameStateSnapshot.cs。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GameState:
    """从 game_state.json 解析的游戏状态快照。

    字段映射：
        player.energy.current -> stamina
        player.energy.max     -> max_stamina
        player.gold           -> gold
        player.location       -> location
        player.tile.x         -> x
        player.tile.y         -> y
        player.equipped_item.name -> current_tool
        world.time            -> time (600-2600)
        world.day             -> day
        world.season          -> season
        world.day_of_week     -> day_of_week
    """

    stamina: float
    max_stamina: float
    gold: int
    location: str
    x: float
    y: float
    current_tool: str
    time: int  # 游戏内时间 600-2600
    day: int
    season: str
    day_of_week: str

    @classmethod
    def from_dict(cls, raw: dict) -> GameState:
        """从 game_state.json 的完整 dict 解析 GameState。

        容错处理：缺失字段使用合理默认值，不抛出异常。

        Args:
            raw: game_state.json 解析后的 dict，顶层含 player/world 等键。

        Returns:
            GameState 实例。
        """
        player = raw.get("player") or {}
        world = raw.get("world") or {}

        # energy 是嵌套结构 {"current": int, "max": int}
        energy = player.get("energy") or {}
        if isinstance(energy, dict):
            stamina = float(energy.get("current", 0))
            max_stamina = float(energy.get("max", 0))
        else:
            # 兼容可能的扁平格式
            stamina = float(energy) if energy else 0.0
            max_stamina = float(player.get("max_energy", 0))

        # tile 是嵌套结构 {"x": int, "y": int}
        tile = player.get("tile") or {}
        if isinstance(tile, dict):
            x = float(tile.get("x", 0))
            y = float(tile.get("y", 0))
        else:
            x = 0.0
            y = 0.0

        # equipped_item 是嵌套结构 {"name": str, ...}
        equipped = player.get("equipped_item") or {}
        if isinstance(equipped, dict):
            current_tool = equipped.get("name", "")
        else:
            current_tool = str(equipped) if equipped else ""

        return cls(
            stamina=stamina,
            max_stamina=max_stamina,
            gold=int(player.get("gold", 0)),
            location=str(player.get("location", "")),
            x=x,
            y=y,
            current_tool=current_tool or "",
            time=int(world.get("time", 600)),
            day=int(world.get("day", 1)),
            season=str(world.get("season", "")),
            day_of_week=str(world.get("day_of_week", "")),
        )
