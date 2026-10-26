from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, TYPE_CHECKING

from modules.gateway.server import Session
from modules.runtime.subprocess_handle import AgentSubprocessHandle

if TYPE_CHECKING:
    from modules.agentee.repository import AgenteeRepository

log = logging.getLogger("platform_ws.worker.agent_session")


class AgentSessionWorker:
    """Per-WS-session Agent runtime backed by a dedicated subprocess.

    每个 agent_instance_id 对应一个独立 Python 子进程，经 stdio NDJSON 交换消息；
    生命周期与 WS session 对齐：首条业务消息拉起，session_end 停止。
    """

    def __init__(
        self,
        base_config: dict[str, Any] | None = None,
        platform_url: str | None = None,
        agentee_repo: "AgenteeRepository | None" = None,
        db_pool: Any = None,
    ) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._platform_url = (platform_url or os.environ.get("STARCOPLAY_PLATFORM_URL") or "").rstrip("/")
        self._base_config = base_config
        self._agentee_repo = agentee_repo
        self._db_pool = db_pool

    async def on_message(self, session: Session, msg: dict[str, Any]) -> None:
        sid = session.instance.agent_instance_id
        entry = await self._ensure_session(session)
        if entry is None:
            try:
                await session.send({
                    "type": "session.end",
                    "reason": "agent_spawn_failed",
                })
            except Exception:
                log.debug("notify mod about spawn failure failed", exc_info=True)
            return
        await self._send_entry(sid, entry, msg)

    async def send_to_agent(self, session: Session, msg: dict[str, Any]) -> bool:
        sid = session.instance.agent_instance_id
        entry = await self._ensure_session(session)
        if entry is None:
            return False
        return await self._send_entry(sid, entry, msg)

    async def _ensure_session(self, session: Session) -> dict[str, Any] | None:
        sid = session.instance.agent_instance_id
        entry = self._sessions.get(sid)
        if entry is None:
            entry = await self._build_session(session)
            if entry is None:
                return None
            self._sessions[sid] = entry
        return entry

    async def _send_entry(self, sid: str, entry: dict[str, Any], msg: dict[str, Any]) -> bool:
        handle: AgentSubprocessHandle = entry["handle"]
        ok = await handle.send(msg)
        if not ok:
            log.warning("send to subprocess failed, tearing down session %s", sid)
            await self._teardown(sid)
            return False
        return True

    async def _build_session(self, session: Session) -> dict[str, Any] | None:
        sid = session.instance.agent_instance_id
        platform_token = session.instance.platform_token or ""
        user_id = session.instance.user_id or ""
        tpid = session.instance.target_player_id

        # Fetch agentee persona data for env injection
        agentee_data = None
        if self._agentee_repo and user_id:
            try:
                agentee_data = await self._agentee_repo.get_default_runtime_data(user_id)
            except Exception:
                log.warning("failed to fetch agentee data for user %s", user_id, exc_info=True)
        model_tier = await self._get_user_default_model_tier(user_id)

        try:
            handle = await AgentSubprocessHandle.spawn(
                agent_instance_id=sid,
                platform_token=platform_token,
                user_id=user_id,
                target_player_id=tpid,
                platform_url=self._platform_url,
                model_tier=model_tier,
                agentee_data=agentee_data,
            )
        except Exception:
            log.exception("spawn session_runner failed for %s", sid)
            return None

        loop = asyncio.get_running_loop()

        async def _on_frame(frame: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(session.outbound.put_nowait, frame)
            except Exception:
                log.debug("outbound dispatch failed", exc_info=True)

        async def _on_stderr(line: str) -> None:
            log.warning(
                "agent stderr: %s",
                line,
                extra={
                    "event": "agent.stderr",
                    "user_id": user_id,
                    "agent_instance_id": sid,
                    "connection_id": session.connection_id,
                    "target_player_id": tpid,
                    "pid": handle.pid,
                },
            )

        pump_task = asyncio.create_task(handle.read_loop(_on_frame), name=f"subproc_out_{sid}")
        stderr_task = asyncio.create_task(handle.stderr_loop(_on_stderr), name=f"subproc_err_{sid}")

        log.info(
            "AgentSessionWorker: started session %s user=%s model_tier=%s",
            sid,
            user_id or "<none>",
            model_tier or "<none>",
            extra={
                "event": "agent.session.started",
                "user_id": user_id,
                "agent_instance_id": sid,
                "connection_id": session.connection_id,
                "target_player_id": tpid,
                "pid": handle.pid,
            },
        )
        return {"handle": handle, "pump_task": pump_task, "stderr_task": stderr_task}

    async def on_session_end(self, session: Session) -> None:
        sid = session.instance.agent_instance_id
        await self._teardown(sid)

    async def _teardown(self, sid: str) -> None:
        entry = self._sessions.pop(sid, None)
        if entry is None:
            return
        handle: AgentSubprocessHandle = entry["handle"]
        pump_task: asyncio.Task = entry["pump_task"]
        stderr_task: asyncio.Task | None = entry.get("stderr_task")
        try:
            await handle.stop()
        except Exception:
            log.exception("handle.stop failed for %s", sid)
        for task in (pump_task, stderr_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        log.info(
            "AgentSessionWorker: stopped session %s",
            sid,
            extra={"event": "agent.session.stopped", "agent_instance_id": sid},
        )

    async def _get_user_default_model_tier(self, user_id: str) -> str:
        if self._db_pool is None or not user_id:
            return ""
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT preferences FROM public.profiles WHERE user_id = $1",
                    user_id,
                )
        except Exception:
            log.warning("failed to fetch default model tier for user %s", user_id, exc_info=True)
            return ""
        if not row:
            return ""
        preferences = row["preferences"] or {}
        if isinstance(preferences, str):
            try:
                preferences = json.loads(preferences)
            except Exception:
                log.warning("invalid profile preferences JSON for user %s", user_id)
                return ""
        if not isinstance(preferences, dict):
            return ""
        ai_relay = preferences.get("ai_relay") or {}
        if not isinstance(ai_relay, dict):
            return ""
        value = ai_relay.get("default_model_tier")
        return value.strip() if isinstance(value, str) else ""
