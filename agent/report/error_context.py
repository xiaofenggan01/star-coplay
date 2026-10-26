"""为错误事件提供游戏状态快照上下文。

从 GameStateCache 提取当前状态摘要，用于附加到所有 error.* 事件，
帮助内测期间快速定位错误发生时的游戏场景。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bt_engine.game_state_cache import GameStateCache


def capture_error_context(game_state_cache: GameStateCache | None) -> dict[str, Any]:
    """从 GameStateCache 提取当前状态摘要，用于附加到 error.* 事件。

    GameStateCache.get_latest() 返回 dict | None，所有字段用 dict.get() 安全访问。
    任何异常均被捕获，绝不影响主流程。

    Args:
        game_state_cache: GameStateCache 实例，可为 None。

    Returns:
        游戏状态摘要 dict，至少包含 snapshot 字段标明状态。
    """
    if game_state_cache is None:
        return {"snapshot": "no_cache"}
    try:
        gs = game_state_cache.get_latest()
        if gs is None:
            return {"snapshot": "unavailable"}

        ctx: dict[str, Any] = {}

        # player 信息
        player = gs.get("player")
        if isinstance(player, dict):
            pos = player.get("position") or {}
            if isinstance(pos, dict):
                ctx["player_pos"] = {"x": pos.get("x"), "y": pos.get("y")}
            else:
                ctx["player_pos"] = {"x": player.get("x"), "y": player.get("y")}
            ctx["energy"] = player.get("energy")

        # 当前位置
        location = gs.get("location")
        if isinstance(location, dict):
            ctx["location"] = location.get("name")
        elif isinstance(location, str):
            ctx["location"] = location

        # 游戏时间
        world = gs.get("world")
        if isinstance(world, dict):
            season = world.get("season", "?")
            day = world.get("day", "?")
            ctx["game_time"] = f"{season} {day}"

        return ctx
    except Exception:
        return {"snapshot": "error_during_capture"}
