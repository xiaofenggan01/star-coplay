"""``ClaudeAgentRuntimeAdapter``: real Claude CLI implementation of the
provider-neutral ``AgentRuntimeAdapter`` Protocol.

MCP is not implemented here (see package docstring); checkpoint is
unsupported because session persistence is generation-local.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Optional

import google.protobuf

from modules.runtime_manager.adapters.claude.errors import (
    ClaudeProcessExitedError,
    ClaudeResultMissingError,
    ClaudeTransportError,
    ClaudeTurnFailedError,
    ClaudeTurnTimeoutError,
)
from modules.runtime_manager.adapters.claude.protocol import (
    BOOTSTRAP_PROMPT,
    HOSTED_MCP_ALLOWED_TOOL,
    ClaudeLaunchConfig,
    hosted_mcp_connected,
    resolve_launch_config,
    result_is_success,
)
from modules.runtime_manager.adapters.claude.transport import ClaudeProcessTransport
from modules.runtime_manager.contracts import (
    CheckpointResult,
    PreparedRuntime,
    ProviderStatus,
    ReadinessFailureKind,
    ReadyResult,
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
from modules.runtime_manager.workspace import (
    cleanup_transient_workdir,
    materialize_persona,
    materialize_persona_prompt,
    materialize_system_prompt,
)
from modules.runtime_manager.emergency_terminator import read_process_identity
from common.observability import emit_metric


# Hosted MCP is launched by Claude from the per-runtime workdir, not from the
# application checkout.  Bind the two import roots from this trusted adapter
# module so proxy startup never depends on caller cwd or ambient PYTHONPATH.
_BACKEND_IMPORT_ROOT = Path(__file__).resolve().parents[4]
_REPOSITORY_IMPORT_ROOT = Path(__file__).resolve().parents[6]
_PROTOBUF_IMPORT_ROOT = Path(google.protobuf.__file__).resolve().parents[2]
_HOSTED_PROXY_PYTHONPATH = os.pathsep.join((
    str(_REPOSITORY_IMPORT_ROOT), str(_BACKEND_IMPORT_ROOT), str(_PROTOBUF_IMPORT_ROOT),
))

log = logging.getLogger("runtime_manager.adapters.claude.adapter")

_HOSTED_GAMEPLAY_INSTRUCTIONS = """# Hosted Stardew companion contract

