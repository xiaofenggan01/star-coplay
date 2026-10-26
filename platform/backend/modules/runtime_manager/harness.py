"""In-memory Harness: idle-TTL and reconcile orchestration above an ``AgentRuntimeAdapter``.

Provider-neutral: depends only on ``contracts.AgentRuntimeAdapter``. Carries no
Claude/Gateway-specific fields. ``RuntimeRegistry`` is written so a future
persistent repository could replace the in-memory ``records`` list without
changing its public shape.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from modules.runtime_manager.clock import Clock, SystemClock
from modules.runtime_manager.contracts import (
    AgentRuntimeAdapter,
    ReadyTimeoutError,
    ReconcileAction,
    ReconcileDecision,
    RuntimeActualState,
    RuntimeHandle,
    RuntimeInput,
    RuntimeRecord,
    RuntimeSpec,
    RuntimeConflictError,
    RuntimeStoppingError,
    TurnResult,
)

log = logging.getLogger("runtime_manager.harness")

_RuntimeKey = Tuple[str, int]


class RuntimeEntry:
    """Mutable per-runtime bookkeeping owned by the Harness, guarded by ``lock``."""

    def __init__(self, spec: RuntimeSpec, handle: RuntimeHandle, owner_nonce: str, clock: Clock) -> None:
        self.spec = spec
        self.handle = handle
        self.owner_nonce = owner_nonce
        self.lock = asyncio.Lock()

        self.desired_state = RuntimeActualState.READY
        self.actual_state = RuntimeActualState.READY

        self.turn_inflight = False
        self.turn_epoch = 0
        self.turn_done_event = asyncio.Event()
        self.turn_done_event.set()  # no turn in flight initially
        # Invalidates a previously-started idle drain when new input is
        # admitted before the runtime crosses the irreversible STOPPING edge.
        self.drain_epoch = 0

        now = clock.monotonic()
        self.last_user_activity: float = now
        self.last_effective_runtime_activity: float = now

        self.exit_reason: Optional[str] = None
        # Once True, the entry is on an irreversible path to STOPPED; no new
        # input may be admitted (spec 6.4: after entering STOPPING, callers
        # must be told to restart a new generation rather than race cleanup).
        self.stopping = False


class RuntimeHarness:
    """Registry + idle-TTL orchestration for one ``AgentRuntimeAdapter``."""

    def __init__(self, adapter: AgentRuntimeAdapter, clock: Optional[Clock] = None) -> None:
        self._adapter = adapter
        self._clock = clock or SystemClock()
        self._entries: Dict[_RuntimeKey, RuntimeEntry] = {}
        self._start_lock = asyncio.Lock()
        self._startup_tasks: Dict[_RuntimeKey, Tuple[RuntimeSpec, "asyncio.Task[RuntimeEntry]"]] = {}

    @staticmethod
    def _key(runtime_id: str, generation: int) -> _RuntimeKey:
        return (runtime_id, generation)

    async def start_runtime(self, spec: RuntimeSpec, *, ready_timeout: float = 30.0) -> RuntimeEntry:
        key = self._key(spec.runtime_id, spec.generation)

        async with self._start_lock:
            existing = self._entries.get(key)
            if existing is not None:
                status = await self._adapter.inspect(existing.handle)
                if status.returncode is not None:
                    existing.actual_state = RuntimeActualState.STOPPED
                if existing.spec != spec:
                    raise RuntimeConflictError(
                        f"runtime {spec.runtime_id}/{spec.generation} already exists with a conflicting spec"
                    )
                if existing.spec == spec and existing.actual_state not in (
                    RuntimeActualState.STOPPED,
                    RuntimeActualState.FAILED,
                ):
                    return existing
                if existing.actual_state in (RuntimeActualState.STOPPED, RuntimeActualState.FAILED):
                    self._entries.pop(key, None)
                else:  # pragma: no cover - exhaustive state guard
                    raise RuntimeConflictError(f"runtime {spec.runtime_id}/{spec.generation} cannot restart")

            for (runtime_id, generation), entry in self._entries.items():
                status = await self._adapter.inspect(entry.handle)
                if status.returncode is not None:
                    entry.actual_state = RuntimeActualState.STOPPED
                if runtime_id == spec.runtime_id and generation != spec.generation and entry.actual_state not in (
                    RuntimeActualState.STOPPED,
                    RuntimeActualState.FAILED,
                ):
                    raise RuntimeConflictError(
                        f"runtime {spec.runtime_id} already has active generation {generation}"
                    )

            for (runtime_id, generation), (starting_spec, _) in self._startup_tasks.items():
                if runtime_id != spec.runtime_id:
                    continue
                if generation != spec.generation:
                    raise RuntimeConflictError(
                        f"runtime {spec.runtime_id} already has starting generation {generation}"
                    )
                if starting_spec != spec:
                    raise RuntimeConflictError(
                        f"runtime {spec.runtime_id}/{spec.generation} is starting with a conflicting spec"
                    )

            startup = self._startup_tasks.get(key)
            if startup is None:
                task = asyncio.create_task(
                    self._start_runtime_owned(key, spec, ready_timeout),
                    name=f"runtime-start:{spec.runtime_id}:{spec.generation}",
                )
                # Retrieve a terminal exception even if every awaiting caller is
                # cancelled, so a failed shared startup never leaks an
                # "exception was never retrieved" warning.
                task.add_done_callback(self._consume_startup_exception)
                self._startup_tasks[key] = (spec, task)
            else:
                _, task = startup

        # A caller cancellation must not cancel the startup shared by the
        # remaining idempotent callers.
        return await asyncio.shield(task)

    @staticmethod
    def _consume_startup_exception(task: "asyncio.Task[RuntimeEntry]") -> None:
        if not task.cancelled():
            task.exception()

    async def _start_runtime_owned(
        self, key: _RuntimeKey, spec: RuntimeSpec, ready_timeout: float
    ) -> RuntimeEntry:
        handle: Optional[RuntimeHandle] = None
        try:
            prepared = await self._adapter.prepare(spec)
            handle = await self._adapter.start(prepared)
            owner_nonce = str(prepared.provider_private_metadata.get("nonce", ""))
            entry = RuntimeEntry(spec, handle, owner_nonce, self._clock)
            entry.actual_state = RuntimeActualState.STARTING

            ready_result = await self._adapter.wait_ready(handle, deadline=ready_timeout)
            if not ready_result.ready:
                raise ReadyTimeoutError(
                    f"runtime {spec.runtime_id}/{spec.generation} did not become ready "
                    f"within {ready_timeout}s"
                )

            entry.actual_state = RuntimeActualState.READY
            async with self._start_lock:
                self._entries[key] = entry
            return entry
        except Exception:
            # Best-effort cleanup: stop the partially-started process.
            try:
                if handle is not None:
                    await self._adapter.stop(handle, reason="startup-failed", grace_period=5.0)
            except Exception:
                log.exception("failed to clean up runtime %s/%s after startup failure", *key)
            raise
        finally:
            async with self._start_lock:
                startup = self._startup_tasks.get(key)
                if startup is not None and startup[1] is asyncio.current_task():
                    self._startup_tasks.pop(key, None)

    def get_entry(self, runtime_id: str, generation: int) -> Optional[RuntimeEntry]:
        return self._entries.get(self._key(runtime_id, generation))

    async def report_effective_activity(self, runtime_id: str, generation: int) -> None:
        """Record a successful externally-observed effective activity event."""
        entry = self.get_entry(runtime_id, generation)
        if entry is None:
            return
        async with entry.lock:
            if entry.stopping:
                return
            entry.last_effective_runtime_activity = self._clock.monotonic()

    async def submit_input(self, runtime_id: str, generation: int, value: RuntimeInput) -> TurnResult:
        entry = self.get_entry(runtime_id, generation)
        if entry is None:
            raise RuntimeStoppingError(f"no runtime entry for {runtime_id}/{generation}")
        async with entry.lock:
            if entry.stopping:
                raise RuntimeStoppingError(f"runtime {runtime_id}/{generation} is stopping")
            if entry.actual_state == RuntimeActualState.DRAINING:
                entry.drain_epoch += 1
                entry.actual_state = RuntimeActualState.RUNNING
            entry.turn_epoch += 1
            accepted_turn_epoch = entry.turn_epoch
            entry.turn_inflight = True
            entry.turn_done_event.clear()
            now = self._clock.monotonic()
            entry.last_user_activity = now
            entry.last_effective_runtime_activity = now
            entry.actual_state = RuntimeActualState.RUNNING
        completed = False
        try:
            result = await self._adapter.submit_input(entry.handle, value)
            completed = True
        finally:
            async with entry.lock:
                if entry.turn_epoch == accepted_turn_epoch:
                    entry.turn_inflight = False
                    entry.turn_done_event.set()
                    if not entry.stopping and entry.actual_state != RuntimeActualState.DRAINING:
                        entry.actual_state = RuntimeActualState.IDLE
                if completed:
                    # Only a successful normal completion is effective activity;
                    # timeout/failure retains the earlier input-acceptance time.
                    entry.last_effective_runtime_activity = self._clock.monotonic()
        return result

    def note_log_activity(self, runtime_id: str, generation: int) -> None:
        """Pure stdout/log/heartbeat observation point.

        Intentionally a no-op: logs, heartbeats, and repeated-error loops
        must never refresh effective activity (spec 6).
        """

    async def check_idle_ttl(self, runtime_id: str, generation: int) -> RuntimeActualState:
        """Evaluate idle TTL for one runtime against the current clock.

        No turn in flight and TTL expired: drains fully (checkpoint + stop)
        before returning. A turn in flight at expiry only moves the state to
        DRAINING here; the caller must follow up with
        ``drain_inflight_and_stop`` once the turn finishes (spec 6.3) --
        this method never issues a KILL directly.
        """
        entry = self.get_entry(runtime_id, generation)
        if entry is None:
            return RuntimeActualState.STOPPED
        async with entry.lock:
            if entry.stopping:
                return entry.actual_state
            now = self._clock.monotonic()
            idle_for = now - entry.last_effective_runtime_activity
            if idle_for < entry.spec.idle_ttl_seconds:
                return entry.actual_state
            if entry.actual_state != RuntimeActualState.DRAINING:
                entry.drain_epoch += 1
            entry.actual_state = RuntimeActualState.DRAINING
            if entry.turn_inflight:
                return entry.actual_state
            entry.stopping = True

        # Outside the lock: bounded checkpoint, then stop. Once entry.stopping
        # is True the runtime is irreversibly headed for STOPPED.
        try:
            await self._adapter.checkpoint(entry.handle, deadline=5.0)
        except Exception:  # pragma: no cover - checkpoint is best-effort only
            log.warning("checkpoint failed during idle-TTL drain for %s/%s", runtime_id, generation)
        await self._adapter.stop(entry.handle, reason="idle_ttl_expired", grace_period=5.0)
        async with entry.lock:
            entry.desired_state = RuntimeActualState.STOPPED
            entry.actual_state = RuntimeActualState.STOPPED
        return entry.actual_state

    async def drain_inflight_and_stop(
        self, runtime_id: str, generation: int, wait_timeout: float = 5.0
    ) -> RuntimeActualState:
        """Bounded wait for an in-flight turn to finish, then stop (not KILL)."""
        entry = self.get_entry(runtime_id, generation)
        if entry is None:
            return RuntimeActualState.STOPPED
        async with entry.lock:
            if entry.actual_state != RuntimeActualState.DRAINING or entry.stopping:
                return entry.actual_state
            drain_epoch = entry.drain_epoch
        try:
            await asyncio.wait_for(entry.turn_done_event.wait(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            pass  # bounded wait elapsed; proceed to stop regardless (spec 6.3)
        async with entry.lock:
            if entry.drain_epoch != drain_epoch or entry.actual_state != RuntimeActualState.DRAINING:
                return entry.actual_state
            entry.stopping = True
            entry.actual_state = RuntimeActualState.STOPPING
        await self._adapter.stop(entry.handle, reason="idle_ttl_expired_drained", grace_period=5.0)
        async with entry.lock:
            entry.desired_state = RuntimeActualState.STOPPED
            entry.actual_state = RuntimeActualState.STOPPED
        return entry.actual_state

    def to_record(self, runtime_id: str, generation: int) -> Optional[RuntimeRecord]:
        entry = self.get_entry(runtime_id, generation)
        if entry is None:
            return None
        return RuntimeRecord(
            runtime_id=runtime_id,
            generation=generation,
            desired_state=entry.desired_state,
            actual_state=entry.actual_state,
            workdir=entry.handle.workdir,
            owner_nonce=entry.owner_nonce,
            last_effective_activity_monotonic=entry.last_effective_runtime_activity,
            exit_reason=entry.exit_reason,
        )


class RuntimeRegistry:
    """Reconcile support: replays ``RuntimeRecord``s against ``AgentRuntimeAdapter.reconcile``.

    Deliberately holds no live process handles of its own -- a fresh
    ``RuntimeRegistry``/adapter pair backed only by on-disk owner files must be
    able to reconcile records produced by a Harness that has since been
    dropped (simulating a Manager restart).
    """

    def __init__(self, adapter: AgentRuntimeAdapter, workspace_root: Path) -> None:
        self._adapter = adapter
        self._workspace_root = Path(workspace_root).resolve()

    async def reconcile_all(self, records: List[RuntimeRecord]) -> List[ReconcileDecision]:
        latest_generation: Dict[str, int] = {}
        for record in records:
            latest_generation[record.runtime_id] = max(
                latest_generation.get(record.runtime_id, record.generation), record.generation
            )

        decisions: List[ReconcileDecision] = []
        for record in records:
            if record.generation < latest_generation.get(record.runtime_id, record.generation):
                decisions.append(
                    ReconcileDecision(
                        action=ReconcileAction.IGNORE_STALE_GENERATION,
                        runtime_id=record.runtime_id,
                        generation=record.generation,
                    )
                )
                continue
            decisions.append(await self._adapter.reconcile(record))
        return decisions

    def scan_unknown(self, known: set) -> List[ReconcileDecision]:
        """Find on-disk ``<runtime_id>/generation-<N>/`` dirs with no matching record.

        Never deletes anything itself -- only reports QUARANTINE_UNKNOWN so a
        human/operator decides, per spec 7 ("do not delete unknown directories").
        """
        decisions: List[ReconcileDecision] = []
        if not self._workspace_root.exists():
            return decisions
        for runtime_dir in sorted(self._workspace_root.iterdir()):
            if not runtime_dir.is_dir():
                continue
            for gen_dir in sorted(runtime_dir.iterdir()):
                if not gen_dir.is_dir() or not gen_dir.name.startswith("generation-"):
                    continue
                try:
                    generation = int(gen_dir.name.split("-", 1)[1])
                except ValueError:
                    continue
                key = (runtime_dir.name, generation)
                if key in known:
                    continue
                owner_path = gen_dir / "runtime-owner.json"
                if not owner_path.exists():
                    continue
                decisions.append(
                    ReconcileDecision(
                        action=ReconcileAction.QUARANTINE_UNKNOWN,
                        runtime_id=runtime_dir.name,
                        generation=generation,
                    )
                )
        return decisions
