from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from modules.mcp.context.hosted_credential import (
    HostedCredentialSigner, HostedCredentialVerifier, KeyStore, RevocationStore, SigningKey,
    generate_signing_key,
)
from modules.runtime_manager.domain import BindingState, RuntimeBinding
from modules.runtime_manager.hosted_mcp_authority import (
    AsyncpgHostedMcpSecurityRepository, DurableHostedCredentialVerifier,
    HostedCredentialFileAuthority,
    project_hosted_skill_grants,
)
from modules.runtime_manager.coordinator import EffectiveActivityReporter, HostedMcpActivityReporter


class Security:
    def __init__(self): self.revoked = []; self.revoked_jtis = []
    async def revoke_jti(self, jti): self.revoked_jtis.append(jti)
    async def revoke_binding(self, binding_id, version, epoch):
        self.revoked.append((binding_id, version, epoch))
    async def is_revoked(self, material):
        binding_revoked = any(
            b[0] == material.binding_id and material.binding_version <= b[1]
            and material.lease_epoch <= b[2]
            for b in self.revoked
        )
        return material.jti in self.revoked_jtis or binding_revoked


class RotatingSecurity(Security):
    def __init__(self, replacement):
        super().__init__()
        self.replacement = replacement
        self.key_loads = []

    async def load_or_create_keys(self, **kwargs):
        self.key_loads.append(kwargs)
        return self.replacement


class Bindings:
    def __init__(self, binding): self.binding = binding
    async def get(self, binding_id): return self.binding if binding_id == self.binding.binding_id else None


