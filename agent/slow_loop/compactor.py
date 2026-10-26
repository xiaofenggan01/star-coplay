"""
slow_loop.compactor -- Session 消息压缩（Autocompact）。

职责：
    - 监控 session_messages 的 token 用量
    - 超阈值时调用 LLM 生成摘要，插入 compact_boundary
    - boundary 之前的消息保留但不发给 API（由 get_messages_for_api 切片）

设计参考：Claude Code autocompact（单层 autocompact + LLM 摘要 + boundary 切片）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

COMPACT_SYSTEM_PROMPT = """\
你是一个对话摘要助手。请将以下 Agent 对话历史压缩为简洁的摘要。

要求：
- 保留关键信息：玩家的意图和请求、Agent 已执行的动作及结果、当前游戏状态要点
- 丢弃冗余细节：重复的状态描述、中间推理过程、已完成且不影响后续的动作细节
- 使用中文
- 控制在 300 字以内
- 直接输出摘要内容，不要加前缀或解释"""


class SessionCompactor:
    """Session 消息压缩器。"""

    def __init__(
        self,
        config: dict,
        llm_fn: Callable[..., Any],
        system_message_getter: Callable[[], str],
    ):
        sl_cfg = config.get("slow_loop", {})
        self._context_window: int = sl_cfg.get("context_window", 128000)
        self._reserved_output: int = sl_cfg.get("reserved_output", 4096)
        self._compact_ratio: float = sl_cfg.get("compact_ratio", 0.7)
        self._summary_model: str = sl_cfg.get("compact_summary_model", "")
        self._llm_fn = llm_fn
        self._get_system_message = system_message_getter

    def maybe_compact(self, session_messages: list[dict[str, Any]]) -> None:
        """检查是否需要 compact，超阈值且安全时执行（原地修改 session_messages）。"""
        if not session_messages:
            return
        threshold = self.compute_threshold()
        active_messages = self.get_active_messages(session_messages)
        current_tokens = self.estimate_tokens(active_messages)
        if current_tokens >= threshold:
            if self.is_safe_to_compact(session_messages):
                self._do_compact(session_messages)

    def is_safe_to_compact(self, session_messages: list[dict[str, Any]]) -> bool:
        """最后一条不是未闭合 tool_calls 时才安全。"""
        if not session_messages:
            return True
        last = session_messages[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            return False
        return True

    def get_active_messages(self, session_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """返回最后一个 boundary 之后的消息（用于 token 估算）。"""
        boundary_idx = -1
        for i, msg in enumerate(session_messages):
            if msg.get("metadata", {}).get("type") == "compact_boundary":
                boundary_idx = i
        if boundary_idx >= 0:
            return session_messages[boundary_idx:]
        return session_messages

    def compute_threshold(self) -> int:
        """动态计算 compact 阈值。"""
        system_message = self._get_system_message()
        system_tokens = self.estimate_tokens(
            [{"role": "system", "content": system_message}]
        ) if system_message else 0
        tools_tokens = 1500

        available = self._context_window - self._reserved_output - system_tokens - tools_tokens
        return int(available * self._compact_ratio)

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """粗略估算 messages 的 token 数（每 4 字符约 1 token）。"""
        total_chars = 0
        for msg in messages:
            content = msg.get("content") or ""
            total_chars += len(content)
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        total_chars += len(fn.get("name", "")) + len(fn.get("arguments", ""))
        return total_chars // 4

    def _do_compact(self, session_messages: list[dict[str, Any]]) -> None:
        """生成 LLM 摘要 + 插入 boundary。"""
        active = self.get_active_messages(session_messages)
        summary = self._generate_summary(active)

        boundary_msg: dict[str, Any] = {
            "role": "user",
            "content": f"[对话摘要] {summary}",
            "metadata": {"type": "compact_boundary"},
        }
        ack_msg: dict[str, Any] = {
            "role": "assistant",
            "content": "已了解之前的上下文，继续。",
        }
        session_messages.append(boundary_msg)
        session_messages.append(ack_msg)

    def _generate_summary(self, messages_to_summarize: list[dict[str, Any]]) -> str:
        """调用 LLM 生成对话摘要（fallback: 简单截取）。"""
        formatted = self._format_messages_for_summary(messages_to_summarize)
        summary_messages = [
            {"role": "system", "content": COMPACT_SYSTEM_PROMPT},
            {"role": "user", "content": formatted},
        ]

        try:
            model = self._summary_model or self._resolve_default_model()
            response = self._llm_fn(
                model=model,
                messages=summary_messages,
                max_tokens=800,
                temperature=0.3,
            )
            content = response.choices[0].message.content
            if content and content.strip():
                log.info("compact 摘要生成成功 (%d chars)", len(content.strip()))
                return content.strip()
        except Exception:
            log.warning("compact 摘要 LLM 调用失败，使用 fallback", exc_info=True)

        return self._fallback_summary(messages_to_summarize)

    def _format_messages_for_summary(self, messages: list[dict[str, Any]]) -> str:
        """将 messages 格式化为 LLM 可读的文本。"""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")

            if role == "user":
                parts.append(f"[玩家/系统] {content[:500]}")
            elif role == "assistant":
                if tool_calls:
                    actions = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            actions.append(f"{fn.get('name', '?')}({fn.get('arguments', '')[:100]})")
                    parts.append(f"[Agent 动作] {', '.join(actions)}")
                if content:
                    parts.append(f"[Agent 思考] {content[:300]}")
            elif role == "tool":
                parts.append(f"[工具结果] {content[:200]}")

        return "\n".join(parts)

    def _fallback_summary(self, messages: list[dict[str, Any]]) -> str:
        """LLM 不可用时的降级摘要：取最近几条消息关键内容。"""
        parts: list[str] = []
        for msg in messages[-6:]:
            role = msg.get("role", "")
            content = (msg.get("content") or "")[:100]
            if content:
                parts.append(f"{role}: {content}")
        return "; ".join(parts) if parts else "之前的对话内容"

    def _resolve_default_model(self) -> str:
        """获取默认模型名。"""
        try:
            import llm_client as _llm_client
            return _llm_client.get_default_model() or "gpt-4o-mini"
        except Exception:
            return "gpt-4o-mini"
