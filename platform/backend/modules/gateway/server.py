from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from websockets.exceptions import ConnectionClosed

from common.auth.token_authenticator import AuthResult, TokenAuthenticator
from common.observability import bind_log_context, emit_metric, reset_log_context
from modules.gateway.hosted_session import ClosePublishDeferred
from modules.gateway.runtime_contracts import AuthenticatedSessionEventHook
from modules.runtime.instance_registry import (
    AgentInstance,
    EndedReason,
    InstanceRegistry,
    InstanceState,
)
from modules.runtime.telemetry_writer import TelemetryWriter
from modules.runtime.text_channel_fanout import TextChannelOutputFanout

log = logging.getLogger("platform_ws.gateway")

HANDSHAKE_TIMEOUT_S = 10.0
HEARTBEAT_INTERVAL_S = 30.0
HEARTBEAT_TIMEOUT_S = 90.0
T_DRAIN_MAX_S = 60.0
# Connection resume window for hosted controllers after abnormal
# transport disconnect (ws_close_1006, no close frame, heartbeat silence).
# Independent from RuntimeManager.online_business_silence_ttl (3600 s).
RESUME_TOKEN_TTL_S = 600.0
CONTROLLER_PREPARE_TIMEOUT_S = 5.0
CONTROLLER_PREPARE_RETRY_S = 0.05
CLOSE_PREFENCE_RETRY_S = 0.05
CLOSE_PREFENCE_MAX_RETRIES = 3
PROTOCOL_VERSION = 1
APPEARANCE_INT_RANGES: dict[str, tuple[int, int]] = {
    "skin": (0, 23),
    "hairstyle": (0, 73),
    "accessory": (-1, 30),
    "shirtIndex": (-1, 9999),
    "pantsIndex": (-1, 9999),
}
APPEARANCE_OPTIONAL_INT_RANGES: dict[str, tuple[int, int]] = {
    "hatIndex": (0, 9999),
    "bootsIndex": (0, 9999),
}
APPEARANCE_KEY_ALIASES: dict[str, str] = {
    "hair_color": "hairColor",
    "eye_color": "eyeColor",
    "shirt_index": "shirtIndex",
    "pants_index": "pantsIndex",
    "pants_color": "pantsColor",
    "hat_index": "hatIndex",
    "boots_index": "bootsIndex",
    "facing_direction": "facingDirection",
}
APPEARANCE_ALLOWED_KEYS = {
    "gender",
    "skin",
    "hairstyle",
    "hairColor",
    "eyeColor",
    "accessory",
    "shirtIndex",
    "pantsIndex",
    "pantsColor",
    "hatIndex",
    "bootsIndex",
    "facingDirection",
}


def _coerce_appearance_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return _coerce_appearance_int(value[0])
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped, 10)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("Value", "value"):
            if key in value:
                return _coerce_appearance_int(value[key])
    return None


