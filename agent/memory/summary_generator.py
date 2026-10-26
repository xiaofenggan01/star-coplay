"""
memory.summary_generator — Episode 摘要生成器。

调用 LLM 生成第一人称 Markdown 摘要。
LLM 不可用时自动降级为纯事件列表格式的摘要。

设计原则：
    - 摘要基于存储层的客观事件生成，不基于上一次摘要，防止主观偏差累积
    - 使用较弱的模型（haiku 级别），因为摘要不需要极强的推理能力
    - 第一人称口语风格，带个人感受，像日记而非报告
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import llm_client as _llm_client
from memory._formatters import format_events, format_conversation
from report import events as report_events

log = logging.getLogger(__name__)

# 摘要生成的系统 Prompt
_SYSTEM_PROMPT = """你是一个游戏中的 AI 搭子，正在回顾刚刚一段游戏经历。
请用第一人称、自然口语记录，200字以内。

要求：
- 写的像日记，不是报告。带上你的感受和想法
- 重点：与玩家的互动、情感高峰、重要事件
- 自然忽略日常重复事件（常规浇水），除非有特殊意义
- 体现"我们"的关系感
- 输出格式固定，必须包含三个部分：正文、标签、提取的事实

## 输出格式

[200字以内的第一人称日记正文]

## 标签
[3-5个关键词，逗号分隔]

## 提取的事实
- [值得长期记住的事实，每条一行]
"""

_USER_PROMPT_TEMPLATE = """当前关系状态：
{current_facts}

游戏上下文：{game_context}

事件列表：
{events_text}

对话记录：
{conversation_text}
"""


class SummaryGenerator:
    """Episode 摘要生成器 — 调用 LLM 生成第一人称 Markdown 摘要。

    Args:
        model: LLM 模型档位（留空时从平台注入的 LLM_MODEL 环境变量读取）。
        max_tokens: LLM 生成的最大 token 数（默认 800）。
        temperature: 生成温度（默认 0.7，保持一定创造性）。
        timeout_s: LLM 调用超时时间（秒，默认 30）。
    """

    def __init__(
        self,
        model: str = "",
        max_tokens: int = 800,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
    ) -> None:
        self._model = model or _llm_client.get_default_model()
        if not self._model:
            log.error(
                "SummaryGenerator: model 未配置。请通过平台注入 LLM_MODEL，"
                " 否则摘要生成将降级为 fallback 模式。"
            )
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_s = timeout_s

    def generate(
        self,
        events: list[dict],
        conversation: list[dict],
        game_context: str,
        current_facts: str = "",
    ) -> str:
        """生成 Episode 摘要（Markdown 格式）。

        调用 LLM 生成第一人称、带感受的日记式摘要。
        如果 LLM 不可用或调用失败，自动降级为 generate_fallback()。

        Args:
            events: 原始事件列表（来自 events.jsonl）。
            conversation: 对话记录列表（来自 conversation.jsonl）。
            game_context: 游戏上下文字符串（如"Y1-Spring-Day5"）。
            current_facts: 当前 Facts 快照（Markdown 格式，可为空）。

        Returns:
            Markdown 格式的摘要文本。
        """
        if not self._model:
            log.error("SummaryGenerator: model 未配置，降级为 fallback 摘要")
            return self.generate_fallback(events, conversation, game_context)

        user_content = _USER_PROMPT_TEMPLATE.format(
            current_facts=current_facts.strip() if current_facts else "（暂无关系记录）",
            game_context=game_context or "（未知）",
            events_text=format_events(events),
            conversation_text=format_conversation(conversation),
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            t0 = time.monotonic()
            response = _llm_client.completion(
                model=self._model,
                messages=messages,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                timeout=self._timeout_s,
            )
            latency_ms = (time.monotonic() - t0) * 1000.0
            content = response.choices[0].message.content
            if content and content.strip():
                # 最小结构校验：摘要应包含至少一个 ## 标题
                if "## " not in content:
                    log.warning("SummaryGenerator: LLM 返回缺少结构标题，降级为 fallback")
                    return self.generate_fallback(events, conversation, game_context)
                log.debug("SummaryGenerator: LLM 摘要生成成功，%d 字符", len(content))
                report_events.llm_call(
                    model=self._model,
                    response=response,
                    latency_ms=latency_ms,
                    trigger="memory.summary",
                )
                return content.strip()
            else:
                log.warning("SummaryGenerator: LLM 返回空内容，降级为 fallback")
                return self.generate_fallback(events, conversation, game_context)
        except Exception as e:
            log.warning("SummaryGenerator: LLM 调用失败（%s），降级为 fallback", e)
            return self.generate_fallback(events, conversation, game_context)

    def generate_fallback(
        self,
        events: list[dict],
        conversation: list[dict],
        game_context: str,
    ) -> str:
        """降级版摘要（LLM 不可用时）。直接拼装事件列表为 Markdown。

        Args:
            events: 原始事件列表。
            conversation: 对话记录列表。
            game_context: 游戏上下文字符串。

        Returns:
            纯事件列表格式的 Markdown 摘要。
        """
        lines = []
        lines.append(f"（自动生成 — LLM 摘要不可用）")
        lines.append("")
        if game_context:
            lines.append(f"游戏上下文：{game_context}")
            lines.append("")

        if events:
            lines.append("## 事件记录")
            for evt in events:
                ts_game = evt.get("ts_game", "")
                etype = evt.get("type", "event")
                content = evt.get("content", "")
                if ts_game:
                    lines.append(f"- [{ts_game}] {etype}: {content}")
                else:
                    lines.append(f"- {etype}: {content}")
        else:
            lines.append("## 事件记录")
            lines.append("- （本 Episode 无事件）")

        lines.append("")

        if conversation:
            lines.append("## 对话记录")
            for msg in conversation:
                role = "玩家" if msg.get("role") == "player" else "我"
                content = msg.get("content", "")
                lines.append(f"- {role}：{content}")
        else:
            lines.append("## 对话记录")
            lines.append("- （本 Episode 无对话）")

        lines.append("")
        lines.append("## 标签")
        lines.append("自动归档")
        lines.append("")
        lines.append("## 提取的事实")
        lines.append("- （需人工或 LLM 提取）")

        return "\n".join(lines)

