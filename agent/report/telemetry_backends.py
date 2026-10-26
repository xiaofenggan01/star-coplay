from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from text_sanitize import sanitize_for_utf8_json

log = logging.getLogger(__name__)


class TelemetryAuthError(RuntimeError):
    pass


class TelemetryBackend(ABC):
    @abstractmethod
    def write(self, events: list[dict[str, Any]], *, token: str) -> None:
        """Write a batch of events; raise on failure."""

    def close(self) -> None:
        return None


class HttpBackend(TelemetryBackend):
    def __init__(self, url: str, api_key: str | None = None, timeout_s: float = 15.0) -> None:
        self._url = url.rstrip("/")
        self._api_key = (api_key or "").strip() or None
        self._timeout_s = timeout_s

    def write(self, events: list[dict[str, Any]], *, token: str) -> None:
        body = json.dumps({"events": events}).encode("utf-8")
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["apikey"] = self._api_key
        req = Request(self._url, data=body, method="POST", headers=headers)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(req, timeout=self._timeout_s) as resp:
                    if resp.status not in (200, 201):
                        raise URLError(f"HTTP {resp.status}")
                return
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"http backend failed: {type(last_exc).__name__}: {last_exc}")


class InternalDbWriter(TelemetryBackend):
    """Service-role direct insert into public.events.

    Resolves user_id from ``platform_token`` on first flush and caches it.
    Prefers psycopg (v3) sync via ``DATABASE_URL``; falls back to supabase-py.
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        supabase_url: str | None = None,
        supabase_service_key: str | None = None,
    ) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL") or None
        self._supabase_url = supabase_url or os.environ.get("SUPABASE_URL") or None
        self._supabase_service_key = (
            supabase_service_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or None
        )
        self._user_id_cache: dict[str, str] = {}
        self._conn = None
        self._supabase = None

    def _ensure_pg(self):
        if self._conn is not None:
            return self._conn
        if not self._dsn:
            return None
        try:
            import psycopg
        except ImportError:
            log.info("psycopg not installed; will try supabase fallback")
            return None
        self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def _ensure_supabase(self):
        if self._supabase is not None:
            return self._supabase
        if not self._supabase_url or not self._supabase_service_key:
            return None
        try:
            from supabase import create_client
        except ImportError:
            log.error("supabase-py not installed and no psycopg available")
            return None
        self._supabase = create_client(self._supabase_url, self._supabase_service_key)
        return self._supabase

    def _resolve_user_id(self, token: str) -> str | None:
        if token in self._user_id_cache:
            return self._user_id_cache[token]
        conn = self._ensure_pg()
        if conn is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "select user_id from public.users where platform_token = %s limit 1",
                        (token,),
                    )
                    row = cur.fetchone()
                if row:
                    self._user_id_cache[token] = row[0]
                    return row[0]
                return None
            except Exception as e:
                log.error("pg resolve user_id failed: %s", e)
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        sb = self._ensure_supabase()
        if sb is None:
            return None
        try:
            resp = (
                sb.table("users")
                .select("user_id")
                .eq("platform_token", token)
                .single()
                .execute()
            )
            data = getattr(resp, "data", None)
            if data:
                self._user_id_cache[token] = data["user_id"]
                return data["user_id"]
        except Exception as e:
            log.error("supabase resolve user_id failed: %s", e)
        return None

    def write(self, events: list[dict[str, Any]], *, token: str) -> None:
        user_id = self._resolve_user_id(token)
        if not user_id:
            raise TelemetryAuthError("invalid token / cannot resolve user_id")

        rows: list[dict[str, Any]] = []
        from datetime import datetime, timezone
        received_at = datetime.now(timezone.utc).isoformat()
        for e in events:
            ceid = e.get("client_event_id")
            rows.append({
                "user_id": user_id,
                "type": e.get("type", ""),
                "ts": e.get("ts", received_at),
                "session_id": e.get("session_id", ""),
                "data": sanitize_for_utf8_json(e.get("data") or {}),
                "received_at": received_at,
                "client_event_id": (ceid.strip() if isinstance(ceid, str) and ceid.strip() else None),
            })

        conn = self._ensure_pg()
        if conn is not None:
            try:
                with conn.cursor() as cur:
                    cur.executemany(
                        "insert into public.events "
                        "(user_id, type, ts, session_id, data, received_at, client_event_id) "
                        "values (%s, %s, %s, %s, %s::jsonb, %s, %s) "
                        "on conflict (user_id, client_event_id) do nothing",
                        [
                            (
                                r["user_id"], r["type"], r["ts"], r["session_id"],
                                json.dumps(r["data"], ensure_ascii=False),
                                r["received_at"],
                                r["client_event_id"],
                            )
                            for r in rows
                        ],
                    )
                return
            except Exception as e:
                log.error("pg insert events failed: %s", e)
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

        sb = self._ensure_supabase()
        if sb is None:
            raise RuntimeError("no db backend available for internal writer")
        resp = (
            sb.table("events")
            .upsert(rows, on_conflict="user_id,client_event_id", ignore_duplicates=True)
            .execute()
        )
        err = getattr(resp, "error", None)
        if err:
            raise RuntimeError(f"supabase insert error: {err}")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def build_backend_from_config(cfg: dict[str, Any]) -> TelemetryBackend:
    backend = (cfg.get("backend") or "http").strip().lower()
    if backend == "internal":
        return InternalDbWriter(
            dsn=cfg.get("dsn"),
            supabase_url=cfg.get("supabase_url"),
            supabase_service_key=cfg.get("supabase_service_key"),
        )
    url = cfg.get("events_url") or ""
    api_key = cfg.get("events_api_key") or None
    return HttpBackend(url=url, api_key=api_key)