def _summarize_appearance_payload(appearance: Any) -> str:
    try:
        text = json.dumps(appearance, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        text = repr(appearance)
    return text if len(text) <= 1200 else f"{text[:1200]}..."


def _ws_close_code(exc: ConnectionClosed) -> int | None:
    for attr in ("rcvd", "sent"):
        close = getattr(exc, attr, None)
        code = getattr(close, "code", None)
        if isinstance(code, int):
            return code
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _normalize_appearance_sync_payload(appearance: Any) -> dict[str, Any] | None:
    if not isinstance(appearance, dict):
        return None

    normalized: dict[str, Any] = {}
    for raw_key, value in appearance.items():
        key = APPEARANCE_KEY_ALIASES.get(str(raw_key), str(raw_key))
        if key not in APPEARANCE_ALLOWED_KEYS:
            return None
        normalized[key] = value

    if normalized.get("gender") not in {"Male", "Female"}:
        return None
    for key, (min_value, max_value) in APPEARANCE_INT_RANGES.items():
        raw_value = normalized.get(key)
        value = _coerce_appearance_int(raw_value)
        if key in {"shirtIndex", "pantsIndex"} and raw_value is None:
            value = -1
        if value is None or value < min_value or value > max_value:
            return None
        normalized[key] = value
    normalized["facingDirection"] = 2
    for key, (min_value, max_value) in APPEARANCE_OPTIONAL_INT_RANGES.items():
        raw_value = normalized.get(key)
        value = _coerce_appearance_int(raw_value)
        if raw_value is None:
            continue
        if value is None or value < min_value or value > max_value:
            return None
        normalized[key] = value
    for key in ("hairColor", "eyeColor", "pantsColor"):
        color = normalized.get(key)
        if not isinstance(color, dict):
            return None
        for channel in ("h", "s", "b"):
            channel_value = _coerce_appearance_int(color.get(channel))
            if channel_value is None or channel_value < 0 or channel_value > 100:
                return None
            color[channel] = channel_value
    if "hatIndex" not in normalized:
        normalized["hatIndex"] = None
    if "bootsIndex" not in normalized:
        normalized["bootsIndex"] = None
    normalized["updated_at"] = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    return normalized


def _coerce_profile_config(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return dict(config)
    if isinstance(config, str):
        try:
            parsed = json.loads(config)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


@dataclass
class Session:
    connection_id: str
    instance: AgentInstance
    ws: Any
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_pong_at: float = field(default_factory=time.time)
    last_liveness_mirror_at: float = 0.0
    inbound: asyncio.Queue = field(default_factory=asyncio.Queue)
    outbound: asyncio.Queue = field(default_factory=asyncio.Queue)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    _subscriber_frame_types: dict[asyncio.Queue, frozenset[str] | None] = field(
        default_factory=dict,
        repr=False,
    )
    hosted_facts: Any | None = None
    hosted_game_session_id: str | None = None
    hosted_resume_identity: str | None = None
    hosted_route_selected: bool = False
    hosted_runtime_spec: Any | None = None
    hosted_connection_role: str = "observer"
    hosted_player_id: str | None = None
    accepted_agent_control_revision: int | None = None
    hosted_controller_closed: bool = False
    hosted_transport_detached: bool = False
    explicit_end_requested: bool = False

    async def send(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg, ensure_ascii=False)
        async with self.send_lock:
            await self.ws.send(data)

    def subscribe(self, *, frame_types: frozenset[str] | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subscribers.append(q)
        self._subscriber_frame_types[q] = frame_types
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscriber_frame_types.pop(q, None)
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass

    def fanout(self, msg: dict[str, Any]) -> None:
        dead: list[asyncio.Queue] = []
        for q in self.subscribers:
            frame_types = self._subscriber_frame_types.get(q)
            if frame_types is not None and msg.get("type") not in frame_types:
                continue
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)


WorkerHandle = Callable[[Session, dict[str, Any]], Awaitable[None]]
"""Called for each inbound message from Mod (after hello). Typically dispatches
to an Agent worker / internal RPC.
"""

SessionEndHandle = Callable[[Session], Awaitable[None]]
"""Optional hook called once when a session transitions into destroyed/failed,
allowing workers to stop per-session resources (Agent threads, bridges)."""


@dataclass
class GatewayConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    path: str = "/ws"
    dsn: str | None = None
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    redis_url: str | None = None
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S
    handshake_timeout_s: float = HANDSHAKE_TIMEOUT_S
    drain_max_s: float = T_DRAIN_MAX_S
    resume_token_ttl_s: float = RESUME_TOKEN_TTL_S
    max_connections: int = 50
    admin_secret: str = ""
    hosted_route_enabled: bool = False

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(
            host=os.environ.get("WS_GATEWAY_HOST", "0.0.0.0"),
            port=int(os.environ.get("WS_GATEWAY_PORT", "8765")),
            path=os.environ.get("WS_GATEWAY_PATH", "/ws"),
            dsn=os.environ.get("DATABASE_URL") or None,
            supabase_url=os.environ.get("SUPABASE_URL") or None,
            supabase_service_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or None,
            redis_url=os.environ.get("REDIS_URL") or None,
            heartbeat_interval_s=float(os.environ.get("WS_HEARTBEAT_INTERVAL_S", HEARTBEAT_INTERVAL_S)),
            heartbeat_timeout_s=float(os.environ.get("WS_HEARTBEAT_TIMEOUT_S", HEARTBEAT_TIMEOUT_S)),
            handshake_timeout_s=float(os.environ.get("WS_HANDSHAKE_TIMEOUT_S", HANDSHAKE_TIMEOUT_S)),
            drain_max_s=float(os.environ.get("WS_DRAIN_MAX_S", T_DRAIN_MAX_S)),
            resume_token_ttl_s=float(os.environ.get("WS_RESUME_TOKEN_TTL_S", RESUME_TOKEN_TTL_S)),
            max_connections=int(os.environ.get("WS_MAX_CONNECTIONS", "50")),
            admin_secret=os.environ.get("ADMIN_SECRET", ""),
            hosted_route_enabled=os.environ.get("HOSTED_GAME_SESSION_ROUTE_ENABLED", "").strip().lower() in ("1", "true", "yes"),
        )


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        worker_handle: WorkerHandle,
        *,
        authenticator: TokenAuthenticator | None = None,
        registry: InstanceRegistry | None = None,
        on_session_end: SessionEndHandle | None = None,
        game_session_event_hook: AuthenticatedSessionEventHook | None = None,
        hosted_session_registry: Any | None = None,
        hosted_user_ids: frozenset[str] | None = None,
        hosted_all_authenticated: bool = False,
        hosted_rollout_provider: Any | None = None,
        hosted_runtime_activator: Any | None = None,
        hosted_control_store: Any | None = None,
        hosted_runtime_input_dispatcher: Any | None = None,
    ) -> None:
        self._config = config
        self._worker_handle = worker_handle
        self._on_session_end = on_session_end
        self._game_session_event_hook = game_session_event_hook
        self._hosted_session_registry = hosted_session_registry
        self._hosted_user_ids = hosted_user_ids
        self._hosted_all_authenticated = hosted_all_authenticated
        self._hosted_rollout_provider = hosted_rollout_provider
        self._hosted_runtime_activator = hosted_runtime_activator
        self._hosted_runtime_input_dispatcher = hosted_runtime_input_dispatcher
        if hosted_control_store is None:
            from modules.gateway.hosted_session import HostedControlSelectionStore
            hosted_control_store = HostedControlSelectionStore(config.dsn)
        self._hosted_control_store = hosted_control_store
        if config.hosted_route_enabled and (game_session_event_hook is None or hosted_session_registry is None):
            raise ValueError("hosted route requires session registry and event coordinator")
        self._auth = authenticator or TokenAuthenticator(
            dsn=config.dsn,
            supabase_url=config.supabase_url,
            supabase_service_key=config.supabase_service_key,
        )
        if not config.dsn and not (config.supabase_url and config.supabase_service_key):
            if os.environ.get("STARCOPLAY_WS_AUTH_BYPASS", "").strip().lower() not in ("1", "true", "yes"):
                log.warning(
                    "WS 认证未配置（无 DATABASE_URL、且无 SUPABASE_URL+SUPABASE_SERVICE_ROLE_KEY），"
                    "握手将返回 no_auth_backend。"
                    "本地可：从 Supabase 填 DATABASE_URL，或设 STARCOPLAY_WS_AUTH_BYPASS=1（仅开发）"
                )
        self._registry = registry or InstanceRegistry(redis_url=config.redis_url, dsn=config.dsn)
        self._telemetry_writer = TelemetryWriter(dsn=config.dsn)
        self._text_channel_fanout = TextChannelOutputFanout(writer=self._telemetry_writer)
        self._stop = asyncio.Event()
        self._server: Any | None = None
        self._sessions_by_instance: dict[str, Session] = {}
        self._hosted_sessions_by_owner: dict[str, dict[str, Session]] = {}
        self._hosted_owner_locks: dict[str, asyncio.Lock] = {}
        self._last_hosted_takeover: dict[str, Any] | None = None
        self._accepting = True  # 维护模式开关

    @property
    def registry(self) -> InstanceRegistry:
        return self._registry

    @property
    def authenticator(self) -> TokenAuthenticator:
        return self._auth

    def get_session(self, agent_instance_id: str) -> Optional[Session]:
        return self._sessions_by_instance.get(agent_instance_id)

    def hosted_diagnostics_snapshot(self) -> dict[str, Any]:
        sessions = []
        summary = {"controller": 0, "observer": 0, "reconnecting": 0}
        for session in tuple(self._sessions_by_instance.values()):
            if not session.hosted_route_selected:
                continue
            transport_state = (
                "reconnecting"
                if session.hosted_transport_detached and not session.hosted_controller_closed
                else "connected"
            )
            state = (
                "reconnecting" if transport_state == "reconnecting"
                else session.hosted_connection_role
            )
            if state in summary:
                summary[state] += 1
            facts = session.hosted_facts
            sessions.append({
                "role": session.hosted_connection_role,
                "transport_state": transport_state,
                "target_player_id": str(
                    getattr(facts, "agent_control_target_player_id", "") or ""
                ),
                "control_revision": session.accepted_agent_control_revision or 0,
            })
        return {
            "summary": summary,
            "sessions": tuple(sessions),
            "last_takeover": (
                dict(self._last_hosted_takeover)
                if self._last_hosted_takeover is not None else None
            ),
        }

    def _record_hosted_takeover(self, outcome: str, session: Session,
                                revision: int) -> None:
        facts = session.hosted_facts
        self._last_hosted_takeover = {
            "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "outcome": outcome,
            "target_player_id": str(
                getattr(facts, "agent_control_target_player_id", "") or ""
            ),
            "revision": revision,
        }
        emit_metric("controller_takeover_total", "counter", 1, outcome=outcome)

    def _owns_current_connection(self, session: Session) -> bool:
        if not session.hosted_route_selected:
            return True
        if session.hosted_connection_role != "controller":
            return False
        active = session.instance.meta.get("_hosted_active_connection_id")
        return active is None or active == session.connection_id

    async def serve(self) -> None:
        try:
            import websockets
        except ImportError as e:
            raise RuntimeError("websockets package required (pip install websockets)") from e

        async def _handler(ws):
            await self._handle_connection(ws)

        self._server = await websockets.serve(
            _handler,
            self._config.host,
            self._config.port,
            ping_interval=self._config.heartbeat_interval_s,
            ping_timeout=self._config.heartbeat_timeout_s,
            max_size=8 * 1024 * 1024,
        )
        log.info("WS Gateway listening on %s:%d%s", self._config.host, self._config.port, self._config.path)
        try:
            await self._stop.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()
            await self._auth.close()
            await self._registry.close()
            await self._hosted_control_store.close()
            await self._telemetry_writer.close()

    def stop(self) -> None:
        self._stop.set()

    @property
    def accepting(self) -> bool:
        return self._accepting

    @accepting.setter
    def accepting(self, value: bool) -> None:
        self._accepting = value
        log.info("accepting connections: %s", value)

    @property
    def current_connections(self) -> int:
        return len(self._sessions_by_instance)

    @property
    def max_connections(self) -> int:
        return self._config.max_connections

    async def kick_instance(self, agent_instance_id: str, reason: str = "") -> bool:
        """Admin 踢出指定实例。返回 True 表示成功。"""
        session = self._sessions_by_instance.get(agent_instance_id)
        if session is None:
            return False
        inst = session.instance
        log.warning("admin kick: instance=%s reason=%s", agent_instance_id, reason)
        try:
            await session.send({"type": "error", "code": "admin_kick", "message": reason or "kicked by admin"})
        except Exception:
            pass
        try:
            await session.ws.close()
        except Exception:
            pass
        return True

    async def _promote_hosted_observer(self, session: Session, revision: int) -> None:
        """Claim the sole live controller route without replacing the socket."""
        if session.hosted_connection_role == "controller":
            return
        facts = session.hosted_facts
        session.hosted_connection_role = "controller"
        session.hosted_controller_closed = False
        session.accepted_agent_control_revision = revision
        session.hosted_game_session_id = f"gs_{uuid.uuid4().hex}"
        session.hosted_resume_identity = f"gri_{uuid.uuid4().hex}"
        inst = session.instance
        inst.meta.update({
            "hosted_game_session_id": session.hosted_game_session_id,
            "hosted_resume_identity": session.hosted_resume_identity,
            "hosted_game_instance_nonce": facts.game_instance_nonce,
            "hosted_capability_digest": facts.capability_digest,
            "_hosted_active_connection_id": session.connection_id,
        })
        await self._registry.bind_user(inst, inst.user_id)
        await self._game_session_event_hook.on_authenticated(session, resumed=False)
        try:
            if self._hosted_runtime_activator is not None:
                await self._prepare_hosted_controller(session)
        except BaseException:
            await self._close_hosted_controller(session)
            session.hosted_connection_role = "observer"
            session.instance.meta.pop("_hosted_active_connection_id", None)
            raise
        await session.send({
            "type": "connection_role_changed",
            "connection_role": "controller",
            "accepted_agent_control_revision": revision,
            "game_session_id": session.hosted_game_session_id,
            "resume_identity": session.hosted_resume_identity,
        })
        if self._hosted_runtime_activator is not None:
            task = asyncio.create_task(self._hosted_runtime_activator.activate(session))
            task.add_done_callback(self._log_background_activation_failure)

    async def _prepare_hosted_controller(self, session: Session) -> None:
        """Wait boundedly for CLOSED projection/outbox convergence before replacement."""
        deadline = time.monotonic() + CONTROLLER_PREPARE_TIMEOUT_S
        attempt = 0
        while True:
            attempt += 1
            try:
                await self._hosted_runtime_activator.prepare(session)
                if attempt > 1:
                    log.info(
                        "hosted controller prepare converged after retry",
                        extra={"event": "gateway.controller.prepare_converged",
                               "attempt": attempt,
                               "user_id": session.instance.user_id,
                               "connection_id": session.connection_id},
                    )
                return
            except RuntimeError as exc:
                if str(exc) != "replacement_binding_not_revoked" or time.monotonic() >= deadline:
                    raise
                log.warning(
                    "waiting for previous controller Binding revocation",
                    extra={"event": "gateway.controller.prepare_deferred",
                           "attempt": attempt,
                           "user_id": session.instance.user_id,
                           "connection_id": session.connection_id,
                           "error_code": "replacement_binding_not_revoked"},
                )
                await asyncio.sleep(CONTROLLER_PREPARE_RETRY_S)

    @staticmethod
    def _log_background_activation_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            log.exception("hosted runtime activation failed after controller switch")

    async def _close_hosted_controller(
        self, session: Session, *, stop_runtime: bool = False, revision: int | None = None,
    ) -> None:
        if session.hosted_controller_closed or not session.hosted_game_session_id:
            return
        handler = getattr(self._game_session_event_hook, "on_closed", None)
        if handler is None:
            raise RuntimeError("hosted close event dependency unavailable")
        await handler(session)
        closer = getattr(self._hosted_runtime_activator, "close", None)
        if stop_runtime and closer is not None:
            owner = session.instance.user_id
            if not owner or revision is None:
                raise RuntimeError("explicit hosted close is missing owner or revision")
            await closer(owner, operation_id=f"control-close:{owner}:{revision}")
        session.hosted_controller_closed = True

    async def _handle_connection(self, ws: Any) -> None:
        connection_id = f"conn_{uuid.uuid4().hex[:12]}"
        context_token = bind_log_context(connection_id=connection_id)
        if not self._accepting:
            await self._send_safe(ws, {"type": "error", "code": "maintenance", "message": "server is in maintenance mode"})
            await ws.close()
            return
        if len(self._sessions_by_instance) >= self._config.max_connections:
            await self._send_safe(ws, {"type": "error", "code": "server_full", "message": "max connections reached"})
            await ws.close()
            return
        inst = await self._registry.create(connection_id=connection_id)
        session = Session(connection_id=connection_id, instance=inst, ws=ws)
        runtime_hook_attached = False
        controlled_takeover = False
        controller_recovery_offered = False
        detached_same_target_replacement = False
        displaced_session: Session | None = None
        bind_log_context(agent_instance_id=inst.agent_instance_id)
        log.info(
            "conn %s accepted; instance %s state=connecting",
            connection_id,
            inst.agent_instance_id,
            extra={
                "event": "gateway.connection.accepted",
                "connection_id": connection_id,
                "agent_instance_id": inst.agent_instance_id,
            },
        )

        _explicit_close = False
        try:
            await self._registry.transition(inst, InstanceState.AUTHENTICATING)
            hello = await self._await_hello(ws)
            if hello is None:
                await self._registry.transition(inst, InstanceState.FAILED, ended_reason=EndedReason.HANDSHAKE_TIMEOUT)
                return

            platform_token = (hello.get("platform_token") or "").strip()
            auth = await self._auth.authenticate(platform_token)
            if not auth.ok or auth.user_id is None:
                log.warning(
                    "auth failed: code=%s message=%s",
                    auth.code or "invalid_token",
                    auth.message or "invalid token",
                    extra={
                        "event": "gateway.auth.failed",
                        "connection_id": connection_id,
                        "agent_instance_id": inst.agent_instance_id,
                        "error_code": auth.code or "invalid_token",
                    },
                )
                await self._send_safe(ws, {
                    "type": "auth_error",
                    "code": auth.code or "invalid_token",
                    "message": auth.message or "invalid token",
                })
                await self._registry.transition(inst, InstanceState.FAILED, ended_reason=EndedReason.AUTH_FAILED)
                return
            inst.platform_token = platform_token
            if self._config.hosted_route_enabled and self._hosted_rollout_provider is not None:
                session.hosted_route_selected = await self._hosted_rollout_provider.is_allowed(
                    auth.user_id, auth.email,
                )
            else:
                session.hosted_route_selected = (
                    self._config.hosted_route_enabled
                    and (
                        self._hosted_all_authenticated
                        or self._hosted_user_ids is None
                        or auth.user_id in self._hosted_user_ids
                    )
                )
            if session.hosted_route_selected:
                from modules.gateway.hosted_session import parse_hosted_hello
                session.hosted_facts = parse_hosted_hello(hello)
                session.hosted_player_id = session.hosted_facts.player_id
                owner_lock = self._hosted_owner_locks.setdefault(auth.user_id, asyncio.Lock())
                async with owner_lock:
                    selection = await self._hosted_control_store.accept(
                        auth.user_id,
                        enabled=session.hosted_facts.agent_control_enabled,
                        target_player_id=session.hosted_facts.agent_control_target_player_id,
                        revision=session.hosted_facts.agent_control_revision,
                    )
                    retry_pending = getattr(
                        self._game_session_event_hook, "retry_pending_for_owner", None
                    )
                    if retry_pending is not None:
                        await retry_pending(auth.user_id)
                    session.accepted_agent_control_revision = selection.revision
                    session.hosted_connection_role = (
                        "controller" if selection.enabled
                        and selection.target_player_id == session.hosted_player_id else "observer"
                    )
                    if session.hosted_facts.agent_control_revision != selection.revision:
                        session.hosted_connection_role = "observer"
                    owner_sessions = self._hosted_sessions_by_owner.setdefault(auth.user_id, {})
                    retained_before = await self._hosted_session_registry.resolve_owner_retained(
                        auth.user_id
                    )
                    retained_session = (
                        retained_before._session if retained_before is not None else None
                    )
                    prior_revision = max(
                        [item.accepted_agent_control_revision or -1 for item in owner_sessions.values()]
                        + ([retained_before._session.accepted_agent_control_revision or -1]
                           if retained_before is not None else []),
                        default=-1,
                    )
                    detached_same_target_replacement = (
                        retained_session is not None
                        and retained_session.hosted_transport_detached
                        and not retained_session.hosted_controller_closed
                        and retained_session.hosted_player_id == selection.target_player_id
                        and selection.enabled
                        and selection.target_player_id == session.hosted_player_id
                        and selection.revision > prior_revision
                    )
                    if selection.revision > prior_revision:
                        closed_live_controller = False
                        for old_session in tuple(owner_sessions.values()):
                            if (old_session.hosted_connection_role == "controller"
                                    and old_session.hosted_player_id != selection.target_player_id):
                                old_session.hosted_connection_role = "observer"
                                old_session.instance.meta.pop("_hosted_active_connection_id", None)
                                await self._close_hosted_controller(
                                    old_session, stop_runtime=not selection.enabled,
                                    revision=selection.revision,
                                )
                                closed_live_controller = True
                                await old_session.send({
                                    "type": "connection_role_changed",
                                    "connection_role": "observer",
                                    "accepted_agent_control_revision": selection.revision,
                                })
                        if not closed_live_controller:
                            retained = retained_before
                            if retained is not None and not detached_same_target_replacement:
                                retained_session = retained._session
                                retained_session.hosted_connection_role = "observer"
                                retained_session.instance.meta.pop("_hosted_active_connection_id", None)
                                await self._close_hosted_controller(
                                    retained_session, stop_runtime=not selection.enabled,
                                    revision=selection.revision,
                                )
                            elif not selection.enabled:
                                closer = getattr(self._hosted_runtime_activator, "close", None)
                                if closer is not None:
                                    await closer(
                                        auth.user_id,
                                        operation_id=(
                                            f"control-close:{auth.user_id}:{selection.revision}"
                                        ),
                                    )
                        target_session = next((
                            item for item in owner_sessions.values()
                            if selection.enabled
                            and item.hosted_player_id == selection.target_player_id
                        ), None)
                        if target_session is not None:
                            await self._promote_hosted_observer(target_session, selection.revision)
                    active_controller = next((
                        item for item in owner_sessions.values()
                        if item.hosted_connection_role == "controller"
                    ), None)
                    resume_request = hello.get("resume_token")
                    controller_recovery_offered = (
                        active_controller is None
                        and retained_session is not None
                        and retained_session.hosted_transport_detached
                        and not retained_session.hosted_controller_closed
                        and retained_session.hosted_player_id == selection.target_player_id
                        and selection.enabled
                        and selection.target_player_id == session.hosted_player_id
                        and session.hosted_facts.agent_control_revision == selection.revision
                        and selection.revision <= prior_revision
                        and not (isinstance(resume_request, str) and resume_request.strip())
                    )
                    if controller_recovery_offered:
                        session.hosted_connection_role = "observer"
                        self._record_hosted_takeover(
                            "recovery_offered", session, selection.revision,
                        )
                    exact_transport_resume = (
                        active_controller is not None
                        and isinstance(resume_request, str)
                        and active_controller.instance.resume_token == resume_request.strip()
                    )
                    if exact_transport_resume:
                        active_controller.hosted_connection_role = "observer"
                        active_controller.instance.meta.pop("_hosted_active_connection_id", None)
                        await active_controller.send({
                            "type": "connection_role_changed",
                            "connection_role": "observer",
                            "accepted_agent_control_revision": selection.revision,
                        })
                    elif active_controller is not None:
                        session.hosted_connection_role = "observer"
                        if (
                            selection.enabled
                            and selection.target_player_id == session.hosted_player_id
                        ):
                            self._record_hosted_takeover(
                                "blocked_live_controller", session, selection.revision,
                            )
                    owner_sessions[connection_id] = session
            bind_log_context(user_id=auth.user_id)
            log.info(
                "auth ok: user=%s instance=%s",
                auth.user_id,
                inst.agent_instance_id,
                extra={
                    "event": "gateway.auth.ok",
                    "user_id": auth.user_id,
                    "connection_id": connection_id,
                    "agent_instance_id": inst.agent_instance_id,
                },
            )

            if session.hosted_route_selected and session.hosted_connection_role == "observer":
                await self._registry.bind_observer(inst, auth.user_id)
                await self._registry.transition(inst, InstanceState.STARTING)
                new_resume = await self._registry.issue_resume_token(
                    inst, ttl_s=self._config.resume_token_ttl_s
                )
                await session.send({
                    "type": "hello_ok",
                    "agent_instance_id": inst.agent_instance_id,
                    "protocol_version": PROTOCOL_VERSION,
                    "heartbeat_interval_s": self._config.heartbeat_interval_s,
                    "resume_token": new_resume,
                    "resume_token_ttl_s": self._config.resume_token_ttl_s,
                    "resumed": False,
                    "route_contract_version": "starcoplay.hosted-route.v1",
                    "connection_role": "observer",
                    "accepted_agent_control_revision": session.accepted_agent_control_revision or 0,
                })
                if controller_recovery_offered:
                    await session.send({
                        "type": "hosted_status",
                        "code": "controller_recovery_required",
                        "retryable": True,
                        "accepted_agent_control_revision": (
                            session.accepted_agent_control_revision or 0
                        ),
                    })
                await self._registry.transition(inst, InstanceState.RUNNING)
                self._sessions_by_instance[inst.agent_instance_id] = session
                await self._run_session(session)
                return

            resumed = False
            resume_tok = hello.get("resume_token")
            if isinstance(resume_tok, str) and resume_tok.strip():
                prev = await self._registry.find_by_resume_token(resume_tok.strip())
                if prev is not None and prev.user_id == auth.user_id:
                    log.info(
                        "resume: user=%s prev=%s discarding fresh=%s",
                        auth.user_id,
                        prev.agent_instance_id,
                        inst.agent_instance_id,
                        extra={
                            "event": "gateway.session.resumed",
                            "user_id": auth.user_id,
                            "agent_instance_id": prev.agent_instance_id,
                            "connection_id": connection_id,
                        },
                    )
                    pending = prev.meta.pop("_pending_destroy_task", None)
                    if pending is not None:
                        try: pending.cancel()
                        except Exception: pass
                    await self._registry.transition(
                        inst, InstanceState.FAILED, ended_reason=EndedReason.USER_CLOSE
                    )
                    await self._registry.destroy(inst)
                    inst = prev
                    inst.platform_token = platform_token
                    if inst.state == InstanceState.DRAINING:
                        await self._registry.transition(inst, InstanceState.RUNNING)
                    session.instance = inst
                    resumed = True
                elif prev is not None:
                    # A process-local resume token is owner-bound.  An owner
                    # mismatch is not an ordinary post-restart cache miss.
                    raise RuntimeError("hosted_resume_owner_mismatch")
                elif session.hosted_route_selected:
                    # Resume state is process-local.  A Gateway restart makes
                    # an otherwise legitimate client token unknown; after the
                    # platform token has authenticated, fail fresh instead of
                    # trapping the client in a permanent reconnect loop.  No
                    # prior GameSession identity or authority is inherited.
                    log.warning(
                        "hosted resume token unavailable; starting fresh session",
                        extra={
                            "event": "gateway.session.resume_missed",
                            "user_id": auth.user_id,
                            "connection_id": connection_id,
                            "agent_instance_id": inst.agent_instance_id,
                            "error_code": "hosted_resume_token_unavailable",
                            "outcome": "fresh_session",
                        },
                    )

            if not resumed:
                prior = await self._registry.get_by_user(auth.user_id)
                if (
                    session.hosted_route_selected
                    and prior is not None
                    and prior.agent_instance_id != inst.agent_instance_id
                ):
                    displaced_session = self._sessions_by_instance.get(prior.agent_instance_id)
                    authorized_replacement = (
                        displaced_session is not None
                        and (
                            displaced_session.hosted_controller_closed
                            or (
                                displaced_session.hosted_transport_detached
                                and displaced_session.hosted_player_id
                                    == session.hosted_player_id
                            )
                        )
                        and (session.accepted_agent_control_revision or -1)
                            > (displaced_session.accepted_agent_control_revision or -1)
                    )
                    if not authorized_replacement:
                        raise RuntimeError("hosted_resume_token_required")

                displaced = await self._registry.bind_user(inst, auth.user_id)
                if displaced is not None and displaced.agent_instance_id != inst.agent_instance_id:
                    if session.hosted_route_selected and (
                        prior is None or displaced.agent_instance_id != prior.agent_instance_id
                    ):
                        raise RuntimeError("hosted_displaced_session_race")
                    log.info(
                        "new-kicks-old: user=%s old=%s new=%s",
                        auth.user_id,
                        displaced.agent_instance_id,
                        inst.agent_instance_id,
                        extra={
                            "event": "gateway.session.displaced",
                            "user_id": auth.user_id,
                            "agent_instance_id": inst.agent_instance_id,
                            "connection_id": connection_id,
                            "old_agent_instance_id": displaced.agent_instance_id,
                        },
                    )
                    await self._registry.transition(displaced, InstanceState.DRAINING, ended_reason=EndedReason.ADMIN_KICK)
                    if session.hosted_route_selected:
                        assert displaced_session is not None
                        same_instance = (
                            displaced_session.hosted_player_id == session.hosted_player_id
                            and displaced_session.hosted_facts.game_instance_nonce
                                == session.hosted_facts.game_instance_nonce
                        )
                        displaced_session.hosted_connection_role = "observer"
                        displaced_session.instance.meta.pop("_hosted_active_connection_id", None)
                        if same_instance:
                            await self._game_session_event_hook.on_suspended(displaced_session)
                            controlled_takeover = True
                        else:
                            await self._close_hosted_controller(displaced_session)

                await self._registry.transition(inst, InstanceState.STARTING)
                if isinstance(hello.get("target_player_id"), int):
                    inst.target_player_id = hello["target_player_id"]

            if session.hosted_route_selected:
                facts = session.hosted_facts
                if resumed:
                    game_session_id = inst.meta.get("hosted_game_session_id")
                    resume_identity = inst.meta.get("hosted_resume_identity")
                    if (not isinstance(game_session_id, str) or not isinstance(resume_identity, str)
                            or inst.meta.get("hosted_game_instance_nonce") != facts.game_instance_nonce
                    ):
                        raise RuntimeError("hosted_resume_facts_mismatch")
                elif controlled_takeover and displaced_session is not None:
                    game_session_id = displaced_session.hosted_game_session_id
                    resume_identity = displaced_session.hosted_resume_identity
                    inst.meta["hosted_game_session_id"] = game_session_id
                    inst.meta["hosted_resume_identity"] = resume_identity
                    inst.meta["hosted_game_instance_nonce"] = facts.game_instance_nonce
                    inst.meta["hosted_capability_digest"] = facts.capability_digest
                else:
                    game_session_id = f"gs_{uuid.uuid4().hex}"
                    resume_identity = f"gri_{uuid.uuid4().hex}"
                    inst.meta["hosted_game_session_id"] = game_session_id
                    inst.meta["hosted_resume_identity"] = resume_identity
                    inst.meta["hosted_game_instance_nonce"] = facts.game_instance_nonce
                    inst.meta["hosted_capability_digest"] = facts.capability_digest
                session.hosted_game_session_id = game_session_id
                session.hosted_resume_identity = resume_identity
                inst.meta["_hosted_active_connection_id"] = session.connection_id

                if self._hosted_runtime_activator is None:
                    # ``None`` allowlist is retained only for isolated legacy
                    # Hosted-route contract tests. Production always injects
                    # an explicit rollout set and must fail closed.
                    if self._hosted_user_ids is not None:
                        raise RuntimeError("hosted_runtime_activator_unavailable")

            lifecycle_hook_selected = (
                session.hosted_route_selected or not self._config.hosted_route_enabled
            )
            if lifecycle_hook_selected and self._game_session_event_hook is not None:
                # The injected hook must obtain formal GameSession facts from a
                # trusted source. It may not reinterpret legacy agent_instance_id.
                await self._game_session_event_hook.on_authenticated(
                    session, resumed=resumed or controlled_takeover
                )
                runtime_hook_attached = True

            if session.hosted_route_selected and self._hosted_runtime_activator is not None:
                await self._prepare_hosted_controller(session)

            new_resume = await self._registry.issue_resume_token(
                inst, ttl_s=self._config.resume_token_ttl_s
            )

            await session.send({
                "type": "hello_ok",
                "agent_instance_id": inst.agent_instance_id,
                "protocol_version": PROTOCOL_VERSION,
                "heartbeat_interval_s": self._config.heartbeat_interval_s,
                "resume_token": new_resume,
                "resume_token_ttl_s": self._config.resume_token_ttl_s,
                "resumed": resumed or controlled_takeover,
                "connection_role": "controller" if session.hosted_route_selected else "controller",
                "accepted_agent_control_revision": session.accepted_agent_control_revision or 0,
                **({
                    "route_contract_version": "starcoplay.hosted-route.v1",
                    "game_session_id": session.hosted_game_session_id,
                    "resume_identity": session.hosted_resume_identity,
                    "game_instance_nonce": session.hosted_facts.game_instance_nonce,
                    "capability_digest": session.hosted_facts.capability_digest,
                } if session.hosted_route_selected else {}),
            })
            if session.hosted_route_selected and detached_same_target_replacement:
                self._record_hosted_takeover(
                    "completed", session, session.accepted_agent_control_revision or 0,
                )

            if not resumed:
                await self._registry.transition(inst, InstanceState.RUNNING)
            self._sessions_by_instance[inst.agent_instance_id] = session
            if session.hosted_route_selected and self._hosted_runtime_activator is not None:
                await self._run_session_with_activation(session)
            else:
                await self._run_session(session)
        except ConnectionClosed as e:
            code = _ws_close_code(e)
            if code in (1000, 1001):
                # Explicit close (save-quit, /agent_control close, normal
                # game exit): do NOT offer a resume window.  The finally
                # block will drain_and_destroy immediately.
                _explicit_close = True
                log.info(
                    "conn %s closed normally code=%s: %s",
                    connection_id,
                    code,
                    e,
                    extra={
                        "event": "gateway.connection.closed",
                        "connection_id": connection_id,
                        "agent_instance_id": inst.agent_instance_id,
                    },
                )
            else:
                log.exception(
                    "session panic: %s",
                    e,
                    extra={
                        "event": "gateway.session.panic",
                        "connection_id": connection_id,
                        "agent_instance_id": inst.agent_instance_id,
                        "error_code": f"ws_close_{code}" if code is not None else "ws_closed",
                    },
                )
                if self._owns_current_connection(session):
                    # Hosted controller transport errors (1006, no close frame,
                    # heartbeat silence) are recoverable.  The finally block
                    # transitions to DRAINING + on_suspended + schedule_destroy
                    # with resume_token_ttl_s, preserving the GameSession
                    # identity for TTL-window resume.
                    if not (session.hosted_route_selected
                            and session.hosted_connection_role == "controller"):
                        try:
                            await self._registry.transition(inst, InstanceState.FAILED, ended_reason=EndedReason.PANIC)
                        except Exception:
                            pass
        except Exception as e:
            log.exception(
                "session panic: %s",
                e,
                extra={
                    "event": "gateway.session.panic",
                    "connection_id": connection_id,
                    "agent_instance_id": inst.agent_instance_id,
                    "error_code": "unhandled_exception",
                },
            )
            if self._owns_current_connection(session):
                try:
                    await self._registry.transition(inst, InstanceState.FAILED, ended_reason=EndedReason.PANIC)
                except Exception:
                    pass
        finally:
            owns_connection = self._owns_current_connection(session)
            explicit_game_close = _explicit_close or session.explicit_end_requested
            if owns_connection and session.hosted_route_selected:
                session.hosted_transport_detached = True
            if owns_connection and inst.state not in (InstanceState.FAILED, InstanceState.DESTROYED):
                await self._registry.transition(inst, InstanceState.DRAINING, ended_reason=EndedReason.USER_CLOSE)
            if (owns_connection and explicit_game_close
                    and session.hosted_route_selected
                    and session.hosted_connection_role == "controller"
                    and session.hosted_game_session_id is not None):
                try:
                    await self._close_hosted_controller(
                        session,
                        stop_runtime=True,
                        revision=session.accepted_agent_control_revision,
                    )
                except Exception:
                    log.exception("explicit GameSession close hook failed")
            # Publish SUSPENDED whenever authenticated Hosted GameSession facts
            # were established, even if activation prepare failed before the
            # lifecycle hook was formally attached.  The hosted identity
            # (game_session_id) was assigned before prepare() and independently
            # proves this connection owned a Binding that must converge.
            has_hosted_identity = session.hosted_game_session_id is not None
            if (owns_connection and not explicit_game_close
                    and (runtime_hook_attached or has_hosted_identity)
                    and self._game_session_event_hook is not None):
                try:
                    await self._game_session_event_hook.on_suspended(session)
                except Exception:
                    # The socket is already unavailable. Preserve fail-closed
                    # visibility; the Manager projection/reconcile worker must retry.
                    log.exception("authenticated GameSession suspend hook failed")
            # Explicit close (1000/1001) and connections without a resume
            # token are drained immediately.  Abnormal disconnects
            # (1006 / heartbeat silence) with a hosted controller get a
            # resume_token_ttl_s grace window for transport recovery.
            if (owns_connection and inst.state == InstanceState.DRAINING
                    and inst.resume_token
                    and not explicit_game_close):
                task = asyncio.create_task(self._schedule_destroy(session, self._config.resume_token_ttl_s))
                inst.meta["_pending_destroy_task"] = task
            else:
                await self._drain_and_destroy(session)
            owner = session.instance.user_id
            if owner:
                owner_sessions = self._hosted_sessions_by_owner.get(owner)
                if owner_sessions is not None:
                    owner_sessions.pop(session.connection_id, None)
                    if not owner_sessions:
                        self._hosted_sessions_by_owner.pop(owner, None)
            reset_log_context(context_token)

    async def _run_session_with_activation(self, session: Session) -> None:
        """Start pumps before Claude startup so MCP calls cannot deadlock on output."""
        activation = asyncio.create_task(self._hosted_runtime_activator.activate(session))
        runtime = asyncio.create_task(self._run_session(session))
        done, _ = await asyncio.wait({activation, runtime}, return_when=asyncio.FIRST_COMPLETED)
        if activation in done:
            try:
                await activation
            except BaseException:
                runtime.cancel()
                try:
                    await session.ws.close(code=1011, reason="hosted_runtime_activation_failed")
                finally:
                    try:
                        await runtime
                    except (asyncio.CancelledError, Exception):
                        pass
                raise
            await runtime
            return
        activation.cancel()
        try:
            await activation
        except asyncio.CancelledError:
            pass
        await runtime

    async def _await_hello(self, ws: Any) -> Optional[dict[str, Any]]:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self._config.handshake_timeout_s)
        except asyncio.TimeoutError:
            log.warning("handshake timeout", extra={"event": "gateway.handshake.timeout", "error_code": "handshake_timeout"})
            return None
        except Exception as e:
            log.warning(
                "handshake recv failed: %s",
                e,
                extra={"event": "gateway.handshake.recv_failed", "error_code": "handshake_recv_failed"},
            )
            return None
        try:
            msg = json.loads(raw)
        except Exception:
            return None
        if not isinstance(msg, dict) or msg.get("type") != "hello":
            return None
        return msg

    async def _send_safe(self, ws: Any, msg: dict[str, Any]) -> None:
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception:
            pass

    async def _run_session(self, session: Session) -> None:
        send_task = asyncio.create_task(self._outbound_pump(session))
        watchdog_task = asyncio.create_task(self._heartbeat_watchdog(session))
        try:
            async for raw in session.ws:
                now = time.time()
                session.last_pong_at = now
                if now - session.last_liveness_mirror_at >= 15.0:
                    session.last_liveness_mirror_at = now
                    try:
                        await self._registry.update_activity(session.instance)
                    except Exception:
                        log.debug("liveness mirror failed", exc_info=True)
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                t = msg.get("type")
                if t == "ping":
                    await session.send({"type": "pong", "ts": msg.get("ts")})
                    continue
                if t == "pong":
                    continue
                if t == "session.end":
                    # A domain-level GameSession end (for example, returning
                    # to title) must not enter transport resume grace.
                    session.explicit_end_requested = True
                    break
                if (session.hosted_route_selected
                        and session.hosted_connection_role == "observer"
                        and t not in {"game_event", "state_snapshot", "telemetry", "appearance_sync"}):
                    log.debug(
                        "observer frame ignored",
                        extra={"event": "gateway.observer.frame_ignored", "frame_type": t,
                               "user_id": session.instance.user_id,
                               "connection_id": session.connection_id},
                    )
                    continue
                if t == "capabilities_changed" and session.hosted_route_selected:
                    handler = getattr(self._game_session_event_hook, "on_capabilities_changed", None)
                    if handler is None:
                        raise RuntimeError("hosted capability change dependency unavailable")
                    await handler(session, msg)
                    continue
                if t == "game_event":
                    await self._registry.update_activity(session.instance, game_event=True)
                    if (session.hosted_route_selected
                            and self._hosted_runtime_input_dispatcher is not None):
                        try:
                            await self._hosted_runtime_input_dispatcher.dispatch_game_event(session, msg)
                        except Exception:
                            # A rejected/busy Runtime input must not tear down
                            # the authenticated game transport.
                            log.exception(
                                "hosted runtime input dispatch failed",
                                extra={"event": "gateway.hosted_runtime_input.failed",
                                       "user_id": session.instance.user_id,
                                       "connection_id": session.connection_id},
                            )
                if t in ("game_event", "state_snapshot", "command_result", "adapter_info", "protocol_envelope"):
                    session.fanout(msg)
                if t == "protocol_envelope":
                    await self._registry.update_activity(session.instance, inflight_delta=-1)
                if t == "telemetry":
                    uid = session.instance.user_id
                    log.debug(
                        "runtime telemetry frame skipped",
                        extra={
                            "event": "gateway.runtime_telemetry.skipped",
                            "user_id": uid,
                            "connection_id": session.connection_id,
                            "agent_instance_id": session.instance.agent_instance_id,
                        },
                    )
                    continue
                if t == "appearance_sync":
                    uid = session.instance.user_id
                    if uid:
                        await self._handle_appearance_sync(uid, msg.get("appearance"))
                    continue
                if not session.hosted_route_selected:
                    try:
                        await self._worker_handle(session, msg)
                    except Exception:
                        log.exception(
                            "worker_handle failed",
                            extra={
                                "event": "gateway.worker.failed",
                                "user_id": session.instance.user_id,
                                "connection_id": session.connection_id,
                                "agent_instance_id": session.instance.agent_instance_id,
                                "error_code": "worker_handle_failed",
                            },
                        )
        finally:
            send_task.cancel()
            watchdog_task.cancel()
            for t in (send_task, watchdog_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

    async def _handle_appearance_sync(self, user_id: str, appearance: Any) -> None:
        """Upsert Mod-reported appearance into profiles.config.appearance."""
        normalized_appearance = _normalize_appearance_sync_payload(appearance)
        if normalized_appearance is None:
            log.warning(
                "appearance_sync rejected invalid payload for user %s payload=%s",
                user_id[:8],
                _summarize_appearance_payload(appearance),
            )
            return
        dsn = self._config.dsn
        if not dsn:
            log.warning("appearance_sync skipped for user %s: DATABASE_URL missing", user_id[:8])
            return
        try:
            import asyncpg

            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
            try:
                existing = await pool.fetchrow(
                    "SELECT config FROM public.profiles WHERE user_id = $1",
                    user_id,
                )
                config = _coerce_profile_config(existing["config"] if existing else None)
                config["appearance"] = normalized_appearance
                await pool.execute(
                    """INSERT INTO public.profiles (user_id, config, updated_at)
                       VALUES ($1, $2::jsonb, NOW())
                       ON CONFLICT (user_id) DO UPDATE SET config = $2::jsonb, updated_at = NOW()""",
                    user_id,
                    json.dumps(config),
                )
                log.info("appearance_sync saved for user %s", user_id[:8])
            finally:
                await pool.close()
        except Exception:
            log.exception("appearance_sync failed for user %s", user_id[:8])

    async def _heartbeat_watchdog(self, session: Session) -> None:
        try:
            while True:
                await asyncio.sleep(5.0)
                if time.time() - session.last_pong_at > self._config.heartbeat_timeout_s:
                    log.warning("heartbeat timeout for %s, closing", session.connection_id)
                    try:
                        await session.ws.close()
                    except Exception:
                        pass
                    return
        except asyncio.CancelledError:
            return

    async def _schedule_destroy(
        self, session: Session, delay: float, *, close_retry_attempt: int = 0,
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.info("resume grace cancelled for %s", session.instance.agent_instance_id)
            return
        inst = session.instance
        if not self._owns_current_connection(session):
            return
        inst.meta.pop("_pending_destroy_task", None)
        if inst.state != InstanceState.DRAINING:
            return
        log.info("resume grace expired for %s, destroying", inst.agent_instance_id)
        if session.hosted_route_selected:
            try:
                await self._close_hosted_controller(session)
            except ClosePublishDeferred:
                # on_closed fences the route and retains the exact pending
                # event before publish. A transient publisher failure must not
                # strand the dead transport instance or escape this background
                # task; the next authenticated hello retries that event first.
                log.exception(
                    "hosted GameSession close deferred at resume grace expiry",
                    extra={"event": "gateway.session.close_deferred",
                           "user_id": inst.user_id,
                           "connection_id": session.connection_id,
                           "game_session_id": session.hosted_game_session_id,
                           "error_code": "gateway_closed_publish_failed"},
                )
            except Exception:
                log.exception(
                    "hosted GameSession close failed before route fence",
                    extra={"event": "gateway.session.close_prefence_failed",
                           "user_id": inst.user_id,
                           "connection_id": session.connection_id,
                           "game_session_id": session.hosted_game_session_id,
                           "attempt": close_retry_attempt + 1,
                           "error_code": "gateway_close_prefence_failed"},
                )
                if close_retry_attempt < CLOSE_PREFENCE_MAX_RETRIES:
                    task = asyncio.create_task(self._schedule_destroy(
                        session, CLOSE_PREFENCE_RETRY_S,
                        close_retry_attempt=close_retry_attempt + 1,
                    ))
                    inst.meta["_pending_destroy_task"] = task
                else:
                    log.error(
                        "hosted GameSession close pre-fence retries exhausted",
                        extra={"event": "gateway.session.close_prefence_exhausted",
                               "user_id": inst.user_id,
                               "connection_id": session.connection_id,
                               "game_session_id": session.hosted_game_session_id,
                               "attempt": close_retry_attempt + 1,
                               "error_code": "gateway_close_prefence_exhausted"},
                    )
                return
        await self._drain_and_destroy(session)

    async def _outbound_pump(self, session: Session) -> None:
        try:
            while True:
                msg = await session.outbound.get()
                try:
                    await session.send(msg)
                except Exception as e:
                    log.warning(
                        "outbound send failed (instance=%s type=%s): %s",
                        session.instance.agent_instance_id, msg.get("type"), e,
                    )
                    raise
                if msg.get("type") == "command":
                    log.info(
                        "ws → mod: command seq=%s cap=%s action_id=%s instance=%s",
                        msg.get("seq"), msg.get("capability"),
                        msg.get("action_id"), session.instance.agent_instance_id,
                        extra={
                            "event": "gateway.command.sent",
                            "user_id": session.instance.user_id,
                            "connection_id": session.connection_id,
                            "agent_instance_id": session.instance.agent_instance_id,
                            "action_id": msg.get("action_id"),
                            "capability": msg.get("capability"),
                            "target_player_id": session.instance.target_player_id,
                        },
                    )
                    try:
                        await asyncio.wait_for(
                            self._text_channel_fanout.publish_agent_visible_output(session, msg),
                            timeout=2.0,
                        )
                    except asyncio.TimeoutError:
                        log.warning(
                            "text channel fanout timed out instance=%s action_id=%s",
                            session.instance.agent_instance_id,
                            msg.get("action_id"),
                            extra={
                                "event": "gateway.text_channel.timeout",
                                "user_id": session.instance.user_id,
                                "connection_id": session.connection_id,
                                "agent_instance_id": session.instance.agent_instance_id,
                                "action_id": msg.get("action_id"),
                                "capability": msg.get("capability"),
                                "error_code": "text_channel_timeout",
                            },
                        )
                    except Exception:
                        log.debug("text channel fanout failed", exc_info=True)
                elif msg.get("type") == "protocol_envelope":
                    log.info(
                        "ws → mod: protocol_envelope seq=%s cap=%s action_id=%s instance=%s",
                        msg.get("seq"), msg.get("capability"),
                        msg.get("action_id"), session.instance.agent_instance_id,
                        extra={
                            "event": "gateway.protocol_envelope.sent",
                            "user_id": session.instance.user_id,
                            "connection_id": session.connection_id,
                            "agent_instance_id": session.instance.agent_instance_id,
                            "action_id": msg.get("action_id"),
                            "capability": msg.get("capability"),
                            "target_player_id": session.instance.target_player_id,
                        },
                    )
                    try:
                        await asyncio.wait_for(
                            self._text_channel_fanout.publish_agent_visible_output(session, msg),
                            timeout=2.0,
                        )
                    except asyncio.TimeoutError:
                        log.warning(
                            "text channel fanout timed out instance=%s action_id=%s",
                            session.instance.agent_instance_id,
                            msg.get("action_id"),
                            extra={
                                "event": "gateway.text_channel.timeout",
                                "user_id": session.instance.user_id,
                                "connection_id": session.connection_id,
                                "agent_instance_id": session.instance.agent_instance_id,
                                "action_id": msg.get("action_id"),
                                "capability": msg.get("capability"),
                                "error_code": "text_channel_timeout",
                            },
                        )
                    except Exception:
                        log.debug("text channel fanout failed", exc_info=True)
                session.fanout(msg)
                if msg.get("type") == "command":
                    await self._registry.update_activity(session.instance, inflight_delta=1)
                elif msg.get("type") == "protocol_envelope":
                    await self._registry.update_activity(session.instance, inflight_delta=1)
                elif msg.get("type") == "command_result":
                    await self._registry.update_activity(session.instance, inflight_delta=-1)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("outbound pump ended: %s", e)

    async def _drain_and_destroy(self, session: Session) -> None:
        inst = session.instance
        if not self._owns_current_connection(session):
            try:
                await session.ws.close()
            except Exception:
                pass
            return
        deadline = time.monotonic() + self._config.drain_max_s
        while inst.command_inflight_count > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        if inst.command_inflight_count > 0:
            await self._registry.transition(inst, InstanceState.DESTROYED, ended_reason=EndedReason.DRAIN_TIMEOUT)
        else:
            await self._registry.transition(inst, InstanceState.DESTROYED, ended_reason=EndedReason.DRAIN_COMPLETE)
        if self._on_session_end is not None:
            try:
                await self._on_session_end(session)
            except Exception:
                log.exception("on_session_end hook failed")
        try:
            await session.ws.close()
        except Exception:
            pass
        await self._registry.destroy(inst)
        if self._sessions_by_instance.get(inst.agent_instance_id) is session:
            self._sessions_by_instance.pop(inst.agent_instance_id, None)
        for q in list(session.subscribers):
            try:
                q.put_nowait({"type": "__closed__"})
            except asyncio.QueueFull:
                pass
        log.info("conn %s destroyed (instance %s)", session.connection_id, inst.agent_instance_id)
