from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EventReader:
    """读取 events.jsonl，按 seq 追踪已消费位置；增量 tail 读取避免全文件扫描。"""

    def __init__(self, bridge_dir: Path) -> None:
        self.path = bridge_dir / "events.jsonl"
        self._last_seq: int = -1
        self._byte_offset: int = 0

    def reset_cursor(self) -> None:
        """新游戏日或 bridge 重置时：清空文件偏移与 seq（与 Mod 清空 jsonl 对齐）。"""
        self._byte_offset = 0
        self._last_seq = -1

    def read_new(self) -> list[dict[str, Any]]:
        """读取所有 seq > _last_seq 的事件，更新游标与文件偏移。"""
        if not self.path.is_file():
            return []
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self._byte_offset:
            self._byte_offset = 0
            self._last_seq = -1
        if size == self._byte_offset:
            return []
        try:
            with self.path.open("rb") as f:
                f.seek(self._byte_offset)
                raw = f.read()
        except OSError:
            return []
        if not raw:
            return []
        last_nl = raw.rfind(b"\n")
        if last_nl < 0:
            return []
        complete = raw[: last_nl + 1]
        text = complete.decode("utf-8", errors="replace")
        new_events: list[dict[str, Any]] = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            seq = obj.get("seq")
            if seq is None:
                continue
            if int(seq) > self._last_seq:
                new_events.append(obj)
        if new_events:
            new_events.sort(key=lambda e: int(e.get("seq", 0)))
            self._last_seq = int(new_events[-1]["seq"])
        self._byte_offset += len(complete)
        return new_events

    def read_after(self, last_mtime: float) -> tuple[list[dict[str, Any]], float]:
        new_events = self.read_new()
        return new_events, float(self._last_seq)

    def filter_cmd_results(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in events if e.get("type") == "cmd_result"]

    def wait_cmd_result(self, cmd_id: str, timeout: float, poll_interval: float = 0.3) -> dict[str, Any] | None:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            new_events = self.read_new()
            for event in new_events:
                if event.get("type") == "cmd_result":
                    data = event.get("data") or {}
                    if data.get("cmd_id") == cmd_id:
                        return data if isinstance(data, dict) else None
            time.sleep(poll_interval)
        return None
