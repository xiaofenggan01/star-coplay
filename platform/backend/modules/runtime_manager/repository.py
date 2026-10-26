"""Small durable JSON repository used by the Runtime Adapter Spike.

This is deliberately local-file backed and not a production database.  It
exists to make Manager crash/restart tests cross a real OS process boundary.
Writes are atomic and records contain only provider-neutral RuntimeRecord data.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable, List

from modules.runtime_manager.contracts import RuntimeActualState, RuntimeRecord


class FileRuntimeRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load_all(self) -> List[RuntimeRecord]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [
            RuntimeRecord(
                runtime_id=item["runtime_id"],
                generation=int(item["generation"]),
                desired_state=RuntimeActualState(item["desired_state"]),
                actual_state=RuntimeActualState(item["actual_state"]),
                workdir=Path(item["workdir"]),
                owner_nonce=item["owner_nonce"],
                last_effective_activity_monotonic=item.get("last_effective_activity_monotonic"),
                exit_reason=item.get("exit_reason"),
            )
            for item in raw
        ]

    def save_all(self, records: Iterable[RuntimeRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "runtime_id": r.runtime_id,
                "generation": r.generation,
                "desired_state": r.desired_state.value,
                "actual_state": r.actual_state.value,
                "workdir": str(r.workdir),
                "owner_nonce": r.owner_nonce,
                "last_effective_activity_monotonic": r.last_effective_activity_monotonic,
                "exit_reason": r.exit_reason,
            }
            for r in records
        ]
        tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def apply_exit_event(self, runtime_id: str, generation: int, reason: str) -> bool:
        records = self.load_all()
        latest = max((r.generation for r in records if r.runtime_id == runtime_id), default=generation)
        if generation < latest:
            return False
        changed = False
        updated = []
        for record in records:
            if record.runtime_id == runtime_id and record.generation == generation:
                record = replace(
                    record,
                    desired_state=RuntimeActualState.STOPPED,
                    actual_state=RuntimeActualState.STOPPED,
                    exit_reason=reason,
                )
                changed = True
            updated.append(record)
        if changed:
            self.save_all(updated)
        return changed
