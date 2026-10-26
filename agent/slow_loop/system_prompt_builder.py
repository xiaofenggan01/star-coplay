"""SystemPromptBuilder — 构建 session 级别锁定的 system prompt。

职责：拼接角色定义 + 最小 FC 输出契约 + 记忆上下文。
具体工具清单、参数和操作策略仍由 tools schema 承载。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class SystemPromptBuilder:
    def __init__(
        self,
        memory_system,
        config: dict,
        *,
        skill_routing_summaries: list[dict[str, Any]] | None = None,
    ) -> None:
        self._memory_system = memory_system
        self._config = config
        self._skill_routing_summaries = skill_routing_summaries or []
        self._cached: str | None = None

        self._role_template = self._load_role_template()

    def build(self) -> str:
        if self._cached is not None:
            return self._cached

        parts: list[str] = []

        parts.append(self._build_role_section())
        parts.append(self._build_l4_orchestration_section())
        parts.append(self._build_persona_section())
        parts.append(self._build_memory_section())

        self._cached = "\n\n".join(p for p in parts if p)
        return self._cached

    def _load_role_template(self) -> str:
        path = _PROMPTS_DIR / "system_prompt.txt"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("system_prompt.txt not found, using fallback")
            return "你是一个游戏 AI 搭子。"

    _EXCLUDED_SECTIONS = frozenset([
        "关键：独白 vs 说话",
        "工具是你行动的唯一方式",
        "先回应玩家，再考虑行动",
        "行动后必须反馈",
        "先观察，再行动",
        "技能优先原则",
    ])

    def _build_role_section(self) -> str:
        sl_cfg = self._config.get("slow_loop", {})
        agent_name = sl_cfg.get("agent_name", "小枫")
        player_name = sl_cfg.get("player_name", "玩家")

        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        filled = self._role_template.format_map(
            _SafeDict(agent_name=agent_name, player_name=player_name)
        )

        return self._strip_tool_sections(filled)

    def _build_persona_section(self) -> str:
        """Insert persona card if provided via agentee env vars."""
        sl_cfg = self._config.get("slow_loop", {})
        persona_card = sl_cfg.get("agentee_persona_card")
        if not persona_card:
            return ""
        return f"<persona>\n{persona_card}\n</persona>"

    def _strip_tool_sections(self, text: str) -> str:
        lines = text.split("\n")
        result: list[str] = []
        skipping = False

        for line in lines:
            if line.startswith("【") and "】" in line:
                header = line[1:line.index("】")]
                skipping = header in self._EXCLUDED_SECTIONS
            if not skipping:
                result.append(line)

        return "\n".join(result).strip()

    def _build_l4_orchestration_section(self) -> str:
        lines = [
            "【L4 编排原则】",
            "- 单个明确执行请求：直接调用最匹配的高层 skill/tool，不要先 say 启动确认。",
            "- 多步骤、耗时或高风险请求：先用 say 给玩家自然语言计划摘要，再串行执行；每次只启动一个 long skill。",
            "- 如果 skill routing 声明 owns_discovery=true、handles_precheck=true、verifies_result=true，说明它负责发现目标、预检、执行和复查；不要为了同一目标重复 observe。",
            "- 只有意图、范围、前置条件或安全性不确定时，才先 observe 或 ask。",
            "- 不能凭 observe 或记忆宣称任务完成；完成反馈必须来自执行结果、TaskCompleted 或明确 postcheck。",
            "- 内部调度状态不得进入游戏聊天：不要 say skill 名、job_id、queued、accepted、rejected、deferred、TaskCompleted 或 raw tool/debug 状态。",
            "- 执行中玩家普通聊天不取消当前任务；只有明确取消、修改计划或紧急指令才影响 active/pending 任务。",
        ]

        if self._skill_routing_summaries:
            lines.append("")
            lines.append("【高层 skill 路由摘要】")
            for item in self._skill_routing_summaries:
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                desc = str(item.get("description") or "").strip()
                routing = item.get("routing") if isinstance(item.get("routing"), dict) else {}
                flags: list[str] = []
                for key in (
                    "owns_discovery",
                    "handles_precheck",
                    "verifies_result",
                    "execution_scope",
                    "ambiguity_policy",
                ):
                    if key in routing:
                        value = routing[key]
                        if isinstance(value, bool):
                            value_text = str(value).lower()
                        else:
                            value_text = str(value)
                        flags.append(f"{key}={value_text}")
                suffix = f"；{desc}" if desc else ""
                lines.append(f"- skill-{name}: {', '.join(flags)}{suffix}")

        return "\n".join(lines)

    def _build_persona_section(self) -> str:
        """Generate a concise persona paragraph from config.persona.

        Token budget: ~50 tokens (~100 Chinese chars).
        Format: user description + axis labels + trait tags.
        """
        persona = self._config.get("persona")
        if not persona or not isinstance(persona, dict):
            return ""

        parts: list[str] = []

        # User's original description (most important)
        desc = persona.get("description", "").strip()
        if desc:
            parts.append(desc)

        # Axis labels
        axes = persona.get("axes", {})
        if isinstance(axes, dict):
            axis_parts: list[str] = []
            labels = {
                "speech_style": self._axis_label(
                    axes.get("speech_style", 50),
                    "礼貌严肃", "偏正经", "自然", "偏活泼", "活泼不羁",
                ),
                "game_style": self._axis_label(
                    axes.get("game_style", 50),
                    "效率至上", "偏效率", "均衡", "偏体验", "体验至上",
                ),
                "initiative": self._axis_label(
                    axes.get("initiative", 50),
                    "被动回应", "偏被动", "适中", "偏主动", "主动找话题",
                ),
            }
            for v in labels.values():
                if v:
                    axis_parts.append(v)
            if axis_parts:
                parts.append("、".join(axis_parts))

        # Trait tags
        traits = persona.get("traits", [])
        if isinstance(traits, list) and traits:
            trait_labels = {
                "Tag_Farmer": "种地", "Tag_Miner": "下矿", "Tag_Fisher": "钓鱼",
                "Tag_NightOwl": "夜猫子", "Tag_Socialite": "社交达人",
                "Tag_Capitalist": "赚钱", "Tag_Decorator": "装修", "Tag_Lost": "路痴",
            }
            tags = [trait_labels.get(t, "") for t in traits if trait_labels.get(t)]
            if tags:
                parts.append("偏好：" + "、".join(tags))

        if not parts:
            return ""

        return "【性格】" + "。".join(p.rstrip("。") for p in parts) + "。"

    @staticmethod
    def _axis_label(value: int, low: str, mid_low: str, mid: str, mid_high: str, high: str) -> str:
        if value <= 20:
            return low
        if value <= 40:
            return mid_low
        if value <= 60:
            return mid
        if value <= 80:
            return mid_high
        return high

    def _build_memory_section(self) -> str:
        sections: list[str] = []

        try:
            facts = self._memory_system.get_facts()
            if facts and facts.strip():
                sections.append(f"【关于搭子】\n{facts.strip()}")
        except Exception:
            logger.debug("get_facts() failed, skipping")

        try:
            summaries = self._memory_system.get_recent_summaries()
            if summaries:
                text = "\n".join(f"- {s}" for s in summaries)
                sections.append(f"【最近的经历】\n{text}")
        except Exception:
            logger.debug("get_recent_summaries() failed, skipping")

        return "\n\n".join(sections)
