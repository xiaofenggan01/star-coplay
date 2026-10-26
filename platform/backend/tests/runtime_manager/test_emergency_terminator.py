from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from modules.runtime_manager.emergency_terminator import (
    PosixEmergencyRuntimeTerminator, read_process_identity,
)


@pytest.mark.asyncio
async def test_real_process_ignores_term_then_emergency_uses_kill(tmp_path):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        start_new_session=True,
        env={**os.environ, "STARCOPLAY_RUNTIME_OWNER_NONCE": "owner-nonce"},
    )
    try:
        await asyncio.sleep(0.1)
        identity = read_process_identity(proc.pid)
        assert identity
        workdir = tmp_path / "runtime-1/generation-1"
        workdir.mkdir(parents=True)
        (workdir / "runtime-owner.json").write_text(json.dumps({
            "runtime_id": "runtime-1", "generation": 1, "pid": proc.pid,
            "nonce": "owner-nonce", "process_identity": identity,
        }))
        result = await PosixEmergencyRuntimeTerminator(
            tmp_path, term_timeout=0.15, kill_timeout=2,
        ).terminate("runtime-1", 1, workdir)
        assert result.terminated and result.reason == "kill"
        await asyncio.wait_for(proc.wait(), timeout=2)
        assert proc.returncode is not None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.asyncio
async def test_process_identity_mismatch_quarantines_without_signalling_pid(tmp_path):
    workdir = tmp_path / "runtime-1/generation-1"
    workdir.mkdir(parents=True)
    (workdir / "runtime-owner.json").write_text(json.dumps({
        "runtime_id": "runtime-1", "generation": 1, "pid": os.getpid(),
        "nonce": "owner-nonce", "process_identity": "reused-pid-identity",
    }))
    result = await PosixEmergencyRuntimeTerminator(tmp_path).terminate("runtime-1", 1, workdir)
    assert not result.terminated and result.quarantined
    assert result.reason == "process_identity_mismatch"
