from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class PlanItem:
    label: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None
    result_status: str | None = None
    result: dict[str, Any] | None = None


@dataclass
class PlanState:
    source_input: str
    remaining: list[PlanItem]
    plan_id: str = field(default_factory=lambda: f"plan_{uuid4().hex[:8]}")
    status: str = "planned"
    done: list[PlanItem] = field(default_factory=list)
    active: PlanItem | None = None
    last_result: dict[str, Any] | None = None
    blocked_reason: str | None = None
    recovery_attempts: int = 0
    user_visible_summary_sent: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_items(cls, *, source_input: str, items: list[PlanItem]) -> "PlanState":
        return cls(source_input=source_input, remaining=list(items))

    def start_next(self, *, job_id: str) -> PlanItem | None:
        if self.active is not None or not self.remaining or self.status in {"blocked", "completed", "cancelled"}:
            return None
        item = self.remaining.pop(0)
        item.job_id = job_id
        self.active = item
        self.status = "active"
        self.blocked_reason = None
        self.updated_at = time.time()
        return item

    def apply_completion(self, result: dict[str, Any]) -> None:
        self.last_result = result
        if self.active is None:
            self.updated_at = time.time()
            return

        active = self.active
        active.result = result
        self.active = None

        if _is_successful_result(result) and not _is_partial_result(result):
            active.result_status = "ok"
            self.done.append(active)
            self.status = "planned" if self.remaining else "completed"
            self.blocked_reason = None
        else:
            active.result_status = "failed" if not _is_successful_result(result) else "partial"
            self.status = "blocked"
            self.blocked_reason = _blocked_reason(result, active.result_status)

        self.updated_at = time.time()


def _is_successful_result(result: dict[str, Any]) -> bool:
    return bool(result.get("success", False))


def _is_partial_result(result: dict[str, Any]) -> bool:
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    remaining_fields = (
        "remaining_unwatered",
        "remaining_harvestable",
        "remaining_unpetted",
        "remaining_targets",
    )
    for key in remaining_fields:
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    status = str(result.get("status") or data.get("status") or "").lower()
    return status == "partial"


def _blocked_reason(result: dict[str, Any], fallback: str) -> str:
    error_code = str(result.get("error_code") or "").strip()
    if error_code:
        return error_code
    status = str(result.get("status") or "").strip()
    if status:
        return status
    data = result.get("data")
    if isinstance(data, dict):
        stopped_reason = str(data.get("stopped_reason") or "").strip()
        if stopped_reason and stopped_reason != "all_done":
            return stopped_reason
    return fallback
