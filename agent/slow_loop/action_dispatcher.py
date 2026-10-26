from __future__ import annotations

import asyncio
import heapq
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import TaskCompleted
from report import events
from slow_loop.action_policy import (
    BRIDGE_POLL_INTERVAL_S,
    DEFAULT_LONG_PRIORITY,
    TASK_COMPLETED_PRIORITY,
    is_instant_action,
)

log = logging.getLogger("slow_loop.action_dispatcher")


class _LockedBridgeProxy:
    def __init__(self, bridge: Any, dispatcher: "ActionDispatcher") -> None:
        self._bridge = bridge
        self._dispatcher = dispatcher

    def write_command(self, action: str, params: dict, *args: Any, **kwargs: Any) -> str:
        return self._dispatcher.write_command_sync(action, params, *args, **kwargs)

    def cancel_command(self, action_id: str) -> None:
        self._dispatcher.cancel_command_sync(action_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bridge, name)


@dataclass
class ActionJob:
    job_id: str
    kind: str
    action_id: str | None
    action: str
    params: dict
    wait_result: bool
    timeout_s: float
    priority: int
    trigger_label: str
    needs_followup: bool
    status: str
    created_at: float
    route_key: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    skill_runner: Any = None
    skill_entry: Any = None
    skill_context: Any = None
    result: Any = None
    error: str | None = None
    completion_sent: bool = False


