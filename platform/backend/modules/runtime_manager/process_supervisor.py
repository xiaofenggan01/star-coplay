"""Provider-neutral subprocess supervision: spawn, stdio NDJSON pump, stop escalation.

Scope is deliberately narrow: PID/process-group lifecycle and stdio framing
only. TTL, binding, and persona concerns live above this layer (later
phases). Nothing here imports from ``modules.runtime`` (legacy).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from modules.runtime_manager.clock import Clock, SystemClock
from modules.runtime_manager.contracts import ProviderStatus

log = logging.getLogger("runtime_manager.process_supervisor")

_PUMP_DRAIN_TIMEOUT = 2.0


class ProcessSupervisor:
    """Owns one child process's stdio and lifecycle for a single ``(runtime_id, generation)``."""

    def __init__(self, runtime_id: str, generation: int, clock: Optional[Clock] = None) -> None:
        self._runtime_id = runtime_id
        self._generation = generation
        self._clock = clock or SystemClock()

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._send_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()

        self._stdout_queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        self._log_lines: list[dict[str, Any]] = []
        self._pump_tasks: list[asyncio.Task[None]] = []

        self._ready = False
        self._ready_event = asyncio.Event()
        self._last_frame_monotonic: Optional[float] = None
        self._protocol_error_count = 0

        self._stopped = False
        self._exit_code: Optional[int] = None

    @property
    def runtime_id(self) -> str:
        return self._runtime_id

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def log_lines(self) -> list[dict[str, Any]]:
        return list(self._log_lines)

    async def start(self, *, command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> None:
        if self._proc is not None:
            raise RuntimeError("ProcessSupervisor.start() called twice")
        self._proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=dict(env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        log.info(
            "spawned runtime process pid=%s runtime_id=%s generation=%s",
            self._proc.pid,
            self._runtime_id,
            self._generation,
            extra={
                "event": "runtime_manager.process.spawned",
                "runtime_id": self._runtime_id,
                "generation": self._generation,
                "pid": self._proc.pid,
            },
        )
        self._pump_tasks.append(asyncio.create_task(self._pump_stdout()))
        self._pump_tasks.append(asyncio.create_task(self._pump_stderr()))

    async def _pump_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                return
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                self._protocol_error_count += 1
                self._log_lines.append({"stream": "stdout", "protocol_error": True, "raw": raw[:200]})
                continue
            self._last_frame_monotonic = self._clock.monotonic()
            if (
                not self._ready
                and isinstance(frame, dict)
                and frame.get("type") == "ready"
                and frame.get("runtime_id") == self._runtime_id
                and frame.get("generation") == self._generation
            ):
                self._ready = True
                self._ready_event.set()
                continue  # ready is a control frame consumed via wait_ready(); never enters the response queue
            await self._stdout_queue.put(frame)

    async def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._log_lines.append({"stream": "stderr", "line": line})

    async def wait_ready(self, deadline_seconds: float) -> bool:
        if self._ready:
            return True
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=deadline_seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def send_frame(self, frame: Mapping[str, Any]) -> bool:
        if self._stopped or self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            return False
        data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._send_lock:
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
                return True
            except (ConnectionResetError, BrokenPipeError):
                return False

    async def recv_frame(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        try:
            if timeout is None:
                return await self._stdout_queue.get()
            return await asyncio.wait_for(self._stdout_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def submit_turn(self, frame: Mapping[str, Any], timeout: float) -> Optional[dict[str, Any]]:
        """Send one frame and await its response, serialized against concurrent turns."""
        async with self._turn_lock:
            ok = await self.send_frame(frame)
            if not ok:
                return None
            return await self.recv_frame(timeout=timeout)

    async def inspect(self) -> ProviderStatus:
        returncode = self._proc.returncode if self._proc is not None else None
        healthy = self._proc is not None and returncode is None and self._protocol_error_count == 0
        return ProviderStatus(
            pid=self.pid,
            returncode=returncode,
            ready=self._ready,
            healthy=healthy,
            last_frame_monotonic=self._last_frame_monotonic,
            protocol_errors=self._protocol_error_count,
        )

    def _signal_group(self, sig: int) -> None:
        if self._proc is None or self._proc.pid is None:
            return
        try:
            pgid = os.getpgid(self._proc.pid)
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    async def stop(self, *, app_stop_frame: Mapping[str, Any], grace_period: float) -> "StopEscalationResult":
        if self._proc is None:
            self._stopped = True
            return StopEscalationResult(exit_code=None, escalation="already_stopped")
        if self._stopped or self._proc.returncode is not None:
            self._stopped = True
            await self._drain_pumps()
            return StopEscalationResult(exit_code=self._proc.returncode, escalation="already_stopped")

        escalation = "app_stop"
        await self.send_frame(app_stop_frame)
        code = await self._await_exit_or(grace_period)

        if code is None:
            escalation = "term"
            log.warning(
                "runtime pid=%s ignored app-level stop; sending SIGTERM (runtime_id=%s generation=%s)",
                self.pid,
                self._runtime_id,
                self._generation,
            )
            self._signal_group(signal.SIGTERM)
            code = await self._await_exit_or(grace_period)

        if code is None:
            escalation = "kill"
            log.warning(
                "runtime pid=%s ignored SIGTERM; sending SIGKILL (runtime_id=%s generation=%s)",
                self.pid,
                self._runtime_id,
                self._generation,
            )
            self._signal_group(signal.SIGKILL)
            code = await self._proc.wait()

        self._stopped = True
        self._exit_code = code
        await self._drain_pumps()
        return StopEscalationResult(exit_code=code, escalation=escalation)

    async def _await_exit_or(self, grace_period: float) -> Optional[int]:
        assert self._proc is not None
        try:
            return await asyncio.wait_for(self._proc.wait(), timeout=grace_period)
        except asyncio.TimeoutError:
            return None

    async def _drain_pumps(self) -> None:
        pending = [t for t in self._pump_tasks if not t.done()]
        if pending:
            _done, still_pending = await asyncio.wait(pending, timeout=_PUMP_DRAIN_TIMEOUT)
            for task in still_pending:
                task.cancel()
            if still_pending:
                await asyncio.gather(*still_pending, return_exceptions=True)
        for task in self._pump_tasks:
            if not task.done() or task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                log.warning("pump task raised during drain: %r", exc)


class StopEscalationResult:
    """Internal result of the stop escalation ladder; wrapped into StopResult by the adapter."""

    def __init__(self, exit_code: Optional[int], escalation: str) -> None:
        self.exit_code = exit_code
        self.escalation = escalation
