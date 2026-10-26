from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("platform_ws.auth")

DEFAULT_KEY_ACCESS_USER_TYPES = frozenset(("beta_user", "user", "member"))


@dataclass
class AuthResult:
    ok: bool
    user_id: str | None = None
    email: str | None = None
    code: str | None = None
    message: str | None = None


class TokenAuthenticator:
    """Validate Bearer platform_token against public.users.

    Supports two backends:
      - asyncpg (Postgres direct; preferred when DATABASE_URL is set)
      - supabase-py service-role client (fallback, HTTP to PostgREST)
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        supabase_url: str | None = None,
        supabase_service_key: str | None = None,
    ) -> None:
        self._dsn = dsn
        self._supabase_url = supabase_url
        self._supabase_service_key = supabase_service_key
        self._pool: Any | None = None
        self._supabase: Any | None = None

    def _config_environment(self) -> str:
        value = os.environ.get("STARCOPLAY_CONFIG_ENV", "").strip() or "prod"
        return value if value in ("dev", "test", "prod") else "prod"

    def _parse_allowed_user_types(self, raw: str | None) -> set[str]:
        if not raw:
            return set(DEFAULT_KEY_ACCESS_USER_TYPES)
        try:
            parsed = json.loads(raw)
            value = parsed.get("key_access_user_types") if isinstance(parsed, dict) else None
            if not isinstance(value, list):
                return set(DEFAULT_KEY_ACCESS_USER_TYPES)
            result = {item.strip() for item in value if isinstance(item, str) and item.strip()}
            return result or set(DEFAULT_KEY_ACCESS_USER_TYPES)
        except Exception:
            return set(DEFAULT_KEY_ACCESS_USER_TYPES)

    async def _allowed_user_types_pg(self, conn: Any) -> set[str]:
        try:
            row = await conn.fetchrow(
                """
                select config_value
                from public.system_configs
                where domain = 'platform_access'
                  and config_key = 'onboarding_policy'
                  and environment = $1
                  and status = 1
                limit 1
                """,
                self._config_environment(),
            )
            return self._parse_allowed_user_types(row["config_value"] if row else None)
        except Exception as exc:
            log.warning("platform access policy query failed; using fail-closed default: %s", exc)
            return set(DEFAULT_KEY_ACCESS_USER_TYPES)

    def _allowed_user_types_supabase(self, sb: Any) -> set[str]:
        try:
            resp = (
                sb.table("system_configs")
                .select("config_value")
                .eq("domain", "platform_access")
                .eq("config_key", "onboarding_policy")
                .eq("environment", self._config_environment())
                .eq("status", 1)
                .limit(1)
                .execute()
            )
            data = getattr(resp, "data", None)
            if isinstance(data, list) and data:
                return self._parse_allowed_user_types(data[0].get("config_value"))
            if isinstance(data, dict):
                return self._parse_allowed_user_types(data.get("config_value"))
            return set(DEFAULT_KEY_ACCESS_USER_TYPES)
        except Exception as exc:
            log.warning("platform access policy query failed; using fail-closed default: %s", exc)
            return set(DEFAULT_KEY_ACCESS_USER_TYPES)

    async def _get_pool(self):
        if self._pool is not None:
            return self._pool
        if not self._dsn:
            return None
        try:
            import asyncpg
        except ImportError:
            log.warning("asyncpg not installed; falling back to supabase client")
            return None
        self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=4)
        return self._pool

    def _get_supabase(self):
        if self._supabase is not None:
            return self._supabase
        if not self._supabase_url or not self._supabase_service_key:
            return None
        try:
            from supabase import create_client
        except ImportError:
            log.error("supabase-py not installed and no asyncpg DSN provided")
            return None
        self._supabase = create_client(self._supabase_url, self._supabase_service_key)
        return self._supabase

    async def authenticate(self, token: str) -> AuthResult:
        bypass = os.environ.get("STARCOPLAY_WS_AUTH_BYPASS", "").strip().lower()
        if bypass in ("1", "true", "yes"):
            uid = os.environ.get("STARCOPLAY_WS_AUTH_BYPASS_USER_ID", "").strip()
            if not uid:
                raise RuntimeError(
                    "STARCOPLAY_WS_AUTH_BYPASS_USER_ID must be set when STARCOPLAY_WS_AUTH_BYPASS is enabled"
                )
            log.warning("STARCOPLAY_WS_AUTH_BYPASS=1 — token 未校验，仅限本机开发；切勿用于公网")
            return AuthResult(ok=True, user_id=uid)

        token = (token or "").strip()
        if not token:
            return AuthResult(ok=False, code="missing_token", message="missing token")

        pool = await self._get_pool()
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        select pt.user_id, p.user_type, u.email
                        from public.platform_tokens pt
                        left join public.profiles p on p.user_id = pt.user_id
                        left join public.users u on u.user_id = pt.user_id
                        where pt.token = $1
                        limit 1
                        """,
                        token,
                    )
                    allowed_user_types = await self._allowed_user_types_pg(conn)
                if row is None:
                    return AuthResult(ok=False, code="invalid_token", message="invalid token")
                user_type = row["user_type"]
                if user_type not in allowed_user_types:
                    return AuthResult(
                        ok=False,
                        code="access_not_approved",
                        message="platform access not approved",
                    )
                return AuthResult(ok=True, user_id=row["user_id"], email=row["email"])
            except Exception as e:
                log.error("pg auth query failed: %s", e)
                return AuthResult(ok=False, code="auth_backend_error", message=str(e))

        sb = self._get_supabase()
        if sb is None:
            return AuthResult(ok=False, code="no_auth_backend", message="no auth backend configured")
        try:
            resp = (
                sb.table("platform_tokens")
                .select("user_id")
                .eq("token", token)
                .single()
                .execute()
            )
            data = getattr(resp, "data", None)
            if not data:
                return AuthResult(ok=False, code="invalid_token", message="invalid token")
            user_id = data.get("user_id")
            user_resp = (
                sb.table("users")
                .select("email")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            user_data = getattr(user_resp, "data", None)
            user_row = user_data[0] if isinstance(user_data, list) and user_data else user_data
            email = user_row.get("email") if isinstance(user_row, dict) else None
            profile_resp = (
                sb.table("profiles")
                .select("user_type")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            profile_data = getattr(profile_resp, "data", None)
            profile_row = profile_data[0] if isinstance(profile_data, list) and profile_data else profile_data
            user_type = profile_row.get("user_type") if isinstance(profile_row, dict) else None
            if user_type not in self._allowed_user_types_supabase(sb):
                return AuthResult(
                    ok=False,
                    code="access_not_approved",
                    message="platform access not approved",
                )
            return AuthResult(ok=True, user_id=user_id, email=email)
        except Exception as e:
            log.error("supabase auth query failed: %s", e)
            return AuthResult(ok=False, code="auth_backend_error", message=str(e))

    async def close(self) -> None:
        if self._pool is not None:
            try:
                await self._pool.close()
            except Exception:
                pass
            self._pool = None
