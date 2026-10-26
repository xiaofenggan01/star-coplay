"""
slow_loop.prompt_assembler -- LLM Prompt 组装器。

将游戏状态、工作记忆、触发上下文、对话历史等信息
按 6 段注入顺序组装为结构化 Prompt（system + user messages）。

6 段注入顺序（user message 内）：
    1. 关于搭子（Persistent Facts）         ~500 tokens  P1
    2. 最近的经历（Episode 摘要）            ~600 tokens  P2
    3. 今天发生的事（工作记忆事件）          ~1000 tokens P3
    4. 当前游戏状态（Blackboard 快照）       ~300 tokens  P1
    5. 最近的对话（对话历史）                ~500 tokens  P2
    6. 当前触发（触发上下文）                ~200 tokens  P0
    （旧第 7 段 OUTPUT_SCHEMA 已在 FC 迁移时移除）

超限裁剪策略（P3→P2→P1，P0 不砍）：
    1. 减少工作记忆条数 20→10→5
    2. 减少 Episode 摘要条数 5→3→1
    3. 压缩 Persistent Facts（仅保留 player + relationship）

段模板和注入格式均从 prompts/ 目录下的文本文件加载，
修改 prompt 措辞只需编辑 .txt 文件，无需改动 Python 代码。
Token 预算从 config 的 [slow_loop.prompt] 读取。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from string import Template
from typing import Any

from memory.working_memory import WorkingMemory
from messages.types import (
    EpisodeBoundaryReport,
    EscalateRequest,
    PlayerInput,
    ReportEvent,
    TaskCompleted,
)

# [DEPRECATED] 旧 BT 体系类型，仅在类型注释中用于 skill_summaries 参数。
# FC 模式下该参数始终为 None，此 import 无运行时效果。
try:
    from skills.skill_library import SkillSummary
except ImportError:
    SkillSummary = None  # type: ignore[assignment,misc]

try:
    from .core_tools import get_core_tool_descriptions
except ImportError:
    try:
        from core_tools import get_core_tool_descriptions  # type: ignore[no-redef]
    except ImportError:
        get_core_tool_descriptions = None  # type: ignore[assignment]

log = logging.getLogger("slow_loop.prompt_assembler")

# 默认 token 预算（config 未配置时的 fallback）
_DEFAULT_TOKEN_BUDGET = 3500
_DEFAULT_SEGMENT_BUDGETS = {
    "persistent_facts": 500,
    "episode_summaries": 600,
    "working_memory": 1000,
    "game_state": 300,
    "dialogue_history": 500,
    "trigger": 200,
}

# 触发消息类型 → 模板文件名
_TRIGGER_TEMPLATE_MAP: dict[type, str] = {
    EscalateRequest: "escalate.txt",
    PlayerInput: "player_chat.txt",
    ReportEvent: "notable_event.txt",
    EpisodeBoundaryReport: "episode_boundary.txt",
    TaskCompleted: "task_completed.txt",
}

_DEFAULT_TRIGGER_TEMPLATE = "periodic.txt"


def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数（中文约 2 字/token）。"""
    return max(1, len(text) // 2)


class _SafeDict(dict):
    """用于 str.format_map，缺失 key 返回空字符串而非报错。"""

    def __missing__(self, key: str) -> str:
        log.debug("模板占位符缺失: {%s}，使用空字符串", key)
        return ""


class PromptAssembler:
    """LLM Prompt 组装器。

    将触发消息、游戏状态、工作记忆等上下文组装为
    ``[{"role": "system", ...}, {"role": "user", ...}]`` 格式的消息列表。

    段模板从 ``prompts/segment_templates/`` 加载，
    注入格式从 ``prompts/capabilities_format.txt`` / ``prompts/skills_format.txt`` 加载。
    修改 prompt 措辞只需编辑对应 .txt 文件。

    Args:
        prompts_dir: Prompt 模板目录路径。默认为本文件同级的 ``prompts/``。
        config: 可选配置，支持 ``agent_name``、``player_name``、``token_budget``、
                ``segment_budgets`` 等。通常从 ``default.toml`` 的 ``[slow_loop.prompt]`` 读取。
    """

    def __init__(
        self,
        prompts_dir: Path | None = None,
        config: dict | None = None,
    ) -> None:
        self._config = config or {}
        self._agent_name: str = self._config.get("agent_name", "小星")
        self._player_name: str = self._config.get("player_name", "玩家")
        self._token_budget: int = self._config.get(
            "token_budget", _DEFAULT_TOKEN_BUDGET
        )
        self._segment_budgets: dict = self._config.get(
            "segment_budgets", _DEFAULT_SEGMENT_BUDGETS
        )

        # 模板目录（只读数据，从 bundle 加载）
        if prompts_dir is None:
            import sys as _sys
            if getattr(_sys, 'frozen', False):
                _bundle = Path(_sys._MEIPASS) / "agent" if hasattr(_sys, '_MEIPASS') else Path(_sys.executable).parent
                prompts_dir = _bundle / "slow_loop" / "prompts"
            else:
                prompts_dir = Path(__file__).parent / "prompts"
        self._prompts_dir = prompts_dir

        # 一次性加载并缓存所有模板
        self._system_prompt: str = self._load_template("system_prompt.txt")
        # output_schema.txt 已弃用（FC 模式不使用 JSON schema）
        self._trigger_templates: dict[str, str] = {}
        self._load_trigger_templates()

        # 加载段模板和注入格式模板
        self._segment_templates: dict[str, str] = {}
        self._load_segment_templates()
        self._capabilities_format: str = self._load_template("capabilities_format.txt")
        self._skills_format: str = self._load_template("skills_format.txt")

        # 跟踪上次组装时间，用于 periodic 触发的 elapsed_seconds
        self._last_assemble_at: float = 0.0

        log.info(
            "PromptAssembler 初始化完成: prompts_dir=%s, agent=%s, player=%s, "
            "token_budget=%d, segment_templates=%d",
            self._prompts_dir,
            self._agent_name,
            self._player_name,
            self._token_budget,
            len(self._segment_templates),
        )

    # ── 公开接口 ──────────────────────────────────────────────────────────

    # [DEPRECATED] agent-loop-mode 迭代后应当彻底删除（stateless [system, user] 拼装已被 Agent Loop 累积式 messages 替代）
    # def assemble(
    #     self,
    #     trigger_msg: Any,
    #     game_state: dict,
    #     working_memory: WorkingMemory,
    #     capabilities: list[dict] | None = None,
    #     blackboard_snapshot: dict | None = None,
    #     dialogue_history: list[dict] | None = None,
    #     persistent_facts: dict | None = None,
    #     episode_summaries: list[str] | None = None,
    #     skill_summaries: "list[SkillSummary] | None" = None,
    #     memory_context: str | None = None,
    #     dispatcher_snapshot: dict | None = None,
    # ) -> list[dict]:
    #     """组装完整的 LLM Prompt。
    #
    #     Args:
    #         trigger_msg:       触发消息（EscalateRequest / PlayerInput / ...）
    #         game_state:        游戏状态 dict。
    #         working_memory:    短期工作记忆。
    #         capabilities:      保留参数（向后兼容），不再用于生成能力描述段；
    #                            能力描述改由 core_tools 模块驱动。
    #         blackboard_snapshot: 黑板快照（可选）。
    #         dialogue_history:  对话历史（可选）。
    #         persistent_facts:  持久化事实（可选）。
    #         episode_summaries: Episode 摘要列表（可选）。
    #         skill_summaries:   可用技能摘要列表（可选，来自 SkillLibrary）。
    #         memory_context:    三层记忆上下文（可选，来自 MemorySystem.get_context_for_prompt()）。
    #
    #     Returns:
    #         消息列表，格式 ``[{"role": "system", "content": ...}, {"role": "user", "content": ...}]``。
    #     """
    #     # ── System Message ────────────────────────────────────────────
    #     system_content = self._build_system_message(
    #         skill_summaries=skill_summaries,
    #         memory_context=memory_context,
    #     )
    #
    #     # ── User Message：6 段注入 ────────────────────────────────────
    #     segments = self._build_segments(
    #         trigger_msg=trigger_msg,
    #         game_state=game_state,
    #         working_memory=working_memory,
    #         blackboard_snapshot=blackboard_snapshot,
    #         dialogue_history=dialogue_history,
    #         persistent_facts=persistent_facts,
    #         episode_summaries=episode_summaries,
    #         dispatcher_snapshot=dispatcher_snapshot,
    #     )
    #
    #     # Token 预算裁剪
    #     user_content = self._apply_budget(
    #         segments=segments,
    #         trigger_msg=trigger_msg,
    #         game_state=game_state,
    #         working_memory=working_memory,
    #         blackboard_snapshot=blackboard_snapshot,
    #         dialogue_history=dialogue_history,
    #         persistent_facts=persistent_facts,
    #         episode_summaries=episode_summaries,
    #         dispatcher_snapshot=dispatcher_snapshot,
    #     )
    #
    #     import time as _time
    #     self._last_assemble_at = _time.monotonic()
    #
    #     return [
    #         {"role": "system", "content": system_content},
    #         {"role": "user", "content": user_content},
    #     ]

    # ── 模板加载 ──────────────────────────────────────────────────────

    def _load_template(self, filename: str) -> str:
        """加载单个模板文件，不存在时返回空字符串。"""
        path = self._prompts_dir / filename
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.warning("模板文件缺失: %s", path)
            return ""

    def _load_trigger_templates(self) -> None:
        """加载所有触发模板到缓存。"""
        templates_dir = self._prompts_dir / "trigger_templates"
        if not templates_dir.is_dir():
            log.warning("触发模板目录不存在: %s", templates_dir)
            return
        for path in templates_dir.glob("*.txt"):
            self._trigger_templates[path.name] = path.read_text(encoding="utf-8")
            log.debug("已加载触发模板: %s", path.name)

    def _load_segment_templates(self) -> None:
        """加载所有段模板到缓存。"""
        templates_dir = self._prompts_dir / "segment_templates"
        if not templates_dir.is_dir():
            log.warning("段模板目录不存在: %s，将使用内置格式", templates_dir)
            return
        for path in templates_dir.glob("*.txt"):
            self._segment_templates[path.stem] = path.read_text(encoding="utf-8")
            log.debug("已加载段模板: %s", path.name)

    def _get_segment_template(self, name: str) -> str | None:
        """获取段模板，不存在时返回 None（调用方使用 fallback）。"""
        return self._segment_templates.get(name)

    # ── System Message 构建 ───────────────────────────────────────────

    # [DEPRECATED] agent-loop-mode 迭代后应当彻底删除（system message 构建已迁移至 SystemPromptBuilder）
    # def _build_system_message(
    #     self,
    #     skill_summaries: "list[SkillSummary] | None" = None,
    #     memory_context: str | None = None,
    # ) -> str:
    #     """构建 system message：角色定义 + 可用能力列表 + 可用技能列表 + 记忆上下文。
    #
    #     Args:
    #         skill_summaries: SkillLibrary 提供的技能摘要列表（可选）。
    #         memory_context:  三层记忆上下文字符串（可选，来自 MemorySystem）。
    #     """
    #     # 填充角色定义模板
    #     system_text = self._system_prompt.format_map(
    #         _SafeDict(
    #             agent_name=self._agent_name,
    #             player_name=self._player_name,
    #         )
    #     )
    #
    #     # 注入记忆上下文（在能力列表之前，属于角色认知的一部分）
    #     if memory_context:
    #         system_text += f"\n\n{memory_context}\n"
    #
    #     # 追加可用能力列表（从 core_tools 读取，详细参数通过 tools API 传递）
    #     if get_core_tool_descriptions is not None:
    #         core_descs = get_core_tool_descriptions()
    #         cap_lines = []
    #         for name, desc in core_descs.items():
    #             # 只取第一行作为摘要（完整描述已在 FC schema description 中）
    #             first_line = desc.split("\n")[0]
    #             cap_lines.append(f"- {name}: {first_line}")
    #         entries_text = "\n".join(cap_lines)
    #
    #         if self._capabilities_format:
    #             cap_section = self._capabilities_format.format_map(
    #                 _SafeDict(entries=entries_text)
    #             )
    #         else:
    #             # fallback：模板文件缺失时使用内置格式
    #             cap_section = f"【可用能力（详细参数见 tools）】\n{entries_text}"
    #
    #         system_text += "\n\n" + cap_section
    #
    #     # 追加可用技能列表（仅注入摘要，不含完整 BT，控制 token 消耗）
    #     if skill_summaries:
    #         skill_lines = []
    #         for summary in skill_summaries:
    #             line = f"- {summary.skill_id}: {summary.description}"
    #             if summary.preconditions:
    #                 preconds = "、".join(summary.preconditions)
    #                 line += f"（前提：{preconds}）"
    #             skill_lines.append(line)
    #         entries_text = "\n".join(skill_lines)
    #
    #         if self._skills_format:
    #             skill_section = self._skills_format.format_map(
    #                 _SafeDict(entries=entries_text)
    #             )
    #         else:
    #             # fallback
    #             skill_section = f"【可用技能（可作为工具直接调用）】\n{entries_text}"
    #
    #         system_text += "\n\n" + skill_section
    #
    #     return system_text

    # ── 6 段内容构建 ──────────────────────────────────────────────────

    # [DEPRECATED] agent-loop-mode 迭代后应当彻底删除（user message 不再需要 6 段拼装，改为 _build_user_turn 只含当次触发）
    # def _build_segments(
    #     self,
    #     trigger_msg: Any,
    #     game_state: dict,
    #     working_memory: WorkingMemory,
    #     blackboard_snapshot: dict | None,
    #     dialogue_history: list[dict] | None,
    #     persistent_facts: dict | None,
    #     episode_summaries: list[str] | None,
    #     dispatcher_snapshot: dict | None = None,
    #     *,
    #     event_limit: int = 20,
    #     episode_limit: int = 5,
    #     facts_full: bool = True,
    # ) -> list[str]:
    #     """构建 6 段 user message 内容。
    #
    #     返回段落列表（可能含空字符串，空段在拼接时跳过）。
    #     """
    #     segments: list[str] = []
    #
    #     # 1. 关于搭子（Persistent Facts）
    #     segments.append(
    #         self._format_persistent_facts(persistent_facts, full=facts_full)
    #     )
    #
    #     # 2. 最近的经历（Episode 摘要）
    #     segments.append(self._format_episode_summaries(episode_summaries, episode_limit))
    #
    #     # 3. 今天发生的事（工作记忆）
    #     segments.append(self._format_working_memory(working_memory, event_limit))
    #
    #     # 4. 当前游戏状态（Blackboard 快照）
    #     segments.append(self._format_game_state(game_state, blackboard_snapshot))
    #
    #     segments.append(self._format_dispatcher_snapshot(dispatcher_snapshot))
    #
    #     # 5. 最近的对话（对话历史）
    #     segments.append(self._format_dialogue_history(dialogue_history))
    #
    #     # 6. 当前触发
    #     segments.append(self._format_trigger(trigger_msg))
    #
    #     # 注：旧模式的第 7 段 OUTPUT_SCHEMA 已在 FC 迁移时移除
    #
    #     return segments

    def _apply_budget(
        self,
        segments: list[str],
        trigger_msg: Any,
        game_state: dict,
        working_memory: WorkingMemory,
        blackboard_snapshot: dict | None,
        dialogue_history: list[dict] | None,
        persistent_facts: dict | None,
        episode_summaries: list[str] | None,
        dispatcher_snapshot: dict | None = None,
    ) -> str:
        """检查总 token 并按优先级裁剪，返回最终拼接的 user content。"""
        total = sum(_estimate_tokens(s) for s in segments if s)

        if total <= self._token_budget:
            return self._join_segments(segments)

        log.info(
            "Token 超预算: %d > %d，启动裁剪", total, self._token_budget
        )

        # 裁剪步骤 1: 减少工作记忆 20→10
        segments = self._rebuild_with_limits(
            trigger_msg, game_state, working_memory,
            blackboard_snapshot, dialogue_history,
            persistent_facts, episode_summaries,
            dispatcher_snapshot=dispatcher_snapshot,
            event_limit=10, episode_limit=5, facts_full=True,
        )
        total = sum(_estimate_tokens(s) for s in segments if s)
        if total <= self._token_budget:
            log.info("裁剪后 token: %d（减少工作记忆到 10 条）", total)
            return self._join_segments(segments)

        # 裁剪步骤 2: 工作记忆→5, Episode→3
        segments = self._rebuild_with_limits(
            trigger_msg, game_state, working_memory,
            blackboard_snapshot, dialogue_history,
            persistent_facts, episode_summaries,
            dispatcher_snapshot=dispatcher_snapshot,
            event_limit=5, episode_limit=3, facts_full=True,
        )
        total = sum(_estimate_tokens(s) for s in segments if s)
        if total <= self._token_budget:
            log.info("裁剪后 token: %d（工作记忆 5 条, Episode 3 条）", total)
            return self._join_segments(segments)

        # 裁剪步骤 3: Episode→1, 压缩 Facts
        segments = self._rebuild_with_limits(
            trigger_msg, game_state, working_memory,
            blackboard_snapshot, dialogue_history,
            persistent_facts, episode_summaries,
            dispatcher_snapshot=dispatcher_snapshot,
            event_limit=5, episode_limit=1, facts_full=False,
        )
        total = sum(_estimate_tokens(s) for s in segments if s)
        log.info("最终裁剪后 token: %d（Episode 1 条, Facts 精简）", total)

        return self._join_segments(segments)

    def _rebuild_with_limits(
        self,
        trigger_msg: Any,
        game_state: dict,
        working_memory: WorkingMemory,
        blackboard_snapshot: dict | None,
        dialogue_history: list[dict] | None,
        persistent_facts: dict | None,
        episode_summaries: list[str] | None,
        dispatcher_snapshot: dict | None = None,
        *,
        event_limit: int,
        episode_limit: int,
        facts_full: bool,
    ) -> list[str]:
        """用指定限制重新构建各段。"""
        return self._build_segments(
            trigger_msg=trigger_msg,
            game_state=game_state,
            working_memory=working_memory,
            blackboard_snapshot=blackboard_snapshot,
            dialogue_history=dialogue_history,
            persistent_facts=persistent_facts,
            episode_summaries=episode_summaries,
            dispatcher_snapshot=dispatcher_snapshot,
            event_limit=event_limit,
            episode_limit=episode_limit,
            facts_full=facts_full,
        )

    @staticmethod
    def _join_segments(segments: list[str]) -> str:
        """将非空段落用双换行拼接。"""
        return "\n\n".join(s for s in segments if s.strip())

    # ── 各段格式化（模板驱动 + fallback） ──────────────────────────────

    def _format_persistent_facts(
        self, facts: dict | None, *, full: bool = True
    ) -> str:
        """格式化第 1 段：关于搭子。

        使用 segment_templates/persistent_facts.txt 模板。
        模板缺失时 fallback 到内置逻辑。

        Args:
            facts: 持久化事实 dict，包含 player / relationship / world 等子 dict。
            full: 为 True 时输出完整内容，为 False 时仅保留 player + relationship。
        """
        if not facts:
            return ""

        player = facts.get("player", {})
        relationship = facts.get("relationship", {})

        # 提取数据
        player_name = player.get("name", self._player_name)
        likes = player.get("likes", "")
        habits = player.get("habits", "")
        dislikes = player.get("dislikes", "")

        stage = relationship.get("stage", "初识")
        days_together = relationship.get("days_together", "?")
        my_impression = relationship.get("my_impression", "")
        shared_highlights = relationship.get("shared_highlights", "")
        if isinstance(shared_highlights, list):
            shared_highlights = "、".join(shared_highlights)
        unresolved = relationship.get("unresolved", "")

        # 世界信息（仅 full=True 时）
        world_section = ""
        if full:
            world = facts.get("world", {})
            if world:
                world_tpl = self._get_segment_template("persistent_facts_world")
                parts = []
                if world.get("farm_name"):
                    parts.append(f"农场名：{world['farm_name']}")
                if world.get("season"):
                    parts.append(f"当前季节：{world['season']}")
                if world.get("areas"):
                    areas = world["areas"]
                    if isinstance(areas, list):
                        areas = "、".join(areas)
                    parts.append(f"已解锁区域：{areas}")
                world_info = " | ".join(parts)

                if world_tpl:
                    world_section = world_tpl.format_map(
                        _SafeDict(world_info=world_info)
                    )
                else:
                    world_section = f"【游戏世界】\n{world_info}"

        # 使用模板
        tpl = self._get_segment_template("persistent_facts")
        if tpl:
            result = tpl.format_map(_SafeDict(
                player_name=player_name,
                likes=likes,
                habits=habits,
                dislikes=dislikes,
                stage=stage,
                days_together=days_together,
                my_impression=my_impression,
                shared_highlights=shared_highlights,
                unresolved=unresolved,
                world_section=world_section,
            ))
            # 清理连续空行（模板中缺失字段留下的空行）
            lines = result.split("\n")
            cleaned = []
            for line in lines:
                stripped = line.strip()
                # 跳过只有标签前缀但无内容的行（如 "- 偏好：" 后面为空）
                if stripped.endswith("：") and stripped.startswith("-"):
                    continue
                if stripped.startswith("我对他的印象：") and stripped == "我对他的印象：":
                    continue
                if stripped.startswith("一起经历过的高光：") and stripped == "一起经历过的高光：":
                    continue
                if stripped.startswith("未解决的事：") and stripped == "未解决的事：":
                    continue
                cleaned.append(line)
            return "\n".join(cleaned).strip()

        # fallback：模板缺失时使用内置逻辑
        return self._format_persistent_facts_fallback(facts, full=full)

    def _format_persistent_facts_fallback(
        self, facts: dict, *, full: bool = True
    ) -> str:
        """内置 fallback：模板缺失时使用。"""
        lines: list[str] = []
        player = facts.get("player", {})
        relationship = facts.get("relationship", {})

        player_name = player.get("name", self._player_name)
        if player:
            lines.append(f"【关于{player_name}】")
            if player.get("likes"):
                lines.append(f"- 偏好：{player['likes']}")
            if player.get("habits"):
                lines.append(f"- 习惯：{player['habits']}")
            if player.get("dislikes"):
                lines.append(f"- 不喜欢：{player['dislikes']}")

        if relationship:
            lines.append("")
            lines.append("【我们的关系】")
            stage = relationship.get("stage", "初识")
            days = relationship.get("days_together", "?")
            lines.append(f"关系阶段：{stage}（在一起 {days} 天）")
            if relationship.get("my_impression"):
                lines.append(f"我对他的印象：{relationship['my_impression']}")
            if relationship.get("shared_highlights"):
                highlights = relationship["shared_highlights"]
                if isinstance(highlights, list):
                    highlights = "、".join(highlights)
                lines.append(f"一起经历过的高光：{highlights}")
            if relationship.get("unresolved"):
                lines.append(f"未解决的事：{relationship['unresolved']}")

        if full:
            world = facts.get("world", {})
            if world:
                lines.append("")
                lines.append("【游戏世界】")
                parts = []
                if world.get("farm_name"):
                    parts.append(f"农场名：{world['farm_name']}")
                if world.get("season"):
                    parts.append(f"当前季节：{world['season']}")
                if world.get("areas"):
                    areas = world["areas"]
                    if isinstance(areas, list):
                        areas = "、".join(areas)
                    parts.append(f"已解锁区域：{areas}")
                if parts:
                    lines.append(" | ".join(parts))

        return "\n".join(lines)

    @staticmethod
    def _format_dispatcher_snapshot(snapshot: dict | None) -> str:
        if not snapshot:
            return ""
        lines = []
        active = snapshot.get("active_job")
        pending = snapshot.get("pending_jobs") or []
        if active:
            running_for = float(active.get("running_for") or 0.0)
            pr = active.get("priority", "")
            lines.append(
                f"- active: {active.get('action')}, job_id={active.get('job_id')}, "
                f"priority={pr}, status={active.get('status')}, running_for={running_for:.0f}s, "
                f"trigger={active.get('trigger_label')}"
            )
        for job in pending[:5]:
            pr = job.get("priority", "")
            lines.append(
                f"- pending: {job.get('action')}, job_id={job.get('job_id')}, "
                f"priority={pr}, status={job.get('status')}, trigger={job.get('trigger_label')}"
            )
        if not lines:
            return ""
        return "【当前后台任务】\n" + "\n".join(lines)

    def _format_episode_summaries(
        self, summaries: list[str] | None, limit: int = 5
    ) -> str:
        """格式化第 2 段：最近的经历。使用 segment_templates/episode_summaries.txt。"""
        if not summaries:
            return ""
        trimmed = summaries[-limit:]
        entries = "\n".join(s.strip() for s in trimmed)

        tpl = self._get_segment_template("episode_summaries")
        if tpl:
            return tpl.format_map(_SafeDict(entries=entries)).strip()

        # fallback
        return f"【最近发生的事】\n{entries}"

    def _format_working_memory(
        self, working_memory: WorkingMemory, limit: int = 20
    ) -> str:
        """格式化第 3 段：今天发生的事。使用 segment_templates/working_memory.txt。"""
        events = working_memory.recent_events(limit)
        if not events:
            return ""

        event_lines = []
        for evt in events:
            game_time = evt.get("game_time", evt.get("time", ""))
            event_type = evt.get("type", "event")
            content = evt.get("content", evt.get("description", evt.get("data", "")))
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False)
            line = f"{game_time} {event_type}: {content}" if game_time else f"{event_type}: {content}"
            event_lines.append(line)

        entries = "\n".join(event_lines)

        tpl = self._get_segment_template("working_memory")
        if tpl:
            return tpl.format_map(_SafeDict(
                count=str(len(events)),
                entries=entries,
            )).strip()

        # fallback
        return f"【今天发生的事（最近 {len(events)} 件）】\n{entries}"

    def _format_game_state(
        self, game_state: dict | None, blackboard: dict | None
    ) -> str:
        """格式化第 4 段：当前游戏状态。使用 segment_templates/game_state.txt。"""
        if not game_state:
            return ""

        # 提取数据（world/player 可能存在但值为 None，需要 or {} 兜底）
        world = game_state.get("world") or {}
        time_str = world.get("time_display") or world.get("time") or game_state.get("time", "?")
        weather_obj = world.get("weather") or game_state.get("weather", "?")
        weather = weather_obj.get("current", "?") if isinstance(weather_obj, dict) else str(weather_obj)

        day = world.get("day", "?")
        season = world.get("season", "?")
        year = world.get("year", "?")
        date_str = f"第{year}年 {season} 第{day}天" if world else "?"

        player = game_state.get("player") or {}
        energy = player.get("energy", "?")
        max_energy = player.get("max_energy", "")
        energy_str = f"{energy}/{max_energy}" if max_energy else str(energy)
        location = player.get("location", "?")

        # 额外行（黑板 + 背包）
        extra = []
        if blackboard:
            if blackboard.get("current_task"):
                extra.append(f"当前执行中的任务：{blackboard['current_task']}")
            if blackboard.get("key_inventory"):
                inv = blackboard["key_inventory"]
                if isinstance(inv, list):
                    inv = "、".join(str(i) for i in inv)
                extra.append(f"背包关键物品：{inv}")

        inventory = game_state.get("inventory") or player.get("inventory")
        if inventory and not (blackboard and blackboard.get("key_inventory")):
            if isinstance(inventory, list):
                # 跳过空槽位（is_empty=True 或 name 为空）；ObserveHandler 会保留
                # null 占位以维持槽位索引一致性，提示词展示时只显示非空物品。
                items = []
                for entry in inventory[:12]:
                    if isinstance(entry, dict):
                        if entry.get("is_empty"):
                            continue
                        name = entry.get("name")
                        if not name:
                            continue
                        items.append(str(name))
                    elif entry is None:
                        continue
                    else:
                        items.append(str(entry))
                    if len(items) >= 8:
                        break
                if items:
                    extra.append(f"背包关键物品：{'、'.join(items)}")

        extra_lines = "\n".join(extra)

        tpl = self._get_segment_template("game_state")
        if tpl:
            result = tpl.format_map(_SafeDict(
                date=date_str,
                time=time_str,
                weather=weather,
                energy=energy_str,
                location=location,
                extra_lines=extra_lines,
            ))
            return result.strip()

        # fallback
        lines = ["【现在的状态】"]
        lines.append(f"日期：{date_str} | 时间：{time_str} | 天气：{weather}")
        lines.append(f"体力：{energy_str} | 位置：{location}")
        lines.extend(extra)
        return "\n".join(lines)

    def _format_dialogue_history(
        self, history: list[dict] | None
    ) -> str:
        """格式化第 5 段：最近的对话。使用 segment_templates/dialogue_history.txt。"""
        if not history:
            return ""

        dialogue_lines = []
        for entry in history:
            role = entry.get("role", "?")
            content = entry.get("content", entry.get("text", ""))
            if role in ("player", "user"):
                dialogue_lines.append(f"{self._player_name}：{content}")
            elif role in ("agent", "assistant"):
                dialogue_lines.append(f"我：{content}")
            else:
                dialogue_lines.append(f"{role}：{content}")

        entries = "\n".join(dialogue_lines)

        tpl = self._get_segment_template("dialogue_history")
        if tpl:
            return tpl.format_map(_SafeDict(entries=entries)).strip()

        # fallback
        return f"【最近的对话】\n{entries}"

    def _format_trigger(self, trigger_msg: Any) -> str:
        """格式化第 6 段：当前触发上下文。

        根据 trigger_msg 类型选择对应模板，填充占位符。
        """
        template_name = _TRIGGER_TEMPLATE_MAP.get(
            type(trigger_msg), _DEFAULT_TRIGGER_TEMPLATE
        )
        template_text = self._trigger_templates.get(template_name, "")

        if not template_text:
            log.warning("触发模板未找到: %s，使用内联描述", template_name)
            return f"【当前触发】\n{trigger_msg}"

        # 构建填充变量
        fill_vars = _SafeDict(
            player_name=self._player_name,
            agent_name=self._agent_name,
        )

        if isinstance(trigger_msg, EscalateRequest):
            fill_vars["escalate_reason"] = trigger_msg.reason
            fill_vars["current_bt_node"] = trigger_msg.current_bt_node or "无"
            # 提取关键状态子集
            gs = trigger_msg.game_state
            if gs:
                snapshot_parts = []
                if gs.get("time"):
                    snapshot_parts.append(f"时间={gs['time']}")
                p = gs.get("player", {})
                if p.get("location"):
                    snapshot_parts.append(f"位置={p['location']}")
                if p.get("energy") is not None:
                    snapshot_parts.append(f"体力={p['energy']}")
                fill_vars["context_snapshot"] = ", ".join(snapshot_parts) if snapshot_parts else "无"
            else:
                fill_vars["context_snapshot"] = "无"

        elif isinstance(trigger_msg, PlayerInput):
            fill_vars["player_message"] = trigger_msg.text

        elif isinstance(trigger_msg, ReportEvent):
            fill_vars["event_type"] = trigger_msg.event_type
            data = trigger_msg.data
            if isinstance(data, dict):
                fill_vars["event_description"] = json.dumps(data, ensure_ascii=False)
            else:
                fill_vars["event_description"] = str(data)

        elif isinstance(trigger_msg, EpisodeBoundaryReport):
            fill_vars["trigger_reason"] = trigger_msg.trigger
            ctx = trigger_msg.game_context
            fill_vars["game_date"] = ctx.get("date", ctx.get("time", "?"))
            fill_vars["day_summary"] = trigger_msg.summary_hint or "（无摘要）"

        elif isinstance(trigger_msg, TaskCompleted):
            fill_vars["task_id"] = trigger_msg.node_id
            result = trigger_msg.result
            if isinstance(result, dict):
                fill_vars["result_summary"] = json.dumps(result, ensure_ascii=False)
            else:
                fill_vars["result_summary"] = str(result)
            fill_vars["bt_node_name"] = trigger_msg.node_id
            if trigger_msg.observation:
                fill_vars["observations"] = trigger_msg.observation

        else:
            # periodic 默认模板 — 传入实际 elapsed 值
            import time as _time
            if self._last_assemble_at > 0:
                elapsed = int(_time.monotonic() - self._last_assemble_at)
                fill_vars.setdefault("elapsed_seconds", str(elapsed))
            else:
                fill_vars.setdefault("elapsed_seconds", "首次")
            fill_vars.setdefault("completed_actions", "无")

        try:
            return template_text.format_map(fill_vars)
        except (KeyError, ValueError) as e:
            log.warning("触发模板填充失败 (%s): %s，使用 Template 降级", template_name, e)
            # 降级到 string.Template 的 safe_substitute
            return Template(template_text.replace("{", "${")).safe_substitute(fill_vars)
