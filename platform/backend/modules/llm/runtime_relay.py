"""Generation-scoped credentials and environment for Hosted Runtime LLM relay."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class RuntimeLLMClaims:
    user_id: str
    runtime_id: str
    generation: int
    model_tier: str
    expires_at: int


class RuntimeLLMTokenSigner:
    _PREFIX = b"starcoplay.runtime-llm.v1."

    def __init__(self, secret: str, *, ttl_seconds: int = 6 * 3600,
                 clock=time.time) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("runtime LLM relay secret must be at least 32 bytes")
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def issue(self, *, user_id: str, runtime_id: str, generation: int,
              model_tier: str) -> str:
        if not user_id or not runtime_id or generation < 1 or not model_tier:
            raise ValueError("runtime LLM claims are incomplete")
        payload = {
            "aud": "starcoplay-runtime-llm",
            "user_id": user_id,
            "runtime_id": runtime_id,
            "generation": generation,
            "model_tier": model_tier,
            "exp": int(self._clock()) + self._ttl_seconds,
        }
        encoded = _b64encode(json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))
        signature = _b64encode(hmac.new(
            self._secret, self._PREFIX + encoded.encode("ascii"), hashlib.sha256,
        ).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> RuntimeLLMClaims:
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(
                self._secret, self._PREFIX + encoded.encode("ascii"), hashlib.sha256,
            ).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid signature")
            payload = json.loads(_b64decode(encoded))
        except Exception as exc:
            raise ValueError("invalid runtime LLM credential") from exc
        if not isinstance(payload, Mapping) or payload.get("aud") != "starcoplay-runtime-llm":
            raise ValueError("invalid runtime LLM credential audience")
        try:
            claims = RuntimeLLMClaims(
                user_id=str(payload["user_id"]),
                runtime_id=str(payload["runtime_id"]),
                generation=int(payload["generation"]),
                model_tier=str(payload["model_tier"]),
                expires_at=int(payload["exp"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid runtime LLM credential claims") from exc
        if (not claims.user_id or not claims.runtime_id or claims.generation < 1
                or not claims.model_tier or claims.expires_at <= int(self._clock())):
            raise ValueError("runtime LLM credential expired or incomplete")
        return claims


class DbRuntimeLLMEnvironmentResolver:
    """Snapshot the tenant model tier and mint one credential per generation."""

    def __init__(self, pool: Any, signer: RuntimeLLMTokenSigner,
                 relay_base_url: str, *, default_model_tier: str = "balanced") -> None:
        self._pool = pool
        self._signer = signer
        self._relay_base_url = relay_base_url.rstrip("/")
        self._default_model_tier = default_model_tier

    async def resolve(self, *, runtime_id: str, generation: int, user_id: str,
                      agentee_id: str, provider: str,
                      adapter_config: Any) -> Mapping[str, str]:
        # Hosted runtimes select their own provider adapter; both Claude and
        # DeepSeek Harness consume the same generation-scoped relay credential.
        if provider not in {"claude", "dsh"}:
            return {}
        row = await self._pool.fetchrow(
            "SELECT preferences FROM public.profiles WHERE user_id=$1", user_id,
        )
        preferences = row["preferences"] if row and row["preferences"] is not None else {}
        if isinstance(preferences, str):
            try:
                preferences = json.loads(preferences)
            except json.JSONDecodeError:
                preferences = {}
        ai_relay = preferences.get("ai_relay", {}) if isinstance(preferences, Mapping) else {}
        configured_tier = ai_relay.get("default_model_tier") if isinstance(ai_relay, Mapping) else None
        model_tier = configured_tier.strip() if isinstance(configured_tier, str) else ""
        model_tier = model_tier or self._default_model_tier
        credential = self._signer.issue(
            user_id=user_id,
            runtime_id=runtime_id,
            generation=generation,
            model_tier=model_tier,
        )
        if provider == "dsh":
            # DSH's deepseek-official provider appends /v1/messages to the
            # base URL and sends the credential as x-api-key.  The hosted relay
            # accepts either header, so no separate endpoint is required.
            return {
                "DEEPSEEK_BASE_URL": self._relay_base_url,
                "DEEPSEEK_API_KEY": credential,
            }
        return {
            "ANTHROPIC_BASE_URL": self._relay_base_url,
            "ANTHROPIC_AUTH_TOKEN": credential,
            "ANTHROPIC_MODEL": model_tier,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model_tier,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model_tier,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model_tier,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
        }