- Assistant text is private inner thought. The player cannot see it and it never drives the Mod.
- For every player chat turn that needs a spoken reply, call `mcp__starcoplay-hosted__say` exactly once.
- A normal player turn may produce at most one `say` call. Never combine replies owed to earlier turns into a later `say`.
- Put the complete in-character reply in the tool's `message` argument.
- Do not return the spoken reply as assistant text instead of calling the tool.
- After a successful `say` call, do not repeat the reply in assistant text or call `say` again in the same turn.
- For simple conversation, answer with `say` and do not invent an in-game action.
- When a high-level hosted skill matches the request, prefer it over manually chaining atomic tools.
- For a named destination such as a shop, saloon, clinic, or blacksmith, use `go_to_place` so navigation runs as one cancellable background job.
- Use atomic `go_to` only when the exact map id and destination tile are already known from authoritative context.
- Never replace a failed named-place navigation with repeated `move_to` calls or other short-step movement.
- Observe only the facts needed for a decision; do not guess game state, routes, exits, or the player's location, and do not claim an action succeeded without its result.
- For Stardew facts you are unsure about, search the hosted wiki before answering.
- Never expose coordinates, tile positions, map ids, warp details, refs, tool names, skill names, command ids, or job ids in player-facing speech.
- Stardew chat does not reliably render Unicode emoji. Express emotion with natural text, a supported text face, or the official `emote` tool.
- A message beginning with `Steering preview` is not a player turn. You must not call `say` for it; use it only to adjust the current plan. The same player message will arrive as a normal turn for its spoken reply.
- A message beginning with `Internal background game task completion` is a system completion turn. If its outcome matters to the player, summarize it naturally with exactly one `say`; never expose its raw payload or scheduler state.
- While a background task is running, treat each new normal player message as an independent turn. Answer a simple question directly instead of repeating or batching earlier chat.
- Starting or queueing a background skill is not a completed action. Never tell the player it succeeded until the completion result arrives.
- Walking onto a walk-through warp tile triggers the map transition; do not call `interact` for that warp.
- After a successful movement that can cross maps, query the current game state before deciding whether to retry.
- Treat `warp_triggered=true` and the returned final location as authoritative movement results.
- Never retry `INVALID_PARAMS` with identical arguments; retry at most once, and only after materially correcting the arguments.
- After repeated navigation or interaction failures, observe the current state again, try only a bounded number of alternative approaches, then stop and explain the blocker to the player.
- When a normal player message or `Steering preview` asks you to stop or change tasks, treat the old background job as cancelled, stop the old plan immediately, and do not issue another tool call for it.
- Agent-origin and system-origin chat are not player requests and must never start another response.
"""


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        # Windows reports an exited or invalid PID as ERROR_INVALID_PARAMETER
        # instead of ProcessLookupError.
        if os.name == "nt" and getattr(exc, "winerror", None) == 87:
            return False
        raise


# Only these ambient variables are inherited by the Claude subprocess.  In
# particular, the Gateway process also holds database and platform credentials
# which the agent runtime must never receive implicitly.
_CLAUDE_AMBIENT_ENV_KEYS = frozenset({
    "PATH",
    "SystemRoot",
    "SYSTEMROOT",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL",
})


def _runtime_environment(spec: RuntimeSpec, owner_nonce: str) -> Dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _CLAUDE_AMBIENT_ENV_KEYS
    }
    # RuntimeSpec.env may override a runtime-safe value (for example an
    # isolated HOME or per-runtime Anthropic credential), but it must not turn
    # into an escape hatch for NODE_OPTIONS, loader injection, or Gateway
    # secrets.
    env.update({
        key: value
        for key, value in spec.env.items()
        if key in _CLAUDE_AMBIENT_ENV_KEYS
    })
    # A Hosted Runtime relay token is the sole model credential. Never retain
    # a process-global official Anthropic API key alongside it.
    if spec.env.get("ANTHROPIC_AUTH_TOKEN"):
        env.pop("ANTHROPIC_API_KEY", None)
    env["STARCOPLAY_RUNTIME_OWNER_NONCE"] = owner_nonce
    return env


@dataclass
class _ClaudeHandleState:
    """Provider-private per-handle state. Lives only in ``RuntimeHandle.transport``;
    never leaks into ``RuntimeRecord`` or any public dataclass field."""

    transport: ClaudeProcessTransport
    launch_config: ClaudeLaunchConfig
    turn_counter: int = 0
    bootstrapped: bool = False
    bootstrap_ok: bool = False
    is_hosted_mcp: bool = False


class ClaudeAgentRuntimeAdapter:
    """Provider-neutral ``AgentRuntimeAdapter`` backed by the real ``claude`` CLI."""

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
        hosted_endpoint = spec.provider_private_metadata.get("hosted_mcp_endpoint")
        if hosted_endpoint:
            persona_instructions = (
                spec.persona_artifact.instruction_payload.strip()
                if spec.persona_artifact is not None else ""
            )
            combined_prompt = "\n\n".join(
                part for part in (persona_instructions, _HOSTED_GAMEPLAY_INSTRUCTIONS.strip())
                if part
            )
            persona_prompt_path = materialize_system_prompt(workdir, combined_prompt)
        else:
            persona_prompt_path = materialize_persona_prompt(workdir, spec.persona_artifact)

        nonce = secrets.token_hex(16)
        owner_path.write_text(
            json.dumps(
                {
                    "runtime_id": spec.runtime_id,
                    "generation": spec.generation,
                    "provider": spec.provider,
                    "nonce": nonce,
                    "adapter_config_revision": spec.provider_private_metadata.get("adapter_config_revision"),
                    "adapter_config_hash": spec.provider_private_metadata.get("adapter_config_hash"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        owner_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        launch_metadata = dict(spec.provider_private_metadata)
        if persona_prompt_path:
            launch_metadata["append_system_prompt_file"] = str(persona_prompt_path)
        launch_config = resolve_launch_config(launch_metadata)
        mcp_metadata: Dict[str, Any] = {}
        controlled_server = spec.provider_private_metadata.get("controlled_mcp_server")
        if hosted_endpoint:
            credential_path = workdir / "mcp-credential"
            mcp_config_path = workdir / "mcp-config.json"
            mcp_config = {
                "mcpServers": {
                    "starcoplay-hosted": {
                        "type": "stdio",
                        # The hosted catalog is the runtime's fixed product
                        # surface.  Keep it eagerly callable: with deferred
                        # MCP loading and --tools "", Claude can otherwise
                        # describe a call_mcp_tool wrapper without issuing a
                        # real MCP request after READY_UNBOUND activates.
                        "alwaysLoad": True,
                        "command": sys.executable,
                        "args": ["-m", "modules.mcp.transports.stdio_proxy"],
                        "env": {
                            "STARCOPLAY_HOSTED_MCP_ENDPOINT": str(hosted_endpoint),
                            "STARCOPLAY_MCP_CREDENTIAL_PATH": str(credential_path),
                            "PYTHONPATH": _HOSTED_PROXY_PYTHONPATH,
                            "PYTHONIOENCODING": "utf-8",
                            "PYTHONUTF8": "1",
                        },
                    }
                }
            }
            mcp_config_path.write_text(json.dumps(mcp_config, ensure_ascii=False), encoding="utf-8")
            mcp_config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            mcp_metadata = {
                "mcp_config_path": str(mcp_config_path),
                "mcp_credential_path": str(credential_path),
                "hosted_mcp_endpoint": str(hosted_endpoint),
                # This is a Claude permission grant, not a built-in tool
                # enablement.  Keep it provider-generated and namespace-wide
                # only for the governed hosted MCP surface.
                "claude_allowed_tools": (HOSTED_MCP_ALLOWED_TOOL,),
            }
        elif controlled_server:
            credential = secrets.token_urlsafe(32)
            credential_path = workdir / "mcp-credential"
            credential_path.write_text(credential, encoding="utf-8")
            credential_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            audit_path = workdir / "logs" / "mcp-audit.jsonl"
            revoke_path = workdir / "mcp-revoked"
            mcp_config_path = workdir / "mcp-config.json"
            mcp_config = {
                "mcpServers": {
                    "controlled": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(Path(str(controlled_server)).resolve())],
                        "env": {
                            "CONTROLLED_MCP_CREDENTIAL_PATH": str(credential_path),
                            "CONTROLLED_MCP_REVOKE_MARKER_PATH": str(revoke_path),
                            "CONTROLLED_MCP_AUDIT_LOG_PATH": str(audit_path),
                            "CONTROLLED_MCP_RUNTIME_ID": spec.runtime_id,
                        },
                    }
                }
            }
            mcp_config_path.write_text(json.dumps(mcp_config, ensure_ascii=False), encoding="utf-8")
            mcp_config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            mcp_metadata = {
                "mcp_config_path": str(mcp_config_path),
                "mcp_revoke_path": str(revoke_path),
                "mcp_audit_path": str(audit_path),
                "credential_fingerprint": hashlib.sha256(credential.encode()).hexdigest()[:16],
            }
        return PreparedRuntime(
            spec=spec,
            workdir=workdir,
            provider_private_metadata={
                "nonce": nonce,
                "owner_path": str(owner_path),
                "model": launch_config.model,
                "permission_mode": launch_config.permission_mode,
                "tools": launch_config.tools,
                "ready_deadline_seconds": launch_config.ready_deadline_seconds,
                "turn_deadline_seconds": launch_config.turn_deadline_seconds,
                "persona_path": str(persona_path) if persona_path else None,
                "append_system_prompt_file": str(persona_prompt_path) if persona_prompt_path else None,
                **mcp_metadata,
            },
        )

    async def start(self, prepared: PreparedRuntime) -> RuntimeHandle:
        spec = prepared.spec
        metadata = dict(spec.provider_private_metadata)
        metadata.update(prepared.provider_private_metadata)
        launch_config = resolve_launch_config(metadata)
        # Permission grants are derived from the adapter-generated MCP config,
        # never from caller/provider metadata.  A non-hosted runtime receives
        # no implicit grants even if it supplies the private-looking key.
        launch_config = replace(
            launch_config,
            allowed_tools=(HOSTED_MCP_ALLOWED_TOOL,)
            if prepared.provider_private_metadata.get("hosted_mcp_endpoint")
            else (),
        )
        transport = ClaudeProcessTransport(
            spec.runtime_id, spec.generation,
        )

        env = _runtime_environment(
            spec,
            str(prepared.provider_private_metadata["nonce"]),
        )
        # Isolate Claude's native session persistence (transcripts, project
        # state, conversation history) to a generation-local .claude directory
        # so every (runtime_id, generation) pair is fully independent and
        # transcripts never mix across runtimes.  No global credentials are
        # copied — the directory is empty before Claude initialises it.
        claude_config_dir = (prepared.workdir / ".claude").resolve()
        claude_config_dir.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir)

        await transport.start(command=launch_config.command(), cwd=prepared.workdir, env=env)
        self._record_pid(prepared.workdir, transport.pid)

        state = _ClaudeHandleState(
            transport=transport,
            launch_config=launch_config,
            is_hosted_mcp=bool(prepared.provider_private_metadata.get("hosted_mcp_endpoint")),
        )
        return RuntimeHandle(
            runtime_id=spec.runtime_id,
            generation=spec.generation,
            workdir=prepared.workdir,
            transport=state,
        )

    def _record_pid(self, workdir: Path, pid: Optional[int]) -> None:
        owner_path = workdir / "runtime-owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["pid"] = pid
        owner["process_identity"] = read_process_identity(pid) if pid is not None else None
        owner_path.write_text(json.dumps(owner, ensure_ascii=False), encoding="utf-8")
        owner_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    async def wait_ready(self, handle: RuntimeHandle, deadline: float) -> ReadyResult:
        """Ready is a controlled bootstrap turn, never PID liveness.

        Submits one internal user frame and requires it to converge to a
        successful ``result`` (which itself only follows a matching
        ``system/init``) before declaring the runtime ready for business
        input. Idempotent: a second ``wait_ready`` call after a successful
        bootstrap returns immediately without resubmitting.
        """
        state: _ClaudeHandleState = handle.transport
        if state.bootstrapped:
            return ReadyResult(
                ready=state.bootstrap_ok,
                runtime_id=handle.runtime_id,
                generation=handle.generation,
                detail={"bootstrap": "already-run"},
            )

        state.bootstrapped = True
        try:
            outcome = await asyncio.wait_for(
                state.transport.run_turn(BOOTSTRAP_PROMPT, deadline_seconds=deadline), timeout=deadline
            )
        except asyncio.TimeoutError:
            state.bootstrap_ok = False
            return ReadyResult(
                ready=False,
                runtime_id=handle.runtime_id,
                generation=handle.generation,
                failure_kind=ReadinessFailureKind.TIMEOUT,
                detail={"failure": ReadinessFailureKind.TIMEOUT.value},
            )
        except ClaudeProcessExitedError:
            state.bootstrap_ok = False
            await asyncio.sleep(0)
            diagnostic_tail = []
            for entry in getattr(state.transport, "log_lines", ()):
                if entry.get("stream") == "stderr" and entry.get("line"):
                    diagnostic_tail.append(str(entry["line"])[:500])
                elif entry.get("stream") == "stdout" and entry.get("protocol_error"):
                    diagnostic_tail.append(str(entry.get("raw", ""))[:500])
            diagnostic_tail = diagnostic_tail[-3:]
            log.warning(
                "claude process exited during readiness bootstrap",
                extra={
                    "event": "runtime_manager.claude.readiness.process_exited",
                    "runtime_id": handle.runtime_id,
                    "generation": handle.generation,
                    "returncode": getattr(state.transport, "returncode", None),
                    "diagnostic_tail": diagnostic_tail,
                    "outcome": "failed",
                    "error_code": "process_exited",
                },
            )
            return ReadyResult(
                ready=False,
                runtime_id=handle.runtime_id,
                generation=handle.generation,
                failure_kind=ReadinessFailureKind.PROCESS_EXITED,
                detail={"failure": ReadinessFailureKind.PROCESS_EXITED.value},
            )

        ok = result_is_success(outcome.result_frame)
        state.bootstrap_ok = ok
        failure_kind = None
        detail: Dict[str, Any] = {}
        if not ok:
            # Claude currently reports upstream rejection using
            # terminal_reason=api_error and/or api_error_status. Only the
            # provider-neutral classification crosses this adapter boundary.
            # Never copy the result body or provider status into ReadyResult.
            terminal_reason = outcome.result_frame.get("terminal_reason")
            api_status = outcome.result_frame.get("api_error_status")
            provider_rejected = terminal_reason == "api_error" or (
                isinstance(api_status, int) and 400 <= api_status <= 599
            )
            failure_kind = (
                ReadinessFailureKind.PROVIDER_REJECTED
                if provider_rejected else ReadinessFailureKind.READINESS_FAILED
            )
            detail = {"failure": failure_kind.value}
        elif state.is_hosted_mcp:
            # Hosted MCP readiness gate: bootstrap AI result must succeed
            # AND the bootstrap init must prove starcoplay-hosted is
            # connected/healthy with tool_count > 0.
            # The transport already stores only the sanitised startup
            # summary; the raw init frame was never retained.
            startup = state.transport.bootstrap_startup_summary
            if startup is None:
                state.bootstrap_ok = False
                failure_kind = ReadinessFailureKind.READINESS_FAILED
                detail = {
                    "failure": "readiness_failed",
                    "reason": "hosted_mcp_no_bootstrap_init",
                }
            elif not hosted_mcp_connected(startup):
                state.bootstrap_ok = False
                failure_kind = ReadinessFailureKind.READINESS_FAILED
                detail = {
                    "failure": "readiness_failed",
                    "reason": "hosted_mcp_not_connected",
                    "startup": startup,
                }
            elif startup.get("hosted_tool_count", 0) == 0:
                state.bootstrap_ok = False
                failure_kind = ReadinessFailureKind.READINESS_FAILED
                detail = {
                    "failure": "readiness_failed",
                    "reason": "hosted_mcp_zero_tools",
                    "startup": startup,
                }
        return ReadyResult(
            ready=state.bootstrap_ok,
            runtime_id=handle.runtime_id,
            generation=handle.generation,
            failure_kind=failure_kind,
            detail=detail,
        )

    async def submit_input(self, handle: RuntimeHandle, value: RuntimeInput) -> TurnResult:
        state: _ClaudeHandleState = handle.transport
        if not state.bootstrapped or not state.bootstrap_ok:
            raise RuntimeStoppingError(
                f"runtime {handle.runtime_id}/{handle.generation} is not ready (bootstrap not completed)"
            )

        status = await state.transport.inspect()
        if status.returncode is not None:
            raise RuntimeStoppingError(
                f"runtime {handle.runtime_id}/{handle.generation} is not running (returncode={status.returncode})"
            )
        if not status.healthy:
            await state.transport.stop(grace_period=0.5)
            raise RuntimeStoppingError(
                f"runtime {handle.runtime_id}/{handle.generation} transport is unhealthy"
            )

        turn_started = time.monotonic()
        # Turn deadline is intentionally absent: per the runtime-io-transcript
        # spec, the product-level 60-second Turn failure semantics are removed.
        # The turn runs until Claude ``result``, process exit, or explicit
        # Runtime stop/cancel.  Bootstrap readiness still enforces its own
        # bounded deadline through ``wait_ready``.
        try:
            outcome = await state.transport.run_turn(
                value.content, deadline_seconds=None,
            )
        except ClaudeResultMissingError as exc:
            self._record_turn_failure(
                handle, turn_started, "provider_result_missing", type(exc).__name__,
            )
            # The stream has lost its turn boundary. A late result could be
            # consumed by the next input, so force health reconciliation and
            # generation rollover instead of reusing this process.
            await state.transport.stop(grace_period=0.5)
            raise
        except ClaudeTransportError as exc:
            self._record_turn_failure(
                handle, turn_started, "adapter_transport_failed", type(exc).__name__,
            )
            # The process may still be alive, but its stdio boundary is no
            # longer trustworthy. Stop it so health reconciliation cannot
            # mistake a live PID for a reusable Runtime.
            await state.transport.stop(grace_period=0.5)
            raise
        except asyncio.TimeoutError as exc:
            self._record_turn_failure(handle, turn_started, "adapter_turn_timeout", type(exc).__name__)
            raise ClaudeTurnTimeoutError(str(exc)) from exc
        except ClaudeProcessExitedError as exc:
            self._record_turn_failure(handle, turn_started, "adapter_process_exited", type(exc).__name__)
            raise

        if not result_is_success(outcome.result_frame):
            self._record_turn_failure(handle, turn_started, "adapter_turn_failed", "ClaudeTurnFailedError")
            raise ClaudeTurnFailedError(
                f"turn failed for {handle.runtime_id}/{handle.generation}",
                detail={
                    "is_error": bool(outcome.result_frame.get("is_error", False)),
                    "subtype": outcome.result_frame.get("subtype"),
                },
            )

        state.turn_counter += 1
        return TurnResult(
            request_id=value.request_id,
            runtime_id=handle.runtime_id,
            generation=handle.generation,
            turn=state.turn_counter,
            content=outcome.text,
            detail={"num_turns": outcome.result_frame.get("num_turns")},
        )

    async def steer_input(self, handle: RuntimeHandle, value: RuntimeInput) -> bool:
        """Inject supplemental user input into the active Claude turn."""
        state: _ClaudeHandleState = handle.transport
        if not state.bootstrapped or not state.bootstrap_ok:
            return False
        return await state.transport.steer_turn(value.content)

    async def interrupt_input(self, handle: RuntimeHandle) -> bool:
        """Stop the active Claude turn before a newer player turn is admitted."""
        state: _ClaudeHandleState = handle.transport
        if not state.bootstrapped or not state.bootstrap_ok:
            return False
        return await state.transport.interrupt_turn()

    def receipt_context(self, handle: RuntimeHandle) -> dict[str, str]:
        """Return provider-private fields for an ``InputReceipt``.

        Returns ``provider``, ``session_id`` and ``transcript_cursor``
        (a bounded non-negative native file byte-offset as a decimal string).
        All fields are opaque and meaningful only to this adapter instance.
        """
        state: _ClaudeHandleState = handle.transport
        transport = state.transport
        session_id = transport.session_id or ""
        # Snapshot the native transcript EOF before admitting the new turn.
        # A receipt therefore positions output at records produced by this
        # input, without maintaining a second transcript or message table.
        cursor = 0
        projects_dir = self.claude_projects_dir(handle)
        if session_id and projects_dir.is_dir():
            from modules.runtime_manager.transcript import find_session_file
            transcript = find_session_file(projects_dir, session_id)
            if transcript is not None:
                try:
                    cursor = transcript.stat().st_size
                except OSError:
                    cursor = 0
        return {
            "provider": "claude",
            "session_id": session_id,
            "transcript_cursor": str(cursor),
        }

    def claude_projects_dir(self, handle: RuntimeHandle) -> Path:
        """Return the generation-local ``.claude/projects`` directory where
        Claude's native session JSONL files are persisted.

        The output reader locates the current session file by matching
        *session_id* against the filenames inside this directory tree.
        Never accepts a caller-supplied path.
        """
        return (handle.workdir / ".claude" / "projects").resolve()

    @staticmethod
    def _record_turn_failure(
        handle: RuntimeHandle, started: float, error_code: str, exception_type: str,
    ) -> None:
        duration_ms = (time.monotonic() - started) * 1000
        log.warning(
            "Claude runtime turn failed",
            extra={"event": "runtime.adapter.turn_failed", "runtime_id": handle.runtime_id,
                   "generation": handle.generation, "adapter_kind": "claude",
                   "stage": "turn", "outcome": "failed", "error_code": error_code,
                   "exception_type": exception_type, "duration_ms": duration_ms},
        )
        emit_metric(
            "runtime_adapter_turn_failure_total", "counter", 1,
            outcome="failed", stage="turn", runtime_id=handle.runtime_id,
            generation=handle.generation, adapter_kind="claude", error_code=error_code,
        )
        emit_metric(
            "runtime_adapter_turn_duration_ms", "timing", duration_ms,
            outcome="failed", stage="turn", runtime_id=handle.runtime_id,
            generation=handle.generation, adapter_kind="claude", error_code=error_code,
        )

    async def inspect(self, handle: RuntimeHandle) -> ProviderStatus:
        state: _ClaudeHandleState = handle.transport
        return await state.transport.inspect()

    async def checkpoint(self, handle: RuntimeHandle, deadline: float) -> Optional[CheckpointResult]:
        """Unsupported: runtimes use generation-local CLAUDE_CONFIG_DIR.

        Returning ``None`` is the honest signal per the implementation spec
        (spec section 6) -- this must never fabricate a fake persisted path.
        """
        return None

    async def stop(self, handle: RuntimeHandle, reason: str, grace_period: float) -> StopResult:
        state: _ClaudeHandleState = handle.transport
        result = await state.transport.stop(grace_period=grace_period)
        cleanup_transient_workdir(handle.workdir)
        return StopResult(stopped=True, exit_code=result.exit_code, escalation=result.escalation, reason=reason)

    async def reconcile(self, record: RuntimeRecord) -> ReconcileDecision:
        """Reconcile purely from the on-disk owner file + OS process liveness,
        mirroring ``FakeAgentRuntimeAdapter.reconcile`` -- must work from a
        brand-new adapter instance with no live handle."""
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
        alive = _process_is_alive(pid) if isinstance(pid, int) else False

        if not alive:
            action = (
                ReconcileAction.MARK_STOPPED
                if record.desired_state == RuntimeActualState.STOPPED
                else ReconcileAction.MARK_FAILED
            )
            return ReconcileDecision(
                action=action, runtime_id=record.runtime_id, generation=record.generation, detail={"pid": pid}
            )

        # Claude processes communicate over a stdio pipe that cannot be
        # reattached across adapter/harness instances. ADOPT would claim a
        # live handle we cannot drive; the safe decision is always to
        # terminate the orphan so the caller can restart a fresh process.
        await self._terminate_orphan(pid)
        return ReconcileDecision(
            action=ReconcileAction.TERMINATE_ORPHAN,
            runtime_id=record.runtime_id,
            generation=record.generation,
            detail={"pid": pid, "reason": "stdio-not-reattachable"},
        )

    async def _terminate_orphan(self, pid: int) -> None:
        if os.name == "nt":
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            for _ in range(30):
                if not _process_is_alive(pid):
                    return
                await asyncio.sleep(0.1)
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            return
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(30):
            await asyncio.sleep(0.1)
            if not _process_is_alive(pid):
                return
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
