from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import RuntimeActualState
from modules.runtime_manager.repository import FileRuntimeRepository

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_CLAUDE_TESTS") != "1",
    reason="real Claude CLI tests are opt-in",
)
WORKER = Path(__file__).with_name("manager_crash_worker.py")


async def _wait(
    path: Path, timeout: float = 75.0, *, manager: asyncio.subprocess.Process | None = None
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
    pytest.fail(f"timeout waiting for {path}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_state", ["ready", "running"])
async def test_real_claude_manager_sigkill_reconciles_without_adopt(tmp_path: Path, crash_state: str):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "records.json"
    started = tmp_path / "started.json"
    reconciled = tmp_path / "reconciled.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    manager = await asyncio.create_subprocess_exec(
        sys.executable, str(WORKER), "start", "--provider", "claude", "--state", crash_state,
        "--workspace", str(workspace), "--repository", str(repository), "--sentinel", str(started), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    runtime_pid = None
    try:
        info = await _wait(started, manager=manager)
        runtime_pid = int(info["runtime_pid"])
        assert _alive(runtime_pid)
        os.kill(manager.pid, signal.SIGKILL)
        await manager.wait()
        assert manager.returncode == -signal.SIGKILL

        manager2 = await asyncio.create_subprocess_exec(
            sys.executable, str(WORKER), "reconcile", "--workspace", str(workspace),
            "--repository", str(repository), "--sentinel", str(reconciled), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert await manager2.wait() == 0
        result = await _wait(reconciled, timeout=15.0, manager=manager2)
        assert result["decisions"] in (["terminate_orphan"], ["mark_stopped"])
        assert "adopt" not in result["decisions"]
        record = FileRuntimeRepository(repository).load_all()[0]
        assert record.actual_state == RuntimeActualState.STOPPED
        assert (record.workdir / "mcp-revoked").exists()
        deadline = asyncio.get_running_loop().time() + 5.0
        while _alive(runtime_pid) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert not _alive(runtime_pid)
    finally:
        if manager.returncode is None:
            manager.kill()
            await manager.wait()
        if runtime_pid is not None and _alive(runtime_pid):
            try:
                os.killpg(os.getpgid(runtime_pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
