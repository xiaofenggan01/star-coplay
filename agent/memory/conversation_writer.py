"""
memory.conversation_writer — 对话记录写入器。

独立于事件的 JSONL 存储，管理玩家与 Agent 之间的对话历史。
与 EventWriter 类似但有独立的 FIFO 策略（按轮数而非条数淘汰）。

对话格式（每行一条）：
    {"ts": "2026-04-01T15:01:30Z", "role": "player", "content": "今天去钓鱼吧"}
    {"ts": "2026-04-01T15:01:35Z", "role": "agent", "content": "好啊！去海边还是山上的湖？"}
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from memory.event_writer import utf8_safe_jsonl_line

log = logging.getLogger(__name__)


class ConversationWriter:
    """对话记录写入器 — 独立于事件的 JSONL 存储。

    Args:
        file_path: conversation.jsonl 文件路径（如不存在会自动创建）。
        max_turns: 最大对话轮数（默认 20 轮 = 约 40 条消息），超出后 FIFO 淘汰。
    """

    def __init__(self, file_path: Path, max_turns: int = 20) -> None:
        self._path = file_path
        self._max_turns = max_turns
        self._lock = threading.Lock()

    # ── 写入 ───────────────────────────────────────────────────────────────

    def append(self, role: str, content: str) -> None:
        """追加一条对话记录。

        Args:
            role: 说话方，"player" 或 "agent"。
            content: 对话内容文本。
        """
        ts = datetime.now(timezone.utc).isoformat()
        record = {"ts": ts, "role": role, "content": content}

        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(utf8_safe_jsonl_line(record))
            except OSError as e:
                log.warning("ConversationWriter: 写入对话失败: %s", e)
                return

            # 检查是否需要淘汰旧对话
            self._maybe_evict()

    def _maybe_evict(self) -> None:
        """检查是否需要淘汰，如需要则执行（必须在 _lock 内调用）。

        淘汰策略：保留最近 max_turns 轮对话。
        一轮 = 一条 player + 一条 agent（大约 2 条消息）。
        实际按消息数 max_turns * 2 来限制，确保上下文连贯。
        """
        if not self._path.exists():
            return

        max_messages = self._max_turns * 2

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
        except OSError as e:
            log.warning("ConversationWriter: 读取对话文件失败: %s", e)
            return

        if len(lines) <= max_messages:
            return

        # 保留最新的 max_messages 条
        lines_to_keep = lines[-max_messages:]

        # 避免从孤立的 agent 回复开始（对齐对话轮边界）
        if lines_to_keep:
            try:
                first = json.loads(lines_to_keep[0])
                if first.get("role") == "agent" and len(lines_to_keep) > 1:
                    lines_to_keep = lines_to_keep[1:]
            except json.JSONDecodeError:
                pass

        try:
            tmp_path = self._path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(lines_to_keep)
            os.replace(tmp_path, self._path)
        except OSError as e:
            log.warning("ConversationWriter: 淘汰写回失败: %s", e)

    # ── 读取 ───────────────────────────────────────────────────────────────

    def read_recent(self, n: int = 10) -> list[dict]:
        """读取最近 N 轮对话（每轮可能多条消息）。

        Args:
            n: 返回的轮数上限，实际返回 n*2 条消息以保证每轮完整。

        Returns:
            最近的对话消息列表（最旧的在最前，每条含 ts/role/content）。
        """
        max_messages = n * 2
        with self._lock:
            if not self._path.exists():
                return []
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    lines = [line for line in f if line.strip()]
            except OSError as e:
                log.warning("ConversationWriter: read_recent 失败: %s", e)
                return []

            records = []
            for line in lines:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

            return records[-max_messages:] if len(records) > max_messages else records

    def read_all(self) -> list[dict]:
        """读取所有对话记录（归档时用）。

        Returns:
            文件中的所有对话记录列表。
        """
        with self._lock:
            if not self._path.exists():
                return []
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    lines = [line for line in f if line.strip()]
            except OSError as e:
                log.warning("ConversationWriter: read_all 失败: %s", e)
                return []

            records = []
            for line in lines:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            return records

    def freeze_and_move(self, dest: Path) -> bool:
        """冻结当前文件并移动到归档目录（阶段 1 同步归档）。

        使用 shutil.move() 支持跨文件系统。移动后自动创建新的空文件。

        Args:
            dest: 目标文件路径（含文件名，如 episodes/ep_001/conversation.jsonl）。

        Returns:
            True 成功，False 失败。
        """
        with self._lock:
            dest.parent.mkdir(parents=True, exist_ok=True)

            if not self._path.exists():
                # 源文件不存在 → 创建空目标文件（无对话的 Episode 也合法）
                dest.touch()
                log.info("ConversationWriter: freeze_and_move 源文件不存在，创建空目标: %s", dest)
                return True

            try:
                shutil.move(str(self._path), str(dest))
                # 创建新的空文件，继续接收写入
                self._path.touch()
                log.info("ConversationWriter: 已归档 %s → %s", self._path, dest)
                return True
            except OSError as e:
                log.error("ConversationWriter: freeze_and_move 失败: %s", e)
                return False

    # ── 属性 ───────────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        """对话文件路径。"""
        return self._path
