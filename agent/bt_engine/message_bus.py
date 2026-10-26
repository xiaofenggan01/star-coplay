"""
bt_engine.message_bus — 快环与慢环之间的线程安全消息总线。

设计要点：
    - 快环→慢环方向使用 PriorityQueue，支持优先级调度（如 EscalateRequest 高于 ReportEvent）
    - 慢环→快环方向使用 SimpleQueue，保证 FIFO 且无锁竞争
    - 序列号（_seq）防止 PriorityQueue 在优先级相同时比较消息对象
"""

from __future__ import annotations

import queue
import threading
import heapq
from typing import Any, Callable


class MessageBus:
    """快环-慢环消息总线。

    快环调用 send_to_slow / try_get_from_slow，
    慢环调用 send_to_fast / get_from_fast_blocking / try_get_from_fast。
    """

    def __init__(self) -> None:
        self._fast_to_slow: queue.PriorityQueue[tuple[int, int, Any]] = (
            queue.PriorityQueue()
        )
        self._slow_to_fast: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._seq: int = 0
        self._seq_lock = threading.Lock()

    # ── 快环 → 慢环 ────────────────────────────────────────────────────────

    def send_to_slow(self, message: Any, priority: int = 5) -> None:
        """将消息投入快→慢队列。

        Args:
            message:  任意消息对象（通常为 messages.types 中的 dataclass）。
            priority: 优先级，数值越小越先被消费（与 PriorityQueue 语义一致）。
                      默认 5；EscalateRequest 建议用 1，ReportEvent 用 7。
        """
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        self._fast_to_slow.put((priority, seq, message))

    def try_get_from_slow(self) -> Any | None:
        """非阻塞地从慢→快队列取一条消息。

        Returns:
            消息对象，队列为空时返回 None。
        """
        try:
            return self._slow_to_fast.get_nowait()
        except queue.Empty:
            return None

    # ── 慢环 → 快环 ────────────────────────────────────────────────────────

    def send_to_fast(self, message: Any) -> None:
        """将消息投入慢→快队列（通常为 ReplaceBehaviorTree）。

        Args:
            message: 任意消息对象。
        """
        self._slow_to_fast.put(message)

    def try_get_from_fast(self) -> Any | None:
        """非阻塞地从快→慢队列取一条消息。

        Returns:
            消息对象（去除优先级和序号包装），队列为空时返回 None。
        """
        try:
            _, _, msg = self._fast_to_slow.get_nowait()
            return msg
        except queue.Empty:
            return None

    def get_from_fast_blocking(self, timeout: float = 1.0) -> Any | None:
        """阻塞等待快→慢队列中的消息。

        Args:
            timeout: 最长等待秒数，超时返回 None。

        Returns:
            消息对象（去除优先级和序号包装），超时返回 None。
        """
        try:
            _, _, msg = self._fast_to_slow.get(timeout=timeout)
            return msg
        except queue.Empty:
            return None

    def drain_from_fast(self, predicate: Callable[[Any], bool], limit: int = 20) -> list[Any]:
        drained: list[Any] = []
        with self._fast_to_slow.mutex:
            kept = []
            for item in self._fast_to_slow.queue:
                if len(drained) < limit and predicate(item[2]):
                    drained.append(item[2])
                else:
                    kept.append(item)
            if drained:
                self._fast_to_slow.queue.clear()
                self._fast_to_slow.queue.extend(kept)
                heapq.heapify(self._fast_to_slow.queue)
                self._fast_to_slow.unfinished_tasks -= len(drained)
                if self._fast_to_slow.unfinished_tasks < 0:
                    self._fast_to_slow.unfinished_tasks = 0
                self._fast_to_slow.not_full.notify_all()
        return drained

    # ── 诊断 ───────────────────────────────────────────────────────────────

    def fast_to_slow_qsize(self) -> int:
        """返回快→慢队列当前积压消息数量（近似值）。"""
        return self._fast_to_slow.qsize()

    def slow_to_fast_empty(self) -> bool:
        """返回慢→快队列是否为空。"""
        return self._slow_to_fast.empty()
