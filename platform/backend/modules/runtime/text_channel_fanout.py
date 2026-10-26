from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from modules.runtime.telemetry_writer import TelemetryWriter

if TYPE_CHECKING:
    from modules.gateway.server import Session

log = logging.getLogger("platform_ws.text_channel_output")


@dataclass(frozen=True)
class TextChannelBinding:
    source: str
    channel_key: str
    user_id: str
    agent_instance_id: str
    enabled: bool = True
    metadata: dict[str, Any] | None = None


class TextChannelBindingResolver:
    def __init__(self, writer: TelemetryWriter, *, ttl_s: float = 5.0) -> None:
        self._writer = writer
        self._ttl_s = ttl_s
        self._cache: dict[str, tuple[float, list[TextChannelBinding]]] = {}

    async def resolve(self, agent_instance_id: str) -> list[TextChannelBinding]:
        now = time.monotonic()
        cached = self._cache.get(agent_instance_id)
        if cached is not None and now - cached[0] < self._ttl_s:
            return cached[1]

        bindings = await self._load(agent_instance_id)
        self._cache[agent_instance_id] = (now, bindings)
        return bindings

    async def _load(self, agent_instance_id: str) -> list[TextChannelBinding]:
        pool = await self._writer.get_pool()
        if pool is None:
            return []
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    select route_key, target_user_id, target_agent_instance_id, enabled, title
                    from public.roadshow_routes
                    where enabled = true
                      and target_agent_instance_id = $1
                      and target_user_id is not null
                    """,
                    agent_instance_id,
                )
        except Exception as e:
            log.warning("text channel binding resolve failed: %s", e)
            return []

        bindings: list[TextChannelBinding] = []
        for row in rows:
            source = "roadshow"
            channel_key = str(row["route_key"] or "").strip()
            user_id = str(row["target_user_id"] or "").strip()
            if not channel_key or not user_id:
                continue
            bindings.append(
                TextChannelBinding(
                    source=source,
                    channel_key=channel_key,
                    user_id=user_id,
                    agent_instance_id=str(row["target_agent_instance_id"] or agent_instance_id),
                    enabled=bool(row["enabled"]),
                    metadata={"title": row["title"]},
                )
            )
        return bindings


class TextChannelOutputFanout:
    def __init__(
        self,
        *,
        writer: TelemetryWriter,
        resolver: TextChannelBindingResolver | None = None,
    ) -> None:
        self._writer = writer
        self._resolver = resolver or TextChannelBindingResolver(writer)

    async def publish_agent_visible_output(self, session: "Session", msg: dict[str, Any]) -> None:
        text = _visible_say_text(msg)
        if text is None:
            return

        agent_instance_id = session.instance.agent_instance_id
        bindings = await self._resolver.resolve(agent_instance_id)
        if not bindings:
            return

        action_id = str(msg.get("action_id") or "").strip()
        seq = msg.get("seq")
        session_id = str(getattr(session.instance, "farm_session_id", None) or agent_instance_id)
        for binding in bindings:
            if not binding.enabled:
                continue
            event_id_part = action_id or str(seq or "")
            if not event_id_part:
                event_id_part = str(abs(hash((binding.source, binding.channel_key, text))))
            client_event_id = (
                "external_text:visible_output:"
                f"{binding.source}:{binding.channel_key}:{agent_instance_id}:{event_id_part}"
            )
            data = {
                "source": binding.source,
                "channel_key": binding.channel_key,
                "route_key": binding.channel_key,
                "text": text[:1000],
                "length": len(text),
                "agent_instance_id": agent_instance_id,
                "action_id": action_id or None,
                "seq": seq,
            }
            try:
                await self._writer.write_event(
                    user_id=binding.user_id,
                    event_type="external_text.agent_visible_output",
                    data=data,
                    session_id=session_id,
                    client_event_id=client_event_id,
                )
            except Exception:
                log.debug("text channel visible output write failed", exc_info=True)


def _visible_say_text(msg: dict[str, Any]) -> str | None:
    if msg.get("capability") != "say":
        return None
    msg_type = msg.get("type")
    if msg_type == "command":
        params = msg.get("params")
        if not isinstance(params, dict):
            return None
        text = str(params.get("message") or "").strip()
        return text or None
    if msg_type == "protocol_envelope":
        text = str(msg.get("text_preview") or "").strip()
        return text or None
    return None
