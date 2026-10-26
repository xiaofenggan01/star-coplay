from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from report.telemetry_backends import HttpBackend, TelemetryAuthError, TelemetryBackend
from text_sanitize import sanitize_for_utf8_json, utf8_safe_json_line

log = logging.getLogger(__name__)

_current: Telemetry | None = None

_stats_lock = threading.Lock()
_session_mono_start: float | None = None
_llm_call_count = 0
_action_count = 0

REMOTE_BLOCKED_EVENT_TYPES = {
    "ai.llm_call",
    "ai.error",
    "ai.action",
}
REMOTE_BLOCKED_EVENT_PREFIXES = ("error.",)


def reset_session_counters() -> None:
    global _session_mono_start, _llm_call_count, _action_count
    with _stats_lock:
        _session_mono_start = time.monotonic()
        _llm_call_count = 0
        _action_count = 0


def bump_llm_call() -> None:
    global _llm_call_count
    with _stats_lock:
        _llm_call_count += 1


def bump_action() -> None:
    global _action_count
    with _stats_lock:
        _action_count += 1


def session_end_payload() -> dict[str, Any]:
    with _stats_lock:
        dur_min = 0.0
        if _session_mono_start is not None:
            dur_min = (time.monotonic() - _session_mono_start) / 60.0
        return {
            "report_kind": "regular",
            "duration_min": round(dur_min, 4),
            "total_actions": _action_count,
            "total_llm_calls": _llm_call_count,
        }


def set_current(t: Telemetry | None) -> None:
    global _current
    _current = t


def clear_current() -> None:
    global _current
    _current = None


def track(event_type: str, data: dict[str, Any]) -> None:
    if event_type == "ai.llm_call":
        bump_llm_call()
    elif event_type == "ai.action":
        bump_action()
    t = _current  # 局部快照，避免竞态
    if t is not None:
        t.track(event_type, data)


def should_send_remote_event(event_type: str) -> bool:
    """Keep runtime diagnostics out of Supabase-backed telemetry."""
    if event_type in REMOTE_BLOCKED_EVENT_TYPES:
        return False
    return not any(event_type.startswith(prefix) for prefix in REMOTE_BLOCKED_EVENT_PREFIXES)