class ActionDispatcher:
    def __init__(
        self,
        bridge: Any,
        message_bus: MessageBus,
        working_memory: WorkingMemory,
        stop_event: threading.Event,
        max_long_queue: int = 20,
    ) -> None:
        self._bridge = bridge
        self._bus = message_bus
        self._memory = working_memory
        self._stop_event = stop_event
        self._max_long_queue = max_long_queue
        self._locked_bridge = _LockedBridgeProxy(bridge, self)
        self._fast_queue: queue.Queue[str] = queue.Queue()
        self._long_heap: list[tuple[int, int, str]] = []
        self._long_heap_counter = 0
        self._long_heap_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._jobs: dict[str, ActionJob] = {}
        self._job_id_to_action_id: dict[str, str] = {}
        self._action_id_to_job_id: dict[str, str] = {}
        self._active_long_job_id: str | None = None
        self._jobs_lock = threading.Lock()
        self._running = False
        self._fast_thread: threading.Thread | None = None
        self._long_thread: threading.Thread | None = None

    def locked_bridge(self) -> Any:
        return self._locked_bridge

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._fast_thread = threading.Thread(target=self._fast_loop, name="ActionDispatcherFast", daemon=True)
        self._long_thread = threading.Thread(target=self._long_loop, name="ActionDispatcherLong", daemon=True)
        self._fast_thread.start()
        self._long_thread.start()

    def stop(self) -> None:
        self._running = False
        for thread in (self._fast_thread, self._long_thread):
            if thread is not None:
                thread.join(timeout=2.0)
        self._fast_thread = None
        self._long_thread = None

    def submit(
        self,
        action: str,
        params: dict,
        *,
        timeout_s: float = 15.0,
        wait_result: bool = True,
        trigger_label: str = "",
        needs_followup: bool = False,
        priority: int = DEFAULT_LONG_PRIORITY,
        route_key: str | None = None,
    ) -> str | None:
        kind = "instant" if is_instant_action(action) or not wait_result else "action"
        return self._submit_job(
            kind=kind,
            action=action,
            params=params,
            timeout_s=timeout_s,
            wait_result=wait_result,
            priority=priority,
            trigger_label=trigger_label,
            needs_followup=needs_followup,
            route_key=route_key,
        )

    def submit_skill(
        self,
        tool_name: str,
        params: dict,
        *,
        skill_runner: Any,
        skill_entry: Any,
        trigger_label: str = "",
        needs_followup: bool = True,
        priority: int = DEFAULT_LONG_PRIORITY,
        route_key: str | None = None,
    ) -> str | None:
        timeout_s = float(getattr(skill_entry, "timeout", 300.0) or 300.0) + 30.0
        return self._submit_job(
            kind="skill",
            action=tool_name,
            params=params,
            timeout_s=timeout_s,
            wait_result=True,
            priority=priority,
            trigger_label=trigger_label,
            needs_followup=needs_followup,
            skill_runner=skill_runner,
            skill_entry=skill_entry,
            route_key=route_key,
        )

    def write_command_sync(self, action: str, params: dict, *args: Any, **kwargs: Any) -> str:
        with self._write_lock:
            return self._bridge.write_command(action, params, *args, **kwargs)

    def cancel_command_sync(self, action_id: str) -> None:
        with self._write_lock:
            self._bridge.cancel_command(action_id)

    def cancel_active(self) -> bool:
        action_id = None
        skill_context = None
        with self._jobs_lock:
            job = self._jobs.get(self._active_long_job_id or "")
            if job is None or job.status in {"done", "cancelled", "timeout", "failed"}:
                return False
            job.status = "cancelled"
            action_id = job.action_id
            if job.kind == "skill":
                skill_context = job.skill_context
        if skill_context is not None:
            skill_context.cancel()
        elif action_id:
            self.cancel_command_sync(action_id)
        self._write_status()
        return True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._jobs_lock:
            return self._job_snapshot(self._jobs.get(job_id))

    def cancel_pending(self) -> int:
        cancelled: list[ActionJob] = []
        with self._jobs_lock:
            for job in self._jobs.values():
                if job.kind in {"action", "skill"} and job.status == "pending":
                    job.status = "cancelled"
                    job.finished_at = time.time()
                    cancelled.append(job)
        for job in cancelled:
            if job.kind == "skill":
                self._emit_skill_completion(job, None, 0.0, "cancelled")
            else:
                self._emit_completion(job, None, 0.0, "cancelled")
        if cancelled:
            self._rebuild_long_heap()
            self._write_status()
        return len(cancelled)

    def drain_active(self, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._jobs_lock:
                active = self._active_long_job_id
            if not active:
                return True
            time.sleep(0.05)
        self.cancel_active()
        return False

    def can_accept_long(self) -> bool:
        with self._jobs_lock:
            return self._long_load_locked() < self._max_long_queue

    def snapshot(self) -> dict[str, Any]:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
            active = self._jobs.get(self._active_long_job_id or "")
            counts: dict[str, int] = {}
            for job in jobs:
                counts[job.status] = counts.get(job.status, 0) + 1
            pend_candidates = [
                job for job in jobs
                if job.status == "pending" and job.kind in {"action", "skill"}
            ]
            pend_candidates.sort(key=lambda j: (j.priority, j.created_at))
            pending = [self._job_snapshot(job) for job in pend_candidates[:5]]
            finished = [
                job for job in jobs
                if job.status in {"done", "partial", "blocked", "cancelled", "timeout", "failed"}
            ]
            finished.sort(key=lambda j: j.finished_at or j.created_at, reverse=True)
            recent = [self._job_snapshot(job) for job in finished[:10]]
            with self._long_heap_lock:
                heap_sz = len(self._long_heap)
            return {
                "running": self._running,
                "fast_queue_size": self._fast_queue.qsize(),
                "long_queue_size": heap_sz,
                "jobs": counts,
                "active_job": self._job_snapshot(active) if active else None,
                "pending_jobs": pending,
                "recent_jobs": recent,
            }

    def _enqueue_long(self, job_id: str, priority: int) -> None:
        with self._long_heap_lock:
            self._long_heap_counter += 1
            heapq.heappush(self._long_heap, (priority, self._long_heap_counter, job_id))

    def _pop_long_job(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self._long_heap_lock:
                if self._long_heap:
                    _, _, job_id = heapq.heappop(self._long_heap)
                    return job_id
            if time.monotonic() >= deadline:
                return None
            remaining = deadline - time.monotonic()
            if self._stop_event.wait(timeout=min(0.05, max(remaining, 0.001))):
                return None

    def _rebuild_long_heap(self) -> None:
        with self._jobs_lock:
            active_job_ids = {
                job_id for job_id, job in self._jobs.items()
                if job.status != "cancelled"
            }
            with self._long_heap_lock:
                self._long_heap = [item for item in self._long_heap if item[2] in active_job_ids]
                heapq.heapify(self._long_heap)

    def wait_until_job_writes(self, job_id: str, timeout_s: float = 60.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                if job.action_id is not None:
                    return
                if job.kind == "skill" and (job.skill_context is not None or job.status == "running"):
                    return
                if job.status in {"failed", "timeout", "cancelled", "done", "partial", "blocked"}:
                    return
            time.sleep(0.02)

    def _submit_job(
        self,
        *,
        kind: str,
        action: str,
        params: dict,
        timeout_s: float,
        wait_result: bool,
        priority: int,
        trigger_label: str,
        needs_followup: bool,
        route_key: str | None = None,
        skill_runner: Any = None,
        skill_entry: Any = None,
    ) -> str | None:
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job = ActionJob(
            job_id=job_id,
            kind=kind,
            action_id=None,
            action=action,
            params=params,
            wait_result=wait_result,
            timeout_s=timeout_s,
            priority=priority,
            trigger_label=trigger_label,
            needs_followup=needs_followup,
            status="pending",
            created_at=time.time(),
            route_key=route_key,
            skill_runner=skill_runner,
            skill_entry=skill_entry,
        )
        if not self._running and not self._stop_event.is_set():
            self.start()
        with self._jobs_lock:
            if kind in {"action", "skill"} and self._long_load_locked() >= self._max_long_queue:
                return None
            self._jobs[job_id] = job
        if kind == "instant":
            self._fast_queue.put(job_id)
        else:
            self._enqueue_long(job_id, priority)
        self._write_status()
        return job_id

    def _fast_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            try:
                job_id = self._fast_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            job = self._get_job(job_id)
            if job is not None:
                self._run_instant_job(job)

    def _long_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            job_id = self._pop_long_job(0.25)
            if job_id is None:
                continue
            job = self._get_job(job_id)
            if job is None:
                continue
            with self._jobs_lock:
                if job.status != "pending":
                    continue
                self._active_long_job_id = job.job_id
            try:
                if job.kind == "skill":
                    self._run_skill_job(job)
                else:
                    self._run_action_job(job)
            finally:
                with self._jobs_lock:
                    if self._active_long_job_id == job.job_id:
                        self._active_long_job_id = None

    def _run_instant_job(self, job: ActionJob) -> None:
        start = time.monotonic()
        self._mark_started(job, "writing")
        try:
            action_id = self.write_command_sync(job.action, job.params)
            self._set_action_id(job, action_id)
            self._finish_job(job, "done")
            self._remember_action(job, None)
            try:
                events.action_outcome(action_type=job.action, success=True, duration_ms=(time.monotonic() - start) * 1000.0)
            except Exception:
                log.debug("action_outcome failed", exc_info=True)
        except Exception as e:
            log.warning("instant job failed: %s %s", job.action, e)
            job.error = str(e)
            self._finish_job(job, "failed")
            self._write_status()

    def _run_action_job(self, job: ActionJob) -> None:
        start = time.monotonic()
        self._mark_started(job, "writing")
        result = None
        try:
            if self._is_cancelled(job):
                self._finish_job(job, "cancelled")
                self._emit_completion(job, None, time.monotonic() - start, "cancelled")
                self._write_status()
                return
            action_id = self.write_command_sync(job.action, job.params)
            self._set_action_id(job, action_id)
            if self._is_cancelled(job):
                self._cancel_action_id(action_id)
                self._finish_job(job, "cancelled")
                self._emit_completion(job, None, time.monotonic() - start, "cancelled")
                self._write_status()
                return
            self._set_status(job, "running")
            result = self._poll_bridge_result(job.action_id, job.timeout_s, lambda: job.status == "cancelled")
            if job.status == "cancelled":
                self._emit_completion(job, None, time.monotonic() - start, "cancelled")
                self._write_status()
                return
            if result is None:
                self._cancel_action_id(action_id)
                self._finish_job(job, "timeout")
                self._emit_completion(job, None, time.monotonic() - start, "timeout")
                self._write_status()
                return
            self._finish_job(job, "done")
            self._remember_action(job, result)
            try:
                events.action_outcome(
                    action_type=job.action,
                    success=bool(getattr(result, "success", False)),
                    duration_ms=(time.monotonic() - start) * 1000.0,
                )
            except Exception:
                log.debug("action_outcome failed", exc_info=True)
            self._emit_completion(job, result, time.monotonic() - start, "done")
            self._write_status()
        except Exception as e:
            log.warning("long job failed: %s %s", job.action, e)
            job.error = str(e)
            self._finish_job(job, "failed")
            self._emit_completion(job, None, time.monotonic() - start, "failed")
            self._write_status()

    def _run_skill_job(self, job: ActionJob) -> None:
        start = time.monotonic()
        self._mark_started(job, "running")
        try:
            if self._is_cancelled(job):
                self._finish_job(job, "cancelled")
                self._emit_skill_completion(job, None, time.monotonic() - start, "cancelled")
                self._write_status()
                return
            def on_context(ctx: Any) -> None:
                should_cancel = False
                with self._jobs_lock:
                    job.skill_context = ctx
                    should_cancel = job.status == "cancelled"
                if should_cancel:
                    ctx.cancel()

            result = asyncio.run(job.skill_runner.execute(job.skill_entry, job.params, on_context=on_context))
            error_code = result.get("error", {}).get("code") if isinstance(result, dict) else None
            if job.status == "cancelled" or error_code == "CANCELLED":
                self._finish_job(job, "cancelled")
                self._emit_skill_completion(job, result, time.monotonic() - start, "cancelled")
                self._write_status()
                return
            completion_status = self._skill_completion_status(result)
            self._finish_job(job, completion_status)
            self._remember_action(job, result)
            self._emit_skill_completion(job, result, time.monotonic() - start, completion_status)
            self._write_status()
        except Exception as e:
            log.warning("skill job failed: %s %s", job.action, e)
            job.error = str(e)
            self._finish_job(job, "failed")
            self._emit_skill_completion(job, None, time.monotonic() - start, "failed")
            self._write_status()

    def _poll_bridge_result(self, action_id: str | None, timeout_s: float, is_cancelled: Any) -> Any:
        if action_id is None:
            return None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._stop_event.is_set() or is_cancelled():
                return None
            result = self._bridge.read_command_result(action_id)
            if result is not None:
                return result
            time.sleep(BRIDGE_POLL_INTERVAL_S)
        return None

    def _cancel_action_id(self, action_id: str) -> None:
        with self._write_lock:
            self._bridge.cancel_command(action_id)

    def _emit_completion(self, job: ActionJob, result: Any, duration_s: float, status: str) -> None:
        if job.completion_sent:
            return
        job.completion_sent = True
        payload = self._build_task_completed_result(job, result, status, is_skill=False)
        self._bus.send_to_slow(
            TaskCompleted(
                node_id=job.job_id,
                result=payload,
                observation=payload["observation"],
                route_key=job.route_key,
                duration_s=duration_s,
            ),
            priority=TASK_COMPLETED_PRIORITY,
        )

    def _emit_skill_completion(self, job: ActionJob, result: dict | None, duration_s: float, status: str) -> None:
        if job.completion_sent:
            return
        job.completion_sent = True
        payload = self._build_task_completed_result(job, result, status, is_skill=True)
        self._bus.send_to_slow(
            TaskCompleted(
                node_id=job.job_id,
                result=payload,
                observation=payload["observation"],
                route_key=job.route_key,
                duration_s=duration_s,
            ),
            priority=TASK_COMPLETED_PRIORITY,
        )

    def _build_task_completed_result(self, job: ActionJob, result: Any, status: str, *, is_skill: bool) -> dict[str, Any]:
        if is_skill:
            error = result.get("error", {}) if isinstance(result, dict) else {}
            observation = result.get("observation", "") if isinstance(result, dict) else ""
            success = bool(result.get("success", False)) if isinstance(result, dict) else False
            if status == "cancelled":
                success = False
            message = observation or error.get("message") or status
            error_code = "CANCELLED" if status == "cancelled" else error.get("code")
            data = result.get("data") if isinstance(result, dict) else None
        else:
            observation = getattr(result, "observation", "") if result is not None else ""
            success = bool(getattr(result, "success", False)) if result is not None and status == "done" else False
            message = getattr(result, "message", "") if result is not None else status
            error_code = getattr(result, "error_code", None) if result is not None else status.upper()
            data = getattr(result, "data", None) if result is not None else None
        return {
            "job_id": job.job_id,
            "action_id": job.action_id,
            "action": job.action,
            "status": status,
            "params": job.params,
            "success": success,
            "message": message,
            "error_code": error_code,
            "data": data,
            "observation": observation,
            "needs_followup": job.needs_followup,
            "trigger_label": job.trigger_label,
        }

    def _remember_action(self, job: ActionJob, result: Any) -> None:
        self._memory.remember({}, [{
            "type": f"action_{job.action}",
            "content": {
                "job_id": job.job_id,
                "action": job.action,
                "params": job.params,
                "action_id": job.action_id,
                "result": str(result)[:500] if result is not None else None,
            },
            "ts": time.time(),
        }])

    def _get_job(self, job_id: str) -> ActionJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def _mark_started(self, job: ActionJob, status: str) -> None:
        with self._jobs_lock:
            if job.status == "cancelled":
                return
            job.started_at = time.time()
            job.status = status

    def _set_status(self, job: ActionJob, status: str) -> None:
        with self._jobs_lock:
            if job.status in {"cancelled", "timeout", "failed", "done", "partial", "blocked"}:
                return
            job.status = status

    @staticmethod
    def _skill_completion_status(result: Any) -> str:
        if not isinstance(result, dict):
            return "failed"
        status = str(result.get("status") or "").strip().lower()
        if status in {"done", "ok"}:
            return "done"
        if status in {"partial", "blocked", "failed", "timeout", "cancelled"}:
            return status
        return "done" if bool(result.get("success", False)) else "failed"

    def _set_action_id(self, job: ActionJob, action_id: str) -> None:
        with self._jobs_lock:
            job.action_id = action_id
            self._job_id_to_action_id[job.job_id] = action_id
            self._action_id_to_job_id[action_id] = job.job_id

    def _finish_job(self, job: ActionJob, status: str) -> None:
        with self._jobs_lock:
            job.status = status
            job.finished_at = time.time()

    def _is_cancelled(self, job: ActionJob) -> bool:
        with self._jobs_lock:
            return job.status == "cancelled"

    def _long_load_locked(self) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.kind in {"action", "skill"} and job.status in {"pending", "writing", "running"}
        )

    def _write_status(self) -> None:
        try:
            self._bridge.write_status("running", {"dispatcher": self.snapshot()})
        except Exception:
            log.debug("write_status failed", exc_info=True)

    @staticmethod
    def _job_snapshot(job: ActionJob | None) -> dict[str, Any] | None:
        if job is None:
            return None
        now = time.time()
        return {
            "job_id": job.job_id,
            "action_id": job.action_id,
            "kind": job.kind,
            "action": job.action,
            "status": job.status,
            "has_skill_context": job.skill_context is not None,
            "trigger_label": job.trigger_label,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "duration_s": (
                (job.finished_at - job.started_at)
                if job.finished_at is not None and job.started_at is not None
                else None
            ),
            "running_for": (now - job.started_at) if job.started_at else 0.0,
            "timeout_s": job.timeout_s,
            "priority": job.priority,
            "needs_followup": job.needs_followup,
            "error": job.error,
        }
