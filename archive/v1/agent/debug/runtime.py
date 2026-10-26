from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def setup_debug_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("agent")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def clip_text(value: str, limit: int = 800) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def sanitize_value(value: Any, text_limit: int = 800, list_limit: int = 12) -> Any:
    if isinstance(value, dict):
        return {str(k): sanitize_value(v, text_limit, list_limit) for k, v in value.items()}
    if isinstance(value, list):
        items = [sanitize_value(item, text_limit, list_limit) for item in value[:list_limit]]
        if len(value) > list_limit:
            items.append({"truncated": len(value) - list_limit})
        return items
    if isinstance(value, tuple):
        return sanitize_value(list(value), text_limit, list_limit)
    if isinstance(value, str):
        return clip_text(value, text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return clip_text(repr(value), text_limit)


@dataclass(slots=True)
class TurnTrace:
    turn_id: str
    path: Path
    payload: dict[str, Any]

    def attach(self, key: str, value: Any) -> None:
        self.payload[key] = sanitize_value(value)
        self.persist()

    def record(self, stage: str, value: Any) -> None:
        stages = self.payload.setdefault("stages", [])
        stages.append(
            {
                "stage": stage,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": sanitize_value(value),
            }
        )
        self.persist()

    def finish(self, status: str, value: Any | None = None) -> None:
        self.payload["status"] = status
        self.payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        if value is not None:
            self.payload["final"] = sanitize_value(value)
        self.persist()

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")


class DebugRuntime:
    def __init__(self, debug_dir: Path) -> None:
        self.debug_dir = debug_dir
        self.turn_dir = debug_dir / "turns"
        self.logger = setup_debug_logger(debug_dir)

    def start_turn(self, day_key: str, state_mtime: float, context_summary: str) -> TurnTrace:
        now = datetime.now(timezone.utc)
        turn_id = now.strftime("%Y%m%dT%H%M%S%f")
        path = self.turn_dir / day_key / f"{turn_id}.json"
        payload = {
            "turn_id": turn_id,
            "day_key": day_key,
            "state_mtime": state_mtime,
            "context_summary": context_summary,
            "started_at": now.isoformat(),
            "status": "running",
            "stages": [],
        }
        trace = TurnTrace(turn_id=turn_id, path=path, payload=payload)
        trace.persist()
        self.log("turn_started", {"turn_id": turn_id, "day_key": day_key, "context_summary": context_summary})
        return trace

    def log(self, event: str, payload: dict[str, Any]) -> None:
        record = {"event": event, **sanitize_value(payload)}
        self.logger.info(json.dumps(record, ensure_ascii=False))