def llm_call_data(
    model: str,
    response: Any,
    latency_ms: float,
    trigger: str,
) -> dict[str, Any]:
    u = getattr(response, "usage", None)
    tin = tout = 0
    if isinstance(u, dict):
        tin = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        tout = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
    elif u is not None:
        tin = int(
            getattr(u, "prompt_tokens", None)
            or getattr(u, "input_tokens", None)
            or 0
        )
        tout = int(
            getattr(u, "completion_tokens", None)
            or getattr(u, "output_tokens", None)
            or 0
        )
    return {
        "report_kind": "regular",
        "model": model,
        "tokens_in": tin,
        "tokens_out": tout,
        "latency_ms": int(latency_ms),
        "trigger": trigger,
    }


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Telemetry:
    def __init__(
        self,
        *,
        platform_token: str,
        events_url: str = "",
        api_key: str | None = None,
        session_id: str | None = None,
        flush_interval_s: float = 30.0,
        heartbeat_interval_s: float = 300.0,
        spill_dir: Path | None = None,
        include_chat: bool = True,
        backend: TelemetryBackend | None = None,
    ) -> None:
        self._token = platform_token
        self._api_key = (api_key or "").strip() or None
        self._url = events_url.rstrip("/")
        self._backend: TelemetryBackend = backend or HttpBackend(self._url, self._api_key)
        self._session_id = session_id or str(uuid.uuid4())
        self._flush_interval_s = flush_interval_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._spill_dir = spill_dir or Path.cwd()
        self._include_chat = include_chat
        self._q: Queue[dict[str, Any]] = Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_heartbeat_at = time.monotonic() + heartbeat_interval_s
        self._spill_only_reason: str | None = None

        # ── 增量计数器（heartbeat 时读取并重置）──────────────────────────────
        self._delta_lock = threading.Lock()
        self._delta_actions: int = 0
        self._delta_llm_calls: int = 0
        self._delta_player_messages: int = 0
        self._delta_errors: int = 0
        self._delta_decisions: int = 0
        self._delta_tokens_in: int = 0
        self._delta_tokens_out: int = 0

        # ── 首次体验（onboard）追踪 ─────────────────────────────────────────
        self._onboard_flags: dict[str, bool] = {}
        self._first_session_path = Path(spill_dir or ".") / ".first_session_done"

        # ── heartbeat 回调（可选，由 main.py 注入游戏状态快照）──────────────
        self._heartbeat_snapshot_fn: Callable[[], dict[str, Any]] | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    def track(self, event_type: str, data: dict[str, Any]) -> None:
        # 增量计数器自增
        with self._delta_lock:
            if event_type == "ai.action":
                self._delta_actions += 1
            elif event_type == "ai.llm_call":
                self._delta_llm_calls += 1
                self._delta_tokens_in += int(data.get("tokens_in") or 0)
                self._delta_tokens_out += int(data.get("tokens_out") or 0)
            elif event_type == "ai.decision":
                self._delta_decisions += 1
            elif event_type == "player.message":
                self._delta_player_messages += 1
            elif event_type.startswith("error.") or event_type == "ai.error":
                self._delta_errors += 1

        if not should_send_remote_event(event_type):
            log.debug("telemetry remote event suppressed by policy: %s", event_type)
            return

        self._q.put(
            {
                "type": event_type,
                "ts": _utc_iso(),
                "session_id": self._session_id,
                "data": data,
                "client_event_id": str(uuid.uuid4()),
            }
        )

    # ── 首次体验（onboard）追踪 ──────────────────────────────────────────

    def track_onboard(self, step: str, data: dict[str, Any] | None = None) -> None:
        """追踪首次体验漏斗，每个步骤只触发一次。"""
        if step in self._onboard_flags:
            return
        self._onboard_flags[step] = True
        self.track(f"onboard.{step}", data or {})

    @property
    def is_first_session(self) -> bool:
        """当前是否为首次会话（.first_session_done 文件不存在）。"""
        return not self._first_session_path.exists()

    def mark_session_started(self) -> None:
        """标记首次会话已完成。"""
        try:
            self._first_session_path.parent.mkdir(parents=True, exist_ok=True)
            self._first_session_path.touch(exist_ok=True)
        except OSError:
            pass

    def set_heartbeat_snapshot_fn(self, fn: Callable[[], dict[str, Any]]) -> None:
        """注入 heartbeat 时获取游戏状态快照的回调。"""
        self._heartbeat_snapshot_fn = fn

    def flush(self) -> None:
        batch: list[dict[str, Any]] = []
        while True:
            try:
                batch.append(self._q.get_nowait())
            except Empty:
                break
        if not batch:
            return
        if self._spill_only_reason:
            self._spill_batch(batch)
            return
        try:
            self._backend.write(batch, token=self._token)
            return
        except TelemetryAuthError as exc:
            self._spill_only_reason = str(exc)
            log.warning(
                "telemetry disabled remote writes: %s; spilling %d events locally",
                exc, len(batch),
            )
            self._spill_batch(batch)
        except Exception as exc:
            log.warning(
                "telemetry backend %s failed: %s: %s; spilling %d events",
                type(self._backend).__name__, type(exc).__name__, exc, len(batch),
            )
            log.debug("telemetry flush traceback", exc_info=True)
            self._spill_batch(batch)

    def _spill_batch(self, events: list[dict[str, Any]]) -> None:
        self._spill_dir.mkdir(parents=True, exist_ok=True)
        name = f"telemetry_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
        path = self._spill_dir / name
        line = utf8_safe_json_line({"events": sanitize_for_utf8_json(events)})
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

    def _maybe_heartbeat(self) -> None:
        now = time.monotonic()
        if now < self._next_heartbeat_at:
            return
        self._next_heartbeat_at = now + self._heartbeat_interval_s

        # 读取并重置增量计数器
        with self._delta_lock:
            delta = {
                "delta_actions": self._delta_actions,
                "delta_llm_calls": self._delta_llm_calls,
                "delta_player_messages": self._delta_player_messages,
                "delta_errors": self._delta_errors,
                "delta_decisions": self._delta_decisions,
                "delta_tokens_in": self._delta_tokens_in,
                "delta_tokens_out": self._delta_tokens_out,
            }
            self._delta_actions = 0
            self._delta_llm_calls = 0
            self._delta_player_messages = 0
            self._delta_errors = 0
            self._delta_decisions = 0
            self._delta_tokens_in = 0
            self._delta_tokens_out = 0

        hb_data: dict[str, Any] = {
            "report_kind": "regular",
            "timestamp": _utc_iso(),
            **delta,
        }

        # 附加游戏状态快照（如果有回调）
        if self._heartbeat_snapshot_fn is not None:
            try:
                snapshot = self._heartbeat_snapshot_fn()
                if snapshot:
                    hb_data["game_snapshot"] = snapshot
            except Exception:
                pass

        self.track("session.heartbeat", hb_data)

    def _run(self) -> None:
        while not self._stop.wait(timeout=self._flush_interval_s):
            self._maybe_heartbeat()
            self.flush()
        self._maybe_heartbeat()
        self.flush()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        reset_session_counters()
        self._stop.clear()
        self._next_heartbeat_at = time.monotonic() + self._heartbeat_interval_s
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.track("session.end", session_end_payload())
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._flush_interval_s + 5)
            self._thread = None
        try:
            self._backend.close()
        except Exception:
            pass


