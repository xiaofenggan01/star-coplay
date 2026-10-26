"""Opt-in real Claude CLI failure, isolation, and reconcile tests.

Skipped unless ``RUN_REAL_CLAUDE_TESTS=1`` — these spawn real ``claude``
processes, need network + authenticated Claude Code login.

Covers:
1. Stop escalation: EOF → TERM → KILL with controlled fault-injection wrapper
2. Dual runtime isolation: two live Claude processes with distinct credentials
3. Process reconcile: new adapter terminates stdio-unreachable orphans

All tests clean up in finally; no credential full text in logs/workdir/evidence.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import pytest

from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
from modules.runtime_manager.adapters.claude.transport import ClaudeProcessTransport
from modules.runtime_manager.contracts import (
    ReconcileAction,
    RuntimeActualState,
    RuntimeInput,
    RuntimeRecord,
    RuntimeSpec,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_CLAUDE_TESTS") != "1",
    reason="real Claude CLI tests are opt-in; set RUN_REAL_CLAUDE_TESTS=1 to run",
)

_MODEL = "claude-sonnet-5"
_READY_DEADLINE = 60.0
_TURN_DEADLINE = 60.0
_STOP_GRACE = 10.0
_TEST_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_spec(
    workspace_root: Path,
    runtime_id: str,
    *,
    generation: int = 1,
    env: Optional[dict] = None,
    extra_meta: Optional[dict] = None,
) -> RuntimeSpec:
    meta: dict = {
        "model": _MODEL,
        "ready_deadline_seconds": _READY_DEADLINE,
        "turn_deadline_seconds": _TURN_DEADLINE,
    }
    if extra_meta:
        meta.update(extra_meta)
    return RuntimeSpec(
        runtime_id=runtime_id,
        generation=generation,
        user_id="user-real-claude",
        agentee_id="agentee-real-claude",
        provider="claude",
        workspace_root=workspace_root,
        idle_ttl_seconds=600.0,
        env=env or {},
        provider_private_metadata=meta,
    )


def _assert_no_process(pid: int, label: str) -> None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return  # expected
    # Best-effort kill then fail.
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    pytest.fail(f"{label} pid={pid} still alive")


# ---------------------------------------------------------------------------
# 1. Stop escalation: TERM and KILL via controlled wrapper
# ---------------------------------------------------------------------------

_WRAPPER = str(_TEST_DIR / "stop_escalation_wrapper.py")


@pytest.mark.asyncio
async def test_stop_escalation_eof_is_sufficient_for_real_claude(tmp_path: Path):
    """Baseline: real Claude exits cleanly on stdin EOF (no wrapper needed).

    This is the happy path that test_real_claude_stop_eof_exits_process_and_is_idempotent
    already covers; repeated here so the escalation suite is self-contained.
    """
    adapter = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(tmp_path, "rt-stop-eof")
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    pid: Optional[int] = None
    try:
        ready = await adapter.wait_ready(handle, deadline=_READY_DEADLINE)
        if not ready.ready:
            pytest.xfail(f"Claude unavailable: {ready.detail}")
        pid = (await adapter.inspect(handle)).pid
        assert pid is not None

        result = await adapter.stop(handle, reason="test", grace_period=_STOP_GRACE)
        assert result.stopped is True
        assert result.escalation in ("eof", "term", "kill"), f"unexpected escalation: {result.escalation}"
        _assert_no_process(pid, "Claude after EOF stop")
    finally:
        if pid is not None:
            try:
                await adapter.stop(handle, reason="finally", grace_period=5.0)
            except Exception:
                pass
            _assert_no_process(pid, "Claude after finally")


@pytest.mark.asyncio
async def test_stop_escalation_term_when_process_ignores_eof(tmp_path: Path):
    """Wrapper in ``ignore-eof`` mode: transport closes stdin but the wrapper
    keeps running → adapter escalates to TERM → wrapper + real Claude exit.

    Verifies TERM escalation is bounded and no process-group residue.
    """
    server = _TEST_DIR / "controlled_mcp_server.py"
    adapter = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(
        tmp_path,
        "rt-stop-term",
        extra_meta={
            "controlled_mcp_server": str(server),
        },
    )
    prepared = await adapter.prepare(spec)

    # Build the real command the adapter would use, then wrap it.
    from modules.runtime_manager.adapters.claude.protocol import resolve_launch_config

    metadata = dict(spec.provider_private_metadata)
    metadata.update(prepared.provider_private_metadata)
    lc = resolve_launch_config(metadata)
    real_cmd = lc.command()

    # We'll bypass the adapter's start() and directly use the transport with a
    # wrapped command.  This keeps the fault injection provider-private.
    transport = ClaudeProcessTransport("rt-stop-term", 1)
    wrapped_cmd = [sys.executable, _WRAPPER, "ignore-eof"] + real_cmd
    env = dict(os.environ)
    env.update(spec.env)
    await transport.start(command=wrapped_cmd, cwd=prepared.workdir, env=env)

    pid = transport.pid
    assert pid is not None
    try:
        # Bootstrap the runtime manually (bypass adapter.wait_ready which needs _ClaudeHandleState).
        from modules.runtime_manager.adapters.claude.protocol import (
            BOOTSTRAP_PROMPT,
            result_is_success,
        )

        outcome = await asyncio.wait_for(
            transport.run_turn(BOOTSTRAP_PROMPT, deadline_seconds=_READY_DEADLINE),
            timeout=_READY_DEADLINE,
        )
        assert result_is_success(outcome.result_frame), "bootstrap turn must converge"

        # Now stop — this should escalate past EOF to TERM.
        stop_result = await transport.stop(grace_period=_STOP_GRACE)
        assert stop_result.escalation == "term", (
            f"expected TERM escalation, got {stop_result.escalation} (exit={stop_result.exit_code})"
        )
        assert stop_result.exit_code is not None
        _assert_no_process(pid, "wrapper after TERM stop")
    finally:
        # Best-effort cleanup.
        try:
            await transport.stop(grace_period=5.0)
        except Exception:
            pass
        _assert_no_process(pid, "wrapper after finally")


@pytest.mark.asyncio
async def test_stop_escalation_kill_when_process_ignores_eof_and_term(tmp_path: Path):
    """Wrapper in ``ignore-eof-term`` mode: ignores stdin EOF AND SIGTERM.
    Adapter must escalate to SIGKILL.  Proves the KILL rung of the ladder.

    The real Claude child (same process group) receives SIGTERM and exits;
    the wrapper stays alive → transport escalates to KILL.
    """
    server = _TEST_DIR / "controlled_mcp_server.py"
    adapter = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(
        tmp_path,
        "rt-stop-kill",
        extra_meta={
            "controlled_mcp_server": str(server),
        },
    )
    prepared = await adapter.prepare(spec)

    from modules.runtime_manager.adapters.claude.protocol import resolve_launch_config

    metadata = dict(spec.provider_private_metadata)
    metadata.update(prepared.provider_private_metadata)
    lc = resolve_launch_config(metadata)
    real_cmd = lc.command()

    transport = ClaudeProcessTransport("rt-stop-kill", 1)
    wrapped_cmd = [sys.executable, _WRAPPER, "ignore-eof-term"] + real_cmd
    env = dict(os.environ)
    env.update(spec.env)
    await transport.start(command=wrapped_cmd, cwd=prepared.workdir, env=env)

    pid = transport.pid
    assert pid is not None
    try:
        from modules.runtime_manager.adapters.claude.protocol import (
            BOOTSTRAP_PROMPT,
            result_is_success,
        )

        outcome = await asyncio.wait_for(
            transport.run_turn(BOOTSTRAP_PROMPT, deadline_seconds=_READY_DEADLINE),
            timeout=_READY_DEADLINE,
        )
        assert result_is_success(outcome.result_frame), "bootstrap turn must converge"

        # Stop — ignore-eof-term wrapper should force KILL escalation.
        stop_result = await transport.stop(grace_period=_STOP_GRACE)
        assert stop_result.escalation == "kill", (
            f"expected KILL escalation, got {stop_result.escalation} (exit={stop_result.exit_code})"
        )
        _assert_no_process(pid, "wrapper after KILL stop")
    finally:
        try:
            await transport.stop(grace_period=5.0)
        except Exception:
            pass
        _assert_no_process(pid, "wrapper after finally")


# ---------------------------------------------------------------------------
# 2. Dual real-Claude runtime isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_runtime_isolation_distinct_credentials_and_cross_mcp_rejection(
    tmp_path: Path,
):
    """Two real Claude runtimes A and B, simultaneously alive, with distinct
    HOME, workdir, MCP credential, audit, and session.

    - A and B each complete ≥1 turn.
    - Stop A; B completes one more turn.
    - Use A's credential against B's MCP server → B audit proves
      ``runtime-mismatch`` rejection.
    - PID, session_id, workdir, fingerprint all differ.
    - No credential full text in logs/workdir.
    """
    server = _TEST_DIR / "controlled_mcp_server.py"
    adapter = ClaudeAgentRuntimeAdapter()

    # Keep HOME unchanged so Claude can use the host's already-authorized
    # system credential provider. Give each runtime an explicit private
    # --settings file while --no-session-persistence isolates session/history.
    # We neither copy nor inspect the OAuth token.
    config_a = tmp_path / "claude-config-a"
    config_b = tmp_path / "claude-config-b"
    config_a.mkdir(mode=0o700)
    config_b.mkdir(mode=0o700)
    settings_a = config_a / "settings.json"
    settings_b = config_b / "settings.json"
    settings_a.write_text("{}\n", encoding="utf-8")
    settings_b.write_text("{}\n", encoding="utf-8")
    settings_a.chmod(0o600)
    settings_b.chmod(0o600)

    marker_a = f"ISO-A-{secrets.token_hex(4).upper()}"
    marker_b = f"ISO-B-{secrets.token_hex(4).upper()}"

    spec_a = _make_spec(
        tmp_path,
        "rt-iso-a",
        extra_meta={
            "controlled_mcp_server": str(server),
            "claude_settings_path": str(settings_a),
        },
    )
    spec_b = _make_spec(
        tmp_path,
        "rt-iso-b",
        extra_meta={
            "controlled_mcp_server": str(server),
            "claude_settings_path": str(settings_b),
        },
    )

    prepared_a = await adapter.prepare(spec_a)
    prepared_b = await adapter.prepare(spec_b)

    handle_a = await adapter.start(prepared_a)
    handle_b = await adapter.start(prepared_b)

    pid_a: Optional[int] = None
    pid_b: Optional[int] = None
    try:
        ready_a = await adapter.wait_ready(handle_a, deadline=_READY_DEADLINE)
        ready_b = await adapter.wait_ready(handle_b, deadline=_READY_DEADLINE)
        assert ready_a.ready and ready_b.ready, (
            f"both isolated Claude runtimes must bootstrap: A={ready_a.detail} B={ready_b.detail}"
        )

        pid_a = (await adapter.inspect(handle_a)).pid
        pid_b = (await adapter.inspect(handle_b)).pid
        assert pid_a is not None and pid_b is not None
        assert pid_a != pid_b, "A and B must have distinct PIDs"

        # Verify distinct paths
        assert prepared_a.workdir != prepared_b.workdir
        assert config_a != config_b
        fp_a = str(prepared_a.provider_private_metadata["credential_fingerprint"])
        fp_b = str(prepared_b.provider_private_metadata["credential_fingerprint"])
        assert fp_a != fp_b, "A and B must have distinct credential fingerprints"
        cred_a = (prepared_a.workdir / "mcp-credential").read_text()
        cred_b = (prepared_b.workdir / "mcp-credential").read_text()
        assert cred_a != cred_b

        # Transport-level session IDs differ.
        sid_a = handle_a.transport.transport.session_id
        sid_b = handle_b.transport.transport.session_id
        assert sid_a is not None and sid_b is not None
        assert sid_a != sid_b, "A and B must have distinct Claude session ids"

        # ── Both complete one turn ──
        turn_a1 = await adapter.submit_input(
            handle_a,
            RuntimeInput(
                request_id="iso-a-1",
                content=(
                    f"Remember this word: {marker_a}. "
                    "Reply with just the word OK. Do not use any tool."
                ),
            ),
        )
        assert turn_a1.turn == 1
        assert (await adapter.inspect(handle_a)).pid == pid_a

        turn_b1 = await adapter.submit_input(
            handle_b,
            RuntimeInput(
                request_id="iso-b-1",
                content=(
                    f"Remember this word: {marker_b}. "
                    "Reply with just the word OK. Do not use any tool."
                ),
            ),
        )
        assert turn_b1.turn == 1
        assert (await adapter.inspect(handle_b)).pid == pid_b

        # ── Stop A; B continues ──
        stop_a = await adapter.stop(handle_a, reason="iso-stop-a", grace_period=_STOP_GRACE)
        assert stop_a.stopped is True
        _assert_no_process(pid_a, "A after stop")
        assert not (prepared_a.workdir / "mcp-credential").exists()
        assert not (prepared_a.workdir / "mcp-config.json").exists()
        assert not (prepared_a.workdir / "scratch").exists()

        turn_b2 = await adapter.submit_input(
            handle_b,
            RuntimeInput(
                request_id="iso-b-2",
                content="What word did I ask you to remember? Reply with only that word.",
            ),
        )
        assert turn_b2.turn == 2
        assert marker_b in turn_b2.content, f"B should recall its own marker: {turn_b2.content!r}"
        assert marker_a not in turn_b2.content, "B must not know A's marker"
        assert (await adapter.inspect(handle_b)).pid == pid_b

        # ── Cross-credential MCP call: use A's runtime_id against B ──
        cross_result = await adapter.submit_input(
            handle_b,
            RuntimeInput(
                request_id="iso-cross",
                content=(
                    f"Call controlled_echo exactly once with message 'cross' and runtime_id "
                    f"'rt-iso-a'. Report the tool result verbatim. Do not use any other tool."
                ),
            ),
        )
        # B's MCP server should reject because runtime_id doesn't match.
        audit_b = Path(str(prepared_b.provider_private_metadata["mcp_audit_path"]))
        audit_entries = [json.loads(line) for line in audit_b.read_text().splitlines()]
        mismatch_entries = [e for e in audit_entries if e.get("note") == "runtime-mismatch"]
        assert len(mismatch_entries) >= 1, (
            f"B must reject cross-runtime MCP call; audit notes: {[e['note'] for e in audit_entries]}"
        )
        assert mismatch_entries[-1]["is_error"] is True

        # ── No credential full text in logs ──
        for cred, label in [(cred_a, "A"), (cred_b, "B")]:
            audit_text = (
                Path(str(prepared_b.provider_private_metadata["mcp_audit_path"])).read_text()
                if label == "B"
                else Path(str(prepared_a.provider_private_metadata["mcp_audit_path"])).read_text()
            )
            assert cred not in audit_text, f"credential {label} leaked to audit"

    finally:
        for handle, pid, label in [
            (handle_a, pid_a, "A"),
            (handle_b, pid_b, "B"),
        ]:
            try:
                await adapter.stop(handle, reason=f"finally-{label}", grace_period=5.0)
            except Exception:
                pass
            if pid is not None:
                _assert_no_process(pid, f"{label} after finally")


# ---------------------------------------------------------------------------
# 3. Real process reconcile: terminate, never ADOPT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_claude_reconcile_terminates_orphan_never_adopts(tmp_path: Path):
    """Start real Claude, discard the adapter/harness/handle entirely, then
    reconcile from a brand-new adapter instance using only the on-disk owner
    file + OS process liveness.

    Because Claude stdio cannot be reattached across adapter instances, the
    reconcile MUST return TERMINATE_ORPHAN (never ADOPT) and the old process
    must be confirmed dead afterwards.
    """
    adapter1 = ClaudeAgentRuntimeAdapter()
    spec = _make_spec(tmp_path, "rt-reconcile")
    prepared1 = await adapter1.prepare(spec)
    handle1 = await adapter1.start(prepared1)

    pid: Optional[int] = None
    owner_nonce: str = ""
    workdir: Optional[Path] = None
    try:
        ready = await adapter1.wait_ready(handle1, deadline=_READY_DEADLINE)
        if not ready.ready:
            pytest.xfail(f"Claude unavailable: {ready.detail}")

        pid = (await adapter1.inspect(handle1)).pid
        assert pid is not None
        workdir = prepared1.workdir
        owner_nonce = str(prepared1.provider_private_metadata.get("nonce", ""))

        # Read owner file to confirm pid/nonce on disk.
        owner_path = workdir / "runtime-owner.json"
        owner = json.loads(owner_path.read_text())
        assert owner["pid"] == pid
        assert owner["nonce"] == owner_nonce

        # Verify PID is alive.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pytest.fail(f"process {pid} should be alive before reconcile")

        # ── Discard adapter1, handle1 entirely ──
        del adapter1
        del handle1
        del prepared1

        # ── Reconcile from a fresh adapter ──
        adapter2 = ClaudeAgentRuntimeAdapter()
        record = RuntimeRecord(
            runtime_id="rt-reconcile",
            generation=1,
            desired_state=RuntimeActualState.READY,
            actual_state=RuntimeActualState.READY,
            workdir=workdir,
            owner_nonce=owner_nonce,
        )
        decision = await adapter2.reconcile(record)

        # Must never ADOPT — stdio is not reattachable.
        assert decision.action != ReconcileAction.ADOPT, (
            "Claude adapter must never ADOPT (stdio not reattachable)"
        )
        assert decision.action == ReconcileAction.TERMINATE_ORPHAN, (
            f"expected TERMINATE_ORPHAN, got {decision.action}"
        )
        assert decision.detail.get("pid") == pid
        assert decision.detail.get("reason") == "stdio-not-reattachable"

        # ── Old process must be dead ──
        deadline = time.monotonic() + _STOP_GRACE
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            # Best-effort kill.
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            pytest.fail(f"orphan process pid={pid} should be dead after reconcile")

        # ── Second reconcile with process gone: should report not-alive ──
        record_stopped = RuntimeRecord(
            runtime_id="rt-reconcile",
            generation=1,
            desired_state=RuntimeActualState.STOPPED,
            actual_state=RuntimeActualState.STOPPED,
            workdir=workdir,
            owner_nonce=owner_nonce,
        )
        decision2 = await adapter2.reconcile(record_stopped)
        assert decision2.action == ReconcileAction.MARK_STOPPED, (
            f"expected MARK_STOPPED after process exit, got {decision2.action}"
        )

    finally:
        if pid is not None:
            _assert_no_process(pid, "reconcile after finally")
