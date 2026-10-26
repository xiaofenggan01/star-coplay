"""
game_monitor -- 游戏事件转发线程（替代 ReactiveLoop 的 BT tick 引擎）。

职责：
1. watchdog 监听 game_state.json 变化（通过 GameStateCache）
2. 轮询 events.jsonl -> 转发 PlayerInput 给 DecisionLoop
3. 不做决策、不做保护规则、不做 BT tick

FC 模式下，EventRelay 取代 ReactiveLoop 成为"快环"，
仅做事件采集与转发，所有决策由 DecisionLoop（慢环）通过 Function Calling 完成。
"""

import logging
import threading
import time
from typing import Any

from bt_engine.game_state_cache import GameStateCache
from bt_engine.message_bus import MessageBus
from messages.types import EpisodeBoundaryReport, PlayerInput, ReportEvent
from text_sanitize import sanitize_for_utf8_json

logger = logging.getLogger(__name__)


class EventRelay:
    """极简事件转发线程，替代 ReactiveLoop。

    监听游戏状态文件变化，轮询 events.jsonl 中的新事件，
    将聊天消息转发为 PlayerInput 发送给慢环（DecisionLoop）。

    Args:
        message_bus: 消息总线，用于向慢环发送消息。
        bridge: FileBridge 实例，用于读取事件和状态。
    """

    def __init__(
        self,
        message_bus: MessageBus,
        bridge: Any,
        target_player_id: Any | None = None,
    ):
        self._bus = message_bus
        self._bridge = bridge
        if target_player_id is None:
            target_player_id = getattr(bridge, "target_player_id", None)
        self._target_player_id = self._normalize_player_id(target_player_id)
        self._cache = GameStateCache(bridge.state_path)
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_event_seq = 0

    def start(self) -> None:
        """启动事件转发线程。"""
        if self._running:
            return

        # 跳过启动前的历史事件：扫描当前 events.jsonl 的最大 seq
        try:
            existing_events = self._bridge.read_new_events(after_seq=0)
            if existing_events:
                max_seq = max(evt.get("seq", 0) for evt in existing_events)
                self._last_event_seq = max_seq
                logger.info(
                    "EventRelay: 跳过 %d 条历史事件，从 seq=%d 开始监听",
                    len(existing_events), max_seq,
                )
        except Exception:
            logger.warning("EventRelay: 扫描历史事件失败，将从 seq=0 开始（可能重放历史）")

        self._running = True
        self._cache.register_change_callback(lambda: None)  # watchdog 触发
        self._cache.start()
        self._thread = threading.Thread(
            target=self._run_loop, name="EventRelay", daemon=True
        )
        self._thread.start()
        logger.info("EventRelay: 已启动")

    def stop(self) -> None:
        """停止事件转发线程。"""
        self._running = False
        self._cache.stop()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("EventRelay: 已停止")

    def _run_loop(self) -> None:
        """主循环：轮询游戏事件。"""
        while self._running:
            time.sleep(0.5)  # 轮询间隔
            try:
                self._poll_game_events()
            except Exception:
                logger.exception("EventRelay: 轮询异常")

    def _poll_game_events(self) -> None:
        """读取 events.jsonl 中的新事件，转发关键事件给慢环。"""
        if self._bridge is None:
            return
        events = self._bridge.read_new_events(after_seq=self._last_event_seq)
        for evt in events:
            seq = evt.get("seq", 0)
            if seq > self._last_event_seq:
                self._last_event_seq = seq

            evt_type = evt.get("type")
            data = evt.get("data", {})

            # 聊天消息 -> PlayerInput
            if evt_type == "chat_received":
                if self._is_target_player_chat(data):
                    logger.debug(
                        "EventRelay: skipped target player chat player_id=%s",
                        data.get("player_id"),
                    )
                    continue
                message = sanitize_for_utf8_json(str(data.get("message", "")))
                player_name = sanitize_for_utf8_json(str(data.get("player_name", "???")))
                if message:
                    self._bus.send_to_slow(
                        PlayerInput(text=f"{player_name}: {message}"),
                        priority=2,
                    )
                    logger.info(
                        "EventRelay: chat -> PlayerInput player=%s msg=%s",
                        player_name,
                        message[:50],
                    )

            # 新一天开始 -> ReportEvent（高重要性）
            elif evt_type == "day_started":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="day_started",
                        data=data,
                        significance=8,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.info(
                    "EventRelay: day_started -> ReportEvent day=%s season=%s",
                    data.get("day", "?"), data.get("season", "?"),
                )

            # 一天结束 -> EpisodeBoundaryReport（触发记忆归档）
            elif evt_type == "day_ending":
                day = data.get("day", "?")
                season = data.get("season", "?")
                game_context = {
                    "day": day,
                    "season": season,
                    "trigger_source": "mod_day_ending",
                }
                self._bus.send_to_slow(
                    EpisodeBoundaryReport(
                        trigger="day_end",
                        game_context=game_context,
                        summary_hint=f"{season}第{day}天结束",
                    ),
                    priority=4,
                )
                logger.info(
                    "EventRelay: day_ending -> EpisodeBoundaryReport day=%s season=%s",
                    day, season,
                )

            elif evt_type == "npc_nearby":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="npc_nearby",
                        data=data,
                        significance=6,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.debug("EventRelay: npc_nearby -> ReportEvent name=%s", data.get("name", "?"))

            elif evt_type == "player_warped":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="player_warped",
                        data=data,
                        significance=5,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.debug("EventRelay: player_warped -> ReportEvent to=%s", data.get("to_location", "?"))

            elif evt_type == "energy_low":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="energy_low",
                        data=data,
                        significance=7,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.info("EventRelay: energy_low -> ReportEvent percent=%s", data.get("percent"))

            elif evt_type == "inventory_changed":
                if data.get("high_value_item"):
                    self._bus.send_to_slow(
                        ReportEvent(
                            event_type="inventory_changed",
                            data=data,
                            significance=6,
                            source_node="EventRelay",
                        ),
                        priority=7,
                    )
                    logger.debug("EventRelay: inventory_changed (high_value) -> ReportEvent")

            elif evt_type == "dialogue_opened":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="dialogue_opened",
                        data=data,
                        significance=7,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.debug("EventRelay: dialogue_opened -> ReportEvent")

            elif evt_type == "season_changed":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="season_changed",
                        data=data,
                        significance=8,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.info("EventRelay: season_changed -> ReportEvent %s -> %s",
                            data.get("from"), data.get("to"))

            elif evt_type == "agent_action_cancelled":
                self._bus.send_to_slow(
                    ReportEvent(
                        event_type="agent_action_cancelled",
                        data=data,
                        significance=7,
                        source_node="EventRelay",
                    ),
                    priority=7,
                )
                logger.warning("EventRelay: agent_action_cancelled -> ReportEvent reason=%s",
                               data.get("reason"))

            elif evt_type in ("time_changed", "cmd_result", "dialogue_closed", "user_directive"):
                pass  # 噪音或由其他路径消费

            else:
                logger.debug("EventRelay: 忽略未知事件类型 type=%s", evt_type)

    @staticmethod
    def _normalize_player_id(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _is_target_player_chat(self, data: dict[str, Any]) -> bool:
        if self._target_player_id is None:
            return False
        player_id = self._normalize_player_id(data.get("player_id"))
        return player_id is not None and player_id == self._target_player_id
