"""FakeAgentRuntimeAdapter: spawns the NDJSON fake runtime process for tests.

This is the only adapter implementation in phase 1. It exercises the full
``AgentRuntimeAdapter`` protocol against a real child process so tests prove
behavior against actual stdio/signal semantics, not mocks.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from modules.runtime_manager.clock import Clock, SystemClock
from modules.runtime_manager.contracts import (
    CheckpointResult,
    PreparedRuntime,
    ProtocolViolationError,
    ProviderStatus,
    ReadyResult,
    ReadinessFailureKind,
    ReconcileAction,
    ReconcileDecision,
    RuntimeActualState,
    RuntimeHandle,
    RuntimeInput,
    RuntimeRecord,
    RuntimeSpec,
    RuntimeStoppingError,
    StopResult,
    TurnResult,
    WorkdirConflictError,
)
from modules.runtime_manager.process_supervisor import ProcessSupervisor
from modules.runtime_manager.emergency_terminator import read_process_identity
from modules.runtime_manager.workspace import cleanup_transient_workdir, materialize_persona

_FAKE_RUNTIME_SCRIPT = Path(__file__).resolve().parent / "runtime_process.py"


class FakeAgentRuntimeAdapter:
    """Provider-neutral ``AgentRuntimeAdapter`` backed by ``fake/runtime_process.py``."""

    def __init__(self, clock: Optional[Clock] = None) -> None:
        self._clock = clock or SystemClock()

    async def prepare(self, spec: RuntimeSpec) -> PreparedRuntime:
        workspace_root = spec.workspace_root.resolve()
        workdir = (workspace_root / spec.runtime_id / f"generation-{spec.generation}").resolve()

        if os.path.commonpath([str(workspace_root), str(workdir)]) != str(workspace_root):
            raise WorkdirConflictError(f"workdir {workdir} escapes workspace_root {workspace_root}")

        owner_path = workdir / "runtime-owner.json"
        if owner_path.exists():
            existing = json.loads(owner_path.read_text(encoding="utf-8"))
            if existing.get("runtime_id") != spec.runtime_id or existing.get("generation") != spec.generation:
                raise WorkdirConflictError(
                    f"refusing to reuse workdir {workdir}: owned by "
                    f"{existing.get('runtime_id')}/{existing.get('generation')}"
                )

        (workdir / "scratch").mkdir(parents=True, exist_ok=True)
        (workdir / "logs").mkdir(parents=True, exist_ok=True)
        persona_path = materialize_persona(workdir, spec.persona_artifact)

        nonce = secrets.token_hex(16)
        owner_path.write_text(
            json.dumps(
                {
                    "runtime_id": spec.runtime_id,
                    "generation": spec.generation,
                    "provider": spec.provider,
                    "nonce": nonce,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mcp_path = workdir / "mcp.json"
        credential = secrets.token_hex(16)
        mcp_path.write_text(json.dumps({"credential": credential}), encoding="utf-8")
        mcp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600

        return PreparedRuntime(
            spec=spec,
            workdir=workdir,
            provider_private_metadata={
                "nonce": nonce,
                "owner_path": str(owner_path),
                "persona_path": str(persona_path) if persona_path else None,
            },
        )

    def _build_command(self, spec: RuntimeSpec) -> list[str]:
        meta = spec.provider_private_metadata
        cmd = [sys.executable, str(_FAKE_RUNTIME_SCRIPT)]
        ready_delay = meta.get("fake_ready_delay")
        if ready_delay is not None:
            cmd += ["--ready-delay", str(float(ready_delay))]
        if meta.get("fake_never_ready"):
            cmd.append("--never-ready")
        if meta.get("fake_ignore_stop"):
            cmd.append("--ignore-stop")
        if meta.get("fake_ignore_term"):
            cmd.append("--ignore-term")
        if meta.get("fake_crash_immediately"):
            cmd.append("--crash-immediately")
        return cmd

    async def start(self, prepared: PreparedRuntime) -> RuntimeHandle:
        spec = prepared.spec
        supervisor = ProcessSupervisor(spec.runtime_id, spec.generation, clock=self._clock)
        env = {"PATH": os.environ.get("PATH", "")}
        env.update(spec.env)
        env["STARCOPLAY_RUNTIME_OWNER_NONCE"] = str(prepared.provider_private_metadata["nonce"])
        await supervisor.start(command=self._build_command(spec), cwd=prepared.workdir, env=env)
        self._record_pid(prepared.workdir, supervisor.pid)
        return RuntimeHandle(
            runtime_id=spec.runtime_id,
            generation=spec.generation,
            workdir=prepared.workdir,
            transport=supervisor,
        )

    def _record_pid(self, workdir: Path, pid: Optional[int]) -> None:
        """Persist the child PID into the owner file so a *different* adapter
        instance (e.g. after a simulated Manager restart) can find and verify
        this process purely from disk, without any in-memory handle."""
        owner_path = workdir / "runtime-owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["pid"] = pid
        owner["process_identity"] = read_process_identity(pid) if pid is not None else None
        owner_path.write_text(json.dumps(owner, ensure_ascii=False), encoding="utf-8")

    async def wait_ready(self, handle: RuntimeHandle, deadline: float) -> ReadyResult:
        supervisor: ProcessSupervisor = handle.transport
        ok = await supervisor.wait_ready(deadline)
        return ReadyResult(
            ready=ok,
            runtime_id=handle.runtime_id,
            generation=handle.generation,
            failure_kind=None if ok else ReadinessFailureKind.TIMEOUT,
        )

    async def submit_input(self, handle: RuntimeHandle, value: RuntimeInput) -> TurnResult:
        supervisor: ProcessSupervisor = handle.transport
        status = await supervisor.inspect()
        if not status.healthy and status.returncode is not None:
            raise RuntimeStoppingError(
                f"runtime {handle.runtime_id}/{handle.generation} is not running (returncode={status.returncode})"
            )
        frame = {"type": "input", "request_id": value.request_id, "content": value.content}
        resp = await supervisor.submit_turn(frame, timeout=30.0)
        if resp is None:
            raise ProtocolViolationError(
                f"no response for input request_id={value.request_id} on {handle.runtime_id}/{handle.generation}"
            )
        if resp.get("type") != "output":
            raise ProtocolViolationError(f"unexpected frame in response to input: {resp}")
        return TurnResult(
            request_id=value.request_id,
            runtime_id=resp["runtime_id"],
            generation=resp["generation"],
            turn=resp["turn"],
            content=resp["content"],
            detail=resp,
        )

    async def inspect(self, handle: RuntimeHandle) -> ProviderStatus:
        supervisor: ProcessSupervisor = handle.transport
        return await supervisor.inspect()

    async def checkpoint(self, handle: RuntimeHandle, deadline: float) -> Optional[CheckpointResult]:
        supervisor: ProcessSupervisor = handle.transport
        frame = {"type": "checkpoint", "request_id": secrets.token_hex(8)}
        resp = await supervisor.submit_turn(frame, timeout=deadline)
        if resp is None or resp.get("type") != "checkpoint_result":
            return None
        return CheckpointResult(
            runtime_id=handle.runtime_id,
            generation=handle.generation,
            path=Path(resp["path"]),
            detail=resp,
        )

    async def stop(self, handle: RuntimeHandle, reason: str, grace_period: float) -> StopResult:
        supervisor: ProcessSupervisor = handle.transport
        result = await supervisor.stop(app_stop_frame={"type": "stop", "reason": reason}, grace_period=grace_period)
        cleanup_transient_workdir(handle.workdir)
        return StopResult(stopped=True, exit_code=result.exit_code, escalation=result.escalation, reason=reason)

    async def mcp_initialize(self, handle: RuntimeHandle, request_id: str) -> Dict[str, Any]:
        supervisor: ProcessSupervisor = handle.transport
        resp = await supervisor.submit_turn({"type": "mcp_initialize", "request_id": request_id}, timeout=10.0)
        if resp is None:
            raise ProtocolViolationError(f"no response to mcp_initialize request_id={request_id}")
        return resp

    async def mcp_tools_list(self, handle: RuntimeHandle, request_id: str) -> Dict[str, Any]:
        supervisor: ProcessSupervisor = handle.transport
        resp = await supervisor.submit_turn({"type": "mcp_tools_list", "request_id": request_id}, timeout=10.0)
        if resp is None:
            raise ProtocolViolationError(f"no response to mcp_tools_list request_id={request_id}")
        return resp

    async def mcp_call(
        self, handle: RuntimeHandle, tool: str, args: Optional[Dict[str, Any]], request_id: str
    ) -> Dict[str, Any]:
        supervisor: ProcessSupervisor = handle.transport
        frame = {"type": "mcp_call", "request_id": request_id, "tool": tool, "args": args or {}}
        resp = await supervisor.submit_turn(frame, timeout=10.0)
        if resp is None:
            raise ProtocolViolationError(f"no response to mcp_call request_id={request_id}")
        return resp

    async def revoke_credential(self, handle: RuntimeHandle) -> None:
        """Mark this runtime's credential revoked; the child checks this marker
        on every mcp_call, so a still-alive process rejects calls immediately
        after revocation (structured mcp_error, not a crash/hang)."""
        (handle.workdir / "credential.revoked").write_text("revoked", encoding="utf-8")

    def receipt_context(self, handle: RuntimeHandle) -> dict[str, str]:
        """Return provider-private fields for an ``InputReceipt``.

        The Fake adapter has no persistent transcript; returns a placeholder
        session_id and cursor=0.
        """
        return {
            "provider": "fake",
            "session_id": f"fake-{handle.runtime_id}-{handle.generation}",
            "transcript_cursor": "0",
        }

    def claude_projects_dir(self, handle: RuntimeHandle):
        """The Fake adapter has no native transcript directory."""
        return None

    async def query_isolation(self, handle: RuntimeHandle, request_id: str) -> Dict[str, Any]:
        """Test-only observability call: returns only non-secret identity
        markers (cwd, credential fingerprint, one whitelisted env var, a log
        sentinel) so isolation between two runtimes can be asserted directly
        without ever exposing the full environment or credential text."""
        supervisor: ProcessSupervisor = handle.transport
        resp = await supervisor.submit_turn({"type": "query_isolation", "request_id": request_id}, timeout=10.0)
        if resp is None:
            raise ProtocolViolationError(f"no response to query_isolation request_id={request_id}")
        return resp

    async def reconcile(self, record: RuntimeRecord) -> ReconcileDecision:
        """Reconcile purely from the on-disk owner file + OS process liveness.

        Deliberately holds no reference to any live ``RuntimeHandle``/
        ``ProcessSupervisor`` from a prior adapter instance -- this must work
        even when called from a brand-new ``FakeAgentRuntimeAdapter`` after
        the original one (and its Harness) has been dropped, simulating a
        Manager restart.
        """
        owner_path = record.workdir / "runtime-owner.json"
        if not owner_path.exists():
            return ReconcileDecision(
                action=ReconcileAction.QUARANTINE_UNKNOWN,
                runtime_id=record.runtime_id,
                generation=record.generation,
            )
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        if (
            owner.get("runtime_id") != record.runtime_id
            or owner.get("generation") != record.generation
            or owner.get("nonce") != record.owner_nonce
        ):
            return ReconcileDecision(
                action=ReconcileAction.QUARANTINE_UNKNOWN,
                runtime_id=record.runtime_id,
                generation=record.generation,
            )

        pid = owner.get("pid")
        alive = False
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True  # exists, owned by someone else; treat as alive

        if not alive:
            action = (
                ReconcileAction.MARK_STOPPED
                if record.desired_state == RuntimeActualState.STOPPED
                else ReconcileAction.MARK_FAILED
            )
            return ReconcileDecision(
                action=action, runtime_id=record.runtime_id, generation=record.generation, detail={"pid": pid}
            )

        if record.desired_state == RuntimeActualState.STOPPED:
            await self._terminate_orphan(pid)
            return ReconcileDecision(
                action=ReconcileAction.TERMINATE_ORPHAN,
                runtime_id=record.runtime_id,
                generation=record.generation,
                detail={"pid": pid},
            )

        return ReconcileDecision(
            action=ReconcileAction.ADOPT, runtime_id=record.runtime_id, generation=record.generation, detail={"pid": pid}
        )

    async def _terminate_orphan(self, pid: int) -> None:
        """Escalating stop for a trusted orphan found alive with desired=STOPPED.

        No ``ProcessSupervisor`` is available (it belonged to the dropped
        adapter instance), so this signals the process group directly.
        """
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(30):
            await asyncio.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
