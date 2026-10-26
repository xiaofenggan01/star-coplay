"""memory._formatters — 共享的事件/对话格式化工具。"""

from __future__ import annotations


def format_events(events: list[dict]) -> str:
    """将事件列表格式化为文本（供 Prompt 使用）。"""
    if not events:
        return "（无事件）"
    lines = []
    for evt in events:
        ts_game = evt.get("ts_game", "")
        etype = evt.get("type", "event")
        content = evt.get("content", "")
        pinned = " [重要]" if evt.get("pinned") else ""
        if ts_game:
            lines.append(f"[{ts_game}] {etype}: {content}{pinned}")
        else:
            lines.append(f"{etype}: {content}{pinned}")
    return "\n".join(lines)


def format_conversation(conversation: list[dict]) -> str:
    """将对话列表格式化为文本（供 Prompt 使用）。"""
    if not conversation:
        return "（无对话）"
    lines = []
    for msg in conversation:
        role = "玩家" if msg.get("role") == "player" else "AI"
        content = msg.get("content", "")
        lines.append(f"{role}：{content}")
    return "\n".join(lines)
