"""Async handle for per-session Agent subprocess (stdio NDJSON)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.agentee.repository import AgenteeRuntimeData

log = logging.getLogger("platform_ws.worker.subprocess")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SESSION_RUNNER = _REPO_ROOT / "agent" / "session_runner.py"


class AgentSubprocessHandle:
    def __init__(self, proc: asyncio.subprocess.Process, agent_instance_id: str) -> None:
        self._proc = proc
        self._agent_instance_id = agent_instance_id
        self._send_lock = asyncio.Lock()
        self._stopped = False

    @property
    def agent_instance_id(self) -> str:
        return self._agent_instance_id

    @property
    def pid(self) -> int | None:
        return self._proc.pid

    @classmethod
    async def spawn(
        cls,
        *,
        agent_instance_id: str,
        platform_token: str,
        user_id: str = "",
        target_player_id: Optional[int],
        platform_url: str,
        persona: Optional[dict[str, Any]] = None,
        env: Optional[dict[str, str]] = None,
        model_tier: str = "",
        agentee_data: "Optional[AgenteeRuntimeData]" = None,
    ) -> "AgentSubprocessHandle":
        cmd = [
            sys.executable,
            str(_SESSION_RUNNER),
            "--agent-instance-id", agent_instance_id,
            "--platform-token", platform_token or "",
            "--user-id", user_id or "",
            "--target-player-id", str(target_player_id) if target_player_id is not None else "",
            "--platform-url", platform_url or "",
        ]
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        if persona:
            merged_env["AGENT_PERSONA"] = json.dumps(persona, ensure_ascii=False)
        # Inject LLM relay URL so agent subprocess routes LLM calls through local router
        http_port = os.environ.get("WS_HTTP_PORT", "8766")
        merged_env["LLM_RELAY_BASE_URL"] = (
            os.environ.get("AI_RELAY_AGENT_BASE_URL", "").strip()
            or f"http://127.0.0.1:{http_port}/api/v1/system"
        )
        merged_env["STARCOPLAY_STRUCTURED_LOGGING"] = "1"
        merged_env["STARCOPLAY_LOG_DIR"] = os.environ.get("STARCOPLAY_LOG_DIR", "/var/log/starcoplay")
        merged_env["LLM_RELAY_API_KEY"] = platform_token or ""
        resolved_model_tier = model_tier.strip()
        if not resolved_model_tier:
            resolved_model_tier = os.environ.get("AI_RELAY_AGENT_MODEL_TIER", "").strip()
            if resolved_model_tier:
                log.warning(
                    "using AI_RELAY_AGENT_MODEL_TIER fallback for agent subprocess %s: %s",
                    agent_instance_id,
                    resolved_model_tier,
                )
        if not resolved_model_tier:
            resolved_model_tier = "balanced"
            log.warning(
                "using built-in LLM_MODEL fallback for agent subprocess %s: %s",
                agent_instance_id,
                resolved_model_tier,
            )
        merged_env["LLM_MODEL"] = resolved_model_tier
        # Strip proxy vars and force no_proxy for localhost:
        # httpx reads macOS system proxy (via urllib.getproxies()) even when env vars are unset;
        # the system HTTP proxy (127.0.0.1:7890) refuses to route localhost → 502.
        # Setting NO_PROXY/no_proxy=* overrides system proxy detection in httpx.
        for _pvar in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
            merged_env.pop(_pvar, None)
        merged_env["NO_PROXY"] = "*"
        merged_env["no_proxy"] = "*"
        # Agentee persona injection
        if agentee_data:
            merged_env["STARCOPLAY_AGENTEE_DISPLAY_NAME"] = agentee_data.display_name
            merged_env["STARCOPLAY_AGENTEE_USER_CALL_NAME"] = agentee_data.user_call_name
            if agentee_data.persona_card:
                merged_env["STARCOPLAY_AGENTEE_PERSONA_CARD"] = agentee_data.persona_card
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        log.info(
            "spawned session_runner pid=%s instance=%s",
            proc.pid,
            agent_instance_id,
            extra={"event": "agent.subprocess.spawned", "agent_instance_id": agent_instance_id, "pid": proc.pid},
        )
        return cls(proc, agent_instance_id)

    async def send(self, msg: dict[str, Any]) -> bool:
        if self._stopped or self._proc.stdin is None or self._proc.stdin.is_closing():
            return False
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._send_lock:
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
                return True
            except Exception:
                log.exception("send to subprocess failed (instance=%s)", self._agent_instance_id)
                return False

    async def recv(self) -> Optional[dict[str, Any]]:
        if self._proc.stdout is None:
            return None
        line = await self._proc.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8", errors="replace"))
        except Exception:
            log.warning("bad JSON from subprocess (instance=%s): %s",
                        self._agent_instance_id, line[:120])
            return {}

    async def read_loop(self, on_frame: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        while True:
            frame = await self.recv()
            if frame is None:
                return
            if not frame:
                continue
            try:
                await on_frame(frame)
            except Exception:
                log.exception("on_frame handler failed")

    async def stderr_loop(self, on_line: Callable[[str], Awaitable[None]]) -> None:
        if self._proc.stderr is None:
            return
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                await on_line(line)
            except Exception:
                log.exception("stderr handler failed")

    async def stop(self, grace: float = 3.0) -> int:
        if self._stopped:
            return 0 if self._proc.returncode is None else int(self._proc.returncode)
        # Send stop signal BEFORE setting flag (send() checks _stopped)
        try:
            await self.send({"type": "__stop__"})
        except Exception:
            pass
        self._stopped = True
        try:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            return await asyncio.wait_for(self._proc.wait(), timeout=grace)
        except asyncio.TimeoutError:
            log.warning("session_runner pid=%s SIGTERM (instance=%s)",
                        self._proc.pid, self._agent_instance_id)
            try: self._proc.terminate()
            except Exception: pass
            try:
                return await asyncio.wait_for(self._proc.wait(), timeout=grace)
            except asyncio.TimeoutError:
                log.warning("session_runner pid=%s SIGKILL (instance=%s)",
                            self._proc.pid, self._agent_instance_id)
                try: self._proc.kill()
                except Exception: pass
                try:
                    return await self._proc.wait()
                except Exception:
                    return -1
        except Exception:
            return -1
