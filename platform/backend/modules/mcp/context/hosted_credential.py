"""Production hosted credential: sign, verify, rotate keys, revoke by jti.

Every token is an HMAC-SHA256 JWT-like structure (compact, base64url-encoded
header.payload.signature).  No secret or token text may appear in logs, repr,
persisted config, or error messages.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Mapping, Optional, Tuple

from modules.runtime_manager.authority import AuthorityMaterial

# ── low-level helpers ──────────────────────────────────────────────────────────


def _b64encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(encoded: str) -> bytes:
    padded = encoded + "=" * (-len(encoded) % 4)
    return urlsafe_b64decode(padded.encode("ascii"))


def _constant_time_compare(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def _redact(value: str) -> str:
    """Never preserve credential-derived characters."""
    return "<redacted>" if value else "<empty>"


# ── key material ───────────────────────────────────────────────────────────────


class SigningKey:
    """A single symmetric signing key with a kid and an optional expiry."""
    __slots__ = ("kid", "secret", "created_at", "expires_at")

    def __init__(self, kid: str, secret: bytes, *,
                 created_at: Optional[float] = None,
                 expires_at: Optional[float] = None) -> None:
        if len(secret) < 32:
            raise ValueError("signing key must be at least 32 bytes")
        self.kid = kid
        self.secret = bytes(secret)
        self.created_at = created_at or time.time()
        self.expires_at = expires_at  # None = no expiry

    @property
    def active(self) -> bool:
        return self.is_active()

    def is_active(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return True
        return (now or time.time()) < self.expires_at

    def sign(self, payload: bytes) -> bytes:
        return hmac.digest(self.secret, payload, digest=hashlib.sha256)

    def __repr__(self) -> str:
        return f"SigningKey(kid={self.kid!r}, secret=<redacted>, active={self.active})"


@dataclass
class KeyStore:
    """Holds the current signing key plus a set of still-valid verification keys."""
    current: SigningKey
    valid: Dict[str, SigningKey] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.valid[self.current.kid] = self.current

    def rotate(self, new_key: SigningKey) -> None:
        self.valid[new_key.kid] = new_key
        self.current = new_key
        # Prune expired keys from the verification set, but always keep
        # the one we just rotated *from* for at least one token lifetime.
        stale = {kid for kid, key in self.valid.items() if not key.active and key is not self.current}
        for kid in stale:
            self.valid.pop(kid, None)

    def refresh_from(self, keys: "KeyStore") -> None:
        self.valid.update(keys.valid)
        self.rotate(keys.current)

    def verifier(self, kid: str) -> Optional[SigningKey]:
        return self.valid.get(kid)


# ── revocation ─────────────────────────────────────────────────────────────────


class RevocationStore:
    """In-memory jti revocation.  Production would back this with a DB/cache."""
    def __init__(self) -> None:
        self._revoked: Dict[str, float] = {}       # jti -> revoked_at
        self._binding_revocations: Dict[str, Tuple[int, int]] = {}  # binding_id -> (version, epoch)

    def revoke_jti(self, jti: str) -> None:
        self._revoked[jti] = time.time()

    def revoke_binding(self, binding_id: str, through_binding_version: int,
                       through_lease_epoch: int) -> None:
        current = self._binding_revocations.get(binding_id, (0, 0))
        self._binding_revocations[binding_id] = (
            max(current[0], through_binding_version),
            max(current[1], through_lease_epoch),
        )

    def is_revoked_jti(self, jti: str) -> bool:
        return jti in self._revoked

    def is_revoked_binding(self, binding_id: str, binding_version: int,
                           lease_epoch: int) -> bool:
        revoked_version, revoked_epoch = self._binding_revocations.get(binding_id, (0, 0))
        return binding_version <= revoked_version or lease_epoch <= revoked_epoch


# ── credential structure ───────────────────────────────────────────────────────


_REQUIRED_CLAIMS = frozenset({
    "iss", "aud", "sub", "exp", "jti", "iat",
    "runtime_id", "generation", "binding_id", "binding_version",
    "lease_epoch", "game_session_id", "game_instance_nonce",
    "capability_digest", "user_id", "agentee_id",
    "scopes", "allowed_tools",
})

HEADER_TEMPLATE = {"alg": "HS256", "typ": "JWT"}


def _build_payload(material: AuthorityMaterial, *, now: float) -> Dict[str, object]:
    return {
        "iss": material.iss,
        "aud": material.aud,
        "sub": material.sub,
        "iat": int(now),
        "exp": int(material.expires_at.timestamp()),
        "jti": material.jti,
        "runtime_id": material.runtime_id,
        "generation": material.generation,
        "binding_id": material.binding_id,
        "binding_version": material.binding_version,
        "lease_epoch": material.lease_epoch,
        "game_session_id": material.game_session_id,
        "game_instance_nonce": material.game_instance_nonce,
        "capability_digest": material.capability_digest,
        "user_id": material.user_id,
        "agentee_id": material.agentee_id,
        "scopes": list(material.scopes),
        "allowed_tools": list(material.allowed_tools),
    }


def _extract_material(claims: Mapping[str, object], credential_ref: str) -> AuthorityMaterial:
    def _str(key: str) -> str:
        v = claims.get(key)
        if not isinstance(v, str) or not v:
            raise ValueError(f"token claim '{key}' is missing or not a string")
        return v

    def _int(key: str) -> int:
        v = claims.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"token claim '{key}' is missing or not an integer")
        return v

    def _tuple(key: str) -> Tuple[str, ...]:
        v = claims.get(key)
        if not isinstance(v, list) or not all(isinstance(item, str) and item for item in v):
            raise ValueError(f"token claim '{key}' is missing or invalid")
        return tuple(v)

    return AuthorityMaterial(
        credential_ref=credential_ref,
        iss=_str("iss"),
        aud=_str("aud"),
        sub=_str("sub"),
        jti=_str("jti"),
        runtime_id=_str("runtime_id"),
        generation=_int("generation"),
        binding_id=_str("binding_id"),
        binding_version=_int("binding_version"),
        lease_epoch=_int("lease_epoch"),
        game_session_id=_str("game_session_id"),
        game_instance_nonce=_str("game_instance_nonce"),
        capability_digest=_str("capability_digest"),
        user_id=_str("user_id"),
        agentee_id=_str("agentee_id"),
        scopes=_tuple("scopes"),
        allowed_tools=_tuple("allowed_tools"),
        expires_at=datetime.fromtimestamp(_int("exp"), timezone.utc),
    )


# ── signer ─────────────────────────────────────────────────────────────────────


class HostedCredentialSigner:
    """Production token signer: produces compact HMAC-SHA256 JWT-like tokens."""

    def __init__(self, keys: KeyStore, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._keys = keys
        self._clock = clock

    def sign(self, material: AuthorityMaterial) -> str:
        """Sign AuthorityMaterial into a compact credential string.

        The returned token is the only copy of the secret material that may
        cross a process boundary.  Callers must never log it.
        """
        key = self._keys.current
        if not key.is_active(self._clock()):
            raise ValueError("current signing key has expired")
        header_bytes = json.dumps({**HEADER_TEMPLATE, "kid": key.kid},
                                  separators=(",", ":")).encode("utf-8")
        payload = _build_payload(material, now=self._clock())
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        signing_input = _b64encode(header_bytes) + "." + _b64encode(payload_bytes)
        signature = _b64encode(key.sign(signing_input.encode("ascii")))
        return signing_input + "." + signature

    @property
    def current_kid(self) -> str:
        return self._keys.current.kid

    def current_key_remaining_seconds(self) -> float | None:
        expires_at = self._keys.current.expires_at
        if expires_at is None:
            return None
        return expires_at - self._clock()

    def refresh_keys(self, keys: KeyStore) -> None:
        self._keys.refresh_from(keys)

    def __repr__(self) -> str:
        return f"HostedCredentialSigner(kid={self.current_kid!r}, key=<redacted>)"


# ── verifier ───────────────────────────────────────────────────────────────────


class HostedCredentialVerifier:
    """Production token verifier: validates signature, expiry, audience, issuer,
    and checks jti/binding revocation."""

    EXPECTED_ISSUER = "starcoplay-runtime-manager"
    EXPECTED_AUDIENCE = "starcoplay-mcp"
    MAX_FUTURE_IAT_SKEW = 30.0

    def __init__(self, keys: KeyStore, revocations: RevocationStore, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._keys = keys
        self._revocations = revocations
        self._clock = clock

    def refresh_keys(self, keys: KeyStore) -> None:
        self._keys.refresh_from(keys)

    async def verify(self, credential: str) -> AuthorityMaterial:
        """Verify a compact credential string and return the extracted material.

        Raises ValueError (wrapped as McpError by the caller) on any failure.
        Never includes the token text in the error message.
        """
        if not credential or not isinstance(credential, str):
            raise ValueError("credential is missing or invalid")

        parts = credential.split(".")
        if len(parts) != 3:
            raise ValueError("credential format is invalid")

        encoded_header, encoded_payload, encoded_signature = parts

        try:
            header_bytes = _b64decode(encoded_header)
            header = json.loads(header_bytes)
        except Exception:
            raise ValueError("credential header is malformed")

        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise ValueError("credential algorithm is unsupported")

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ValueError("credential key identifier is missing")

        key = self._keys.verifier(kid)
        if key is None or not key.is_active(self._clock()):
            raise ValueError("credential signing key is unknown or expired")

        # Verify signature before decoding payload (avoid parse cost on tampered tokens).
        signing_input = (encoded_header + "." + encoded_payload).encode("ascii")
        expected_sig = _b64encode(key.sign(signing_input))
        if not _constant_time_compare(expected_sig.encode("ascii"),
                                       encoded_signature.encode("ascii")):
            raise ValueError("credential signature is invalid")

        try:
            payload_bytes = _b64decode(encoded_payload)
            payload = json.loads(payload_bytes)
        except Exception:
            raise ValueError("credential payload is malformed")

        if not isinstance(payload, Mapping):
            raise ValueError("credential payload is invalid")

        # Validate required claims exist and have correct types.
        for claim in _REQUIRED_CLAIMS:
            if claim not in payload:
                raise ValueError(f"required claim is missing")

        now = self._clock()

        # Expiry is absolute. Clock skew may permit a slightly future iat,
        # but must never extend an already expired authority credential.
        exp = payload.get("exp")
        if not isinstance(exp, int) or isinstance(exp, bool):
            raise ValueError("credential expiry is invalid")
        if now >= exp:
            raise ValueError("credential has expired")

        # Issued-at must not be in the far future.
        iat = payload.get("iat")
        if not isinstance(iat, int) or isinstance(iat, bool):
            raise ValueError("credential issued-at is invalid")
        if iat > now + self.MAX_FUTURE_IAT_SKEW:
            raise ValueError("credential issued-at is in the future")

        # Issuer / audience.
        if payload.get("iss") != self.EXPECTED_ISSUER:
            raise ValueError("credential issuer is invalid")
        if payload.get("aud") != self.EXPECTED_AUDIENCE:
            raise ValueError("credential audience is invalid")

        # Runtime Manager freezes sub as the runtime principal.
        sub = payload.get("sub")
        runtime_id = payload.get("runtime_id")
        if (
            not isinstance(sub, str) or not sub
            or not isinstance(runtime_id, str) or not runtime_id
            or not hmac.compare_digest(sub, runtime_id)
        ):
            raise ValueError("credential subject does not match runtime identity")

        # Validate positive integers.
        for field in ("generation", "binding_version", "lease_epoch"):
            v = payload.get(field)
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ValueError(f"credential {field} is invalid")

        for field in (
            "jti", "binding_id", "game_session_id", "game_instance_nonce",
            "capability_digest", "user_id", "agentee_id",
        ):
            value = payload.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"credential {field} is invalid")
        for field in ("scopes", "allowed_tools"):
            values = payload.get(field)
            if (
                not isinstance(values, list)
                or not all(isinstance(value, str) and value for value in values)
                or len(values) != len(set(values))
            ):
                raise ValueError(f"credential {field} is invalid")

        # Build material for jti/binding revocation checks.
        credential_ref = f"hosted:{payload['jti']}"
        material = _extract_material(payload, credential_ref)

        # JTI revocation.
        if self._revocations.is_revoked_jti(material.jti):
            raise ValueError("credential has been revoked")

        # Binding-level revocation.
        if self._revocations.is_revoked_binding(
            material.binding_id, material.binding_version, material.lease_epoch
        ):
            raise ValueError("credential binding has been revoked")

        return material

    async def is_revoked(self, material: AuthorityMaterial) -> bool:
        return (
            self._revocations.is_revoked_jti(material.jti)
            or self._revocations.is_revoked_binding(
                material.binding_id, material.binding_version, material.lease_epoch
            )
        )


# ── factory helpers ────────────────────────────────────────────────────────────


def generate_signing_key(*, kid: Optional[str] = None,
                         lifetime_seconds: Optional[float] = None) -> SigningKey:
    """Generate a fresh random signing key."""
    kid_value = kid or secrets.token_urlsafe(16)
    secret = secrets.token_bytes(64)  # 512-bit HMAC key
    now = time.time()
    expires = (now + lifetime_seconds) if lifetime_seconds is not None else None
    return SigningKey(kid_value, secret, created_at=now, expires_at=expires)


def create_credential_signer(*, kid: Optional[str] = None,
                             lifetime_seconds: float = 3600.0) -> HostedCredentialSigner:
    """Create a signer with a single fresh key, suitable for testing."""
    key = generate_signing_key(kid=kid, lifetime_seconds=lifetime_seconds)
    store = KeyStore(current=key)
    return HostedCredentialSigner(store)


def create_credential_verifier(signer: HostedCredentialSigner,
                               revocations: Optional[RevocationStore] = None
                               ) -> HostedCredentialVerifier:
    """Create a verifier from an existing signer's keystore."""
    return HostedCredentialVerifier(
        signer._keys,  # type: ignore[arg-type]
        revocations or RevocationStore(),
    )
