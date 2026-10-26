"""
slow_loop.deliberative_loop -- 慢环主体（Function Calling 模式）。

慢环运行在独立 daemon Thread 中，通过阻塞等待 MessageBus 消息驱动执行：
    1. 等待 MessageBus 中的 EscalateRequest / PlayerInput / ReportEvent / EpisodeBoundaryReport / TaskCompleted
    2. 调用 PromptAssembler 组装上下文
    3. 调用 LLM（Function Calling），LLM 返回 tool_calls
    4. 逐个执行 tool_calls（通过 FileBridge.write_command）
    5. 更新 WorkingMemory

节流策略：min_call_interval_s 限制 LLM 调用频率。
防抖策略：player_input_debounce_s 合并短时间内多条玩家输入。

迁移说明（v2 FC Migration）：
    - 旧模式：LLM 输出 JSON → Planner 解析 → action_plan → BT → 快环执行
    - 新模式：LLM 输出 tool_calls → 逐个直接执行（通过 FileBridge）
    - Planner / BT 引擎代码保留但不再使用（标注弃用）
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import llm_client as _llm_client
from bt_engine.message_bus import MessageBus
from bridge.bridge_backend import BridgeBackend
from memory.working_memory import WorkingMemory
from messages.types import (
    DirectAction,
    EpisodeBoundaryReport,
    EscalateRequest,
    PlayerInput,
    ReplaceBehaviorTree,
    ReportEvent,
    TaskCompleted,
    UpdateBlackboard,
)
from report import events
from slow_loop.action_dispatcher import ActionDispatcher
from slow_loop.action_policy import (
    BRIDGE_POLL_INTERVAL_S,
    DEFAULT_LONG_PRIORITY,
    HIGH_LONG_PRIORITY,
    INSTANT_ACTIONS,
    LONG_ACTIONS,
    QUERY_ACTIONS,
    QUERY_TIMEOUT_S,
    timeout_for_action,
)
from slow_loop.plan_state import PlanItem, PlanState
# [DEPRECATED] agent-loop-mode 迭代后应当彻底删除（stateless 模式已被 Agent Loop 替代）
# from slow_loop.prompt_assembler import PromptAssembler
from slow_loop.compactor import SessionCompactor
from slow_loop.system_prompt_builder import SystemPromptBuilder

# [DEPRECATED] 旧 BT 体系 SkillLibrary。FC 模式不使用，_skill_library 始终为 None。
# 新增 Skill 请使用 v2_skills/builtin/<name>/{skill.md, script.py}。
try:
    from skills.skill_library import SkillLibrary
except ImportError:
    SkillLibrary = None  # type: ignore[assignment,misc]

try:
    from memory.memory_system import MemorySystem
except ImportError:
    MemorySystem = None  # type: ignore[assignment,misc]

try:
    from knowledge.game_knowledge import GameKnowledge
except ImportError:
    GameKnowledge = None  # type: ignore[assignment,misc]

try:
    from v2_skills.registry import SkillRegistry
    from v2_skills.runner import SkillRunner
except ImportError:
    SkillRegistry = None  # type: ignore[assignment,misc]
    SkillRunner = None  # type: ignore[assignment,misc]


def extract_situation(trigger_msg: object) -> str:
    """从触发消息中提取情境描述字符串（用于 L2 缓存查找与写入）。

    优先使用 reason 属性，其次 text 属性，兜底为类名。
    提取为独立模块函数以方便单元测试。

    Args:
        trigger_msg: 任意触发消息对象。

    Returns:
        情境描述字符串。
    """
    if hasattr(trigger_msg, "reason") and trigger_msg.reason:  # type: ignore[union-attr]
        return f"{type(trigger_msg).__name__}:{trigger_msg.reason}"  # type: ignore[union-attr]
    if hasattr(trigger_msg, "event_type"):
        et = str(getattr(trigger_msg, "event_type", "") or "").strip()
        if et:
            return et
    if hasattr(trigger_msg, "trigger"):
        tr = str(getattr(trigger_msg, "trigger", "") or "").strip()
        if tr:
            return tr
    if hasattr(trigger_msg, "node_id"):
        nid = str(getattr(trigger_msg, "node_id", "") or "").strip()
        if nid:
            return nid
    if hasattr(trigger_msg, "text") and trigger_msg.text:  # type: ignore[union-attr]
        return f"{type(trigger_msg).__name__}:{trigger_msg.text}"  # type: ignore[union-attr]
    return type(trigger_msg).__name__


def _route_key_from_trigger(trigger_msg: object) -> str | None:
    route_key = (getattr(trigger_msg, "route_key", None) or "").strip()
    return route_key or None


_QUOTA_ERROR_KEYWORDS = (
    "quota insufficient",
    "insufficient quota",
    "quota is not enough",
    "user quota is not enough",
    "preconsumequota",
    "preconsume quota",
    "precharge",
    "balance is not enough",
    "insufficient balance",
    "余额不足",
    "额度不足",
    "积分余额不足",
    "预扣费",
)


def _is_quota_or_precharge_error(error: BaseException | str) -> bool:
    """Return True for NewAPI quota/precharge failures that users can fix."""
    text = str(error or "").lower()
    if not text:
        return False
    if any(keyword in text for keyword in _QUOTA_ERROR_KEYWORDS):
        return True
    if "newapi_auth_failed" in text:
        return any(term in text for term in ("quota", "precharge", "balance", "insufficient", "not enough"))
    return False


def _tool_schema_names(tools: list[dict]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _public_external_text(text: str, source: str) -> str:
    clean = (text or "").strip()
    prefix = f"[{source}]"
    if clean.lower().startswith(prefix):
        return clean[len(prefix):].strip()
    return clean


def _extract_reasoning_content(message_obj: Any) -> str | None:
    candidates: list[Any] = [getattr(message_obj, "reasoning_content", None)]

    model_extra = getattr(message_obj, "model_extra", None)
    if isinstance(model_extra, dict):
        candidates.append(model_extra.get("reasoning_content"))

    model_dump = getattr(message_obj, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            candidates.append(dumped.get("reasoning_content"))

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _copy_reasoning_content(target: dict[str, Any], message_obj: Any) -> None:
    reasoning_content = _extract_reasoning_content(message_obj)
    if reasoning_content:
        target["reasoning_content"] = reasoning_content


log = logging.getLogger("slow_loop.deliberative_loop")

# FC 多轮回传 LLM 时单条 tool 内容上限（避免上下文爆炸导致续轮失败且无反馈）
_TOOL_RESULT_MAX_CHARS = 2800

_URGENT_TEXT_EN = frozenset({
    "urgent", "emergency", "stop", "cancel", "halt", "help me", "help!", "abort",
})

_DIRECT_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "skill-water_crops",
        re.compile(r"(帮我|给我|去|先|快)?[^。！？\n]*浇水"),
    ),
    (
        "skill-harvest_crops",
        re.compile(r"(帮我|给我|去|先|快)?[^。！？\n]*(收菜|收作物|收庄稼|采摘|摘作物|摘菜)"),
    ),
)

_DIRECT_ROUTE_BLOCKERS = ("再", "然后", "顺便", "之后", "先", "同时")
_WAYPOINT_ROUTE_PATTERN = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*[:：]\s*(-?\d+)\s*[,，]\s*(-?\d+)"
)
_URGENT_TEXT_CN = ("紧急", "快点", "停下", "取消", "别做了", "重来", "救命", "危险", "马上", "立刻")
_SLEEP_TEXT_CN = ("睡觉", "去睡", "上床", "入眠", "休息", "睡了", "困", "入眠")
_SLEEP_TEXT_EN = frozenset({"sleep", "go bed", "go_bed", "bedtime", "go to bed", "hit the hay"})
_ALWAYS_FOLLOWUP_ACTIONS = frozenset({"go_bed", "buy_items", "ship_items"})
_NO_FOLLOWUP_SKILLS = frozenset({"chop_trees"})

# core_tools 声明、但执行委托给 v2_skills/builtin/<skill_id>/script.py 的工具映射
# key: LLM 调用的工具名（core_tool name）
# value: SkillRegistry 中的 skill_id（与目录名一致）
# 注意：被列入此表的 skill 应同时加入 SKILLS_EXCLUDED_FROM_FC（registry.py），避免双重暴露
L3_BACKED_CORE_TOOLS: dict[str, str] = {
    "observe": "observe",
    "inspect": "inspect",
    "find_interactions": "find_interactions",
    "search_facts": "search_facts",
}


def _normalize_skill_tool_name(tool_name: str) -> str:
    """Return the SkillRegistry id from a skill tool name.

    Current FC tool names use ``skill-foo`` because OpenAI function names do
    not accept ``:``. Some older tests and internal call sites still use
    ``skill:foo``; keep both accepted at the execution boundary.
    """
    if tool_name.startswith("skill-"):
        return tool_name.removeprefix("skill-")
    if tool_name.startswith("skill:"):
        return tool_name.removeprefix("skill:")
    return tool_name


def _player_preemption_reason(text: str) -> str | None:
    raw = (text or "").strip()
    tail = raw.split(":", 1)[-1].strip() if ":" in raw else raw
    blob = tail.lower()
    for kw in _URGENT_TEXT_CN:
        if kw in tail:
            return f"urgent:{kw}"
    for kw in _URGENT_TEXT_EN:
        if kw in blob:
            return f"urgent:{kw}"
    for kw in _SLEEP_TEXT_CN:
        if kw in tail:
            return f"sleep:{kw}"
    for kw in _SLEEP_TEXT_EN:
        if kw in blob:
            return f"sleep:{kw}"
    return None


# ── Fallback 行为树（弃用，保留兼容）────────────────────────────────────────

FALLBACK_BT: dict = {
    "type": "selector",
    "name": "SafeFallback",
    "children": [
        {
            "type": "sequence",
            "name": "LowEnergyGoHome",
            "children": [
                {"type": "condition", "check": "energy < 15"},
                {"type": "action", "call": "move_to", "params": {"target": "home"}},
            ],
        },
        {
            "type": "action",
            "name": "FollowPlayer",
            "call": "move_to",
            "params": {"target": "player"},
        },
    ],
}


class DeliberativeLoop:
    """慢环主体：在独立 daemon Thread 中运行，消费 MessageBus 消息并通过 Function Calling 驱动 LLM 决策。

    Args:
        message_bus: 快环-慢环消息总线。
        bridge: Mod 文件桥，用于读取游戏状态和能力列表。
        working_memory: 短期工作记忆。
        config: 可选配置 dict，从 config["slow_loop"] 读取参数。
    """

    def __init__(
        self,
        message_bus: MessageBus,
        bridge: BridgeBackend,
        working_memory: WorkingMemory,
        config: dict | None = None,
        skill_library: "SkillLibrary | None" = None,
        memory_system: "MemorySystem | None" = None,
        game_knowledge: "GameKnowledge | None" = None,
    ) -> None:
        self._bus = message_bus
        self._bridge = bridge
        self._memory = working_memory
        self._skill_library = skill_library
        self._memory_system = memory_system
        self._knowledge = game_knowledge

        # 从 config["slow_loop"] 提取参数
        sl_cfg = (config or {}).get("slow_loop", {})
        self._min_call_interval_s: float = sl_cfg.get("min_call_interval_s", 30.0)
        self._player_input_debounce_s: float = sl_cfg.get("player_input_debounce_s", 1.5)

        llm_cfg = sl_cfg.get("llm", {})
        # 优先从平台注入的 LLM_MODEL 读取模型档位，回退到配置文件，未配置时记错误日志
        env_model = _llm_client.get_default_model()
        if env_model:
            self._model: str = env_model
            log.info("使用环境变量 LLM_MODEL: %s", self._model)
        else:
            cfg_model = llm_cfg.get("model", "").strip()
            if cfg_model:
                self._model = cfg_model
                log.info("使用配置文件 model: %s", self._model)
            else:
                log.error(
                    "LLM_MODEL 未在 .env 中配置，也未在 config/default.toml 中配置。"
                    " 慢环 LLM 调用将失败。请通过平台注入 LLM_MODEL。"
                )
                self._model = ""

        self._max_tokens: int = llm_cfg.get("max_tokens", 1500)
        self._temperature: float = llm_cfg.get("temperature", 0.7)
        self._timeout_s: float = llm_cfg.get("timeout_s", 30.0)

        # LLM 调用函数（可注入 mock）
        self._llm_fn = _llm_client.completion

        # [DEPRECATED] agent-loop-mode 迭代后应当彻底删除（stateless 模式已被 Agent Loop 替代）
        # self._assembler = PromptAssembler(config=sl_cfg)

        # 运行状态
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_call_at: float = 0.0

        # ── Agent Loop 模式：session 状态 ──
        self._session_messages: list[dict[str, Any]] = []
        self._system_message: str = ""
        self._session_initialized: bool = False
        self._turn_count: int = 0
        self._config = config or {}
        self._compactor = SessionCompactor(
            config=self._config,
            llm_fn=self._llm_fn,
            system_message_getter=lambda: self._system_message,
        )

        # L3 SkillRuntime 初始化
        self._skill_registry = None
        self._skill_runner = None
        if SkillRegistry is not None:
            try:
                skills_dir = Path(__file__).parent.parent / "v2_skills"
                self._skill_registry = SkillRegistry(skills_dir)
                # game_state.json 路径从 bridge 的 data 目录推导
                game_state_path = bridge._base_dir / "game_state.json" if hasattr(bridge, "_base_dir") else Path("data/game_state.json")
                self._skill_runner = SkillRunner(bridge, game_state_path)
                log.info("[FC] L3 SkillRuntime 已加载，%d 个技能", len(self._skill_registry.list_skills()))
            except Exception as e:
                log.warning("[FC] L3 SkillRuntime 加载失败: %s", e)

        # 弃用：BT 相关状态（保留兼容，FC 模式不使用）
        self._current_bt_json: dict | None = None

        # 玩家输入防抖缓冲
        self._pending_player_inputs: list[PlayerInput] = []
        self._player_input_timer: float = 0.0
        self._plan_state: PlanState | None = None
        self._dispatcher = ActionDispatcher(
            bridge=self._bridge,
            message_bus=self._bus,
            working_memory=self._memory,
            stop_event=self._stop_event,
            max_long_queue=int(sl_cfg.get("max_long_queue", 20)),
        )
        if self._skill_runner is not None and hasattr(self._skill_runner, "set_bridge"):
            self._skill_runner.set_bridge(self._dispatcher.locked_bridge())

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动慢环 daemon 线程。"""
        if not self._stop_event.is_set() and self._thread and self._thread.is_alive():
            log.warning("DeliberativeLoop 已在运行中，忽略重复 start()")
            return
        self._stop_event.clear()
        self._dispatcher.start()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="DeliberativeLoop",
            daemon=True,
        )
        self._thread.start()
        log.info("DeliberativeLoop 已启动")

    def stop(self) -> None:
        """停止慢环线程，等待最多 5 秒退出。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log.warning("DeliberativeLoop 线程未在 5 秒内退出")
            self._thread = None
        self._dispatcher.stop()
        log.info("DeliberativeLoop 已停止")

    # ── Session 生命周期 ────────────────────────────────────────────────────

    def _ensure_session(self) -> None:
        """懒初始化 session：首次 trigger 时构建 system prompt。"""
        if self._session_initialized:
            return
        skill_routing_summaries: list[dict[str, Any]] = []
        if self._skill_registry is not None:
            try:
                get_summaries = getattr(self._skill_registry, "get_routing_summaries", None)
                if callable(get_summaries):
                    skill_routing_summaries = get_summaries()
            except Exception:
                log.debug("get_routing_summaries failed; skipping L4 skill routing prompt", exc_info=True)
        builder = SystemPromptBuilder(
            memory_system=self._memory_system,
            config=self._config,
            skill_routing_summaries=skill_routing_summaries,
        )
        self._system_message = builder.build()
        self._session_messages = []
        self._turn_count = 0
        self._session_initialized = True

    def _reset_session(self) -> None:
        """重置 session 状态，下次 trigger 重新初始化。"""
        self._session_initialized = False
        self._session_messages = []
        self._system_message = ""
        self._turn_count = 0

    def _do_compact(self) -> None:
        """执行 compact：委托给 SessionCompactor。"""
        self._compactor._do_compact(self._session_messages)

    def _maybe_compact(self) -> None:
        """检查是否需要 compact，委托给 SessionCompactor。"""
        self._compactor.maybe_compact(self._session_messages)

    def _get_active_messages(self) -> list[dict[str, Any]]:
        """返回最后一个 boundary 之后的消息。"""
        return self._compactor.get_active_messages(self._session_messages)

    def _is_safe_to_compact(self) -> bool:
        """检查当前是否处于安全边界。"""
        return self._compactor.is_safe_to_compact(self._session_messages)

    def _compute_compact_threshold(self) -> int:
        """计算动态 compact 阈值。"""
        return self._compactor.compute_threshold()

    def _estimate_tokens_for_messages(self, messages: list[dict]) -> int:
        """粗略估算 messages 的 token 数。"""
        return self._compactor.estimate_tokens(messages)

    def _generate_summary(self) -> str:
        """生成对话摘要，委托给 SessionCompactor。"""
        active = self._compactor.get_active_messages(self._session_messages)
        return self._compactor._generate_summary(active)

    def _drain_queued_messages(self) -> list[str]:
        """从 MessageBus 提取 FC 期间排队的玩家消息。"""
        drain = getattr(self._bus, "drain_from_fast", None)
        if drain is None:
            return []
        inputs = drain(lambda msg: isinstance(msg, PlayerInput), limit=10)
        if not inputs:
            return []
        texts: list[str] = []
        for msg in inputs:
            if isinstance(msg, PlayerInput) and msg.text:
                self._record_player_input(msg)
                texts.append(msg.text.strip())
        return texts

    def _process_queued_messages(self, queued_texts: list[str]) -> None:
        """将排队消息合并为一个新 user turn，递归调用 _process_trigger。"""
        if not queued_texts:
            return
        merged_text = "在你刚才工作的时候，玩家又说了：\n" + "\n".join(f"- {t}" for t in queued_texts)
        queued_trigger = PlayerInput(text=merged_text)
        self._process_trigger(queued_trigger)

    def _build_user_turn(self, trigger_msg: Any, game_state: dict) -> str:
        """构造本次触发的 user message 内容（只含本次触发信息 + 当前游戏状态）。"""
        parts: list[str] = []

        # 游戏状态摘要
        if game_state:
            player = game_state.get("player") or {}
            time_str = game_state.get("time", "")
            location = player.get("location", "")
            energy = player.get("energy", "?")
            if time_str or location:
                parts.append(f"【当前状态】{time_str} | 位置: {location} | 体力: {energy}")

        # 触发内容
        if isinstance(trigger_msg, PlayerInput):
            text = (trigger_msg.text or "").strip()
            parts.append(f"【玩家说】{text}")
        elif hasattr(trigger_msg, "event_type"):
            parts.append(f"【事件】{trigger_msg.event_type}: {getattr(trigger_msg, 'data', '')}")
        elif hasattr(trigger_msg, "reason"):
            parts.append(f"【升级请求】{trigger_msg.reason}")
        elif hasattr(trigger_msg, "trigger"):
            parts.append(f"【日结】{trigger_msg.trigger}")
        elif hasattr(trigger_msg, "node_id"):
            result = getattr(trigger_msg, "result", "")
            parts.append(f"【任务完成】{trigger_msg.node_id}: {result}")
        else:
            parts.append(f"【触发】{type(trigger_msg).__name__}")

        plan_context = self._format_plan_context()
        if plan_context:
            parts.append(plan_context)

        return "\n".join(parts)

    def _get_messages_for_api(self) -> list[dict[str, Any]]:
        """返回发送给 LLM API 的完整 messages 列表（system + boundary 后的 session_messages）。"""
        messages: list[dict[str, Any]] = []
        if self._system_message:
            messages.append({"role": "system", "content": self._system_message})

        # 找到最后一个 compact_boundary 的位置
        boundary_idx = -1
        for i, msg in enumerate(self._session_messages):
            if msg.get("metadata", {}).get("type") == "compact_boundary":
                boundary_idx = i

        source = self._session_messages[boundary_idx:] if boundary_idx >= 0 else self._session_messages

        # Strip internal metadata before sending to LLM API
        for msg in source:
            clean = {k: v for k, v in msg.items() if k != "metadata"}
            messages.append(clean)

        return messages

    # ── 主循环 ───────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """慢环主循环：阻塞等待消息 -> 分发处理。"""
        log.info("慢环主循环开始运行")
        while not self._stop_event.is_set():
            try:
                msg = self._bus.get_from_fast_blocking(timeout=1.0)
                if msg is None:
                    self._check_debounce_flush()
                    continue
                self._handle_message(msg)
            except Exception as e:
                log.exception("慢环主循环异常")
                events.ai_error_from_exception(exc=e, component="deliberative_loop")
        log.info("慢环主循环已退出")

    # ── 消息分发 ─────────────────────────────────────────────────────────────

    def _handle_message(self, msg: Any) -> None:
        """根据消息类型分发处理。"""
        if isinstance(msg, PlayerInput):
            self._record_player_input(msg)
            self._buffer_player_input(msg)

        elif isinstance(msg, EscalateRequest):
            self._process_trigger(msg)

        elif isinstance(msg, ReportEvent):
            # 注入工作记忆
            self._memory.remember(
                {},
                [
                    {
                        "type": msg.event_type,
                        "content": msg.data,
                        "ts": msg.sent_at,
                        "significance": msg.significance,
                    }
                ],
            )
            # MemorySystem：记录游戏事件
            if self._memory_system is not None:
                try:
                    event_dict = {
                        "type": msg.event_type,
                        "content": msg.data,
                        "ts": msg.sent_at,
                        "significance": msg.significance,
                    }
                    self._memory_system.append_event(event_dict)
                except Exception:
                    log.debug("MemorySystem.append_event 失败，跳过", exc_info=True)
            # 仅高重要性事件触发 LLM
            if msg.significance >= 7:
                self._process_trigger(msg)

        elif isinstance(msg, EpisodeBoundaryReport):
            cancelled = self._dispatcher.cancel_pending()
            drained = self._dispatcher.drain_active(timeout_s=2.0)
            self._memory.remember({}, [{
                "type": "dispatcher_boundary_state",
                "content": {
                    "cancelled_pending": cancelled,
                    "active_drained": drained,
                    "snapshot": self._dispatcher.snapshot(),
                },
                "ts": time.time(),
            }])
            # MemorySystem：归档当前 Episode
            if self._memory_system is not None:
                try:
                    game_context_str = str(msg.game_context)
                    episode_id = self._memory_system.archive_episode(
                        trigger=msg.trigger,
                        game_context=game_context_str,
                    )
                    log.info(
                        "MemorySystem: EpisodeBoundaryReport 触发归档 episode_id=%s trigger=%s",
                        episode_id,
                        msg.trigger,
                    )
                    # 阶段 2：后台线程处理摘要生成 + Facts 更新（不阻塞慢环）
                    import threading
                    def _bg_process_archives(ms=self._memory_system):
                        try:
                            processed = ms.process_pending_archives()
                            log.info("MemorySystem: 后台处理了 %d 个 pending 归档任务", processed)
                            # 顺便重试之前失败的 Facts 更新
                            retried = ms.retry_pending_facts()
                            if retried > 0:
                                log.info("MemorySystem: 重试了 %d 个 pending Facts 更新", retried)
                        except Exception:
                            log.warning("MemorySystem 后台处理失败", exc_info=True)
                    threading.Thread(target=_bg_process_archives, name="MemoryArchive", daemon=True).start()
                except Exception:
                    log.warning("MemorySystem.archive_episode 失败，跳过", exc_info=True)
            gs = self._bridge.read_game_state() or {}
            ctx = msg.game_context if isinstance(msg.game_context, dict) else {}
            w_ctx = ctx.get("world") if isinstance(ctx.get("world"), dict) else {}
            world = w_ctx if w_ctx else (gs.get("world") or {})
            player = gs.get("player") or {}
            farm = gs.get("farm_summary") or {}
            crops = farm.get("crops") if isinstance(farm.get("crops"), dict) else {}
            tasks_done = int(crops.get("harvestable_count") or 0)
            events.day_summary(
                game_day=world.get("day"),
                season=world.get("season"),
                tasks_done=tasks_done,
                income=player.get("gold"),
                boundary_trigger=msg.trigger,
            )
            self._process_trigger(msg)

        elif isinstance(msg, TaskCompleted):
            # 注入工作记忆
            self._memory.remember(
                {},
                [
                    {
                        "type": "task_completed",
                        "content": msg.result,
                        "node_id": msg.node_id,
                        "ts": msg.sent_at,
                    }
                ],
            )
            result = msg.result if isinstance(msg.result, dict) else {}
            if self._handle_plan_task_completed(msg, result):
                return
            should_process = (
                not bool(result.get("success", False))
                or bool(result.get("needs_followup"))
            )
            if should_process:
                self._process_trigger(msg)

        else:
            # 检查是否为 session 控制消息（通过 duck typing 避免硬依赖）
            msg_type = type(msg).__name__
            if msg_type == "CompactRequest":
                self._do_compact()
            elif msg_type == "ResetRequest":
                self._reset_session()
            else:
                log.debug("忽略未知消息类型: %s", msg_type)

    # ── 防抖逻辑 ─────────────────────────────────────────────────────────────

    def _buffer_player_input(self, msg: PlayerInput) -> None:
        """将玩家输入放入防抖缓冲区。"""
        self._pending_player_inputs.append(msg)
        self._player_input_timer = time.monotonic()

    def _record_player_input(self, msg: PlayerInput) -> None:
        if msg.text:
            events.player_message(
                text=msg.text,
                source=getattr(msg, "source", "unknown"),
            )
            events.onboard_first_conversation(source="player_input")

        if self._memory_system is not None and msg.text:
            try:
                self._memory_system.append_conversation("player", msg.text)
            except Exception:
                log.debug("MemorySystem.append_conversation 失败，跳过", exc_info=True)
        if msg.text:
            self._memory.remember(
                {},
                [{"type": "player_said", "content": msg.text, "ts": time.time()}],
            )

    def _apply_player_preemption(self, msg: PlayerInput) -> None:
        preempt = _player_preemption_reason(msg.text or "")
        if not preempt:
            return
        n_pending = self._dispatcher.cancel_pending()
        active_hit = self._dispatcher.cancel_active()
        if self._plan_state is not None and self._plan_state.status not in {"completed", "cancelled"}:
            self._plan_state.status = "cancelled"
            self._plan_state.blocked_reason = preempt
            self._plan_state.active = None
            self._plan_state.remaining.clear()
            self._plan_state.updated_at = time.time()
        self._memory.remember({}, [{
            "type": "dispatcher_player_preempt",
            "content": {
                "reason": preempt,
                "pending_cancelled": n_pending,
                "active_cancel_requested": active_hit,
                "plan_cancelled": self._plan_state is not None,
            },
            "ts": time.time(),
        }])

    def _drain_mid_turn_player_inputs(self, conversation: list[dict[str, Any]]) -> int:
        drain = getattr(self._bus, "drain_from_fast", None)
        if drain is None:
            return 0
        inputs = drain(lambda msg: isinstance(msg, PlayerInput), limit=10)
        if not inputs:
            return 0
        player_inputs = [msg for msg in inputs if isinstance(msg, PlayerInput)]
        for msg in player_inputs:
            self._record_player_input(msg)
            self._apply_player_preemption(msg)
        merged = self._merge_player_inputs(player_inputs)
        self._record_external_text_merged_input(merged, len(player_inputs))
        text = (merged.text or "").strip()
        if text:
            label = "【外部输入中途补充】" if self._external_channel_key(merged) else "【玩家中途补充】"
            conversation.append({
                "role": "user",
                "content": f"{label}\n{text}",
            })
            self._memory.remember({}, [{
                "type": "mid_turn_player_input",
                "content": text,
                "ts": time.time(),
            }])
        return len(player_inputs)

    def _check_debounce_flush(self) -> None:
        """检查防抖窗口是否到期，到期则合并并触发处理。"""
        if (
            self._pending_player_inputs
            and time.monotonic() - self._player_input_timer >= self._player_input_debounce_s
        ):
            input_count = len(self._pending_player_inputs)
            merged = self._merge_player_inputs(self._pending_player_inputs)
            self._pending_player_inputs.clear()
            text = (merged.text or "").strip()
            if text.startswith("/report"):
                body = text[len("/report"):].strip()
                if not body:
                    # 空内容：提示用户补充，不上报
                    self._dispatcher.submit("say", {"message": "请在 /report 后面写上你要反馈的内容哦，比如：/report 黑屏bug"}, wait_result=False)
                    return
                # 有内容：上报（附带游戏状态快照）+ 确认反馈 + 不继续作为普通输入处理
                game_ctx = self._capture_game_context()
                try:
                    events.user_report(body=body, game_context=game_ctx)
                except Exception:
                    log.debug("user_report 遥测失败", exc_info=True)
                self._dispatcher.submit("say", {"message": f"收到反馈，谢谢！我已经记录下来了：{body[:50]}"}, wait_result=False)
                return
            self._record_external_text_merged_input(merged, input_count)
            self._process_trigger(merged)

    @staticmethod
    def _merge_player_inputs(inputs: list[PlayerInput]) -> PlayerInput:
        """合并多条玩家输入为一条。"""
        combined_text = "\n".join(inp.text for inp in inputs if inp.text)
        sources = {getattr(inp, "source", "game_chat") for inp in inputs}
        source = inputs[-1].source if len(sources) == 1 else "mixed"
        input_ids = [inp.input_id for inp in inputs if inp.input_id]
        route_keys = [(inp.route_key or "").strip() for inp in inputs if (inp.route_key or "").strip()]
        return PlayerInput(
            text=combined_text,
            source=source,
            input_id=",".join(input_ids) if input_ids else None,
            route_key=(route_keys[0] if len(set(route_keys)) == 1 else None),
            sender_label=inputs[-1].sender_label,
            sent_at=inputs[-1].sent_at,
        )

    @staticmethod
    def _external_channel_key(msg: Any) -> str | None:
        if not isinstance(msg, PlayerInput):
            return None
        source = (getattr(msg, "source", "") or "").strip()
        if not source or source in ("game_chat", "mixed"):
            return None
        route_key = (getattr(msg, "route_key", None) or "").strip()
        return route_key or None

    def _record_external_text_merged_input(self, msg: PlayerInput, input_count: int) -> None:
        source = (getattr(msg, "source", "") or "").strip()
        channel_key = self._external_channel_key(msg)
        text = _public_external_text(msg.text or "", source)
        if not source or not channel_key or not text:
            return
        input_ids = [item for item in (msg.input_id or "").split(",") if item]
        try:
            events.external_text_input_merged(
                source=source,
                channel_key=channel_key,
                text=text,
                input_ids=input_ids,
                input_count=input_count,
            )
        except Exception:
            log.debug("external_text_input_merged telemetry failed", exc_info=True)

    @staticmethod
    def _is_async_tool_name(name: str) -> bool:
        return name.startswith("skill-") or name in LONG_ACTIONS

    @staticmethod
    def _plan_source_input(trigger_msg: Any) -> str:
        if isinstance(trigger_msg, PlayerInput):
            return (trigger_msg.text or "").strip()
        return extract_situation(trigger_msg)

    @staticmethod
    def _plan_label_for_tool(name: str, args: dict[str, Any]) -> str:
        target = args.get("location") or args.get("target") or args.get("verb")
        if name == "move_to":
            return f"去{target}" if target else "移动到目标位置"
        if name == "do":
            return f"完成{target}" if target else "执行动作"
        if name == "use_tool":
            return "使用工具"
        if name == "equip":
            return "换好工具"
        if name == "face":
            return "调整朝向"
        return str(target or name).replace("_", " ")

    def _plan_label_for_item(self, name: str, args: dict[str, Any]) -> str:
        if name.startswith("skill-") and self._skill_registry is not None:
            skill_name = _normalize_skill_tool_name(name)
            try:
                entry = self._skill_registry.get(skill_name)
            except Exception:
                entry = None
            metadata = getattr(entry, "metadata", None)
            if isinstance(metadata, dict):
                label = self._shorten_plan_label(str(metadata.get("description") or ""))
                if label:
                    return label
        return self._plan_label_for_tool(name, args)

    @staticmethod
    def _shorten_plan_label(description: str) -> str:
        text = (description or "").strip()
        if not text:
            return ""
        for sep in ("。", ".", "\n"):
            if sep in text:
                text = text.split(sep, 1)[0].strip()
                break
        for phrase in ("当玩家说", "当玩家要求", "，直接调用", ", directly call"):
            if phrase in text:
                text = text.split(phrase, 1)[0].strip(" ，,")
        return text[:48]

    @staticmethod
    def _contains_internal_status_text(text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in (
            "skill-",
            "skill:",
            "job_id",
            "queued",
            "accepted",
            "rejected",
            "deferred",
            "planned:",
            "taskcompleted",
            "action_jobs:",
        ))

    def _submit_plan_visible_feedback(
        self,
        message: str,
        *,
        route_key: str | None,
    ) -> None:
        clean = (message or "").strip()
        if not clean or self._contains_internal_status_text(clean):
            log.warning("跳过含内部状态的计划反馈: %s", clean[:120])
            return
        self._submit_visible_say(clean, external_channel_key=route_key)

    @staticmethod
    def _join_plan_labels(labels: list[str]) -> str:
        clean = [label.strip() for label in labels if label and label.strip()]
        if not clean:
            return ""
        if len(clean) == 1:
            return clean[0]
        if len(clean) == 2:
            return f"{clean[0]}，再{clean[1]}"
        return "，再".join(clean[:-1]) + f"，最后{clean[-1]}"

    def _format_plan_started_message(self, plan: PlanState) -> str:
        labels: list[str] = []
        if plan.active is not None:
            labels.append(plan.active.label)
        labels.extend(item.label for item in plan.remaining)
        joined = self._join_plan_labels(labels)
        return f"我按顺序来：先{joined}。" if joined else ""

    def _format_plan_progress_message(self, plan: PlanState, completed_label: str) -> str:
        if plan.status == "completed":
            return f"{completed_label}完成了，计划里的事都做完了。"
        if plan.status == "active" and plan.active is not None:
            return f"{completed_label}完成了，接下来{plan.active.label}。"
        if plan.status == "blocked":
            reason = plan.blocked_reason or "执行受阻"
            return f"{completed_label}只完成了一部分，后面的计划先暂停：{reason}。"
        return ""

    def _format_plan_context(self) -> str:
        plan = self._plan_state
        if plan is None or plan.status in {"completed", "cancelled"}:
            return ""
        payload: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "source_input": plan.source_input,
            "status": plan.status,
            "done": [
                {
                    "label": item.label,
                    "result_status": item.result_status,
                }
                for item in plan.done
            ],
            "active": (
                {
                    "label": plan.active.label,
                    "tool": plan.active.tool,
                }
                if plan.active is not None else None
            ),
            "remaining": [
                {
                    "label": item.label,
                    "tool": item.tool,
                }
                for item in plan.remaining
            ],
            "blocked_reason": plan.blocked_reason,
        }
        return "【当前计划】" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _queued_job_id(result_text: str) -> str | None:
        marker = "queued: job_id="
        if marker not in result_text:
            return None
        tail = result_text.split(marker, 1)[1].strip()
        return tail.split()[0].strip() or None

    def _create_plan_from_async_items(
        self,
        *,
        trigger_msg: Any,
        items: list[PlanItem],
    ) -> PlanState | None:
        if len(items) <= 1:
            return None
        if self._plan_state is not None and self._plan_state.status not in {"completed", "cancelled"}:
            return None
        self._plan_state = PlanState.from_items(
            source_input=self._plan_source_input(trigger_msg),
            items=items,
        )
        self._memory.remember({}, [{
            "type": "plan_created",
            "content": {
                "plan_id": self._plan_state.plan_id,
                "source_input": self._plan_state.source_input,
                "remaining": [
                    {"label": item.label, "tool": item.tool, "args": item.args}
                    for item in self._plan_state.remaining
                ],
            },
            "ts": time.time(),
        }])
        return self._plan_state

    def _start_next_plan_item(
        self,
        *,
        trigger_label: str,
        route_key: str | None,
        priority: int = DEFAULT_LONG_PRIORITY,
    ) -> str | None:
        plan = self._plan_state
        if plan is None or plan.active is not None or not plan.remaining:
            return None
        item = plan.remaining[0]
        if item.tool.startswith("skill-"):
            result_text = self._queue_skill(
                item.tool,
                item.args,
                trigger_label,
                priority,
                needs_followup=False,
                route_key=route_key,
            )
            job_id = self._queued_job_id(result_text)
        elif item.tool in LONG_ACTIONS:
            job_id = self._dispatcher.submit(
                item.tool,
                item.args,
                timeout_s=timeout_for_action(item.tool),
                wait_result=True,
                trigger_label=trigger_label,
                needs_followup=False,
                priority=priority,
                route_key=route_key,
            )
            result_text = (
                f"{item.tool} queued: job_id={job_id}"
                if job_id is not None
                else f"{item.tool} rejected: async queue is full"
            )
        else:
            plan.status = "blocked"
            plan.blocked_reason = f"unsupported_plan_tool:{item.tool}"
            return None

        if job_id is None:
            plan.status = "blocked"
            plan.blocked_reason = result_text
            return None

        plan.start_next(job_id=job_id)
        self._memory.remember({}, [{
            "type": "plan_step_started",
            "content": {
                "plan_id": plan.plan_id,
                "job_id": job_id,
                "tool": item.tool,
                "label": item.label,
            },
            "ts": time.time(),
        }])
        return result_text

    def _handle_plan_task_completed(self, msg: TaskCompleted, result: dict[str, Any]) -> bool:
        plan = self._plan_state
        if plan is None or plan.active is None:
            return False
        job_id = str(result.get("job_id") or msg.node_id or "").strip()
        if not job_id or plan.active.job_id != job_id:
            return False

        completed_label = plan.active.label
        plan.apply_completion(result)
        self._memory.remember({}, [{
            "type": "plan_step_completed",
            "content": {
                "plan_id": plan.plan_id,
                "job_id": job_id,
                "status": plan.status,
                "blocked_reason": plan.blocked_reason,
                "remaining": [
                    {"label": item.label, "tool": item.tool, "args": item.args}
                    for item in plan.remaining
                ],
            },
            "ts": time.time(),
        }])

        if plan.status == "planned":
            self._start_next_plan_item(
                trigger_label=str(result.get("trigger_label") or f"PlanState:{plan.plan_id}"),
                route_key=msg.route_key,
                priority=DEFAULT_LONG_PRIORITY,
            )
        feedback = self._format_plan_progress_message(plan, completed_label)
        if feedback:
            self._submit_plan_visible_feedback(feedback, route_key=msg.route_key)
        return True

    # ── Tool Schema 构建 ──────────────────────────────────────────────────────

    def _build_tool_schemas(self, capabilities: list[dict] | None = None) -> list[dict]:
        """构建 LLM 可见的 FC tools schema（v2.4.x）。

        数据源：
          1. core_tools.get_core_tool_schemas()  — 基本 Protocol 能力
          2. core_tools.get_local_tool_schemas() — 本地 observe / wiki / memory 工具

        adapter_info capabilities 不再参与 FC schema 构建；仅 Mod 内部声明用。
        `capabilities` 参数保留签名兼容性，实际忽略。
        """
        is_protocol_transport_enabled = getattr(
            self._bridge,
            "is_protocol_transport_enabled",
            lambda: False,
        )
        if bool(is_protocol_transport_enabled()):
            from .core_tools import get_core_tool_schemas, get_local_tool_schemas

            # Runtime adapter_info is asynchronous and must not gate the LLM-facing
            # schema set. core_tools.py is the source of truth for direct tools.
            tools = list(get_core_tool_schemas())
            tools.extend(get_local_tool_schemas())
            if self._skill_registry:
                tools.extend(self._skill_registry.get_fc_schemas())
            tools.extend(self._dispatcher_tool_schemas())
            if not tools:
                log.warning("Protocol transport enabled but no runtime protocol tools are visible")
            else:
                log.info(
                    "Protocol tools visible to LLM: %s",
                    ",".join(_tool_schema_names(tools)),
                )
            return tools

        from .core_tools import get_core_tool_schemas, get_local_tool_schemas

        # 1. 9 个基本能力（say / interact / use_tool / equip / go_to /
        #                  observe / search_wiki / read_wiki / remember）
        tools = list(get_core_tool_schemas())
        tools.extend(get_local_tool_schemas())
        if self._skill_registry:
            tools.extend(self._skill_registry.get_fc_schemas())

        # 2. 后台动作取消（main 引入的异步执行配套工具，与 core_tools 解耦）
        tools.extend(self._dispatcher_tool_schemas())
        return tools

    @staticmethod
    def _dispatcher_tool_schemas() -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": "cancel_active_action",
                "description": "取消当前后台正在执行的动作（玩家紧急改指令时可调用）；队列里未开始的长任务也会在玩家说「紧急/睡觉」等时被自动清空。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_action_jobs",
                "description": "查询后台动作队列状态，包括当前执行、排队和最近完成/失败/取消的任务；当玩家追问进度或你准备改计划时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "可选。指定 job_id 时只查询这个任务。",
                        }
                    },
                },
            },
        }]

    # ── 决策流水线（FC 模式）──────────────────────────────────────────────────

    def _process_trigger(self, trigger_msg: Any) -> None:
        """决策流水线（FC 模式）：节流 -> 读取状态 -> 组装 Prompt -> 调用 LLM（FC）-> 执行 tool_calls。"""
        external_channel_key = self._external_channel_key(trigger_msg) or _route_key_from_trigger(trigger_msg)

        # 1. 最小间隔检查
        now = time.monotonic()
        elapsed = now - self._last_call_at
        if elapsed < self._min_call_interval_s and not isinstance(trigger_msg, PlayerInput):
            wait = self._min_call_interval_s - elapsed
            log.debug("节流跳过 %.1f 秒", wait)
            self._memory.remember({}, [{
                "type": "trigger_skipped_by_throttle",
                "content": {"trigger": type(trigger_msg).__name__, "remaining_s": wait},
                "ts": time.time(),
            }])
            return

        if isinstance(trigger_msg, PlayerInput):
            self._apply_player_preemption(trigger_msg)
            if self._try_route_direct_player_skill(trigger_msg, external_channel_key):
                return

        # 2. 读取最新游戏状态
        game_state = self._bridge.read_game_state()
        if game_state is None:
            log.warning("游戏状态不可用（文件不存在或未启动），使用空状态")
            game_state = {}

        # 3. 读取能力列表（adapter_info，保留供 prompt_assembler 兼容参数传递）
        caps = self._bridge.capabilities()

        # 4. 构建 tools（v2.4.x：来自 core_tools + SkillRegistry，不透传 adapter_info）
        tools = self._build_tool_schemas(caps)

        # 4b. 获取可用技能摘要（保留，供 prompt 注入）
        skill_summaries = None
        if self._skill_library is not None:
            try:
                skill_summaries = self._skill_library.list_available()
            except Exception:
                log.debug("获取技能摘要失败，跳过技能注入")

        # 4c. Agent Loop 模式：初始化 session + 检查 compact + 构建 user turn
        self._ensure_session()

        # 4d. 检查是否需要 autocompact（在追加新 user turn 之前）
        self._maybe_compact()

        user_content = self._build_user_turn(trigger_msg, game_state)
        self._session_messages.append({"role": "user", "content": user_content})

        # 5. 获取 API messages（system + 累积 session_messages）
        messages = self._get_messages_for_api()

        trigger_label = f"{type(trigger_msg).__name__}:{extract_situation(trigger_msg)}"

        # 6. 调用 LLM（Function Calling）
        current_model = _llm_client.get_default_model() or self._model

        # Dev-only: dump full LLM request to JSONL for observability
        import sys as _sys
        if not getattr(_sys, 'frozen', False):
            try:
                _app = Path(__file__).parent.parent
                _req_path = _app / "data" / "llm" / "llm_requests.jsonl"
                _req_path.parent.mkdir(parents=True, exist_ok=True)
                _req_entry = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "trigger": trigger_label,
                    "model": current_model,
                    "messages": messages,
                    "tool_names": _tool_schema_names(tools),
                    "tools": tools,
                    "protocol_transport": bool(
                        getattr(self._bridge, "is_protocol_transport_enabled", lambda: False)()
                    ),
                }
                with open(_req_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(_req_entry, ensure_ascii=False) + "\n")
            except Exception:
                pass

        t0 = time.monotonic()
        response = None
        plan_exc: Exception | None = None
        try:
            response = self._llm_fn(
                model=current_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                timeout=self._timeout_s,
            )
        except Exception as e:
            log.exception("LLM 调用异常")
            plan_exc = e

        latency_ms = (time.monotonic() - t0) * 1000.0

        # 6b. 遥测
        if plan_exc is not None:
            _err_ctx: dict[str, Any] = {}
            try:
                if game_state:
                    _p = game_state.get("player") or {}
                    _w = game_state.get("world") or {}
                    _pos = _p.get("position") or {}
                    _err_ctx = {
                        "player_pos": {"x": _pos.get("x"), "y": _pos.get("y")},
                        "energy": _p.get("energy"),
                        "location": (game_state.get("location") or {}).get("name")
                            if isinstance(game_state.get("location"), dict)
                            else game_state.get("location"),
                        "game_time": f"{_w.get('season', '?')} {_w.get('day', '?')}",
                    }
            except Exception:
                _err_ctx = {"snapshot": "error_during_capture"}

            _err_type_name = type(plan_exc).__name__
            if "timeout" in _err_type_name.lower() or "timeout" in str(plan_exc).lower():
                events.llm_timeout(
                    model=current_model,
                    timeout_s=self._timeout_s,
                    component="deliberative_loop",
                    trigger=trigger_label,
                    error_ctx=_err_ctx,
                )
            else:
                events.llm_api_error(
                    model=current_model,
                    error_type=_err_type_name,
                    error_msg=str(plan_exc),
                    component="deliberative_loop",
                    trigger=trigger_label,
                    error_ctx=_err_ctx,
                )
            events.ai_error(
                error_type=_err_type_name,
                message=str(plan_exc),
                component="deliberative_loop",
                trigger=trigger_label,
                stack_trace=traceback.format_exc(),
            )
            # 区分 429 限流和其他错误，给玩家不同的反馈
            is_rate_limit = "429" in str(plan_exc) or "rate" in str(plan_exc).lower()
            is_timeout = "timeout" in _err_type_name.lower() or "timeout" in str(plan_exc).lower()
            if _is_quota_or_precharge_error(plan_exc):
                self._submit_visible_say("积分余额不足", external_channel_key=external_channel_key)
            elif is_rate_limit:
                self._submit_visible_say("脑子转太快被限速了，等一下再回你～", external_channel_key=external_channel_key)
            elif is_timeout:
                self._submit_visible_say("想了太久没想出来，稍等我再试试...", external_channel_key=external_channel_key)
            else:
                self._execute_fallback(trigger_msg)
            self._last_call_at = time.monotonic()
            return

        # 遥测：成功的 LLM 调用
        if response is not None:
            events.llm_call_with_trigger_label(
                model=current_model,
                response=response,
                latency_ms=latency_ms,
                trigger="deliberative_fc",
                trigger_label=trigger_label,
            )

        # 7. Tool call 循环：finish_reason=tool_calls 时送回结果让 LLM 继续
        MAX_FC_ROUNDS = 5  # 限制续轮次数（3 轮不够：失败→查询→移动→重试→反馈 至少需要 5 轮）
        all_tool_calls_log: list[dict] = []
        executed_actions: list[str] = []  # 收集所有执行的动作名称，用于 FC 摘要
        fc_start_time = time.monotonic()
        tool_results: list[dict] = []
        fc_had_error = False
        async_action_count = 0
        executed_tool_call_ids_for_response: set[str] | None = None
        executed_tool_call_round_for_response: int | None = None

        def _tool_call_id(tc: Any, round_index: int) -> str:
            return getattr(tc, "id", None) or f"call_{round_index}_{tc.function.name}"

        def _assistant_message_from_tool_calls(
            message_obj: Any,
            tool_calls_for_message: list[Any],
            round_index: int,
        ) -> dict[str, Any]:
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": (message_obj.content or "") or None,
            }
            _copy_reasoning_content(assistant_msg, message_obj)
            if tool_calls_for_message:
                assistant_msg["tool_calls"] = [
                    {
                        "id": _tool_call_id(tc, round_index),
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls_for_message
                ]
            return assistant_msg

        def _append_executed_tool_block(
            message_obj: Any,
            tool_calls_for_message: list[Any],
            results_for_message: list[dict],
            round_index: int,
        ) -> None:
            self._session_messages.append(
                _assistant_message_from_tool_calls(
                    message_obj,
                    tool_calls_for_message,
                    round_index,
                )
            )
            for tr in results_for_message:
                self._session_messages.append(tr)

        def _append_internal_assistant_note(content: str) -> None:
            self._session_messages.append({
                "role": "assistant",
                "content": content,
            })

        for fc_round in range(MAX_FC_ROUNDS):
            message_obj = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            if finish_reason == "length":
                fc_had_error = True
                log.warning("LLM finish_reason=length，输出可能被截断（fc_round=%d）", fc_round)
                if isinstance(trigger_msg, PlayerInput):
                    self._submit_visible_say("刚才输出太长被截断了，你再说一下要做什么～", external_channel_key=external_channel_key)

            # 提取 monologue（从 content）
            monologue = message_obj.content or ""
            if monologue:
                log.info("Monologue: %s", monologue[:200])
                self._memory.remember(
                    {},
                    [{"type": "agent_thought", "content": monologue, "ts": time.time()}],
                )

            tool_calls = message_obj.tool_calls or []

            if not tool_calls:
                if (
                    fc_round == 0
                    and isinstance(trigger_msg, TaskCompleted)
                    and monologue.strip()
                    and not executed_actions
                ):
                    self._submit_visible_say(monologue.strip(), external_channel_key=external_channel_key)
                    break
                if fc_round == 0:
                    log.warning("LLM 未返回任何 tool_calls")
                    self._execute_no_tool_call_fallback(trigger_msg)
                    fc_had_error = True
                elif isinstance(trigger_msg, PlayerInput) and not executed_actions:
                    log.warning("FC 后续轮次无 tool_calls 且本轮未执行任何动作")
                    self._execute_no_tool_call_fallback(trigger_msg)
                    fc_had_error = True
                break

            # 记录日志
            for tc in tool_calls:
                all_tool_calls_log.append({
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })

            # 执行本轮 tool_calls，收集结果
            tool_results: list[dict] = []
            should_stop = False
            round_has_async_action = False
            has_query_call = any(getattr(tc.function, "name", "") in QUERY_ACTIONS for tc in tool_calls)
            round_plan: PlanState | None = None
            async_candidates_this_round: list[PlanItem] = []
            if not has_query_call:
                for tc in tool_calls:
                    nm = getattr(tc.function, "name", "")
                    if not self._is_async_tool_name(nm):
                        continue
                    raw = getattr(tc.function, "arguments", "") or ""
                    try:
                        parsed_args = json.loads(raw) if raw else {}
                    except (json.JSONDecodeError, TypeError):
                        parsed_args = {}
                    if not isinstance(parsed_args, dict):
                        parsed_args = {}
                    async_candidates_this_round.append(PlanItem(
                        label=self._plan_label_for_item(nm, parsed_args),
                        tool=nm,
                        args=parsed_args,
                    ))
                round_plan = self._create_plan_from_async_items(
                    trigger_msg=trigger_msg,
                    items=async_candidates_this_round,
                )
            async_candidate_ordinal = 0

            for tc in tool_calls:
                name = tc.function.name
                tc_id = _tool_call_id(tc, fc_round)
                raw_args = tc.function.arguments
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except (json.JSONDecodeError, TypeError):
                    log.warning("tool_call 参数解析失败: %s", raw_args[:200] if raw_args else "None")
                    tool_results.append({"tool_call_id": tc_id, "role": "tool", "content": "参数解析失败"})
                    continue

                result_text = ""
                if name in L3_BACKED_CORE_TOOLS:
                    # core_tool 执行委托给 v2_skills 实现（不带 skill: 前缀）
                    # observe 等查询型 core_tool 走同步路径，绕过异步队列以保持低延迟
                    skill_id = L3_BACKED_CORE_TOOLS[name]
                    result_text = self._execute_skill(f"skill-{skill_id}", args)
                elif name.startswith("skill-"):
                    async_candidate_ordinal += 1
                    if has_query_call:
                        result_text = f"{name} deferred: query results returned first"
                    elif async_action_count >= 1:
                        result_text = f"{name} planned: waiting for previous async action"
                        should_stop = True
                    elif round_plan is not None and async_candidate_ordinal > 1:
                        result_text = f"{name} planned: waiting for previous plan step"
                        should_stop = True
                    else:
                        result_text = self._queue_skill(
                            name, args, trigger_label,
                            self._long_priority_for_trigger(trigger_msg),
                            needs_followup=self._should_followup_after_skill(
                                name,
                                trigger_msg,
                                is_last_async_for_trigger=round_plan is None,
                            ),
                            route_key=external_channel_key,
                        )
                        job_id = self._queued_job_id(result_text)
                        if job_id is not None:
                            if round_plan is not None:
                                round_plan.start_next(job_id=job_id)
                                if isinstance(trigger_msg, PlayerInput) or external_channel_key:
                                    self._submit_plan_visible_feedback(
                                        self._format_plan_started_message(round_plan),
                                        route_key=external_channel_key,
                                    )
                            async_action_count += 1
                            round_has_async_action = True
                            should_stop = True
                elif name == "cancel_active_action":
                    result_text = "cancelled active action" if self._dispatcher.cancel_active() else "no active action to cancel"
                elif name == "query_action_jobs":
                    result_text = self._format_dispatcher_query(args)
                elif name == "remember":
                    self._handle_remember(args)
                    result_text = "已记住"
                elif name in ("search_wiki", "read_wiki"):
                    result_text = self._handle_wiki_tool(name, args)
                elif name == "say":
                    if has_query_call:
                        result_text = "say deferred: query results returned first"
                    else:
                        self._handle_say(args)
                        result_text = f"accepted: say {args.get('message', '')[:50]}"
                elif name in QUERY_ACTIONS:
                    # Query 工具必须等待结果并回传给 LLM（否则 LLM 看不到数据）
                    query_result = self._execute_game_action(
                        name, args, wait=True, timeout=QUERY_TIMEOUT_S, cancel_on_timeout=False
                    )
                    result_text = self._format_query_result(name, query_result)
                elif name in INSTANT_ACTIONS:
                    if has_query_call:
                        result_text = f"{name} deferred: query results returned first"
                    else:
                        job_id = self._dispatcher.submit(
                            name,
                            args,
                            wait_result=False,
                            trigger_label=trigger_label,
                            route_key=external_channel_key,
                        )
                        result_text = f"{name} accepted: job_id={job_id}"
                        round_has_async_action = True
                elif name in LONG_ACTIONS:
                    async_candidate_ordinal += 1
                    if has_query_call:
                        result_text = f"{name} deferred: query results returned first"
                    elif async_action_count >= 1:
                        result_text = f"{name} planned: waiting for previous async action"
                        should_stop = True
                    elif round_plan is not None and async_candidate_ordinal > 1:
                        result_text = f"{name} planned: waiting for previous plan step"
                        should_stop = True
                    else:
                        job_id = self._dispatcher.submit(
                            name,
                            args,
                            timeout_s=timeout_for_action(name),
                            wait_result=True,
                            trigger_label=trigger_label,
                            needs_followup=self._should_followup_after_action(
                                name,
                                trigger_msg,
                                is_last_async_for_trigger=round_plan is None,
                            ),
                            priority=self._long_priority_for_trigger(trigger_msg),
                            route_key=external_channel_key,
                        )
                        if job_id is None:
                            result_text = f"{name} rejected: async queue is full"
                        else:
                            result_text = f"{name} queued: job_id={job_id}"
                            if round_plan is not None:
                                round_plan.start_next(job_id=job_id)
                                if isinstance(trigger_msg, PlayerInput) or external_channel_key:
                                    self._submit_plan_visible_feedback(
                                        self._format_plan_started_message(round_plan),
                                        route_key=external_channel_key,
                                    )
                            async_action_count += 1
                            round_has_async_action = True
                            should_stop = True
                else:
                    log.warning("未知 tool_call: %s", name)
                    result_text = f"未知工具: {name}"

                if len(result_text) > _TOOL_RESULT_MAX_CHARS:
                    result_text = (
                        result_text[: _TOOL_RESULT_MAX_CHARS - 24]
                        + "...(truncated_for_context)"
                    )
                tool_results.append({"tool_call_id": tc_id, "role": "tool", "content": result_text})
                executed_actions.append(name)
                # 每个 tool_call 都要有 tool response；后续 async 只记录到 PlanState，不立即提交。

            executed_tool_call_ids_for_response = {
                _tool_call_id(tc, fc_round) for tc in tool_calls
            }
            executed_tool_call_round_for_response = fc_round

            # 如果 finish_reason != tool_calls 或遇到长时操作，结束循环
            if round_has_async_action:
                should_stop = True

            if finish_reason != "tool_calls" or should_stop:
                break

            if fc_round == MAX_FC_ROUNDS - 1:
                log.warning(
                    "MAX_FC_ROUNDS exhausted; closing executed tool block without "
                    "persisting another unexecuted assistant tool_call"
                )
                _append_executed_tool_block(message_obj, tool_calls, tool_results, fc_round)
                self._drain_mid_turn_player_inputs(self._session_messages)
                executed_tool_call_ids_for_response = None
                executed_tool_call_round_for_response = None
                try:
                    response = self._llm_fn(
                        model=current_model,
                        messages=self._get_messages_for_api(),
                        tools=tools,
                        tool_choice="none",
                        max_tokens=self._max_tokens,
                        temperature=self._temperature,
                        timeout=self._timeout_s,
                    )
                except Exception as e:
                    log.exception("FC final no-tool LLM call failed after max rounds: %s", e)
                    response = None
                    fc_had_error = True
                    _append_internal_assistant_note(
                        "Function-calling round limit reached before a final response."
                    )
                break

            # finish_reason == tool_calls → 把 assistant message + tool results 送回 LLM
            log.info("FC 轮次 %d 完成，finish_reason=tool_calls，继续下一轮", fc_round + 1)

            # 构造多轮消息：追加 assistant message + tool results 到 session
            _append_executed_tool_block(message_obj, tool_calls, tool_results, fc_round)
            self._drain_mid_turn_player_inputs(self._session_messages)
            executed_tool_call_ids_for_response = None
            executed_tool_call_round_for_response = None

            # 再次调用 LLM
            try:
                response = self._llm_fn(
                    model=current_model,
                    messages=self._get_messages_for_api(),
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                    timeout=self._timeout_s,
                )
            except Exception as e:
                log.exception("FC 续轮 LLM 调用失败: %s", e)
                fc_had_error = True
                if isinstance(trigger_msg, PlayerInput):
                    err_s = str(e)
                    if _is_quota_or_precharge_error(e):
                        hint = "积分余额不足"
                    elif "429" in err_s or "rate" in err_s.lower():
                        hint = "上游限流了，稍后再试～"
                    else:
                        hint = "对话太长或请求失败，我稍后再试～"
                    self._submit_visible_say(hint, external_channel_key=external_channel_key)
                break

        # 8. 追加最后一轮的 assistant message 到 session（break 时未追加）
        if response is not None and not fc_had_error:
            last_message_obj = response.choices[0].message
            last_monologue = last_message_obj.content or ""
            last_tool_calls = last_message_obj.tool_calls or []
            if last_tool_calls:
                if executed_tool_call_round_for_response is None:
                    last_tool_call_ids = {
                        getattr(tc, "id", None) or tc.function.name
                        for tc in last_tool_calls
                    }
                else:
                    last_tool_call_ids = {
                        _tool_call_id(tc, executed_tool_call_round_for_response)
                        for tc in last_tool_calls
                    }

                if (
                    executed_tool_call_round_for_response is not None
                    and executed_tool_call_ids_for_response == last_tool_call_ids
                ):
                    _append_executed_tool_block(
                        last_message_obj,
                        last_tool_calls,
                        tool_results,
                        executed_tool_call_round_for_response,
                    )
                else:
                    log.warning(
                        "Skipping final assistant tool_calls because they were not "
                        "executed in this FC turn: ids=%s",
                        sorted(last_tool_call_ids),
                    )
                    _append_internal_assistant_note(
                        "Function-calling round limit reached; no further tool calls were executed."
                    )
            else:
                last_assistant_msg: dict[str, Any] = {"role": "assistant", "content": last_monologue or None}
                _copy_reasoning_content(last_assistant_msg, last_message_obj)
                self._session_messages.append(last_assistant_msg)

        self._turn_count += 1

        # 8a. 检查 FC 期间排队的玩家消息
        queued_texts = self._drain_queued_messages()
        if queued_texts:
            self._process_queued_messages(queued_texts)

        # 8b. FC 摘要日志
        fc_elapsed = time.monotonic() - fc_start_time
        trigger_type = type(trigger_msg).__name__
        log.info(
            "FC完成: trigger=%s rounds=%d actions=[%s] 耗时=%.1fs",
            trigger_type, fc_round + 1, ",".join(executed_actions), fc_elapsed,
        )

        try:
            tc_payload: list[dict[str, Any]] = []
            _budget = 0
            for _tc in all_tool_calls_log[:60]:
                _nm = str(_tc.get("name", ""))[:128]
                _arg = str(_tc.get("arguments", ""))[:2048]
                _budget += len(_nm) + len(_arg)
                if _budget > 32000:
                    break
                tc_payload.append({"name": _nm, "arguments": _arg})
            events.fc_decision_summary(
                trigger=trigger_label,
                fc_rounds=fc_round + 1,
                executed_actions=executed_actions,
                had_error=fc_had_error,
                tool_calls=tc_payload or None,
            )
        except Exception:
            log.debug("fc_decision_summary 失败", exc_info=True)

        # 8b. 自动反馈保底：如果 FC 循环中有实际动作但 LLM 没有调 say，自动生成摘要反馈给玩家
        #     仅在玩家触发（PlayerInput）时才做保底反馈，避免自动事件触发时刷屏
        FEEDBACK_WORTHY_ACTIONS = {
            "water_crops", "harvest_crops", "plant_crops", "chop_trees", "mine_rocks",
            "go_bed", "ship_items", "buy_items", "move_to",
            "do", "use_tool", "observe", "equip", "face", "emote",
            "search_wiki", "read_wiki",
        }
        KNOWLEDGE_ACTIONS = {"search_wiki", "read_wiki"}
        def _is_internal_feedback_text(text: str) -> bool:
            lowered = text.lower()
            return (
                "job_id=" in lowered
                or " queued:" in lowered
                or " accepted:" in lowered
                or " rejected:" in lowered
                or " deferred:" in lowered
                or " planned:" in lowered
                or lowered.startswith("action_jobs:")
            )

        has_say = "say" in executed_actions
        worthy_actions = [a for a in executed_actions if a in FEEDBACK_WORTHY_ACTIONS]
        is_player_triggered = isinstance(trigger_msg, PlayerInput)
        last_monologue_text = ""
        if response is not None:
            try:
                last_monologue_text = str(response.choices[0].message.content or "").strip()
            except Exception:
                last_monologue_text = ""

        has_active_plan = self._plan_state is not None and self._plan_state.status in {"planned", "active"}
        if not has_active_plan and not has_say and worthy_actions and (is_player_triggered or external_channel_key):
            if not is_player_triggered and last_monologue_text:
                self._submit_visible_say(last_monologue_text, external_channel_key=external_channel_key)
                log.info("TaskCompleted natural follow-up forwarded: %s", last_monologue_text[:100])
                return
            # 从最后一次 tool_results 中提取摘要
            summary_parts = []
            for tr in tool_results:
                content = tr.get("content", "")
                if _is_internal_feedback_text(content):
                    continue
                if any(wa in content for wa in FEEDBACK_WORTHY_ACTIONS):
                    # 取 "|" 分隔的前两段作为简要摘要
                    brief = " | ".join(content.split(" | ")[:2])
                    summary_parts.append(brief)
            if summary_parts:
                auto_msg = "；".join(summary_parts[:3])  # 最多 3 条
            elif any(a in KNOWLEDGE_ACTIONS for a in executed_actions):
                auto_msg = "我查到一些资料了：春天赚钱作物优先看草莓；买不到草莓时，土豆和花椰菜也值得考虑。我继续边做边查。"
            else:
                auto_msg = f"做完了（{', '.join(worthy_actions)}）"
            self._submit_visible_say(auto_msg, external_channel_key=external_channel_key)
            log.info("自动反馈保底: %s", auto_msg[:100])

        # 9. 日志：写入完整 LLM 响应
        try:
            import sys as _sys
            _app = Path(_sys.executable).parent if getattr(_sys, 'frozen', False) else Path(__file__).parent.parent
            log_path = _app / "data" / "llm" / "llm_responses.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "trigger": type(trigger_msg).__name__,
                "mode": "function_calling",
                "monologue": monologue[:500] if monologue else "",
                "tool_calls": all_tool_calls_log,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

        # 10. 更新最后调用时间
        self._last_call_at = time.monotonic()

    # ── FC 执行方法 ──────────────────────────────────────────────────────────

    def _execute_game_action(
        self,
        action: str,
        params: dict,
        wait: bool = False,
        timeout: float = 15.0,
        *,
        cancel_on_timeout: bool = True,
    ) -> "BridgeResult | None":
        """执行游戏动作（通过 FileBridge.write_command）。

        Args:
            action: 动作名称。
            params: 动作参数。
            wait: 是否等待结果（长时操作）。
            timeout: 等待超时时间（秒），compound actions 需要更长。

        Returns:
            BridgeResult（wait=True 时），或 None。
        """
        t0 = time.monotonic()
        action_id = self._dispatcher.write_command_sync(action, params)
        log.info("执行动作: %s params=%s action_id=%s wait=%s timeout=%.0fs", action, params, action_id, wait, timeout)

        if wait:
            # 等待结果（带超时）
            result = self._wait_for_result(
                action_id, timeout=timeout, cancel_on_timeout=cancel_on_timeout
            )
            dur_ms = (time.monotonic() - t0) * 1000.0
            ok = result is not None and bool(getattr(result, "success", False))
            try:
                events.action_outcome(action_type=action, success=ok, duration_ms=dur_ms)
            except Exception:
                log.debug("action_outcome 遥测失败", exc_info=True)
            self._memory.remember({}, [{
                "type": f"action_{action}",
                "content": {"action": action, "params": params, "result": str(result)[:500] if result else None, "action_id": action_id},
                "ts": time.time(),
            }])
            return result
        else:
            dur_ms = (time.monotonic() - t0) * 1000.0
            try:
                events.action_outcome(action_type=action, success=True, duration_ms=dur_ms)
            except Exception:
                log.debug("action_outcome 遥测失败", exc_info=True)
            # 即时动作不等待
            self._memory.remember({}, [{
                "type": f"action_{action}",
                "content": {"action": action, "params": params, "action_id": action_id},
                "ts": time.time(),
            }])
            return None

    def _wait_for_result(
        self, action_id: str, timeout: float = 30.0, *, cancel_on_timeout: bool = True
    ) -> Any:
        """等待命令执行结果。

        Args:
            action_id: 命令 action_id。
            timeout: 超时时间（秒）。

        Returns:
            BridgeResult 或 None（超时/停止）。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self._bridge.read_command_result(action_id)
            if result is not None:
                return result
            if self._stop_event.wait(timeout=BRIDGE_POLL_INTERVAL_S):
                return None  # 收到停止信号
        log.warning("等待结果超时: action_id=%s timeout=%.1fs", action_id, timeout)
        if cancel_on_timeout:
            self._dispatcher.cancel_command_sync(action_id)
        return None

    @staticmethod
    def _format_action_result(action: str, params: dict, result: "BridgeResult | None") -> str:
        """将 Mod 返回的 BridgeResult 格式化为 LLM 可理解的自然语言 tool_result。

        Args:
            action: 动作名称。
            params: 动作参数。
            result: BridgeResult 实例，或 None（超时）。

        Returns:
            描述执行结果的字符串，回传给 LLM 的 FC 多轮对话。
        """
        if result is None:
            return f"{action} timed out — no response from game"

        if not result.success:
            code = result.error_code or "UNKNOWN"
            msg = result.message or "no details"
            return f"{action} failed: [{code}] {msg}"

        data = result.data
        if not data:
            obs = result.observation or ""
            return f"{action} completed successfully" + (f" ({obs})" if obs else "")

        # Compound 动作的结构化结果
        parts = [f"{action} completed"]
        stopped = data.get("stopped_reason", "")

        if action == "water_crops":
            parts.append(f"watered={data.get('watered', 0)}, skipped={data.get('skipped', 0)}, refilled={data.get('refilled', 0)}, location={data.get('location', '?')}")
        elif action == "harvest_crops":
            parts.append(f"harvested={data.get('harvested', 0)}, replanted={data.get('replanted', 0)}, skipped={data.get('skipped', 0)}, location={data.get('location', '?')}")
        elif action == "plant_crops":
            parts.append(f"planted={data.get('planted', 0)}, skipped={data.get('skipped', 0)}, seed={data.get('seed', '?')}, location={data.get('location', '?')}")
        elif action == "chop_trees":
            parts.append(f"chopped={data.get('chopped', 0)}, skipped={data.get('skipped', 0)}, strikes={data.get('total_strikes', 0)}, location={data.get('location', '?')}")
        elif action == "mine_rocks":
            parts.append(f"mined={data.get('mined', 0)}, skipped={data.get('skipped', 0)}, strikes={data.get('total_strikes', 0)}, location={data.get('location', '?')}")
        elif action == "ship_items":
            parts.append(f"shipped={data.get('shipped_count', 0)}, income={data.get('total_value', 0)}g")
        elif action == "buy_items":
            parts.append(f"bought={data.get('bought_count', 0)}, spent={data.get('total_cost', 0)}g")
        elif action == "go_bed":
            parts.append(f"method={data.get('method', '?')}")
        elif action == "move_to":
            parts.append(f"arrived={data.get('arrived', '?')}")
        else:
            # 通用：输出 data 中所有键值对
            kv = [f"{k}={v}" for k, v in data.items() if k != "stopped_reason"]
            if kv:
                parts.append(", ".join(kv))

        if stopped:
            parts.append(f"stopped_reason={stopped}")

        # 附加 observation
        if result.observation:
            parts.append(f"obs: {result.observation}")

        return " | ".join(parts)

    @staticmethod
    def _format_observe_result(data: dict) -> str:
        """将 observe 结果格式化为 LLM 友好文本。"""
        lines: list[str] = []

        # 基础位置/时间信息
        world = data.get("world") or {}
        self_state = data.get("self") or {}
        pos = self_state.get("position") or {}
        loc = pos.get("location") or world.get("location_name") or "?"
        time_disp = (world.get("time") or {}).get("display") or str(world.get("time", "?"))
        lines.append(f"位置：{loc}  时间：{time_disp}")

        # entities: NPC + 动物 + 出口
        entities = data.get("entities") or []
        npcs: list[str] = []
        animals: list[str] = []
        exits_list: list[str] = []

        for ent in entities:
            etype = (ent.get("type") or "").lower()
            name = ent.get("name") or ""
            ent_id = ent.get("id") or ""
            epos = ent.get("position") or {}
            tx = epos.get("tile_x") or epos.get("x") or "?"
            ty = epos.get("tile_y") or epos.get("y") or "?"

            if etype == "npc":
                npcs.append(f"  - {name} 在 [{tx},{ty}] [npc:{name}]")
            elif etype == "animal":
                # 动物 ObserveId 来自 entity Id 字段中的 animal_ 部分
                # 格式示例: "Coop<guid>:animal_甜豆" → [animal:甜豆]
                animal_qualifier = name
                if ":animal_" in ent_id:
                    animal_qualifier = ent_id.split(":animal_", 1)[1]
                animals.append(f"  - {name} 在 [{tx},{ty}] [animal:{animal_qualifier}]")
            elif etype == "map_exit" or etype == "exit":
                target_map = (ent.get("properties") or {}).get("target_map") or name
                exits_list.append(f"  → {target_map} [exit:{target_map.lower()}]")

        if npcs:
            lines.append(f"当前地图 NPC（{len(npcs)} 位）：")
            lines.extend(npcs)
            lines.append("提示：先用 find_interactions() 获取 NPC 交互候选；先看 execution_gate/execution_warnings，ready 后再按 recommended_steps 调用 schema-valid 的 move_to/interact。")
        if animals:
            lines.append(f"动物（{len(animals)} 只）：")
            lines.extend(animals)
            lines.append("提示：先用 find_interactions() 获取动物交互候选；批量照料优先使用对应 L3 skill，单次交互先看 execution_gate，ready 后再按 recommended_steps 执行。")
        if exits_list:
            lines.append("可用出口：")
            lines.extend(exits_list)
            lines.append("提示：跨地图移动必须调用 go_to(map_id=..., x=..., y=..., arrival_mode=...)；不要使用 target 字符串或 ObserveId 作为参数。")

        # 追加原始 JSON 供 LLM 读取完整数据
        try:
            raw_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if len(raw_json) > 2000:
                raw_json = raw_json[:2000] + "...(truncated)"
            lines.append(f"raw_data: {raw_json}")
        except (TypeError, ValueError):
            pass

        return "\n".join(lines)

    @staticmethod
    def _format_query_result(action: str, result: "BridgeResult | None") -> str:
        """将 query 工具的结果格式化为 LLM 可读的 JSON 字符串。

        query 工具的核心价值就是返回数据，LLM 必须看到完整的查询结果。
        observe 结果额外嵌入 go_to hint 文案（ObserveId 格式）。
        """
        if result is None:
            return f"{action}: no response (timeout)"

        if not result.success:
            code = result.error_code or "UNKNOWN"
            msg = result.message or "no details"
            return f"{action} failed: [{code}] {msg}"

        # 将 data 完整序列化给 LLM（query 结果通常不大，完整回传）
        data = result.data
        if data:
            # observe 结果使用带 go_to hint 的友好格式
            if action == "observe" and isinstance(data, dict):
                return DeliberativeLoop._format_observe_result(data)
            try:
                data_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                # 截断过长的结果（防止单次 query 撑爆 token 预算）
                if len(data_str) > 3000:
                    data_str = data_str[:3000] + "...(truncated)"
                return f"{action}: {data_str}"
            except (TypeError, ValueError):
                return f"{action}: {str(data)[:3000]}"

        # data 为空但有 observations
        if result.observation:
            return f"{action}: {result.observation}"

        return f"{action}: (empty result)"

    def _handle_say(self, args: dict) -> None:
        """处理 say 动作。

        当 args 包含 emote 参数时，先发送 emote 命令（装饰性，失败不阻塞），
        再发送 say 命令。
        """
        message = args.get("message", "")
        if not message:
            return

        # 处理合并的 emote 参数（LLM 工具面设计：say 包含可选 emote）
        emote = args.get("emote", "")
        if emote:
            _VALID_EMOTES = {
                "happy", "sad", "angry", "heart",
                "exclamation", "question", "laugh", "thinking",
            }
            if emote in _VALID_EMOTES:
                try:
                    self._bridge.write_command("emote", {"type": emote})
                except Exception:
                    # emote 是装饰性动作，失败不阻塞 say
                    log.debug("say 附带 emote(%s) 发送失败，跳过", emote, exc_info=True)
            else:
                log.warning("say 附带非法 emote 值 '%s'，跳过", emote)

        if message:
            self._dispatcher.submit("say", {"message": message}, wait_result=False)
            if self._memory_system:
                try:
                    self._memory_system.append_conversation("agent", message)
                except Exception:
                    pass
            self._memory.remember(
                {},
                [{"type": "agent_replied", "content": message, "ts": time.time()}],
            )
            # 埋点
            events.agent_say(text=message)
            events.agent_dialogue(is_player_initiated=True, response_latency_ms=0)

    def _submit_visible_say(self, message: str, *, external_channel_key: str | None = None) -> None:
        self._handle_say({"message": message})

    def _try_route_direct_player_skill(self, trigger_msg: PlayerInput, route_key: str | None) -> bool:
        text = str(getattr(trigger_msg, "text", "") or "").strip()
        if not text:
            return False
        waypoint_args = self._extract_walk_route_args(text)
        if waypoint_args is not None:
            result_text = self._queue_skill(
                "skill-walk_route",
                waypoint_args,
                f"{type(trigger_msg).__name__}:{extract_situation(trigger_msg)}",
                self._long_priority_for_trigger(trigger_msg),
                needs_followup=True,
                route_key=route_key,
            )
            return "queued: job_id=" in result_text
        normalized_text = text.replace(" ", "")
        if any(token in normalized_text for token in _DIRECT_ROUTE_BLOCKERS):
            return False
        for tool_name, pattern in _DIRECT_SKILL_PATTERNS:
            if not pattern.search(normalized_text):
                continue
            result_text = self._queue_skill(
                tool_name,
                {},
                f"{type(trigger_msg).__name__}:{extract_situation(trigger_msg)}",
                self._long_priority_for_trigger(trigger_msg),
                needs_followup=True,
                route_key=route_key,
            )
            if "queued: job_id=" not in result_text:
                return False
            return True
        return False

    @staticmethod
    def _extract_walk_route_args(text: str) -> dict | None:
        matches = list(_WAYPOINT_ROUTE_PATTERN.finditer(text or ""))
        if len(matches) < 2:
            return None
        waypoints: list[dict[str, int | str]] = []
        for match in matches:
            map_id = str(match.group(1)).strip()
            x = int(match.group(2))
            y = int(match.group(3))
            waypoints.append({"map_id": map_id, "x": x, "y": y})
        if len(waypoints) < 2:
            return None
        return {"waypoints": waypoints}

    def _handle_remember(self, args: dict) -> None:
        """处理 remember 内部工具。"""
        mem_type = args.get("type", "note")
        content = args.get("content", "")
        ts = time.time()
        self._memory.remember({}, [{
            "type": mem_type,
            "content": content,
            "ts": ts,
        }])
        # 写入 MemorySystem（持久化到 current/events.jsonl）
        if self._memory_system is not None and content:
            try:
                self._memory_system.append_event({
                    "type": f"remember_{mem_type}",
                    "content": content,
                    "ts": ts,
                    "pinned": True,
                    "source": "llm_remember_tool",
                })
            except Exception:
                log.debug("MemorySystem.append_event (remember) 失败，跳过", exc_info=True)

    def _handle_wiki_tool(self, tool_name: str, args: dict) -> str:
        """处理 wiki 知识查询工具（search_wiki / read_wiki）。"""
        if self._knowledge is None:
            return "Wiki knowledge base is not available."
        try:
            result = self._knowledge.execute_tool(tool_name, args)
            log.info("Wiki tool %s(%s) → %d chars", tool_name, args, len(result))
            if len(result) > _TOOL_RESULT_MAX_CHARS:
                return result[: _TOOL_RESULT_MAX_CHARS - 24] + "...(truncated_for_context)"
            return result
        except Exception as e:
            log.warning("Wiki tool %s 执行失败: %s", tool_name, e)
            return f"Error querying wiki: {e}"

    # ── 游戏状态快照 ──────────────────────────────────────────────────────────

    def _capture_game_context(self) -> dict[str, Any] | None:
        """从 FileBridge 提取当前游戏状态摘要，用于附加到 /report 上报。

        复用 error_context.py 的提取逻辑模式，任何异常均被捕获不影响主流程。
        """
        try:
            gs = self._bridge.read_game_state()
            if gs is None:
                return None
            ctx: dict[str, Any] = {}
            player = gs.get("player")
            if isinstance(player, dict):
                pos = player.get("position") or {}
                if isinstance(pos, dict):
                    ctx["player_pos"] = {"x": pos.get("x"), "y": pos.get("y")}
                ctx["energy"] = player.get("energy")
            location = gs.get("location")
            if isinstance(location, dict):
                ctx["location"] = location.get("name")
            elif isinstance(location, str):
                ctx["location"] = location
            world = gs.get("world")
            if isinstance(world, dict):
                ctx["game_time"] = f"{world.get('season', '?')} {world.get('day', '?')}"
            return ctx if ctx else None
        except Exception:
            log.debug("_capture_game_context 异常，跳过", exc_info=True)
            return None

    # ── L3 技能执行 ──────────────────────────────────────────────────────────

    def _execute_skill(self, tool_name: str, args: dict) -> str:
        """同步执行 L3 Skill（用于 L3_BACKED_CORE_TOOLS 路由的 core_tool，如 observe）。

        skill: 前缀工具走 _queue_skill 异步队列；core_tool 走此同步路径，
        直接 asyncio.run SkillRunner.execute，返回格式化结果给 LLM。

        慢环在独立 daemon Thread 中运行（无 running event loop），asyncio.run 安全。
        """
        import asyncio

        skill_name = _normalize_skill_tool_name(tool_name)
        if not self._skill_runner or not self._skill_registry:
            return f"[{tool_name}] SkillRuntime 未加载"
        entry = self._skill_registry.get(skill_name)
        if entry is None:
            return f"[{tool_name}] 技能不存在: {skill_name}"
        try:
            result = asyncio.run(self._skill_runner.execute(entry, args))
        except Exception as e:
            log.exception("_execute_skill 异常: %s", tool_name)
            return f"[{tool_name}] 执行失败: {e}"
        return self._format_skill_result(skill_name, result)

    def _queue_skill(
        self,
        tool_name: str,
        args: dict,
        trigger_label: str,
        priority: int,
        *,
        needs_followup: bool = True,
        route_key: str | None = None,
    ) -> str:
        skill_name = _normalize_skill_tool_name(tool_name)
        if not self._skill_runner or not self._skill_registry:
            return f"[{tool_name}] SkillRuntime 未加载"
        entry = self._skill_registry.get(skill_name)
        if entry is None:
            return f"[{tool_name}] 技能不存在: {skill_name}"
        job_id = self._dispatcher.submit_skill(
            tool_name,
            args,
            skill_runner=self._skill_runner,
            skill_entry=entry,
            trigger_label=trigger_label,
            needs_followup=needs_followup,
            priority=priority,
            route_key=route_key,
        )
        if job_id is None:
            return f"{tool_name} rejected: async queue is full"
        return f"{tool_name} queued: job_id={job_id}"

    @staticmethod
    def _should_followup_after_action(
        action: str,
        trigger_msg: Any,
        *,
        is_last_async_for_trigger: bool = True,
    ) -> bool:
        if action in _ALWAYS_FOLLOWUP_ACTIONS:
            return True
        if isinstance(trigger_msg, PlayerInput):
            return is_last_async_for_trigger
        return False

    @staticmethod
    def _should_followup_after_skill(
        skill_name: str = "",
        trigger_msg: Any = None,
        *,
        is_last_async_for_trigger: bool = True,
    ) -> bool:
        normalized = _normalize_skill_tool_name(skill_name)
        if normalized in _NO_FOLLOWUP_SKILLS:
            return False
        if isinstance(trigger_msg, PlayerInput):
            return is_last_async_for_trigger
        return True

    def _format_dispatcher_query(self, args: dict) -> str:
        job_id = str(args.get("job_id", "") or "").strip()
        if job_id:
            payload = {"job": self._dispatcher.get_job(job_id)}
        else:
            snapshot = self._dispatcher.snapshot()
            payload = {
                "running": snapshot.get("running"),
                "fast_queue_size": snapshot.get("fast_queue_size"),
                "long_queue_size": snapshot.get("long_queue_size"),
                "jobs": snapshot.get("jobs"),
                "active_job": snapshot.get("active_job"),
                "pending_jobs": snapshot.get("pending_jobs"),
                "recent_jobs": snapshot.get("recent_jobs"),
            }
        return "action_jobs: " + json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    @staticmethod
    def _long_priority_for_trigger(trigger_msg: Any) -> int:
        return HIGH_LONG_PRIORITY if isinstance(trigger_msg, PlayerInput) else DEFAULT_LONG_PRIORITY

    @staticmethod
    def _format_skill_result(skill_name: str, result: dict) -> str:
        """将 L3 技能的 Result dict 格式化为 LLM 可读文本。

        Args:
            skill_name: 技能名称。
            result: SkillRunner.execute 返回的标准 Result dict。

        Returns:
            格式化后的文本字符串。
        """
        if not isinstance(result, dict):
            return f"[skill:{skill_name}] 完成"

        observation = result.get("observation", "")
        status = result.get("status", "unknown")
        data = result.get("data", {})

        parts = [f"[skill:{skill_name}]"]
        if observation:
            parts.append(observation)
        if skill_name == "find_interactions":
            actionable = DeliberativeLoop._format_find_interactions_actionable(data)
            if actionable:
                parts.append(actionable)
        elif skill_name in {"observe", "search_facts", "inspect"}:
            summary = DeliberativeLoop._format_l3_query_summary(data)
            if summary:
                parts.append(summary)
        if data and isinstance(data, dict):
            details = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
            if len(details) > _TOOL_RESULT_MAX_CHARS:
                details = details[:_TOOL_RESULT_MAX_CHARS] + "...[truncated]"
            parts.append(f"data={details}")
        if status == "partial":
            parts.append("[部分完成]")
        elif status == "failed":
            error = result.get("error", {})
            parts.append(f"[失败: {error.get('message', 'unknown')}]")

        return " ".join(parts)

    @staticmethod
    def _format_l3_query_summary(data: Any, *, limit: int = 5) -> str:
        if not isinstance(data, dict):
            return ""
        payload: dict[str, Any] = {}

        groups = data.get("groups")
        if isinstance(groups, list) and groups:
            compact_groups: list[dict[str, Any]] = []
            for group in groups[:limit]:
                if not isinstance(group, dict):
                    continue
                representatives = group.get("representatives")
                compact_reps: dict[str, Any] = {}
                if isinstance(representatives, dict):
                    ref_items = representatives.get("items")
                    if isinstance(ref_items, list):
                        compact_reps["items"] = ref_items[:5]
                        compact_reps["items_in_summary"] = len(compact_reps["items"])
                        if len(ref_items) > len(compact_reps["items"]):
                            compact_reps["items_available"] = len(ref_items)
                            compact_reps["items_truncated"] = True
                    for key in ("sort", "limit", "has_more", "next_query"):
                        if key in representatives:
                            compact_reps[key] = representatives[key]
                compact_group = {
                    key: group.get(key)
                    for key in ("fact_type", "label", "count", "summary")
                    if key in group
                }
                if compact_reps:
                    compact_group["representatives"] = compact_reps
                compact_groups.append({
                    key: value
                    for key, value in compact_group.items()
                    if value not in ({}, [], None)
                })
            if compact_groups:
                payload["groups"] = compact_groups
                payload["group_count"] = len(groups)
                payload["groups_in_summary"] = len(compact_groups)
                if len(groups) > len(compact_groups):
                    payload["groups_truncated"] = True

        items = data.get("items")
        if isinstance(items, list) and items:
            compact_items: list[dict[str, Any]] = []
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                compact = {
                    key: item.get(key)
                    for key in ("ref_id", "type", "label", "summary", "resolved", "status", "position", "distance", "slot")
                    if key in item
                }
                fact = item.get("fact")
                if isinstance(fact, dict):
                    compact_fact = DeliberativeLoop._compact_query_fact(fact)
                    if compact_fact:
                        compact["fact"] = compact_fact
                compact_items.append({
                    key: value
                    for key, value in compact.items()
                    if value not in ({}, [], None, "")
                })
            if compact_items:
                payload["items"] = compact_items
                payload["items_in_summary"] = len(compact_items)
                if len(items) > len(compact_items):
                    payload["items_available"] = len(items)
                    payload["items_truncated"] = True

        schema = data.get("schema")
        if isinstance(schema, dict):
            compact_schema = {
                key: schema.get(key)
                for key in ("fact_type", "label", "default_sort", "sort_fields", "aggregate_fields")
                if key in schema
            }
            filter_fields = schema.get("filter_fields")
            if isinstance(filter_fields, list):
                compact_schema["filter_fields"] = filter_fields[:30]
                if len(filter_fields) > 30:
                    compact_schema["filter_fields_truncated"] = True
            operators = schema.get("operators")
            if isinstance(operators, dict):
                compact_schema["operators"] = {
                    key: operators[key]
                    for key in list(operators)[:20]
                }
                if len(operators) > 20:
                    compact_schema["operators_truncated"] = True
            payload["schema"] = {
                key: value
                for key, value in compact_schema.items()
                if value not in ({}, [], None)
            }

        for key in (
            "scope",
            "fact_type",
            "detail",
            "total",
            "limit",
            "offset",
            "next_offset",
            "has_more",
            "sort",
            "filters",
            "requested_refs",
            "next_query",
        ):
            if key in data:
                payload[key] = data[key]

        if not payload:
            return ""
        details = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        return f"query_summary={details}"

    @staticmethod
    def _compact_query_fact(fact: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, value in fact.items():
            if len(compact) >= limit:
                compact["truncated"] = True
                break
            if value in ({}, [], None, ""):
                continue
            if isinstance(value, (str, int, float, bool)):
                compact[key] = value
            elif isinstance(value, dict):
                shallow = {
                    nested_key: nested_value
                    for nested_key, nested_value in value.items()
                    if isinstance(nested_value, (str, int, float, bool)) and nested_value not in (None, "")
                }
                if shallow:
                    compact[key] = shallow
        return compact

    @staticmethod
    def _format_find_interactions_actionable(data: Any, *, limit: int = 5) -> str:
        if not isinstance(data, dict):
            return ""
        raw_items = data.get("items") or data.get("candidates") or []
        if not isinstance(raw_items, list):
            return ""
        items: list[dict[str, Any]] = []
        for raw in raw_items[:limit]:
            if not isinstance(raw, dict):
                continue
            steps = raw.get("recommended_steps")
            if not isinstance(steps, list) or not steps:
                continue
            compact_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                tool = step.get("tool")
                args = step.get("args")
                if isinstance(tool, str) and isinstance(args, dict):
                    compact_steps.append({"tool": tool, "args": args})
            if compact_steps:
                execution_gate = raw.get("execution_gate")
                execution_warnings = raw.get("execution_warnings")
                compact_item = {
                    "ref_id": raw.get("ref_id"),
                    "action": raw.get("action"),
                    "reason": raw.get("reason"),
                }
                tool_requirement = raw.get("tool_requirement")
                if isinstance(tool_requirement, dict):
                    compact_tool_requirement = DeliberativeLoop._compact_interaction_tool_requirement(tool_requirement)
                    if compact_tool_requirement:
                        compact_item["tool_requirement"] = compact_tool_requirement
                if isinstance(execution_gate, dict):
                    compact_item["execution_gate"] = {
                        key: execution_gate.get(key)
                        for key in (
                            "status",
                            "recommended_steps_policy",
                            "warning_codes",
                        )
                        if key in execution_gate
                    }
                preconditions = raw.get("preconditions")
                if isinstance(preconditions, list) and preconditions:
                    compact_item["preconditions"] = [
                        str(condition)
                        for condition in preconditions[:8]
                        if condition
                    ]
                if isinstance(execution_warnings, list) and execution_warnings:
                    compact_item["execution_warnings"] = [
                        DeliberativeLoop._compact_interaction_warning(warning)
                        for warning in execution_warnings[:3]
                        if isinstance(warning, dict)
                    ]
                compact_item["recommended_steps"] = compact_steps
                items.append({
                    key: value
                    for key, value in compact_item.items()
                    if value not in ({}, [], None)
                })
        if not items:
            return ""
        payload: Any = items
        next_query = data.get("next_query")
        if isinstance(next_query, dict):
            payload = {"items": items, "next_query": next_query}
        details = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        return f"actionable={details}"

    @staticmethod
    def _compact_interaction_tool_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: requirement.get(key)
            for key in ("kind", "category", "item_name", "equip_tool_args")
            if key in requirement
        }
        matching_items = requirement.get("matching_inventory_items")
        if isinstance(matching_items, list) and matching_items:
            compact["matching_inventory_items"] = [
                DeliberativeLoop._compact_inventory_tool_option(item)
                for item in matching_items[:3]
                if isinstance(item, dict)
            ]
        return {key: value for key, value in compact.items() if value not in ({}, [], None)}

    @staticmethod
    def _compact_interaction_warning(warning: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: warning.get(key)
            for key in ("code", "message", "required_tool_category", "suggested_next")
            if key in warning
        }
        matching_items = warning.get("matching_inventory_items")
        if isinstance(matching_items, list) and matching_items:
            compact["matching_inventory_items"] = [
                DeliberativeLoop._compact_inventory_tool_option(item)
                for item in matching_items[:3]
                if isinstance(item, dict)
            ]
        return {key: value for key, value in compact.items() if value not in ({}, [], None)}

    @staticmethod
    def _compact_inventory_tool_option(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in ("slot", "item_id", "display_name", "equip_args")
            if key in item
        }

    # ── Fallback 策略 ────────────────────────────────────────────────────────

    def _execute_fallback(self, trigger_msg: Any) -> None:
        """LLM 调用失败或未返回 tool_calls 时的兜底策略。

        玩家直接输入才发送安抚消息；TaskCompleted 等内部触发只记录，
        避免自动事件在游戏聊天里刷“让我想想...”。
        """
        route_key = self._external_channel_key(trigger_msg) or _route_key_from_trigger(trigger_msg)
        if not isinstance(trigger_msg, PlayerInput) and route_key is None:
            log.warning("Fallback: 内部触发不发送可见兜底消息 trigger=%s", type(trigger_msg).__name__)
            self._memory.remember({}, [{
                "type": "fallback_suppressed",
                "content": {"trigger": type(trigger_msg).__name__},
                "ts": time.time(),
            }])
            return
        log.warning("Fallback: 发送兜底消息")
        self._submit_visible_say(
            "让我想想...",
            external_channel_key=route_key,
        )

    def _execute_no_tool_call_fallback(self, trigger_msg: Any) -> None:
        """Report malformed model responses without changing generic fallback."""
        route_key = self._external_channel_key(trigger_msg) or _route_key_from_trigger(trigger_msg)
        if not isinstance(trigger_msg, PlayerInput) and route_key is None:
            log.warning("No-tool-call fallback: 内部触发不发送可见消息 trigger=%s", type(trigger_msg).__name__)
            self._memory.remember({}, [{
                "type": "fallback_suppressed",
                "content": {"trigger": type(trigger_msg).__name__, "reason": "no_tool_call"},
                "ts": time.time(),
            }])
            return
        log.warning("No-tool-call fallback: 发送模型响应错误消息")
        self._submit_visible_say(
            "模型响应错误（未进行 tool call）",
            external_channel_key=route_key,
        )

    # ── 辅助方法（弃用，保留兼容）─────────────────────────────────────────────

    def _send_replace_bt(self, bt_json: dict, correlation_id: str | None = None) -> None:
        """[弃用] 构造 ReplaceBehaviorTree 消息并发送到快环。FC 模式不使用。"""
        msg = ReplaceBehaviorTree(
            bt_json=bt_json,
            priority=0,
            ttl_seconds=30.0,
            correlation_id=correlation_id,
        )
        self._bus.send_to_fast(msg)

    @staticmethod
    def _get_correlation_id(msg: Any) -> str | None:
        """安全提取消息的 correlation_id 属性。"""
        return getattr(msg, "correlation_id", None)

    @staticmethod
    def _action_plan_to_bt(actions: list[dict]) -> dict:
        """[弃用] action_plan 列表 -> sequence BT JSON。FC 模式不使用。"""
        children = []
        for i, act in enumerate(actions):
            children.append({
                "type": "action",
                "name": f"step_{i}_{act['action']}",
                "call": act["action"],
                "params": act.get("params", {}),
            })
        return {"type": "sequence", "name": "LLMPlan", "children": children}
