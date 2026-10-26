"""
memory.event_writer — 单写者事件写入器。

消除 events.jsonl 的并发写入竞态。
快环和慢环通过 append() 提交事件，EventWriter 内部用 threading.Lock 保证顺序写入。

钉针机制：
    - event dict 中有 "pinned": true 字段的事件标记为钉针
    - FIFO 淘汰时跳过 pinned 事件（pinned 事件不会被推出）
    - player_chat 类型事件自动设置 pinned: true
    - pinned 事件有独立容量上限（默认 50 条）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

from text_sanitize import sanitize_for_utf8_json, utf8_safe_json_line as utf8_safe_jsonl_line

log = logging.getLogger(__name__)

# 自动钉针的事件类型
_AUTO_PIN_TYPES: frozenset[str] = frozenset({"player_chat"})


class EventWriter:
    """单写者事件写入器 — 线程安全的 JSONL 追加写入。

    消除 events.jsonl 的并发写入竞态。
    快环和慢环通过 append() 提交事件，EventWriter 内部用 threading.Lock 保证顺序写入。

    Args:
        file_path: events.jsonl 文件路径（如不存在会自动创建）。
        max_events: 普通事件最大保留数量（默认 200，超出后 FIFO 淘汰，跳过 pinned）。
        max_pinned: pinned 事件最大保留数量（默认 50，超出后淘汰最旧 pinned）。
    """

    def __init__(
        self,
        file_path: Path,
        max_events: int = 200,
        max_pinned: int = 50,
    ) -> None:
        self._path = file_path
        self._max_events = max_events
        self._max_pinned = max_pinned
        self._lock = threading.Lock()
        self._count = 0

        # 启动时从文件行数恢复 _count
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._count = sum(1 for line in f if line.strip())
            except OSError as e:
                log.warning("EventWriter: 恢复事件计数失败: %s", e)
                self._count = 0

    # ── 写入 ───────────────────────────────────────────────────────────────

    def append(self, event: dict) -> None:
        """追加一条事件到 JSONL 文件。线程安全。

        player_chat 类型事件自动设置 pinned: true。

        Args:
            event: 事件字典，至少含 type 和 ts 字段。
        """
        # 自动钉针
        if event.get("type") in _AUTO_PIN_TYPES and not event.get("pinned"):
            event = {**event, "pinned": True}

        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(utf8_safe_jsonl_line(event))
                self._count += 1
            except OSError as e:
                log.warning("EventWriter: 写入事件失败: %s", e)
                return

            # 超出上限时触发 FIFO 淘汰
            self._maybe_evict()

    def _maybe_evict(self) -> None:
        """检查是否需要淘汰，如需要则执行（必须在 _lock 内调用）。"""
        # 快速路径：总数未超过普通事件上限时，普通事件必然不超限
        # （pinned 超限仅当 count > max_pinned，这里也能捕获大多数情况）
        if self._count <= self._max_events and self._count <= self._max_pinned:
            return

        if not self._path.exists():
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
        except OSError as e:
            log.warning("EventWriter: 读取文件失败: %s", e)
            return

        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("EventWriter: 跳过无效 JSON 行: %r", line[:80])

        pinned = [e for e in events if e.get("pinned")]
        normal = [e for e in events if not e.get("pinned")]

        changed = False

        # 淘汰普通事件（超出 max_events）
        if len(normal) > self._max_events:
            excess = len(normal) - self._max_events
            normal = normal[excess:]
            changed = True

        # 淘汰 pinned 事件（超出 max_pinned）
        if len(pinned) > self._max_pinned:
            excess = len(pinned) - self._max_pinned
            pinned = pinned[excess:]
            changed = True

        if changed:
            # 合并 pinned + normal，按原始顺序（用 ts 字段排序）
            all_events = pinned + normal
            all_events.sort(key=lambda e: e.get("ts", ""))

            try:
                tmp_path = self._path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for e in all_events:
                        f.write(utf8_safe_jsonl_line(e))
                os.replace(tmp_path, self._path)
                self._count = len(all_events)
            except OSError as ex:
                log.error("EventWriter: FIFO 淘汰写回失败，原文件保留不受影响: %s", ex)
                # tmp→replace 失败时原 events.jsonl 未被修改，_count 不更新
                # 下次 append 会重新触发 evict

    # ── 读取 ───────────────────────────────────────────────────────────────

    def read_recent(self, n: int) -> list[dict]:
        """读取最近 N 条事件（包含 pinned 和普通事件，按时间顺序）。

        Args:
            n: 返回的最大事件数量。

        Returns:
            最近 n 条事件列表（最旧的在最前）。
        """
        with self._lock:
            return self._read_recent_unlocked(n)

    def _read_recent_unlocked(self, n: int) -> list[dict]:
        """不加锁的读取实现（供内部使用）。"""
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
        except OSError as e:
            log.warning("EventWriter: 读取失败: %s", e)
            return []

        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        return events[-n:] if len(events) > n else events

    def read_all(self) -> list[dict]:
        """读取所有事件（归档时用）。

        Returns:
            文件中的所有事件列表。
        """
        with self._lock:
            if not self._path.exists():
                return []
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    lines = [line for line in f if line.strip()]
            except OSError as e:
                log.warning("EventWriter: read_all 失败: %s", e)
                return []

            events = []
            for line in lines:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            return events

    def freeze_and_move(self, dest: Path) -> bool:
        """冻结当前文件并移动到归档目录（阶段 1 同步归档）。

        使用 shutil.move() 支持跨文件系统。移动后自动创建新的空文件（重置计数器）。

        Args:
            dest: 目标文件路径（含文件名，如 episodes/ep_001/events.jsonl）。

        Returns:
            True 成功，False 失败。
        """
        with self._lock:
            dest.parent.mkdir(parents=True, exist_ok=True)

            if not self._path.exists():
                # 源文件不存在 → 创建空目标文件（无事件的 Episode 也合法）
                dest.touch()
                log.info("EventWriter: freeze_and_move 源文件不存在，创建空目标: %s", dest)
                return True

            try:
                shutil.move(str(self._path), str(dest))
                # 创建新的空文件，继续接收写入
                self._path.touch()
                self._count = 0
                log.info("EventWriter: 已归档 %s → %s", self._path, dest)
                return True
            except OSError as e:
                log.error("EventWriter: freeze_and_move 失败: %s", e)
                return False

    # ── 属性 ───────────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """当前文件中的事件数量。"""
        return self._count

    @property
    def path(self) -> Path:
        """事件文件路径。"""
        return self._path
