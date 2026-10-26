from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.bridge.file_bridge import get_mtime, read_json


@dataclass(slots=True)
class ResultWaitOutcome:
    results: list[dict[str, Any]] | None
    status: str
    attempts: int
    observed_mtime: float
    payload_size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "attempts": self.attempts,
            "observed_mtime": self.observed_mtime,
            "payload_size": self.payload_size,
            "matched_count": len(self.results or []),
        }


class ResultReader:
    def __init__(self, bridge_dir: Path, poll_interval: float) -> None:
        self.path = bridge_dir / "action_result.json"
        self.poll_interval = poll_interval

    def wait(self, request_id: str, timeout: float, after_mtime: float) -> ResultWaitOutcome:
        deadline = time.time() + timeout
        attempts = 0
        seen_payload = False
        last_payload_size = 0
        last_mtime = get_mtime(self.path)
        while time.time() < deadline:
            attempts += 1
            current_mtime = get_mtime(self.path)
            if current_mtime <= after_mtime:
                time.sleep(self.poll_interval)
                continue
            payload = read_json(self.path)
            if not isinstance(payload, list):
                time.sleep(self.poll_interval)
                continue
            seen_payload = True
            last_payload_size = len(payload)
            last_mtime = current_mtime
            matched = [
                item
                for item in payload
                if isinstance(item, dict) and item.get("request_id") == request_id
            ]
            if matched:
                return ResultWaitOutcome(
                    results=matched,
                    status="matched",
                    attempts=attempts,
                    observed_mtime=current_mtime,
                    payload_size=last_payload_size,
                )
            time.sleep(self.poll_interval)
        status = "request_id_mismatch" if seen_payload else "timeout"
        return ResultWaitOutcome(
            results=None,
            status=status,
            attempts=attempts,
            observed_mtime=last_mtime,
            payload_size=last_payload_size,
        )