def from_env(
    *,
    events_url: str | None = None,
    spill_dir: Path | None = None,
    flush_interval_s: float = 30.0,
    heartbeat_interval_s: float = 300.0,
    include_chat: bool = True,
) -> Telemetry | None:
    import os

    token = os.environ.get("STARCOPLAY_PLATFORM_TOKEN", "").strip()
    if not token:
        return None
    backend_name = os.environ.get("STARCOPLAY_TELEMETRY_BACKEND", "").strip().lower()
    url = (events_url or os.environ.get("STARCOPLAY_EVENTS_URL", "")).strip()
    ak = os.environ.get("STARCOPLAY_EVENTS_APIKEY", "").strip() or None

    if backend_name == "internal":
        from report.telemetry_backends import build_backend_from_config
        backend = build_backend_from_config({
            "backend": "internal",
            "events_url": url,
            "events_api_key": ak,
            "dsn": os.environ.get("DATABASE_URL", "").strip() or None,
            "supabase_url": os.environ.get("SUPABASE_URL", "").strip() or None,
            "supabase_service_key": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or None,
        })
        return Telemetry(
            platform_token=token,
            events_url=url,
            api_key=ak,
            flush_interval_s=flush_interval_s,
            heartbeat_interval_s=heartbeat_interval_s,
            spill_dir=spill_dir,
            include_chat=include_chat,
            backend=backend,
        )

    if not url:
        return None
    return Telemetry(
        platform_token=token,
        events_url=url,
        api_key=ak,
        flush_interval_s=flush_interval_s,
        heartbeat_interval_s=heartbeat_interval_s,
        spill_dir=spill_dir,
        include_chat=include_chat,
    )


# ── 模块级辅助函数（供慢环/快环通过 telemetry 模块调用）────────────────────


def track_onboard(step: str, data: dict[str, Any] | None = None) -> None:
    """模块级 onboard 追踪（委派到当前 Telemetry 实例）。"""
    t = _current  # 局部快照，避免竞态
    if t is not None:
        t.track_onboard(step, data)


def get_include_chat() -> bool:
    """返回当前 Telemetry 实例的 include_chat 配置。"""
    t = _current  # 局部快照，避免竞态
    if t is not None:
        return t._include_chat
    return True  # 默认包含
