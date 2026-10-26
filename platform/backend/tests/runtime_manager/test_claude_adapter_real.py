"""Opt-in real Claude CLI adapter tests.

Skipped unless ``RUN_REAL_CLAUDE_TESTS=1`` -- these spawn a real ``claude``
CLI process, need network + an authenticated Claude Code login, and are not
run by default (see ``implementation-spec.md`` section 10 / the turn-1
driver prompt). Model is explicitly pinned to ``claude-sonnet-5`` throughout.

Budget: each turn's deadline is <=60s; test-level ``pytest-timeout`` is not
assumed to be installed, so the overall wall-clock ceiling (<=180s) is
enforced by the per-call ``asyncio.wait_for``/deadline arguments, not an
external timeout plugin.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
from modules.runtime_manager.contracts import RuntimeInput, RuntimeSpec

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_CLAUDE_TESTS") != "1",
    reason="real Claude CLI tests are opt-in; set RUN_REAL_CLAUDE_TESTS=1 to run",
)

_MODEL = "claude-sonnet-5"
_READY_DEADLINE = 60.0
_TURN_DEADLINE = 60.0

# Evidence/source roots to scan for marker leakage. Kept relative to this
# test file so it works from any checkout path.
_REPO_ROOT = Path(__file__).resolve().parents[3]  # platform/backend
_SPIKE_EVIDENCE_ROOT = _REPO_ROOT.parent / "spike" / "agent-runtime-adapter"


def _make_spec(workspace_root: Path, runtime_id: str) -> RuntimeSpec:
    return RuntimeSpec(
        runtime_id=runtime_id,
        generation=1,
        user_id="user-real-claude",
        agentee_id="agentee-real-claude",
        provider="claude",
        workspace_root=workspace_root,
        idle_ttl_seconds=600.0,
        env={},
        provider_private_metadata={
            "model": _MODEL,
            "ready_deadline_seconds": _READY_DEADLINE,
            "turn_deadline_seconds": _TURN_DEADLINE,
        },
    )


def _scan_for_marker(marker: str, *roots: Path) -> list[str]:
    """Grep-equivalent scan for a marker string under each root. Returns the
    list of file paths (as str) that contain it, empty if none."""
    hits: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            proc = subprocess.run(
                ["grep", "-rl", "--", marker, str(root)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if proc.returncode == 0:
            hits.extend(line for line in proc.stdout.splitlines() if line)
    return hits


@pytest.mark.asyncio
async def test_real_claude_ready_is_bootstrap_turn_not_pid_alive(tmp_path: Path):
    adapter = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(tmp_path, "rt-real-ready")
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        # PID is alive immediately after spawn, before any protocol handshake.
        status = await adapter.inspect(handle)
        assert status.pid is not None
        assert status.returncode is None
        assert status.ready is False, "ready must not be true before the bootstrap turn converges"

        result = await adapter.wait_ready(handle, deadline=_READY_DEADLINE)
        assert result.ready is True, f"bootstrap turn did not converge: {result.detail}"
        assert result.runtime_id == "rt-real-ready"
        assert result.generation == 1
    finally:
        stop_result = await adapter.stop(handle, reason="test-teardown", grace_period=10.0)
        assert stop_result.stopped is True


@pytest.mark.asyncio
async def test_real_claude_three_turns_same_pid_and_marker_recall_without_disk_leak(tmp_path: Path):
    adapter = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(tmp_path, "rt-real-turns")
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    marker = f"MARKER-{secrets.token_hex(8).upper()}"
    try:
        ready = await adapter.wait_ready(handle, deadline=_READY_DEADLINE)
        assert ready.ready is True

        pid_after_ready = (await adapter.inspect(handle)).pid
        assert pid_after_ready is not None

        turn1 = await adapter.submit_input(
            handle,
            RuntimeInput(
                request_id="req-1",
                content=(
                    f"Remember this secret word for the rest of this conversation: {marker}. "
                    "Do not write it to any file or use any tool. Just acknowledge you will remember it."
                ),
            ),
        )
        assert turn1.turn == 1
        assert (await adapter.inspect(handle)).pid == pid_after_ready

        turn2 = await adapter.submit_input(
            handle,
            RuntimeInput(request_id="req-2", content="What is 2 + 2? Answer with just the number."),
        )
        assert turn2.turn == 2
        assert (await adapter.inspect(handle)).pid == pid_after_ready

        # Third turn does NOT repeat the marker in the prompt -- recall proves
        # in-process conversational memory, not prompt echoing.
        turn3 = await adapter.submit_input(
            handle,
            RuntimeInput(
                request_id="req-3",
                content="What was the secret word I asked you to remember earlier? Reply with only that word.",
            ),
        )
        assert turn3.turn == 3
        assert (await adapter.inspect(handle)).pid == pid_after_ready
        assert marker in turn3.content, f"expected marker recall in turn3.content, got: {turn3.content!r}"

        # The marker must never have touched disk: scan the runtime's own
        # workdir plus this repo's test/adapter source and the protocol
        # evidence directory.
        hits = _scan_for_marker(
            marker,
            prepared.workdir,
            Path(__file__).resolve().parent,
            Path(__file__).resolve().parents[2] / "modules" / "runtime_manager",
            _SPIKE_EVIDENCE_ROOT,
        )
        assert hits == [], f"marker leaked to disk in: {hits}"
    finally:
        await adapter.stop(handle, reason="test-teardown", grace_period=10.0)


@pytest.mark.asyncio
async def test_real_claude_stop_eof_exits_process_and_is_idempotent(tmp_path: Path):
    adapter = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(tmp_path, "rt-real-stop")
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    pid: Optional[int] = None
    try:
        ready = await adapter.wait_ready(handle, deadline=_READY_DEADLINE)
        assert ready.ready is True
        pid = (await adapter.inspect(handle)).pid
        assert pid is not None

        first = await adapter.stop(handle, reason="first-stop", grace_period=10.0)
        assert first.stopped is True
        assert first.escalation in ("eof", "term", "kill")

        second = await adapter.stop(handle, reason="second-stop", grace_period=10.0)
        assert second.stopped is True
        assert second.escalation == "already_stopped"
        assert second.exit_code == first.exit_code

        status = await adapter.inspect(handle)
        assert status.returncode is not None, "process must have exited after stop()"
    finally:
        if pid is not None:
            try:
                os.kill(pid, 0)
                # Still alive despite stop() -- best-effort escalation so the
                # test process never leaves a runaway Claude CLI behind.
                await adapter.stop(handle, reason="finally-cleanup", grace_period=5.0)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_real_claude_calls_controlled_mcp_and_observes_revocation(tmp_path: Path):
    """Opt-in only: proves the installed Claude CLI actually speaks to our stdio server.

    Acceptance criteria (turn-1 real-MCP gate):
    1. Same real Claude PID completes ready, echo success, controlled_fail,
       revoke → echo rejected; assert PID unchanged at each stage.
    2. Server audit in order: protocol-init, protocol-tools-list, ok,
       deliberate-failure, revoked; no secret full text.
    3. controlled_fail and revoke MCP tool results are isError:true.
    4. initialize/tools/list have real protocol evidence in audit.
    5. No Claude/MCP child process residue after finally stop.

    Authentication/quota failures are reported as an explicit xfail, never a
    passing MCP result.
    """
    server = Path(__file__).with_name("controlled_mcp_server.py")
    spec = _make_spec(tmp_path, "rt-real-mcp")
    spec = RuntimeSpec(**{**spec.__dict__, "provider_private_metadata": {
        **spec.provider_private_metadata, "controlled_mcp_server": str(server)
    }})
    adapter = ClaudeAgentRuntimeAdapter()
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    pid: Optional[int] = None
    try:
        ready = await adapter.wait_ready(handle, deadline=_READY_DEADLINE)
        if not ready.ready:
            pytest.xfail(f"Claude authentication/quota/readiness unavailable: {ready.detail}")

        pid = (await adapter.inspect(handle)).pid
        assert pid is not None, "PID must be known after bootstrap"

        audit_path = Path(str(prepared.provider_private_metadata["mcp_audit_path"]))
        credential_path = prepared.workdir / "mcp-credential"
        secret = credential_path.read_text() if credential_path.exists() else ""
        fingerprint = str(prepared.provider_private_metadata["credential_fingerprint"])

        # ── Stage 1: controlled_echo success ──
        echo_result = await adapter.submit_input(handle, RuntimeInput(
            request_id="mcp-echo",
            content=(
                "Call controlled_echo exactly once with message 'wire-ok' and runtime_id "
                "'rt-real-mcp'. Return the tool result verbatim. Do not use any other tool."
            ),
        ))
        # criterion 1: PID unchanged
        assert (await adapter.inspect(handle)).pid == pid, "PID changed after echo"
        # criterion 1: fingerprint in model output
        assert fingerprint in echo_result.content, (
            f"fingerprint {fingerprint!r} not in echo result: {echo_result.content!r}"
        )
        # Stage-1 audit: ok, isError=False
        echo_entry = json.loads(audit_path.read_text().splitlines()[-1])
        assert echo_entry["note"] == "ok"
        assert echo_entry["is_error"] is False

        # ── Stage 2: controlled_fail → isError:true ──
        fail_result = await adapter.submit_input(handle, RuntimeInput(
            request_id="mcp-fail",
            content=(
                "Call controlled_fail exactly once with message 'test-fail'. "
                "Report the tool result verbatim. Do not use any other tool."
            ),
        ))
        # criterion 1: PID unchanged
        assert (await adapter.inspect(handle)).pid == pid, "PID changed after controlled_fail"
        # criterion 3: tool result is isError:true per audit
        fail_entry = json.loads(audit_path.read_text().splitlines()[-1])
        assert fail_entry["note"] == "deliberate-failure"
        assert fail_entry["is_error"] is True, (
            f"controlled_fail must be isError:true, got {fail_entry}"
        )

        # ── Stage 3: revoke then echo → isError:true ──
        Path(str(prepared.provider_private_metadata["mcp_revoke_path"])).touch()
        revoked_result = await adapter.submit_input(handle, RuntimeInput(
            request_id="mcp-revoked",
            content=(
                "Call controlled_echo exactly once with message 'after-revoke' and runtime_id "
                "'rt-real-mcp'. Report the tool error verbatim. Do not use any other tool."
            ),
        ))
        # criterion 1: PID unchanged
        assert (await adapter.inspect(handle)).pid == pid, "PID changed after revoke"
        # criterion 1: model reports revocation
        assert "revoked" in revoked_result.content.lower(), (
            f"'revoked' not in model response: {revoked_result.content!r}"
        )
        # criterion 3: tool result is isError:true per audit
        revoke_entry = json.loads(audit_path.read_text().splitlines()[-1])
        assert revoke_entry["note"] == "revoked"
        assert revoke_entry["is_error"] is True, (
            f"revoked echo must be isError:true, got {revoke_entry}"
        )

        # ── Criterion 2: full audit sequence ──
        entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
        notes = [e["note"] for e in entries]
        expected_notes = ["protocol-init", "protocol-tools-list", "ok", "deliberate-failure", "revoked"]
        assert notes == expected_notes, f"unexpected audit sequence: {notes}"

        # ── Criterion 2: no secret full text in audit ──
        audit_text = audit_path.read_text()
        assert secret not in audit_text, "secret credential leaked to audit log"

        # ── Criterion 4: protocol events have correct shape ──
        for e in entries:
            assert set(e.keys()) == {"timestamp", "tool", "is_error", "fingerprint", "note"}, (
                f"unexpected audit entry keys: {set(e.keys())}"
            )
        # protocol-init and protocol-tools-list are non-error protocol events
        assert entries[0]["tool"] == "__protocol__" and entries[0]["note"] == "protocol-init"
        assert entries[1]["tool"] == "__protocol__" and entries[1]["note"] == "protocol-tools-list"

    finally:
        # ── Criterion 5: no child process residue ──
        stop_result = await adapter.stop(handle, reason="test-teardown", grace_period=10.0)
        assert stop_result.stopped is True
        if pid is not None:
            try:
                os.kill(pid, 0)
                # Best-effort cleanup before failing
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
                pytest.fail(f"Claude process pid={pid} still alive after stop()")
            except ProcessLookupError:
                pass  # expected: process is gone