class KeyRepositoryConnection:
    def __init__(self):
        self.rows = [{
            "kid": "expiring-db-key", "secret": b"a" * 64,
            "created": 1000.0, "expires": 4660.0, "db_now": 4600.0,
            "active": True,
        }]
        self.deactivated = False

    def transaction(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass

    async def execute(self, sql, *args):
        if "SET active=false" in sql:
            self.deactivated = True
        return "OK"

    async def fetch(self, _sql):
        return self.rows

    async def fetchrow(self, _sql, kid, secret, lifetime_seconds):
        return {
            "kid": kid, "secret": secret, "created": 4600.0,
            "expires": 4600.0 + lifetime_seconds, "active": True,
        }


class KeyRepositoryPool:
    def __init__(self): self.connection = KeyRepositoryConnection()
    def acquire(self): return self.connection


def active_binding():
    return RuntimeBinding(
        "binding-a", "runtime-a", 1, "user-a", "agentee-a", 2, 1,
        BindingState.ACTIVE, "game-a", "nonce-a", "digest-a", "resume-a",
        datetime.now(timezone.utc),
    )


def test_runtime_capability_grants_project_to_fail_closed_skill_surface():
    assert project_hosted_skill_grants((
        "say", "query_runtime_snapshot",
        "skill:stardew.basic.observe_current_view@1.0.0",
        "skill:stardew.advanced.water_crops@1.0.0",
        "water_crops", "legacy_unknown", "say",
    )) == (
        "skill:stardew.advanced.water_crops@1.0.0",
        "skill:stardew.basic.observe_current_view@1.0.0",
        "skill:stardew.proto.query_runtime_snapshot@1.0.0",
        "skill:stardew.proto.say@1.0.0",
    )


@pytest.mark.asyncio
async def test_normal_issue_does_not_revoke_old_jti_grace_period(tmp_path):
    """Normal renewal publishes new token but preserves old JTI for grace period."""
    binding = active_binding()
    security = Security()
    keys = KeyStore(generate_signing_key(kid="product-key", lifetime_seconds=3600))
    signer = HostedCredentialSigner(keys)
    authority = HostedCredentialFileAuthority(
        signer, security, Bindings(binding), tmp_path, ttl_seconds=1800,
    )
    first = await authority.issue(
        binding, scopes=("game:read",), allowed_tools=("observe",), jti="jti-first",
    )
    path = tmp_path / "runtime-a" / "generation-1" / "mcp-credential"
    first_token = path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    verified = await HostedCredentialVerifier(keys, RevocationStore()).verify(first_token)
    assert verified.jti == "jti-first"
    assert verified.allowed_tools == ()  # unknown legacy grant is never widened

    # Second issue (normal renewal) — must NOT revoke old JTI.
    await authority.issue(
        binding, scopes=("game:read",), allowed_tools=("observe",), jti="jti-second",
    )
    assert path.read_text() != first_token
    # Old JTI is preserved for grace period, not revoked.
    assert security.revoked_jtis == []
    assert not list(path.parent.glob(".mcp-credential.*"))
    # Old token still verifies.
    assert await HostedCredentialVerifier(keys, RevocationStore()).verify(first_token)

    # Explicit revoke removes file, revokes binding, AND revokes current JTI.
    await authority.revoke(binding.binding_id, binding.binding_version, binding.lease_epoch)
    assert security.revoked == [("binding-a", 2, 1)]
    assert not path.exists()


@pytest.mark.asyncio
async def test_issue_rotates_key_before_it_can_expire_during_credential_lifetime(tmp_path):
    now = datetime.now(timezone.utc).timestamp()
    old_keys = KeyStore(SigningKey(
        "expiring-key", b"a" * 64, created_at=now - 3600, expires_at=now + 60,
    ))
    replacement = KeyStore(SigningKey(
        "replacement-key", b"b" * 64, created_at=now, expires_at=now + 86400,
    ))
    security = RotatingSecurity(replacement)
    signer = HostedCredentialSigner(old_keys)
    authority = HostedCredentialFileAuthority(
        signer, security, Bindings(active_binding()), tmp_path, ttl_seconds=1800,
    )

    await authority.issue(active_binding(), jti="jti-after-rotation")

    assert signer.current_kid == "replacement-key"
    assert security.key_loads == [{
        "lifetime_seconds": 86400,
        "minimum_validity_seconds": 2100,
    }]


@pytest.mark.asyncio
async def test_verifier_refreshes_keys_when_another_instance_rotates(tmp_path):
    now = datetime.now(timezone.utc).timestamp()
    old_keys = KeyStore(SigningKey(
        "old-instance-key", b"a" * 64, created_at=now, expires_at=now + 86400,
    ))
    replacement = KeyStore(SigningKey(
        "other-instance-key", b"b" * 64, created_at=now, expires_at=now + 86400,
    ))
    security = RotatingSecurity(replacement)
    issuing_authority = HostedCredentialFileAuthority(
        HostedCredentialSigner(replacement), security, Bindings(active_binding()),
        tmp_path, ttl_seconds=1800,
    )
    await issuing_authority.issue(active_binding(), jti="jti-other-instance")
    token = (tmp_path / "runtime-a" / "generation-1" / "mcp-credential").read_text()
    verifier = DurableHostedCredentialVerifier(
        HostedCredentialVerifier(old_keys, RevocationStore()), security,
    )

    verified = await verifier.verify(token)

    assert verified.jti == "jti-other-instance"
    assert security.key_loads == [{}]


@pytest.mark.asyncio
async def test_key_repository_rotates_before_active_key_loses_minimum_validity():
    pool = KeyRepositoryPool()
    repository = AsyncpgHostedMcpSecurityRepository(pool)

    keys = await repository.load_or_create_keys(
        lifetime_seconds=86400, minimum_validity_seconds=2100,
    )

    assert keys.current.kid != "expiring-db-key"
    assert pool.connection.deactivated is True


@pytest.mark.asyncio
async def test_hosted_mcp_success_refreshes_fixed_600_second_product_ttl(tmp_path):
    from tests.runtime_manager.test_product_composition import (
        Adapter, Authority, MemoryRepository, PersonaReader, spec,
    )
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    from modules.runtime_manager.domain import RuntimeState

    repository, authority, adapter = MemoryRepository(), Authority(), Adapter()
    manager = RuntimeManagerCoordinator(
        repository, PersonaReader(), authority, {"fake": adapter}, tmp_path,
    )
    await manager.ensure(spec())
    reporter = HostedMcpActivityReporter(EffectiveActivityReporter(repository))
    await reporter.report_succeeded("runtime-1", 1)
    refreshed = (await repository.get("runtime-1")).last_effective_activity_at
    assert refreshed is not None
    assert await manager.sweep_idle(refreshed + timedelta(seconds=599)) == 0
    assert (await repository.get("runtime-1")).actual_state is RuntimeState.READY_UNBOUND
    assert await manager.sweep_idle(refreshed + timedelta(seconds=600)) == 1
    assert (await repository.get("runtime-1")).actual_state is RuntimeState.STOPPED


@pytest.mark.asyncio
async def test_default_credential_ttl_is_1800_seconds():
    """Credential TTL defaults to 1800s (not the old 300s)."""
    import inspect
    sig = inspect.signature(HostedCredentialFileAuthority.__init__)
    assert sig.parameters["ttl_seconds"].default == 1800


@pytest.mark.asyncio
async def test_default_renew_before_is_300_seconds():
    """Renewal threshold defaults to 300s (not the old 90s)."""
    from modules.runtime_manager.hosted_mcp_authority import HostedCredentialFileAuthority
    import inspect
    sig = inspect.signature(HostedCredentialFileAuthority.renew_active)
    assert sig.parameters["renew_before_seconds"].default == 300


@pytest.mark.asyncio
async def test_explicit_revoke_revokes_current_jti_immediately(tmp_path):
    """Explicit revoke() must revoke binding AND current credential JTI immediately."""
    binding = active_binding()
    security = Security()
    keys = KeyStore(generate_signing_key(kid="revoke-key", lifetime_seconds=3600))
    signer = HostedCredentialSigner(keys)
    authority = HostedCredentialFileAuthority(
        signer, security, Bindings(binding), tmp_path, ttl_seconds=1800,
    )
    first = await authority.issue(
        binding, scopes=("game:read",), allowed_tools=("observe",), jti="jti-revoke-test",
    )
    path = tmp_path / "runtime-a" / "generation-1" / "mcp-credential"
    first_token = path.read_text()

    # Explicit revoke immediately.
    await authority.revoke(binding.binding_id, binding.binding_version, binding.lease_epoch)

    # Binding revoked.
    assert security.revoked == [("binding-a", 2, 1)]
    # File deleted.
    assert not path.exists()
    # Token should be rejected by verifier (binding revoked).
    revocations = RevocationStore()
    revocations.revoke_binding(binding.binding_id, binding.binding_version, binding.lease_epoch)
    verifier = HostedCredentialVerifier(keys, revocations)
    with pytest.raises(ValueError, match="revoked"):
        await verifier.verify(first_token)
