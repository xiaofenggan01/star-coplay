from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

log = logging.getLogger("platform_ws.registry")


class InstanceState(str, Enum):
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    DRAINING = "draining"
    SUSPENDED = "suspended"
    DESTROYED = "destroyed"
    FAILED = "failed"


class EndedReason(str, Enum):
    USER_CLOSE = "user_close"
    IDLE_TIMEOUT = "idle_timeout"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    DRAIN_COMPLETE = "drain_complete"
    DRAIN_TIMEOUT = "drain_timeout"
    AUTH_FAILED = "auth_failed"
    PANIC = "panic"
    ADMIN_KICK = "admin_kick"
    DEPLOY = "deploy"
    HANDSHAKE_TIMEOUT = "handshake_timeout"


_ALLOWED_TRANSITIONS: dict[InstanceState, set[InstanceState]] = {
    InstanceState.CONNECTING: {InstanceState.AUTHENTICATING, InstanceState.FAILED, InstanceState.DESTROYED},
    InstanceState.AUTHENTICATING: {InstanceState.STARTING, InstanceState.FAILED, InstanceState.DESTROYED},
    InstanceState.STARTING: {InstanceState.RUNNING, InstanceState.FAILED, InstanceState.DRAINING},
    InstanceState.RUNNING: {
        InstanceState.DEGRADED, InstanceState.DRAINING, InstanceState.FAILED, InstanceState.SUSPENDED
    },
    InstanceState.DEGRADED: {InstanceState.RUNNING, InstanceState.DRAINING, InstanceState.FAILED},
    InstanceState.SUSPENDED: {InstanceState.RUNNING, InstanceState.DRAINING, InstanceState.FAILED},
    InstanceState.DRAINING: {InstanceState.DESTROYED, InstanceState.FAILED, InstanceState.RUNNING},
    InstanceState.FAILED: {InstanceState.DESTROYED},
    InstanceState.DESTROYED: set(),
}


@dataclass
class AgentInstance:
    agent_instance_id: str
    connection_id: str
    user_id: str | None = None
    worker_id: str | None = None
    farm_session_id: str | None = None
    target_player_id: int | None = None
    state: InstanceState = InstanceState.CONNECTING
    state_changed_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    last_game_event_at: float | None = None
    command_inflight_count: int = 0
    ended_reason: EndedReason | None = None
    resume_token: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    platform_token: str | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_instance_id": self.agent_instance_id,
            "connection_id": self.connection_id,
            "user_id": self.user_id,
            "worker_id": self.worker_id,
            "farm_session_id": self.farm_session_id,
            "target_player_id": self.target_player_id,
            "state": self.state.value,
            "state_changed_at": self.state_changed_at,
            "created_at": self.created_at,
            "last_game_event_at": self.last_game_event_at,
            "command_inflight_count": self.command_inflight_count,
            "ended_reason": self.ended_reason.value if self.ended_reason else None,
            "resume_token": self.resume_token,
            "meta": self.meta,
        }


