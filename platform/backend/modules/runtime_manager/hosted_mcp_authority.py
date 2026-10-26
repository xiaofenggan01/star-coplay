"""Durable Hosted MCP key/revocation and short-lived credential lifecycle."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.mcp.context.hosted_credential import (
    HostedCredentialSigner, HostedCredentialVerifier, KeyStore, RevocationStore, SigningKey,
)
from modules.runtime_manager.authority import AuthorityMaterial
from common.observability import emit_metric

log = logging.getLogger("runtime_manager.hosted_authority")


def project_hosted_skill_grants(allowed_capabilities) -> tuple[str, ...]:
    """Project internal Runtime grants into versioned Hosted MCP Tool grants.

    Runtime configuration names neutral capabilities, while the dynamic MCP
    projection authorizes final version-pinned Skill identities.  Map only the
    audited Proto release set, preserve explicit Skill grants, and ignore
    unknown/legacy names rather than widening authority.
    """
    from modules.mcp.release_manifest import PROTO_CAPABILITIES

    proto = frozenset(PROTO_CAPABILITIES)
    projected: set[str] = set()
    for value in allowed_capabilities:
        if not isinstance(value, str):
            continue
        if value in proto:
            projected.add(f"skill:stardew.proto.{value}@1.0.0")
        elif value.startswith("skill:") and "@" in value:
            projected.add(value)
    return tuple(sorted(projected))


class AsyncpgHostedMcpSecurityRepository:
    def __init__(self, pool) -> None: self.pool = pool

    async def load_or_create_keys(
        self, lifetime_seconds: int = 86400, minimum_validity_seconds: int = 0,
    ) -> KeyStore:
        if lifetime_seconds <= minimum_validity_seconds or minimum_validity_seconds < 0:
            raise ValueError("signing key lifetime must exceed minimum validity")
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('hosted-mcp-signing-key',0))")
            rows = await conn.fetch(
                "SELECT kid,secret,extract(epoch from created_at) created,"
                "extract(epoch from expires_at) expires,extract(epoch from now()) db_now,active "
                "FROM public.runtime_mcp_signing_keys WHERE expires_at > now() ORDER BY created_at"
            )
            active = next((row for row in reversed(rows) if row["active"]), None)
            if active is not None and (
                float(active["expires"]) - float(active["db_now"])
                <= minimum_validity_seconds
            ):
                active = None
            if active is None:
                await conn.execute("UPDATE public.runtime_mcp_signing_keys SET active=false WHERE active")
                kid, secret = secrets.token_urlsafe(16), secrets.token_bytes(64)
                row = await conn.fetchrow(
                    "INSERT INTO public.runtime_mcp_signing_keys(kid,secret,expires_at,active) "
                    "VALUES($1,$2,now()+make_interval(secs => $3),true) "
                    "RETURNING kid,secret,extract(epoch from created_at) created,extract(epoch from expires_at) expires,active",
                    kid, secret, lifetime_seconds,
                )
                rows = [*rows, row]
                active = row
        keys = {
            row["kid"]: SigningKey(row["kid"], bytes(row["secret"]),
                                   created_at=float(row["created"]), expires_at=float(row["expires"]))
            for row in rows
        }
        return KeyStore(keys[active["kid"]], keys)

    async def revoke_jti(self, jti: str) -> None:
        await self.pool.execute(
            "INSERT INTO public.runtime_mcp_jti_revocations(jti) VALUES($1) ON CONFLICT DO NOTHING", jti
        )

    async def revoke_binding(self, binding_id: str, version: int, epoch: int) -> None:
        await self.pool.execute(
            """INSERT INTO public.runtime_mcp_binding_revocations(binding_id,through_binding_version,through_lease_epoch)
               VALUES($1,$2,$3) ON CONFLICT(binding_id) DO UPDATE SET
               through_binding_version=greatest(runtime_mcp_binding_revocations.through_binding_version,$2),
               through_lease_epoch=greatest(runtime_mcp_binding_revocations.through_lease_epoch,$3),revoked_at=now()""",
            binding_id, version, epoch,
        )

    async def is_revoked(self, material: AuthorityMaterial) -> bool:
        row = await self.pool.fetchrow(
            """SELECT EXISTS(SELECT 1 FROM public.runtime_mcp_jti_revocations WHERE jti=$1) jti,
                      through_binding_version,through_lease_epoch
               FROM public.runtime_mcp_binding_revocations WHERE binding_id=$2""",
            material.jti, material.binding_id,
        )
        if row is None:
            jti = await self.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.runtime_mcp_jti_revocations WHERE jti=$1)", material.jti
            )
            return bool(jti)
        # Binding version and lease epoch form one fence.  A resumed session may
        # legitimately advance only the binding version while retaining the
        # same lease epoch, so either component alone must not revoke it.
        return bool(row["jti"] or (
            material.binding_version <= row["through_binding_version"]
            and material.lease_epoch <= row["through_lease_epoch"]
        ))


class DurableHostedCredentialVerifier:
    def __init__(self, verifier: HostedCredentialVerifier,
                 repository: AsyncpgHostedMcpSecurityRepository, *,
                 key_refresh_cooldown_seconds: float = 5.0) -> None:
        self.verifier, self.repository = verifier, repository
        self.key_refresh_cooldown_seconds = key_refresh_cooldown_seconds
        self._key_refresh_lock = asyncio.Lock()
        self._last_key_refresh_at = 0.0

    async def verify(self, credential: str) -> AuthorityMaterial:
        try:
            material = await self.verifier.verify(credential)
        except ValueError as error:
            if str(error) != "credential signing key is unknown or expired":
                raise
            async with self._key_refresh_lock:
                now = time.monotonic()
                if now - self._last_key_refresh_at >= self.key_refresh_cooldown_seconds:
                    keys = await self.repository.load_or_create_keys()
                    self.verifier.refresh_keys(keys)
                    self._last_key_refresh_at = now
                    emit_metric(
                        "runtime_signing_key_refresh_total", "counter", 1,
                        outcome="refreshed", stage="rotation",
                    )
            material = await self.verifier.verify(credential)
        if await self.repository.is_revoked(material):
            raise ValueError("credential has been revoked")
        return material

    async def is_revoked(self, material: AuthorityMaterial) -> bool:
        return await self.repository.is_revoked(material)


class HostedCredentialFileAuthority:
    """Outbox target: sign ACTIVE grants; revoke before deleting runtime material."""
    def __init__(self, signer: HostedCredentialSigner, security: AsyncpgHostedMcpSecurityRepository,
                 bindings, workspace_root: Path, *, ttl_seconds: int = 1800,
                 signing_key_lifetime_seconds: int = 86400,
                 signing_key_renew_before_seconds: int = 300) -> None:
        self.signer, self.security, self.bindings = signer, security, bindings
        self.workspace_root, self.ttl_seconds = Path(workspace_root).resolve(), ttl_seconds
        self.signing_key_lifetime_seconds = signing_key_lifetime_seconds
        self.signing_key_renew_before_seconds = signing_key_renew_before_seconds
        self._signing_key_rotation_lock = asyncio.Lock()

    def _path(self, runtime_id: str, generation: int) -> Path:
        path = (self.workspace_root / runtime_id / f"generation-{generation}" / "mcp-credential").resolve()
        if not path.is_relative_to(self.workspace_root): raise ValueError("credential path escapes workspace")
        return path

    async def issue(self, binding, scopes=("game:read", "game:write"), allowed_tools=(), jti=""):
        try:
            await self._ensure_signing_key()
            return await self._issue(binding, scopes=scopes, allowed_tools=allowed_tools, jti=jti)
        except Exception as error:
            log.error(
                "hosted credential issue failed",
                extra={"event": "runtime.credential.issue_failed",
                       "runtime_id": binding.runtime_id, "generation": binding.generation,
                       "binding_id": binding.binding_id, "binding_state": binding.status.value,
                       "outcome": "failed", "error_code": "credential_issue_failed",
                       "exception_type": type(error).__name__},
            )
            emit_metric(
                "runtime_credential_total", "counter", 1, outcome="failed",
                runtime_id=binding.runtime_id, generation=binding.generation,
                binding_id=binding.binding_id, binding_state=binding.status.value,
                error_code="credential_issue_failed", stage="apply",
            )
            raise

    async def _ensure_signing_key(self) -> None:
        minimum_validity = self.ttl_seconds + self.signing_key_renew_before_seconds
        remaining = self.signer.current_key_remaining_seconds()
        if remaining is None or remaining > minimum_validity:
            return
        async with self._signing_key_rotation_lock:
            remaining = self.signer.current_key_remaining_seconds()
            if remaining is None or remaining > minimum_validity:
                return
            loader = getattr(self.security, "load_or_create_keys", None)
            if loader is None:
                raise RuntimeError("signing_key_rotation_unavailable")
            keys = await loader(
                lifetime_seconds=self.signing_key_lifetime_seconds,
                minimum_validity_seconds=minimum_validity,
            )
            previous_kid = self.signer.current_kid
            self.signer.refresh_keys(keys)
            refreshed = self.signer.current_key_remaining_seconds()
            if refreshed is not None and refreshed <= minimum_validity:
                raise RuntimeError("signing_key_rotation_incomplete")
            emit_metric(
                "runtime_signing_key_rotation_total", "counter", 1,
                outcome=("rotated" if self.signer.current_kid != previous_kid else "refreshed"),
                stage="rotation",
            )

    async def _issue(self, binding, scopes=("game:read", "game:write"), allowed_tools=(), jti=""):
        allowed_tools = project_hosted_skill_grants(allowed_tools)
        material = AuthorityMaterial(
            "ephemeral", "starcoplay-runtime-manager", "starcoplay-mcp", binding.runtime_id,
            jti or secrets.token_urlsafe(24), binding.runtime_id, binding.generation, binding.binding_id,
            binding.binding_version, binding.lease_epoch, binding.game_session_id or "",
            binding.game_instance_nonce or "", binding.capability_digest or "", binding.user_id,
            binding.agentee_id, tuple(scopes), tuple(allowed_tools),
            datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
        )
        token = self.signer.sign(material)
        path = self._path(binding.runtime_id, binding.generation)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        try:
            os.write(fd, token.encode())
            os.fsync(fd)
        finally: os.close(fd)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        path.chmod(0o600)
        log.info(
            "hosted credential issued",
            extra={"event": "runtime.credential.issued", "runtime_id": binding.runtime_id,
                   "generation": binding.generation, "binding_id": binding.binding_id,
                   "binding_state": binding.status.value, "outcome": "success"},
        )
        emit_metric(
            "runtime_credential_total", "counter", 1, outcome="issued",
            runtime_id=binding.runtime_id, generation=binding.generation,
            binding_id=binding.binding_id, binding_state=binding.status.value,
        )
        return material

    async def revoke(self, binding_id: str, through_binding_version: int, through_lease_epoch: int):
        try:
            return await self._revoke(binding_id, through_binding_version, through_lease_epoch)
        except Exception as error:
            log.error(
                "hosted credential revoke failed",
                extra={"event": "runtime.credential.revoke_failed", "binding_id": binding_id,
                       "outcome": "failed", "error_code": "credential_revoke_failed",
                       "exception_type": type(error).__name__},
            )
            emit_metric(
                "runtime_credential_total", "counter", 1, outcome="failed",
                binding_id=binding_id, error_code="credential_revoke_failed", stage="apply",
            )
            raise

    async def _revoke(self, binding_id: str, through_binding_version: int, through_lease_epoch: int):
        await self.security.revoke_binding(binding_id, through_binding_version, through_lease_epoch)
        binding = await self.bindings.get(binding_id)
        if binding is not None:
            path = self._path(binding.runtime_id, binding.generation)
            # Revoke the active credential JTI so in-flight tokens are
            # rejected immediately.  Normal renewal preserves old JTIs for a
            # controlled grace period; explicit revoke must not.
            try:
                payload = path.read_text(encoding="utf-8").split(".")[1]
                payload += "=" * (-len(payload) % 4)
                jti = json.loads(base64.urlsafe_b64decode(payload))["jti"]
                await self.security.revoke_jti(jti)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                pass
            path.unlink(missing_ok=True)
        log.info(
            "hosted credential revoked",
            extra={"event": "runtime.credential.revoked", "binding_id": binding_id,
                   "outcome": "success"},
        )
        emit_metric(
            "runtime_credential_total", "counter", 1, outcome="revoked",
            binding_id=binding_id,
        )

    async def renew_active(self, runtimes, *, renew_before_seconds: int = 300) -> int:
        renewed = 0
        now = datetime.now(timezone.utc).timestamp()
        for binding in await self.bindings.list_active():
            path = self._path(binding.runtime_id, binding.generation)
            expires = 0
            try:
                payload = path.read_text(encoding="utf-8").split(".")[1]
                payload += "=" * (-len(payload) % 4)
                expires = int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                pass
            if expires - now > renew_before_seconds:
                continue
            runtime = await runtimes.get_generation(binding.runtime_id, binding.generation)
            if runtime is None:
                continue
            await self.issue(
                binding, allowed_tools=tuple(runtime.spec.allowed_capabilities),
                jti=secrets.token_urlsafe(24),
            )
            renewed += 1
        return renewed


class HostedAuthorityWorker:
    def __init__(self, outbox, authority: HostedCredentialFileAuthority, runtimes) -> None:
        self.outbox, self.authority, self.runtimes = outbox, authority, runtimes

    async def run_once(self) -> int:
        return await self.outbox.run_once() + await self.authority.renew_active(self.runtimes)
