from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import RuntimeActualState, RuntimeRecord
from modules.runtime_manager.repository import FileRuntimeRepository

WORKER = Path(__file__).with_name("manager_crash_worker.py")


async def _wait_file(
    path: Path, timeout: float = 20.0, *, manager: asyncio.subprocess.Process | None = None
) -> dict:
    async def early_exit_detail() -> str:
        if manager is None or manager.stderr is None:
            return ""
        return (await manager.stderr.read()).decode(errors="replace").strip()

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                if manager is not None and manager.returncode is not None:
                    stderr = await early_exit_detail()
                    pytest.fail(
                        f"manager exited early (code={manager.returncode}, stderr={stderr!r}) "
                        "with incomplete sentinel"
                    )
                await asyncio.sleep(0.05)
                continue
        if manager is not None and manager.returncode is not None:
            stderr = await early_exit_detail()
            pytest.fail(
                f"manager exited early (code={manager.returncode}, stderr={stderr!r}) "
                "without writing sentinel"
            )
        await asyncio.sleep(0.05)
    pytest.fail(f"timed out waiting for {path}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_state", ["starting", "ready", "running", "draining"])
async def test_sigkill_manager_then_fresh_manager_reconciles_without_adopt(
    tmp_path: Path, crash_state: str
):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "runtime-records.json"
    started = tmp_path / "started.json"
    reconciled = tmp_path / "reconciled.json"
    env = dict(os.environ)
    backend = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
    manager = await asyncio.create_subprocess_exec(
        sys.executable, str(WORKER), "start",
        "--workspace", str(workspace), "--repository", str(repository),
        "--sentinel", str(started), "--state", crash_state,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    runtime_pid = None
    try:
        info = await _wait_file(started, manager=manager)
        runtime_pid = int(info["runtime_pid"])
        assert info["manager_pid"] == manager.pid
        assert _alive(runtime_pid)

        os.kill(manager.pid, signal.SIGKILL)
        await manager.wait()
        assert manager.returncode == -signal.SIGKILL
        assert _alive(runtime_pid), "runtime must survive Manager SIGKILL to exercise reconcile"

        manager2 = await asyncio.create_subprocess_exec(
            sys.executable, str(WORKER), "reconcile",
            "--workspace", str(workspace), "--repository", str(repository),
            "--sentinel", str(reconciled), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        assert await manager2.wait() == 0
        result = await _wait_file(reconciled, manager=manager2)
        assert result["decisions"] in (["terminate_orphan"], ["mark_stopped"]), (
            "fresh Manager must terminate a live orphan or explicitly observe it stopped; never fake ADOPT"
        )

        deadline = asyncio.get_running_loop().time() + 5.0
        while _alive(runtime_pid) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert not _alive(runtime_pid)

        records = FileRuntimeRepository(repository).load_all()
        assert len(records) == 1
        assert records[0].actual_state == RuntimeActualState.STOPPED
        assert records[0].exit_reason == "manager-crash-reconciled"
        assert (records[0].workdir / "credential.revoked").read_text() == "revoked"
    finally:
        if manager.returncode is None:
            manager.kill()
            await manager.wait()
        if runtime_pid is not None and _alive(runtime_pid):
            try:
                os.killpg(os.getpgid(runtime_pid), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_stale_generation_exit_event_cannot_pollute_new_generation(tmp_path: Path):
    repo = FileRuntimeRepository(tmp_path / "records.json")
    old = RuntimeRecord("rt", 1, RuntimeActualState.STOPPED, RuntimeActualState.STOPPED, tmp_path / "g1", "n1")
    current = RuntimeRecord("rt", 2, RuntimeActualState.READY, RuntimeActualState.READY, tmp_path / "g2", "n2")
    repo.save_all([old, current])
    assert repo.apply_exit_event("rt", 1, "late-old-exit") is False
    after = repo.load_all()
    assert next(r for r in after if r.generation == 2).actual_state == RuntimeActualState.READY
    assert next(r for r in after if r.generation == 2).exit_reason is None