class InstanceRegistry:
    """In-memory instance registry with optional Redis mirroring.

    Enforces state-machine transitions; records per-user bindings so the
    gateway can enforce "new kicks old" on duplicate connections.
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        worker_id: str | None = None,
        dsn: str | None = None,
    ) -> None:
        self._by_id: dict[str, AgentInstance] = {}
        self._by_user: dict[str, str] = {}
        self._resume_tokens: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._redis_url = redis_url
        self._redis: Any | None = None
        self._dsn = dsn
        self._pg_pool: Any | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as redis_asyncio
        except ImportError:
            log.info("redis not installed; registry mirror disabled")
            return None
        self._redis = redis_asyncio.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _new_instance_id() -> str:
        return f"ai_{uuid.uuid4().hex}"

    async def create(
        self,
        connection_id: str,
        user_id: str | None = None,
        target_player_id: int | None = None,
    ) -> AgentInstance:
        inst = AgentInstance(
            agent_instance_id=self._new_instance_id(),
            connection_id=connection_id,
            user_id=user_id,
            worker_id=self._worker_id,
            target_player_id=target_player_id,
        )
        async with self._lock:
            self._by_id[inst.agent_instance_id] = inst
        await self._mirror(inst)
        return inst

    async def get(self, agent_instance_id: str) -> AgentInstance | None:
        async with self._lock:
            return self._by_id.get(agent_instance_id)

    async def get_by_user(self, user_id: str) -> AgentInstance | None:
        async with self._lock:
            iid = self._by_user.get(user_id)
            return self._by_id.get(iid) if iid else None

    async def get_by_platform_token(self, token: str) -> AgentInstance | None:
        token = (token or "").strip()
        if not token:
            return None
        async with self._lock:
            for inst in self._by_id.values():
                if inst.platform_token and inst.platform_token == token:
                    return inst
            return None

    async def list_all(self) -> list[AgentInstance]:
        async with self._lock:
            return list(self._by_id.values())

    async def bind_user(self, inst: AgentInstance, user_id: str) -> AgentInstance | None:
        """Bind user → instance; returns the displaced previous instance (if any)."""
        async with self._lock:
            prev_iid = self._by_user.get(user_id)
            prev = self._by_id.get(prev_iid) if prev_iid else None
            self._by_user[user_id] = inst.agent_instance_id
            inst.user_id = user_id
        return prev

    async def bind_observer(self, inst: AgentInstance, user_id: str) -> None:
        """Attach authenticated identity without claiming the controller slot."""
        async with self._lock:
            inst.user_id = user_id
        await self._mirror(inst)

    async def transition(
        self,
        inst: AgentInstance,
        target: InstanceState,
        *,
        ended_reason: EndedReason | None = None,
    ) -> bool:
        async with self._lock:
            if target not in _ALLOWED_TRANSITIONS.get(inst.state, set()):
                log.warning(
                    "invalid transition %s -> %s for %s",
                    inst.state.value, target.value, inst.agent_instance_id,
                )
                return False
            inst.state = target
            inst.state_changed_at = time.time()
            if ended_reason is not None:
                inst.ended_reason = ended_reason
        await self._mirror(inst)
        return True

    async def update_activity(self, inst: AgentInstance, *, game_event: bool = False, inflight_delta: int = 0) -> None:
        async with self._lock:
            if game_event:
                inst.last_game_event_at = time.time()
            if inflight_delta:
                inst.command_inflight_count = max(0, inst.command_inflight_count + inflight_delta)
        await self._mirror(inst)

    async def issue_resume_token(self, inst: AgentInstance, *, ttl_s: float = 300.0) -> str:
        token = uuid.uuid4().hex
        async with self._lock:
            old = inst.resume_token
            if old:
                self._resume_tokens.pop(old, None)
            inst.resume_token = token
            self._resume_tokens[token] = (inst.agent_instance_id, time.time() + ttl_s)
        await self._mirror(inst)
        return token

    async def find_by_resume_token(self, token: str) -> AgentInstance | None:
        async with self._lock:
            item = self._resume_tokens.get(token)
            if not item:
                return None
            iid, expires_at = item
            if time.time() > expires_at:
                self._resume_tokens.pop(token, None)
                return None
            inst = self._by_id.get(iid)
            if inst is None:
                self._resume_tokens.pop(token, None)  # 实例已销毁，清理孤立 token
            return inst

    async def destroy(self, inst: AgentInstance) -> None:
        async with self._lock:
            self._by_id.pop(inst.agent_instance_id, None)
            if inst.user_id and self._by_user.get(inst.user_id) == inst.agent_instance_id:
                self._by_user.pop(inst.user_id, None)
            if inst.resume_token:
                self._resume_tokens.pop(inst.resume_token, None)
        r = await self._get_redis()
        if r is not None:
            try:
                await r.delete(f"agent_instance:{inst.agent_instance_id}")
            except Exception:
                pass
        await self._mirror_pg(inst)

    async def _mirror(self, inst: AgentInstance) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                import json as _json
                await r.set(
                    f"agent_instance:{inst.agent_instance_id}",
                    _json.dumps(inst.to_dict(), default=str),
                    ex=86400,
                )
            except Exception as e:
                log.debug("redis mirror failed: %s", e)
        await self._mirror_pg(inst)

    async def _get_pg(self):
        if self._pg_pool is not None:
            return self._pg_pool
        if not self._dsn:
            return None
        try:
            import asyncpg
        except ImportError:
            log.info("asyncpg not installed; DB mirror disabled")
            return None
        try:
            self._pg_pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        except Exception as e:
            log.warning("asyncpg pool init failed: %s", e)
            self._pg_pool = None
        return self._pg_pool

    @staticmethod
    def _ts(value: float | None):
        if value is None:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value, tz=timezone.utc)

    async def _mirror_pg(self, inst: AgentInstance) -> None:
        pool = await self._get_pg()
        if pool is None:
            return
        try:
            import json as _json
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    insert into public.agent_instances (
                        agent_instance_id, user_id, worker_id, connection_id,
                        farm_session_id, target_player_id, state, state_changed_at,
                        created_at, last_game_event_at, command_inflight_count,
                        ended_reason, resume_token, meta
                    ) values (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb
                    )
                    on conflict (agent_instance_id) do update set
                        user_id = excluded.user_id,
                        worker_id = excluded.worker_id,
                        connection_id = excluded.connection_id,
                        farm_session_id = excluded.farm_session_id,
                        target_player_id = excluded.target_player_id,
                        state = excluded.state,
                        state_changed_at = excluded.state_changed_at,
                        last_game_event_at = excluded.last_game_event_at,
                        command_inflight_count = excluded.command_inflight_count,
                        ended_reason = excluded.ended_reason,
                        resume_token = excluded.resume_token,
                        meta = excluded.meta
                    """,
                    inst.agent_instance_id,
                    inst.user_id,
                    inst.worker_id,
                    inst.connection_id,
                    inst.farm_session_id,
                    inst.target_player_id,
                    inst.state.value,
                    self._ts(inst.state_changed_at),
                    self._ts(inst.created_at),
                    self._ts(inst.last_game_event_at),
                    inst.command_inflight_count,
                    inst.ended_reason.value if inst.ended_reason else None,
                    inst.resume_token,
                    _json.dumps(inst.meta or {}),
                )
        except Exception as e:
            log.debug("pg mirror failed: %s", e)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None
        if self._pg_pool is not None:
            try:
                await self._pg_pool.close()
            except Exception:
                pass
            self._pg_pool = None
