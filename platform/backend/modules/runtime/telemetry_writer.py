from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("platform_ws.telemetry_writer")

_DROPPED_EVENT_TYPES = {"ai.llm_call", "ai.error", "ai.action"}
_DROPPED_EVENT_PREFIXES = ("error.",)


def _coerce_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _sanitize_for_json(value: Any) -> Any:
    if isinstance(value, str):
        return "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in value)
    if isinstance(value, dict):
        return {_sanitize_for_json(k): _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_for_json(v) for v in value]
    return value


def should_persist_event_type(event_type: Any) -> bool:
    if not isinstance(event_type, str):
        return False
    return event_type not in _DROPPED_EVENT_TYPES and not event_type.startswith(_DROPPED_EVENT_PREFIXES)


class TelemetryWriter:
    """Write explicit business events into public.events.

    Runtime diagnostics must use common.observability file logs. Gateway does
    not persist runtime telemetry frames into public.events.
    """

    def __init__(self, *, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._pool: Any | None = None

    async def _get_pool(self):
        if self._pool is not None:
            return self._pool
        if not self._dsn:
            return None
        try:
            import asyncpg
        except ImportError:
            log.info("asyncpg not installed; telemetry writer disabled")
            return None
        try:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        except Exception as e:
            log.warning("telemetry writer asyncpg pool init failed: %s", e)
            self._pool = None
        return self._pool

    async def get_pool(self):
        """Return the shared asyncpg pool for small platform-side event helpers."""
        return await self._get_pool()

    async def write(self, *, user_id: str, msg: dict[str, Any]) -> None:
        ev_type = str(msg.get("type_name") or msg.get("event_type") or "telemetry")
        ts_val = msg.get("ts") or datetime.now(timezone.utc).isoformat()
        session_id = str(msg.get("session_id") or "")
        data = msg.get("data") or {}
        ceid = msg.get("client_event_id")
        await self.write_event(
            user_id=user_id,
            event_type=ev_type,
            data=data,
            session_id=session_id,
            ts=ts_val,
            client_event_id=ceid if isinstance(ceid, str) else None,
        )

    async def write_event(
        self,
        *,
        user_id: str,
        event_type: str,
        data: dict[str, Any] | Any,
        session_id: str = "",
        ts: str | None = None,
        client_event_id: str | None = None,
    ) -> None:
        ev_type = str(event_type or "telemetry")
        if not should_persist_event_type(ev_type):
            log.debug("telemetry event suppressed by persistence policy: %s", ev_type)
            return
        pool = await self._get_pool()
        if pool is None:
            return
        ts_val = _coerce_ts(ts)
        if not isinstance(data, dict):
            data = {"_raw": data}
        data = _sanitize_for_json(data)
        ceid = (client_event_id or "").strip() or None
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    insert into public.events
                        (user_id, type, ts, session_id, data, received_at, client_event_id)
                    values ($1, $2, $3, $4, $5::jsonb, now(), $6)
                    on conflict (user_id, client_event_id) do nothing
                    """,
                    user_id, ev_type, ts_val, session_id,
                    json.dumps(data, ensure_ascii=False), ceid,
                )
        except Exception as e:
            log.warning("telemetry insert failed: %s", e)

    async def close(self) -> None:
        if self._pool is not None:
            try:
                await self._pool.close()
            except Exception:
                pass
            self._pool = None
