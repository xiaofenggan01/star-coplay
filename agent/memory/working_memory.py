"""
memory.working_memory — 慢环短期工作记忆。

保留最近 N 条游戏事件和最新游戏状态快照，
供 PromptAssembler 组装 LLM 上下文。

设计：
    - _events: 循环缓冲队列（deque with maxlen），自动淘汰最旧事件
    - _last_state: 最新游戏状态快照（来自 FileBridge.read_game_state()）
    - summarize(): 生成人类可读的简短摘要（用于 LLM Prompt）
    - events_since(ts): 返回指定时间戳之后的事件（用于 EpisodeBoundary 总结）
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

log = logging.getLogger("memory.working_memory")


class WorkingMemory:
    """慢环短期工作记忆。

    Args:
        max_events: 保留的最大事件数量（超出时 FIFO 淘汰）。
    """

    def __init__(self, max_events: int = 200) -> None:
        self.max_events = max_events
        self._events: deque[dict] = deque(maxlen=max_events)
        self._last_state: Optional[dict] = None

    # ── 写入 ───────────────────────────────────────────────────────────────

    def remember(self, state: dict, events: list[dict]) -> None:
        """更新游戏状态快照并追加新事件。

        Args:
            state:  来自 FileBridge.read_game_state() 的状态 dict。
            events: 来自 FileBridge.read_new_events() 的事件列表。
        """
        self._last_state = state
        for evt in events:
            self._events.append(evt)

    def clear(self) -> None:
        """清空所有事件（通常在 Episode 边界时调用）。"""
        self._events.clear()
        self._last_state = None

    # ── 读取 ───────────────────────────────────────────────────────────────

    @property
    def last_state(self) -> Optional[dict]:
        """最新游戏状态快照。"""
        return self._last_state

    @property
    def event_count(self) -> int:
        """当前缓冲中的事件数量。"""
        return len(self._events)

    def recent_events(self, n: int = 20) -> list[dict]:
        """Return the most recent n buffered events."""
        events = list(self._events)
        return events[-n:] if len(events) > n else events

    def events_since(self, timestamp: float) -> list[dict]:
        """返回指定时间戳之后的事件列表。

        Args:
            timestamp: Unix epoch 秒，仅返回 evt.get('ts', 0) > timestamp 的事件。

        Returns:
            按时间顺序排列的事件列表。
        """
        return [e for e in self._events if e.get("ts", 0) > timestamp]

    # [DEPRECATED] agent-loop-mode 迭代后应当彻底删除（Agent Loop 模式下记忆事件通过 session messages 累积，不再单独提取）
    # def recent_events(self, n: int = 20) -> list[dict]:
    #     """返回最近 n 条事件。
    #
    #     Args:
    #         n: 返回的事件数量上限。
    #
    #     Returns:
    #         最近 n 条事件列表（最新的在最后）。
    #     """
    #     events = list(self._events)
    #     return events[-n:] if len(events) > n else events

    # ── 摘要 ───────────────────────────────────────────────────────────────

    def summarize(self) -> str:
        """生成人类可读的状态摘要，用于 LLM Prompt 组装。

        Returns:
            多行字符串，包含玩家位置、能量、金钱及近期事件统计。
        """
        parts: list[str] = []

        if self._last_state:
            player = self._last_state.get("player", {})
            location = player.get("location", "?")
            energy = player.get("energy", "?")
            max_energy = player.get("max_energy", "?")
            gold = player.get("gold", "?")
            game_time = self._last_state.get("time", "?")
            parts.append(
                f"Location: {location} | "
                f"Energy: {energy}/{max_energy} | "
                f"Gold: {gold}g | "
                f"Time: {game_time}"
            )
        else:
            parts.append("Game state: not yet loaded")

        event_count = len(self._events)
        parts.append(f"Recent events buffered: {event_count}/{self.max_events}")

        if event_count > 0:
            recent = list(self._events)[-5:]
            event_types = [e.get("type", "?") for e in recent]
            parts.append(f"Last events: {', '.join(event_types)}")

        return "\n".join(parts)
