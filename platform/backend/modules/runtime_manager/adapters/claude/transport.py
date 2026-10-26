"""Claude-private process transport: spawn, stdio NDJSON pump, turn
accumulation, stop escalation.

This deliberately mirrors the *shape* of
``modules.runtime_manager.process_supervisor.ProcessSupervisor`` (spawn once,
concurrently drain stdout/stderr into structured events, serialize writes
under a lock, TERM-then-KILL escalation) rather than subclassing or importing
it: Claude's frame semantics differ enough (no single "ready" control frame;
a turn is delimited by ``result``, not a request id) that reusing
``ProcessSupervisor``'s ready-detection and turn-response matching directly
would require bending its fake-runtime-shaped state machine to a protocol it
was never designed for. Process/stdio/stop-escalation *responsibilities*
stay identical; framing does not.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from modules.runtime_manager.adapters.claude.errors import (
    ClaudeProcessExitedError,
    ClaudeResultMissingError,
    ClaudeTransportError,
)
from modules.runtime_manager.adapters.claude.protocol import (
    _MAX_STDERR_DIAGNOSTICS,
    classify_stderr_diagnostic,
    extract_assistant_text,
    extract_startup_summary,
    is_assistant,
    is_result,
    is_system_api_retry,
    is_system_init,
    redact_stderr_secrets,
)
from modules.runtime_manager.contracts import ProtocolViolationError, ProviderStatus
from common.observability import emit_metric

log = logging.getLogger("runtime_manager.adapters.claude.transport")

_PUMP_DRAIN_TIMEOUT = 2.0
_CLAUDE_STREAM_READER_LIMIT = 8 * 1024 * 1024


class ClaudeTurnOutcome:
    """Accumulated result of one turn: assistant text + the terminal result frame."""

    def __init__(self, text: str, result_frame: Mapping[str, Any], session_id: Optional[str]) -> None:
        self.text = text
        self.result_frame = result_frame
        self.session_id = session_id


class ClaudeProcessTransport:
    """Owns one Claude CLI child process's stdio and lifecycle.

    Does NOT write a custom transcript tee.  Claude's native session
    persistence lands under ``$CLAUDE_CONFIG_DIR/projects/`` and the
    output reader locates it by matching the current transport session_id.
    """

    def __init__(self, runtime_id: str, generation: int) -> None:
        self._runtime_id = runtime_id
        self._generation = generation

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._send_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._turn_inflight = False
        self._turn_terminal_seen = False

        self._stdout_queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        self._log_lines: List[Dict[str, Any]] = []
        self._pump_tasks: List[asyncio.Task[None]] = []

        self._last_frame_monotonic: Optional[float] = None
        self._protocol_error_count = 0
        self._session_id: Optional[str] = None
        self._retry_count = 0
        self._stdout_pump_failed = False
        self._stdout_pump_error_type: Optional[str] = None
        self._stderr_pump_failed = False
        self._stderr_pump_error_type: Optional[str] = None

        # Bootstrap init is captured once (first system/init frame) and is
        # immutable thereafter -- later per-turn inits must not overwrite it.
        # Only the *sanitised* startup summary is retained; the raw init frame
        # is never stored.
        self._bootstrap_startup_summary: Optional[Dict[str, Any]] = None
        self._bootstrap_init_captured = False
        # MCP-related stderr diagnostics accumulated during the process lifetime.
        self._stderr_diagnostics: List[Dict[str, Any]] = []

        self._stopped = False
        self._exit_code: Optional[int] = None

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None else None

    @property
    def returncode(self) -> Optional[int]:
        return self._proc.returncode if self._proc is not None else None

    @property
    def turn_inflight(self) -> bool:
        return self._turn_inflight and not self._turn_terminal_seen

    @property
    def session_id(self) -> Optional[str]:
        """Claude-private session id, for adapter-private logging/tests only.

        Never surfaced through the public ``AgentRuntimeAdapter`` contract.
        """
        return self._session_id

    @property
    def protocol_error_count(self) -> int:
        return self._protocol_error_count

    @property
    def log_lines(self) -> List[Dict[str, Any]]:
        return list(self._log_lines)

    @property
    def bootstrap_startup_summary(self) -> Optional[Dict[str, Any]]:
        """Sanitised startup summary derived from the first (bootstrap)
        ``system/init`` frame.  Never contains raw frame data, credentials,
        or secrets.

        Immutable after first capture; later per-turn inits do not overwrite it.
        """
        return self._bootstrap_startup_summary

    @property
    def stderr_diagnostics(self) -> List[Dict[str, Any]]:
        """Sanitised MCP-related stderr diagnostics accumulated during the
        process lifetime.  Safe to surface in ``ProviderStatus.detail``."""
        return list(self._stderr_diagnostics)

    async def start(self, *, command: Sequence[str], cwd: Path, env: Mapping[str, str]) -> None:
        if self._proc is not None:
            raise RuntimeError("ClaudeProcessTransport.start() called twice")
        started = time.monotonic()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                env=dict(env),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=_CLAUDE_STREAM_READER_LIMIT,
            )
        except Exception as error:
            duration_ms = (time.monotonic() - started) * 1000
            log.error(
                "claude runtime process spawn failed",
                extra={"event": "runtime_manager.claude.process.spawn_failed",
                       "runtime_id": self._runtime_id, "generation": self._generation,
                       "adapter_kind": "claude", "outcome": "failed",
                       "error_code": "adapter_spawn_failed",
                       "exception_type": type(error).__name__, "duration_ms": duration_ms},
            )
            emit_metric(
                "runtime_adapter_spawn_total", "counter", 1, outcome="failed",
                adapter_kind="claude", runtime_id=self._runtime_id, generation=self._generation,
                error_code="adapter_spawn_failed", stage="start",
            )
            emit_metric(
                "runtime_adapter_spawn_duration_ms", "timing", duration_ms, outcome="failed",
                adapter_kind="claude", runtime_id=self._runtime_id,
                generation=self._generation, error_code="adapter_spawn_failed", stage="start",
            )
            raise
        log.info(
            "spawned claude runtime process",
            extra={
                "event": "runtime_manager.claude.process.spawned",
                "runtime_id": self._runtime_id,
                "generation": self._generation,
                "pid": self._proc.pid,
            },
        )
        emit_metric(
            "runtime_adapter_spawn_total", "counter", 1, outcome="success",
            adapter_kind="claude", runtime_id=self._runtime_id, generation=self._generation,
            stage="start",
        )
        emit_metric(
            "runtime_adapter_spawn_duration_ms", "timing", (time.monotonic() - started) * 1000,
            outcome="success", adapter_kind="claude", runtime_id=self._runtime_id,
            generation=self._generation, stage="start",
        )
        self._pump_tasks.append(asyncio.create_task(self._pump_stdout()))
        self._pump_tasks.append(asyncio.create_task(self._pump_stderr()))

    async def _pump_stdout(self) -> None:
        try:
            await self._pump_stdout_frames()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._publish_pump_failure("stdout", error)

    async def _pump_stdout_frames(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                if self._proc.returncode is None:
                    await self._proc.wait()
                await self._stdout_queue.put({"type": "__eof__"})
                return
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                self._protocol_error_count += 1
                self._log_lines.append({
                    "stream": "stdout",
                    "protocol_error": True,
                    "raw": redact_stderr_secrets(raw)[:200],
                })
                continue
            # No custom transcript tee — Claude's native persistence writes
            # its own JSONL under $CLAUDE_CONFIG_DIR/projects/.
            self._last_frame_monotonic = time.monotonic()
            if isinstance(frame, dict) and is_system_init(frame):
                sid = frame.get("session_id")
                if isinstance(sid, str):
                    self._session_id = sid
                if not self._bootstrap_init_captured:
                    self._bootstrap_startup_summary = extract_startup_summary(frame)
                    self._bootstrap_init_captured = True
            if isinstance(frame, dict) and is_system_api_retry(frame):
                self._retry_count += 1
                self._log_lines.append({"stream": "stdout", "event": "api_retry", "count": self._retry_count})
            if isinstance(frame, dict) and (
                is_result(frame)
                or (
                    is_assistant(frame)
                    and isinstance(frame.get("message"), Mapping)
                    and frame["message"].get("stop_reason") == "end_turn"
                )
            ):
                self._turn_terminal_seen = True
            await self._stdout_queue.put(frame)

    async def _pump_stderr(self) -> None:
        try:
            await self._pump_stderr_frames()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._publish_pump_failure("stderr", error)

    async def _pump_stderr_frames(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            # Always store a secret-redacted version in _log_lines; never
            # retain the raw line.
            safe = redact_stderr_secrets(line)
            self._log_lines.append({"stream": "stderr", "line": safe})
            # MCP-related diagnostics are additionally classified and capped.
            if len(self._stderr_diagnostics) >= _MAX_STDERR_DIAGNOSTICS:
                continue
            diagnostic = classify_stderr_diagnostic(line)
            if diagnostic is not None:
                self._stderr_diagnostics.append(diagnostic)

    async def _publish_pump_failure(self, stream: str, error: Exception) -> None:
        error_type = type(error).__name__
        if stream == "stdout":
            self._stdout_pump_failed = True
            self._stdout_pump_error_type = error_type
            self._protocol_error_count += 1
        else:
            self._stderr_pump_failed = True
            self._stderr_pump_error_type = error_type
        log.error(
            "claude %s pump failed",
            stream,
            extra={
                "event": f"runtime_manager.claude.{stream}_pump_failed",
                "runtime_id": self._runtime_id,
                "generation": self._generation,
                "adapter_kind": "claude",
                "outcome": "failed",
                "error_code": "adapter_transport_failed",
                "exception_type": error_type,
            },
        )
        emit_metric(
            "runtime_adapter_transport_failure_total", "counter", 1,
            outcome="failed", stage="turn", adapter_kind="claude",
            runtime_id=self._runtime_id, generation=self._generation,
            error_code="adapter_transport_failed",
        )
        await self._stdout_queue.put({
            "type": "__transport_error__",
            "stream": stream,
            "error_type": error_type,
        })

    async def send_frame(self, frame: Mapping[str, Any]) -> bool:
        async with self._send_lock:
            return await self._send_frame_locked(frame)

    async def _send_frame_locked(self, frame: Mapping[str, Any]) -> bool:
        if self._stopped or self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            return False
        data = (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()
            return True
        except (ConnectionResetError, BrokenPipeError):
            return False

    async def steer_turn(self, content: str) -> bool:
        """Inject user input into the active Claude turn without a second reader."""
        from modules.runtime_manager.adapters.claude.protocol import build_user_frame

        async with self._send_lock:
            if not self.turn_inflight:
                return False
            return await self._send_frame_locked(build_user_frame(content))

    async def interrupt_turn(self) -> bool:
        """Ask Claude to stop the active turn at its protocol boundary."""
        async with self._send_lock:
            if not self.turn_inflight:
                return False
            return await self._send_frame_locked({
                "type": "control_request",
                "request_id": f"interrupt-{uuid.uuid4().hex}",
                "request": {"subtype": "interrupt"},
            })

    async def run_turn(
        self,
        content: str,
        *,
        deadline_seconds: Optional[float] = None,
        terminal_result_grace_seconds: float = 5.0,
    ) -> ClaudeTurnOutcome:
        """Send one user frame and accumulate frames until ``result`` or the
        process exits, serialized against concurrent turns. Never treats
        ``api_retry`` or PID liveness as a completed turn.

        When *deadline_seconds* is not ``None`` the absolute deadline is
        computed once and the remaining time is used on each queue wait.
        When ``None`` the turn waits indefinitely (product business-turn
        semantics — process exit or explicit stop only).
        """
        from modules.runtime_manager.adapters.claude.protocol import build_user_frame

        async with self._turn_lock:
            self._turn_inflight = True
            self._turn_terminal_seen = False
            ok = await self.send_frame(build_user_frame(content))
            if not ok:
                async with self._send_lock:
                    self._turn_inflight = False
            if not ok:
                raise ClaudeProcessExitedError(
                    f"stdin closed before sending turn for {self._runtime_id}/{self._generation}",
                    returncode=self.returncode,
                )

            try:
                assistant_text_parts: List[str] = []
                loop = asyncio.get_event_loop()
                deadline_at: Optional[float] = (
                    loop.time() + deadline_seconds if deadline_seconds is not None else None
                )
                terminal_deadline_at: Optional[float] = None
                while True:
                    deadlines = tuple(
                        value for value in (deadline_at, terminal_deadline_at)
                        if value is not None
                    )
                    if deadlines:
                        remaining = min(deadlines) - loop.time()
                        if remaining <= 0:
                            if terminal_deadline_at is not None and terminal_deadline_at <= loop.time():
                                raise ClaudeResultMissingError(
                                    f"terminal assistant frame was not followed by result for "
                                    f"{self._runtime_id}/{self._generation}",
                                    assistant_text="".join(assistant_text_parts),
                                )
                            raise asyncio.TimeoutError(
                                f"no result frame within {deadline_seconds}s for "
                                f"{self._runtime_id}/{self._generation}"
                            )
                        try:
                            frame = await asyncio.wait_for(self._stdout_queue.get(), timeout=remaining)
                        except asyncio.TimeoutError:
                            terminal_deadline_was_first = (
                                terminal_deadline_at is not None
                                and (
                                    deadline_at is None
                                    or terminal_deadline_at <= deadline_at
                                )
                            )
                            if terminal_deadline_was_first:
                                raise ClaudeResultMissingError(
                                    f"terminal assistant frame was not followed by result for "
                                    f"{self._runtime_id}/{self._generation}",
                                    assistant_text="".join(assistant_text_parts),
                                ) from None
                            raise asyncio.TimeoutError(
                                f"no result frame within {deadline_seconds}s for "
                                f"{self._runtime_id}/{self._generation}"
                            ) from None
                    else:
                        frame = await self._stdout_queue.get()

                    if frame.get("type") == "__eof__":
                        if self._stdout_pump_failed or self._stderr_pump_failed:
                            stream = "stdout" if self._stdout_pump_failed else "stderr"
                            error_type = (
                                self._stdout_pump_error_type
                                if self._stdout_pump_failed else self._stderr_pump_error_type
                            )
                            raise ClaudeTransportError(
                                f"claude {stream} pump failed for "
                                f"{self._runtime_id}/{self._generation} "
                                f"({error_type or 'unknown'})"
                            )
                        raise ClaudeProcessExitedError(
                            f"claude process exited mid-turn for {self._runtime_id}/{self._generation}",
                            returncode=self.returncode,
                        )
                    if frame.get("type") == "__transport_error__":
                        raise ClaudeTransportError(
                            f"claude {frame.get('stream', 'stdio')} pump failed for "
                            f"{self._runtime_id}/{self._generation} "
                            f"({frame.get('error_type', 'unknown')})"
                        )
                    if is_assistant(frame):
                        assistant_text_parts.append(extract_assistant_text(frame))
                        message = frame.get("message")
                        if (
                            isinstance(message, Mapping)
                            and message.get("stop_reason") == "end_turn"
                        ):
                            terminal_deadline_at = loop.time() + terminal_result_grace_seconds
                        continue
                    if is_system_init(frame) or is_system_api_retry(frame):
                        continue  # per-turn init / transient retry: neither ends nor fails the turn
                    if is_result(frame):
                        return ClaudeTurnOutcome(
                            text="".join(assistant_text_parts),
                            result_frame=frame,
                            session_id=self._session_id,
                        )
                    # Unrecognized frame type: log and keep waiting for the result boundary.
                    self._log_lines.append({
                        "stream": "stdout",
                        "unrecognized_frame_type": frame.get("type"),
                    })
            finally:
                async with self._send_lock:
                    self._turn_inflight = False
                    self._turn_terminal_seen = False

    async def inspect(self) -> ProviderStatus:
        returncode = self.returncode
        healthy = (
            self._proc is not None
            and returncode is None
            and self._protocol_error_count == 0
            and not self._stdout_pump_failed
            and not self._stderr_pump_failed
        )
        detail: Dict[str, Any] = {"retry_count": self._retry_count}
        detail["stdout_pump_failed"] = self._stdout_pump_failed
        if self._stdout_pump_error_type is not None:
            detail["stdout_pump_error_type"] = self._stdout_pump_error_type
        detail["stderr_pump_failed"] = self._stderr_pump_failed
        if self._stderr_pump_error_type is not None:
            detail["stderr_pump_error_type"] = self._stderr_pump_error_type
        if self._stderr_diagnostics:
            detail["stderr_diagnostics"] = list(self._stderr_diagnostics)
        return ProviderStatus(
            pid=self.pid,
            returncode=returncode,
            ready=self._session_id is not None,
            healthy=healthy,
            last_frame_monotonic=self._last_frame_monotonic,
            protocol_errors=self._protocol_error_count,
            detail=detail,
        )

    def _signal_group(self, sig: int) -> None:
        if self._proc is None or self._proc.pid is None:
            return
        try:
            pgid = os.getpgid(self._proc.pid)
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    async def stop(self, *, grace_period: float) -> "ClaudeStopEscalationResult":
        if self._proc is None:
            self._stopped = True
            return ClaudeStopEscalationResult(exit_code=None, escalation="already_stopped")
        if self._stopped or self._proc.returncode is not None:
            self._stopped = True
            await self._drain_pumps()
            return ClaudeStopEscalationResult(exit_code=self._proc.returncode, escalation="already_stopped")

        escalation = "eof"
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
        except (ConnectionResetError, BrokenPipeError):
            pass
        code = await self._await_exit_or(grace_period)

        if code is None:
            escalation = "term"
            log.warning(
                "claude pid=%s did not exit after stdin EOF; sending SIGTERM (runtime_id=%s generation=%s)",
                self.pid,
                self._runtime_id,
                self._generation,
            )
            self._signal_group(signal.SIGTERM)
            code = await self._await_exit_or(grace_period)

        if code is None:
            escalation = "kill"
            log.warning(
                "claude pid=%s ignored SIGTERM; sending SIGKILL (runtime_id=%s generation=%s)",
                self.pid,
                self._runtime_id,
                self._generation,
            )
            self._signal_group(signal.SIGKILL)
            code = await self._proc.wait()

        self._stopped = True
        self._exit_code = code
        await self._drain_pumps()
        return ClaudeStopEscalationResult(exit_code=code, escalation=escalation)

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
                log.warning("claude pump task raised during drain: %r", exc)


class ClaudeStopEscalationResult:
    def __init__(self, exit_code: Optional[int], escalation: str) -> None:
        self.exit_code = exit_code
        self.escalation = escalation
