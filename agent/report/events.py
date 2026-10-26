"""所有上报事件的类型化构造函数。

调用方只需 `from report import events; events.player_message(...)`,
不需要知道事件 schema 细节。所有事件 type 字符串和字段定义集中在此文件。
"""
from __future__ import annotations

import traceback
from typing import Any

from .telemetry import track, track_onboard, get_include_chat, llm_call_data


# ── Session 事件 ──────────────────────────────────────────────────────────


def session_start(*, is_first_session: bool, **extra: Any) -> None:
    """Agent 启动时调用。extra 可包含 os, game_version, mod_version 等。"""
    track("session.start", {"report_kind": "regular", "is_first_session": is_first_session, **extra})


# ── 玩家交互事件 ──────────────────────────────────────────────────────────


def player_message(*, text: str, source: str = "unknown") -> None:
    """玩家发送消息时调用。"""
    data: dict[str, Any] = {
        "length": len(text),
        "is_command": text.startswith("/"),
        "trigger_source": source,
    }
    if get_include_chat():
        data["text"] = text[:500]
    track("player.message", data)


def agent_say(*, text: str) -> None:
    """AI 说话时调用（来自 ActionLeaf say 动作）。"""
    data: dict[str, Any] = {"length": len(text)}
    if get_include_chat():
        data["text"] = text[:500]
    track("agent.say", data)


def external_text_input_merged(
    *,
    source: str,
    channel_key: str,
    text: str,
    input_ids: list[str] | None = None,
    input_count: int = 1,
) -> None:
    """外部文本通道可展示的合并后用户输入。"""
    data: dict[str, Any] = {
        "source": source,
        "channel_key": channel_key,
        "route_key": channel_key,
        "input_ids": input_ids or [],
        "input_count": input_count,
        "length": len(text),
        "text": text[:1000],
    }
    track("external_text.input_merged", data)


def roadshow_input_merged(
    *,
    route_key: str,
    text: str,
    input_ids: list[str] | None = None,
    input_count: int = 1,
) -> None:
    """兼容旧调用：路演是 external_text 的一个 source。"""
    external_text_input_merged(
        source="roadshow",
        channel_key=route_key,
        text=text,
        input_ids=input_ids,
        input_count=input_count,
    )


def agent_dialogue(*, is_player_initiated: bool = True, response_latency_ms: float = 0) -> None:
    """AI 对话回复时调用（来自慢环 say 处理）。"""
    track(
        "ai.dialogue",
        {
            "report_kind": "regular",
            "is_player_initiated": is_player_initiated,
            "response_latency_ms": response_latency_ms,
        },
    )


def user_report(*, body: str, game_context: dict[str, Any] | None = None) -> None:
    """玩家发送 /report 命令时调用。

    Args:
        body: 用户反馈文本。
        game_context: 可选的游戏状态快照（位置/体力/时间等），自动附加到上报数据。
    """
    data: dict[str, Any] = {
        "report_kind": "user_report",
        "schema_version": 2,
        "message": {
            "text": body[:8000],
            "source": "agent_chat_command",
        },
    }
    if game_context:
        data["game_context"] = game_context
    track("user_report", data)


# ── AI/LLM 事件 ──────────────────────────────────────────────────────────


def llm_call(*, model: str, response: Any, latency_ms: float, trigger: str) -> None:
    """成功的 LLM 调用。"""
    meta = llm_call_data(model, response, latency_ms, trigger=trigger)
    if meta:
        track("ai.llm_call", meta)


def llm_call_with_trigger_label(*, model: str, response: Any, latency_ms: float,
                                 trigger: str, trigger_label: str) -> None:
    """成功的 LLM 调用（附加 trigger_label）。"""
    meta = llm_call_data(model, response, latency_ms, trigger=trigger)
    if meta:
        track("ai.llm_call", {**meta, "trigger": trigger_label})


def fc_decision_summary(
    *,
    trigger: str,
    fc_rounds: int,
    executed_actions: list[str],
    had_error: bool,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "report_kind": "regular",
        "trigger": trigger,
        "fc_rounds": fc_rounds,
        "executed_actions": executed_actions[:80],
        "executed_action_count": len(executed_actions),
        "had_error": had_error,
    }
    if tool_calls:
        payload["tool_calls"] = tool_calls[:80]
    track("ai.decision", payload)


