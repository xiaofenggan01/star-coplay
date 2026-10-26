"""
main — StarCoPlay Agent V2 主入口。

FC 模式架构：
    事件转发（EventRelay）：轮询 events.jsonl，转发 PlayerInput 给慢环
    决策环（DeliberativeLoop）：LLM Function Calling 驱动，直接执行 tool_calls

两环通过 MessageBus 通信：
    EventRelay → DecisionLoop：PlayerInput
    DecisionLoop → WSBridge：write_command（直接执行动作）
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            pass

import json
import logging
import os
import platform as py_platform
import signal
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── 路径基准（兼容源码和 PyInstaller 编译模式）──────────────────────────────
# BUNDLE_DIR: 只读数据（config, wiki, prompts, skills/builtin）的根目录
# APP_DIR:    可写数据（.env, data/, skills/learned）的根目录
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent.resolve()
    BUNDLE_DIR = Path(sys._MEIPASS) / "agent" if hasattr(sys, '_MEIPASS') else APP_DIR
else:
    APP_DIR = Path(__file__).parent.resolve()
    BUNDLE_DIR = APP_DIR


# .env 加载（标准库实现，无需额外依赖）
def _load_dotenv(env_path: Path) -> None:
    """从 .env 文件加载环境变量（不覆盖已有值）。"""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value

_load_dotenv(APP_DIR / ".env")

import llm_client as _llm_client
from bridge.ws_bridge import WSBridge
from game_launcher import GameLauncher, iter_starco_mod_dir_candidates
from bt_engine.game_state_cache import GameStateCache
from bt_engine.message_bus import MessageBus
from game_monitor import EventRelay
from memory.memory_system import MemorySystem
from memory.working_memory import WorkingMemory
from knowledge.game_knowledge import GameKnowledge
from slow_loop.deliberative_loop import DeliberativeLoop
from messages.types import EscalateRequest, PlayerInput
from report.error_context import capture_error_context
from report.telemetry import Telemetry, clear_current, set_current
from report import events


if os.environ.get("STARCOPLAY_STRUCTURED_LOGGING") != "1":
    # 配置日志：stderr（与 stdout 合并管道时供 Setup UI 读 UTF-8）+ 文件
    _log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    _stdout_handler = logging.StreamHandler(sys.stderr)
    _stdout_handler.setLevel(logging.INFO)
    _stdout_handler.setFormatter(_log_formatter)

    # 文件 handler — 主日志（INFO+，决策+动作+事件，所有模块汇总）
    _data_logs = APP_DIR / "data" / "logs"
    _data_logs.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(_data_logs / "agent.log", encoding="utf-8")
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(_log_formatter)

    # 按模块名前缀过滤的 Filter
    class _ModuleFilter(logging.Filter):
        def __init__(self, prefix: str):
            super().__init__()
            self.prefix = prefix

        def filter(self, record: logging.LogRecord) -> bool:
            return record.name.startswith(self.prefix)

    # LLM 日志（DEBUG+，仅 llm_client 模块）
    _llm_handler = logging.FileHandler(_data_logs / "llm.log", encoding="utf-8")
    _llm_handler.setLevel(logging.DEBUG)
    _llm_handler.setFormatter(_log_formatter)
    _llm_handler.addFilter(_ModuleFilter("llm_client"))

    # Memory 日志（DEBUG+，仅 memory.* 模块）
    _memory_handler = logging.FileHandler(_data_logs / "memory.log", encoding="utf-8")
    _memory_handler.setLevel(logging.DEBUG)
    _memory_handler.setFormatter(_log_formatter)
    _memory_handler.addFilter(_ModuleFilter("memory"))

    # Bridge 日志（DEBUG+，仅 bridge.* 模块）
    _bridge_handler = logging.FileHandler(_data_logs / "bridge.log", encoding="utf-8")
    _bridge_handler.setLevel(logging.DEBUG)
    _bridge_handler.setFormatter(_log_formatter)
    _bridge_handler.addFilter(_ModuleFilter("bridge"))

    # 根 logger 配置
    _root_logger = logging.getLogger()
    _root_logger.setLevel(logging.DEBUG)
    _root_logger.addHandler(_stdout_handler)
    _root_logger.addHandler(_file_handler)
    _root_logger.addHandler(_llm_handler)
    _root_logger.addHandler(_memory_handler)
    _root_logger.addHandler(_bridge_handler)

# 第三方库日志降噪：只显示 WARNING 及以上
logging.getLogger("fsevents").setLevel(logging.WARNING)
logging.getLogger("watchdog").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
# openai._base_client 的 429 日志级别是 DEBUG（内部重试机制），
# 但我们需要在 WARNING 级别看到它，故单独提升到 INFO
logging.getLogger("openai._base_client").setLevel(logging.INFO)

log = logging.getLogger("main")

_version_cache: str | None = None

def get_version() -> str:
    """读取 version.json 返回版本号字符串。"""
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    version_file = BUNDLE_DIR / "version.json"
    try:
        import json as _json
        data = _json.loads(version_file.read_text(encoding="utf-8"))
        _version_cache = data.get("version", "unknown")
    except Exception:
        _version_cache = "unknown"
    return _version_cache


@dataclass
class AgentHandle:
    """Handle返回 build_agent：封装可停止的 Agent 运行时。"""

    deliberative: DeliberativeLoop
    event_relay: EventRelay
    telemetry: Telemetry | None
    bridge: Any
    message_bus: MessageBus

    def submit_external_text(
        self,
        *,
        text: str,
        source: str = "roadshow",
        input_id: str | None = None,
        route_key: str | None = None,
        sender_label: str | None = None,
    ) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        display_text = clean
        if source == "roadshow":
            display_text = f"[roadshow] {clean}"
        self.message_bus.send_to_slow(
            PlayerInput(
                text=display_text,
                source=source or "external",
                input_id=input_id,
                route_key=route_key,
                sender_label=sender_label,
            ),
            priority=2,
        )
        log.info(
            "external text submitted source=%s input_id=%s len=%d",
            source,
            input_id or "",
            len(clean),
        )

    def stop(self) -> None:
        try:
            self.event_relay.stop()
        except Exception:
            log.debug("event_relay.stop failed", exc_info=True)
        try:
            self.deliberative.stop()
        except Exception:
            log.debug("deliberative.stop failed", exc_info=True)
        if self.telemetry is not None:
            try:
                self.telemetry.shutdown()
                clear_current()
            except Exception:
                log.debug("telemetry.shutdown failed", exc_info=True)


def _apply_llm_overrides(overrides: dict[str, str] | None) -> None:
    """把平台 System LLM relay 端点与 key 写入 llm_client 会话覆盖。

    当多会话并存时"最后一个 build_agent 生效"——MVP 默认单会话/单进程，
    需要进程隔离时由上层 spawn 子进程。
    """
    if not overrides:
        return
    base = (overrides.get("api_base") or "").strip()
    key = (overrides.get("api_key") or "").strip()
    _llm_client.set_session_overrides(api_base=base, api_key=key)


def build_agent(
    bridge: Any,
    config: dict,
    *,
    llm_overrides: dict[str, str] | None = None,
    start_telemetry: bool = True,
) -> AgentHandle:
    """按给定 bridge 装配 Agent 运行时并启动 DeliberativeLoop / EventRelay。

    参数:
        bridge: FileBridge 或 WSBridge 实例；需提供 read_game_state/state_path 等接口。
        config: 已加载的配置 dict（来自 load_config() 或定制）。
        llm_overrides: {"api_base": ..., "api_key": ...}，强制 LLM 走平台 System LLM relay。
        start_telemetry: 是否启动 telemetry（Gateway 场景下一般由 Gateway 侧内写）。

    返回 AgentHandle，可调用 .stop() 优雅停止。
    """
    _apply_llm_overrides(llm_overrides)

    bus = MessageBus()

    memory = WorkingMemory(
        max_events=config.get("memory", {}).get("max_events", 200),
    )

    event_relay = EventRelay(
        message_bus=bus,
        bridge=bridge,
        target_player_id=getattr(bridge, "target_player_id", None),
    )
    state_path = getattr(bridge, "state_path", None)
    game_state_cache = GameStateCache(state_path=state_path) if state_path else None

    telemetry: Telemetry | None = None
    tel_cfg = config.get("telemetry", {})
    if start_telemetry and tel_cfg.get("enabled"):
        tok = (tel_cfg.get("platform_token") or os.environ.get("STARCOPLAY_PLATFORM_TOKEN", "")).strip()
        url = (tel_cfg.get("events_url") or os.environ.get("STARCOPLAY_EVENTS_URL", "")).strip()
        ak = (
            tel_cfg.get("events_api_key")
            or os.environ.get("STARCOPLAY_EVENTS_APIKEY", "")
        ).strip() or None
        backend_name = (tel_cfg.get("backend") or "http").strip().lower()
        if tok:
            from report.telemetry_backends import build_backend_from_config
            spill_rel = tel_cfg.get("spill_dir", "data/telemetry")
            backend_cfg = {
                "backend": backend_name,
                "events_url": url,
                "events_api_key": ak,
                "dsn": (tel_cfg.get("dsn") or os.environ.get("DATABASE_URL") or "").strip() or None,
                "supabase_url": (tel_cfg.get("supabase_url") or os.environ.get("SUPABASE_URL") or "").strip() or None,
                "supabase_service_key": (
                    tel_cfg.get("supabase_service_key")
                    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
                    or ""
                ).strip() or None,
            }
            if backend_name == "http" and not url:
                log.warning("telemetry backend=http 但 events_url 未配置，已跳过")
            else:
                backend = build_backend_from_config(backend_cfg)
                telemetry = Telemetry(
                    platform_token=tok,
                    events_url=url,
                    api_key=ak,
                    flush_interval_s=float(tel_cfg.get("flush_interval_s", 30.0)),
                    heartbeat_interval_s=float(tel_cfg.get("heartbeat_interval_s", 300.0)),
                    spill_dir=APP_DIR / spill_rel,
                    include_chat=bool(tel_cfg.get("include_chat", True)),
                    backend=backend,
                )
        else:
            log.warning("telemetry.enabled 但未配置 platform_token，已跳过")

    mem_cfg = config.get("memory", {})
    memory_root_cfg = mem_cfg.get("memory_root", "data/memory/default_save")
    _mr = Path(memory_root_cfg)
    memory_root_fs = _mr if _mr.is_absolute() else (APP_DIR / _mr)
    default_model = _llm_client.get_default_model()
    summary_model = default_model or mem_cfg.get("summary_model", "")
    facts_model = default_model or mem_cfg.get("facts_model", "")
    if not summary_model:
        log.error("summary_model 未配置，MemorySystem 摘要功能将不可用。")
    if not facts_model:
        log.error("facts_model 未配置，MemorySystem Facts 更新功能将不可用。")
    memory_system: MemorySystem | None = None
    try:
        memory_system = MemorySystem(
            memory_root=memory_root_fs,
            max_events=mem_cfg.get("max_events", 200),
            max_pinned=mem_cfg.get("max_pinned", 50),
            max_conversation_turns=mem_cfg.get("max_conversation_turns", 20),
            summary_model=summary_model,
            facts_model=facts_model,
        )
        log.info("MemorySystem 初始化成功: %s", memory_system.root)
    except Exception:
        log.warning("MemorySystem 初始化失败，将以无记忆模式运行", exc_info=True)
        memory_system = None

    knowledge_cfg = config.get("knowledge", {})
    wiki_dir_str = (
        os.environ.get("WIKI_DATA_PATH")
        or knowledge_cfg.get("wiki_dir", "")
    )
    game_knowledge: GameKnowledge | None = None
    if wiki_dir_str:
        wiki_path = Path(wiki_dir_str)
        if not wiki_path.is_absolute():
            wiki_path = BUNDLE_DIR / wiki_path
    else:
        wiki_path = BUNDLE_DIR / "knowledge" / "wiki"
    if wiki_path.exists():
        game_knowledge = GameKnowledge(wiki_dir=wiki_path)
        loaded = game_knowledge.load()
        log.info("GameKnowledge 已加载: %d 页面 (路径: %s)", loaded, wiki_path)
    else:
        log.info("Wiki 数据目录不存在 (%s)，GameKnowledge 未启用", wiki_path)

    gs = bridge.read_game_state() or {}
    player_info = gs.get("player") or {}
    agent_name = player_info.get("name") or "AI"
    if "slow_loop" not in config:
        config["slow_loop"] = {}
    # Agentee persona display_name takes priority over game character name
    if not config["slow_loop"].get("agentee_display_name"):
        config["slow_loop"]["agent_name"] = agent_name
        log.info("从 game_state 读取 AI 角色名字: %s", agent_name)
    else:
        log.info(
            "agentee persona 优先: display_name=%s (game_state name=%s)",
            config["slow_loop"]["agentee_display_name"],
            agent_name,
        )

    deliberative = DeliberativeLoop(
        message_bus=bus,
        bridge=bridge,
        working_memory=memory,
        config=config,
        memory_system=memory_system,
        game_knowledge=game_knowledge,
    )

    if telemetry is not None:
        set_current(telemetry)
        telemetry.start()
        start_data = _session_start_data(bridge)
        events.session_start(is_first_session=telemetry.is_first_session, **start_data)
        events.onboard_agent_connected()
        telemetry.mark_session_started()
        if game_state_cache is not None:
            def _hb_snapshot() -> dict:
                return capture_error_context(game_state_cache)
            telemetry.set_heartbeat_snapshot_fn(_hb_snapshot)

    event_relay.start()
    deliberative.start()
    bus.send_to_slow(
        EscalateRequest(
            reason="agent_connected",
            correlation_id=str(uuid.uuid4()),
            game_state=bridge.read_game_state() or {},
        ),
        priority=1,
    )

    log.info("Agent build_agent 完成（EventRelay + DeliberativeLoop 已启动）")

    return AgentHandle(
        deliberative=deliberative,
        event_relay=event_relay,
        telemetry=telemetry,
        bridge=bridge,
        message_bus=bus,
    )


def _session_start_data(bridge: Any) -> dict[str, Any]:
    gs = bridge.read_game_state() or {}
    world = gs.get("world") or {}
    try:
        info = bridge.load_adapter_info()
    except Exception:
        info = {}
    return {
        "report_kind": "regular",
        "agent_version": get_version(),
        "os": f"{py_platform.system()}-{py_platform.release()}",
        "game_version": str(gs.get("game_version") or ""),
        "mod_version": str(info.get("mod_version") or info.get("adapter_version") or ""),
        "world_day": world.get("day"),
        "world_season": world.get("season"),
    }


def load_config() -> dict:
    """加载默认配置文件。

    Returns:
        配置 dict（来自 config/default.toml）。
    """
    config_path = BUNDLE_DIR / "config" / "default.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    log.warning("config/default.toml not found, using empty config")
    return {}


def detect_bridge_dir() -> str | None:
    """在 Mods/StarCoPlay 下查找 bridge 或 bridge_<玩家ID> 目录。

    Mod 目录候选与 game_launcher 一致（注册表、libraryfolders、GAME_PATH 等）。
    多个 bridge_* 时：若环境变量 TARGET_PLAYER_ID 已设置且存在对应目录则优先；
    否则选最近修改的目录（多人联机时最近活跃存档）。
    """
    found_bridges: list[Path] = []
    for mod_dir in iter_starco_mod_dir_candidates():
        if not mod_dir.is_dir():
            continue
        for bridge_dir in mod_dir.glob("bridge*"):
            if bridge_dir.is_dir():
                found_bridges.append(bridge_dir)
    if not found_bridges:
        log.debug("未找到 bridge 目录（Mod 未运行或 GAME_PATH 未配置）")
        return None
    pid_str = os.environ.get("TARGET_PLAYER_ID", "").strip()
    if pid_str.isdigit():
        want = next((b for b in found_bridges if b.name == f"bridge_{pid_str}"), None)
        if want is not None:
            log.info("TARGET_PLAYER_ID=%s，选用 bridge: %s", pid_str, want)
            return str(want)
    if len(found_bridges) == 1:
        log.info("自动探测到 bridge 目录: %s", found_bridges[0])
        return str(found_bridges[0])
    best = max(found_bridges, key=lambda p: p.stat().st_mtime)
    log.info(
        "检测到 %d 个 bridge 目录，自动选择最近活跃的: %s",
        len(found_bridges),
        best,
    )
    return str(best)


if __name__ == "__main__":
    print(
        "legacy FileBridge 调试入口（main.py:main）已于 R1 清理中删除。\n"
        "生产入口请使用 session_runner.py（由 platform_ws/workers/session_subprocess.py spawn）。",
        file=sys.stderr,
    )
