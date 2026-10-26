"""Short-lived, replay-safe service claims for Runtime Control APIs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Mapping

ISSUER = "starcoplay-control-plane"
AUDIENCE = "starcoplay-runtime-manager"
MAX_TTL_SECONDS = 60
CLOCK_SKEW_SECONDS = 5


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def issue_runtime_control_claim(secret: str, service_id: str, *, now: int | None = None,
                                ttl_seconds: int = 30, jti: str | None = None) -> str:
    """Controlled issuer: authorization scope is intentionally absent from the token."""
    if not secret or not service_id or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError("invalid runtime control claim input")
    issued = int(time.time()) if now is None else int(now)
    payload = {"iss":ISSUER, "aud":AUDIENCE, "service_id":service_id,
               "iat":issued, "nbf":issued, "exp":issued+ttl_seconds,
               "jti":jti or secrets.token_urlsafe(18)}
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


class RuntimeControlClaimsVerifier:
    def __init__(self, secret: str, services: Mapping[str, Mapping[str, object]], pool,
                 *, now=lambda: int(time.time())) -> None:
        self.secret, self.services, self.pool, self.now = secret, dict(services), pool, now

    async def verify(self, token: str) -> Mapping[str, object]:
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).digest()
            actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not self.secret or not hmac.compare_digest(actual, expected): raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            required = {"iss","aud","service_id","iat","nbf","exp","jti"}
            if set(payload) != required or payload["iss"] != ISSUER or payload["aud"] != AUDIENCE:
                raise ValueError
            iat, nbf, exp, now = int(payload["iat"]), int(payload["nbf"]), int(payload["exp"]), self.now()
            if nbf < iat or exp <= nbf or exp - iat > MAX_TTL_SECONDS:
                raise ValueError
            if iat > now + CLOCK_SKEW_SECONDS or nbf > now + CLOCK_SKEW_SECONDS or exp <= now - CLOCK_SKEW_SECONDS:
                raise ValueError
            service_id, jti = str(payload["service_id"]), str(payload["jti"])
            scope = self.services.get(service_id)
            if not jti or scope is None: raise ValueError
            if scope.get("all_tenants") is not True and not (scope.get("user_id") and scope.get("agentee_id")):
                raise ValueError
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute("DELETE FROM public.runtime_control_claim_nonces WHERE expires_at < now()")
                inserted = await conn.fetchval(
                    """INSERT INTO public.runtime_control_claim_nonces(jti,service_id,expires_at)
                       VALUES($1,$2,to_timestamp($3)) ON CONFLICT(jti) DO NOTHING RETURNING jti""",
                    jti, service_id, exp)
                if inserted is None: raise ValueError
            return {"service_id":service_id, **dict(scope)}
        except Exception as error:
            raise ValueError("invalid_runtime_control_claim") from error