def llm_timeout(*, model: str, timeout_s: float, component: str = "",
                trigger: str = "", error_ctx: dict | None = None) -> None:
    """LLM 调用超时。"""
    data: dict[str, Any] = {
        "report_kind": "error",
        "model": model,
        "timeout_s": timeout_s,
        "component": component,
        "trigger": trigger,
    }
    if error_ctx:
        data.update(error_ctx)
    track("error.llm_timeout", data)


def llm_api_error(*, model: str, error_type: str, error_msg: str,
                  component: str = "", trigger: str = "",
                  error_ctx: dict | None = None) -> None:
    """LLM API 错误。"""
    data: dict[str, Any] = {
        "report_kind": "error",
        "model": model,
        "error_type": error_type,
        "error_msg": error_msg[:200],
        "component": component,
        "trigger": trigger,
    }
    if error_ctx:
        data.update(error_ctx)
    track("error.llm_api", data)


def ai_error(*, error_type: str, message: str, component: str,
             trigger: str = "", stack_trace: str = "") -> None:
    """通用 AI 错误上报。"""
    data: dict[str, Any] = {
        "report_kind": "error",
        "error_type": error_type,
        "message": message[:500],
        "component": component,
    }
    if trigger:
        data["trigger"] = trigger
    if stack_trace:
        data["stack_trace"] = stack_trace[:8000]
    track("ai.error", data)


def ai_error_from_exception(*, exc: Exception, component: str, trigger: str = "") -> None:
    """从异常对象构造 ai.error 事件。"""
    ai_error(
        error_type=type(exc).__name__,
        message=str(exc),
        component=component,
        trigger=trigger,
        stack_trace=traceback.format_exc(),
    )


# ── 动作事件 ──────────────────────────────────────────────────────────────


def action_outcome(*, action_type: str, success: bool, duration_ms: float) -> None:
    """动作执行结果（成功或失败）。"""
    track(
        "ai.action",
        {
            "report_kind": "regular",
            "action_type": action_type,
            "success": success,
            "duration_ms": int(duration_ms),
        },
    )


def feature_use(*, feature: str, frequency: int = 1) -> None:
    """功能使用次数。"""
    track(
        "game.feature_use",
        {
            "report_kind": "regular",
            "feature": feature,
            "frequency": frequency,
        },
    )


def action_failed(*, capability: str, error_code: str = "unknown",
                  error_msg: str = "", **extra: Any) -> None:
    """动作执行失败。"""
    data: dict[str, Any] = {"capability": capability, "error_code": error_code}
    if error_msg:
        data["error_msg"] = error_msg[:200]
    data.update(extra)
    track("error.action_failed", data)


# ── 游戏事件 ──────────────────────────────────────────────────────────────


def day_summary(*, game_day: Any = None, season: Any = None,
                tasks_done: int = 0, income: Any = None,
                boundary_trigger: str = "") -> None:
    """日终总结事件。"""
    track(
        "game.day_summary",
        {
            "report_kind": "regular",
            "game_day": game_day,
            "season": season,
            "tasks_done": tasks_done,
            "income": income,
            "boundary_trigger": boundary_trigger,
        },
    )


# ── Onboard 漏斗事件 ─────────────────────────────────────────────────────


def onboard_agent_connected() -> None:
    """首次连接漏斗步骤。"""
    track_onboard("agent_connected")


def onboard_first_action(*, capability: str) -> None:
    """首次执行动作。"""
    track_onboard("first_action", {"capability": capability})


def onboard_first_conversation(*, source: str = "player") -> None:
    """首次对话（玩家侧）。"""
    track_onboard("first_conversation", {"source": source})


def onboard_first_conversation_ai() -> None:
    """首次对话（AI 侧）。"""
    track_onboard("first_conversation_ai")


def onboard_first_skill_complete(*, skill_name: str) -> None:
    """首次技能执行完成。"""
    track_onboard("first_skill_complete", {"skill": skill_name})


# ── Bridge 连接状态事件 ────────────────────────────────────────────────────


def bridge_connected() -> None:
    """Bridge 文件桥首次检测到就绪时触发（session 内只触发一次）。"""
    track_onboard("bridge_connected")


def bridge_disconnected(*, reason: str) -> None:
    """Bridge 文件桥断连时触发（每次断连都上报）。"""
    track("bridge.disconnected", {"reason": reason})
