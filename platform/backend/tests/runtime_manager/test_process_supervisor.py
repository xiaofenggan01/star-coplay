from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from modules.runtime_manager.process_supervisor import ProcessSupervisor

_FAKE_RUNTIME_SCRIPT = (
    Path(__file__).resolve().parents[2] / "modules" / "runtime_manager" / "fake" / "runtime_process.py"
)


def _write_owner(workdir: Path, runtime_id: str, generation: int) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "runtime-owner.json").write_text(
        json.dumps({"runtime_id": runtime_id, "generation": generation, "provider": "fake", "nonce": "n"}),
        encoding="utf-8",
    )


async def _spawn(workdir: Path, runtime_id: str, generation: int, extra_args: list[str]) -> ProcessSupervisor:
    _write_owner(workdir, runtime_id, generation)
    supervisor = ProcessSupervisor(runtime_id, generation)

    env = {"PATH": os.environ.get("PATH", "")}
    await supervisor.start(command=[sys.executable, str(_FAKE_RUNTIME_SCRIPT), *extra_args], cwd=workdir, env=env)
    return supervisor


@pytest.mark.asyncio
async def test_normal_stop_does_not_escalate(tmp_path: Path):
    supervisor = await _spawn(tmp_path, "rt-a", 1, [])
    ok = await supervisor.wait_ready(5.0)
    assert ok is True

    result = await supervisor.stop(app_stop_frame={"type": "stop", "reason": "test"}, grace_period=2.0)

    assert result.escalation == "app_stop"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_ignored_app_stop_escalates_to_term(tmp_path: Path):
    supervisor = await _spawn(tmp_path, "rt-b", 1, ["--ignore-stop"])
    ok = await supervisor.wait_ready(5.0)
    assert ok is True

    result = await supervisor.stop(app_stop_frame={"type": "stop", "reason": "test"}, grace_period=0.5)

    assert result.escalation == "term"
    # Default Python SIGTERM handling exits the process; exact code is platform-dependent
    # but must be a real exit, not None (which would mean it never converged).
    assert result.exit_code is not None


@pytest.mark.asyncio
async def test_ignored_term_escalates_to_kill(tmp_path: Path):
    supervisor = await _spawn(tmp_path, "rt-c", 1, ["--ignore-stop", "--ignore-term"])
    ok = await supervisor.wait_ready(5.0)
    assert ok is True

    result = await supervisor.stop(app_stop_frame={"type": "stop", "reason": "test"}, grace_period=0.5)

    assert result.escalation == "kill"
    assert result.exit_code is not None


@pytest.mark.asyncio
async def test_stdout_stderr_drain_converges_and_bad_json_is_protocol_error(tmp_path: Path):
    supervisor = await _spawn(tmp_path, "rt-d", 1, [])
    ok = await supervisor.wait_ready(5.0)
    assert ok is True

    # Send malformed input directly on stdin bypassing the frame helpers.
    assert supervisor._proc is not None and supervisor._proc.stdin is not None
    supervisor._proc.stdin.write(b"not-json\n")
    await supervisor._proc.stdin.drain()

    resp = await supervisor.recv_frame(timeout=2.0)
    assert resp is not None
    assert resp["type"] == "protocol_error"
    assert resp["reason"] == "bad_json"

    status = await supervisor.inspect()
    assert status.protocol_errors == 0  # the bad frame was emitted BY the child, not received FROM it
    assert status.ready is True

    result = await supervisor.stop(app_stop_frame={"type": "stop", "reason": "cleanup"}, grace_period=2.0)
    assert result.exit_code == 0

    # All pump tasks must have converged (finished) after stop.
    for task in supervisor._pump_tasks:
        assert task.done()


@pytest.mark.asyncio
async def test_crash_immediately_reports_nonzero_exit_and_pumps_converge(tmp_path: Path):
    supervisor = await _spawn(tmp_path, "rt-e", 1, ["--crash-immediately"])
    ready = await supervisor.wait_ready(1.0)
    assert ready is False

    result = await supervisor.stop(app_stop_frame={"type": "stop", "reason": "cleanup"}, grace_period=1.0)
    assert result.exit_code == 17
    for task in supervisor._pump_tasks:
        assert task.done()
