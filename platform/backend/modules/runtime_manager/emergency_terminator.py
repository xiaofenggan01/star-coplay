"""Fail-closed emergency termination based on verified OS process identity."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


def read_process_identity(pid: int) -> Optional[str]:
    """Return a PID-reuse-resistant OS start identity, or None if absent."""
    if not isinstance(pid, int) or pid <= 1:
        return None
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        fields = proc_stat.read_text(encoding="utf-8").split()
        return f"linux-start-ticks:{fields[21]}" if len(fields) > 21 else None
    except OSError:
        pass
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], check=False,
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    started = " ".join(completed.stdout.split())
    return f"ps-lstart:{started}" if completed.returncode == 0 and started else None


def process_has_owner_nonce(pid: int, nonce: str) -> bool:
    marker = f"STARCOPLAY_RUNTIME_OWNER_NONCE={nonce}"
    try:
        values = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        return marker.encode() in values
    except OSError:
        pass
    try:
        completed = subprocess.run(
            ["ps", "eww", "-p", str(pid)], check=False,
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and marker in completed.stdout


@dataclass(frozen=True)
class EmergencyTerminationResult:
    terminated: bool
    quarantined: bool = False
    reason: str = ""


class EmergencyRuntimeTerminator(Protocol):
    async def terminate(self, runtime_id: str, generation: int,
                        workdir: Path) -> EmergencyTerminationResult: ...


class PosixEmergencyRuntimeTerminator:
    def __init__(self, workspace_root: Path, *, term_timeout: float = 2.0,
                 kill_timeout: float = 2.0) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.term_timeout = term_timeout
        self.kill_timeout = kill_timeout

    async def terminate(self, runtime_id: str, generation: int,
                        workdir: Path) -> EmergencyTerminationResult:
        resolved = Path(workdir).resolve()
        expected = (self.workspace_root / runtime_id / f"generation-{generation}").resolve()
        owner_path = resolved / "runtime-owner.json"
        if resolved != expected or not resolved.is_relative_to(self.workspace_root) or owner_path.is_symlink():
            return EmergencyTerminationResult(False, True, "owner_path_mismatch")
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return EmergencyTerminationResult(False, True, "owner_evidence_unavailable")
        pid = owner.get("pid")
        recorded_identity = owner.get("process_identity")
        owner_nonce = owner.get("nonce")
        if (
            owner.get("runtime_id") != runtime_id
            or owner.get("generation") != generation
            or not isinstance(pid, int) or pid <= 1
            or not isinstance(recorded_identity, str) or not recorded_identity
            or not isinstance(owner_nonce, str) or not owner_nonce
        ):
            return EmergencyTerminationResult(False, True, "owner_identity_invalid")
        live_identity = read_process_identity(pid)
        if live_identity is None:
            return EmergencyTerminationResult(True, False, "already_exited")
        if live_identity != recorded_identity:
            return EmergencyTerminationResult(False, True, "process_identity_mismatch")
        if not process_has_owner_nonce(pid, owner_nonce):
            return EmergencyTerminationResult(False, True, "process_owner_nonce_mismatch")
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return EmergencyTerminationResult(True, False, "already_exited")
        if pgid != pid:
            return EmergencyTerminationResult(False, True, "process_group_identity_mismatch")
        os.killpg(pgid, signal.SIGTERM)
        if await self._wait_gone(pid, recorded_identity, self.term_timeout):
            return EmergencyTerminationResult(True, False, "term")
        # Revalidate immediately before the irreversible signal.
        if read_process_identity(pid) != recorded_identity:
            return EmergencyTerminationResult(False, True, "process_identity_changed_before_kill")
        if not process_has_owner_nonce(pid, owner_nonce):
            return EmergencyTerminationResult(False, True, "process_owner_changed_before_kill")
        os.killpg(pgid, signal.SIGKILL)
        if await self._wait_gone(pid, recorded_identity, self.kill_timeout):
            return EmergencyTerminationResult(True, False, "kill")
        return EmergencyTerminationResult(False, True, "process_survived_kill")

    @staticmethod
    async def _wait_gone(pid: int, identity: str, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            live = read_process_identity(pid)
            if live is None or live != identity:
                return True
            await asyncio.sleep(0.05)
        return read_process_identity(pid) is None
