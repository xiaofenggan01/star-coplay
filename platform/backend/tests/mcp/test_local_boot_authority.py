from __future__ import annotations

from dataclasses import replace

import pytest

from modules.mcp.context import EphemeralSecret, LocalBootAuthority, LocalBootMaterial
from modules.mcp.core.errors import McpError


def material(**changes):
    value = LocalBootMaterial.create(
        capability_digest="digest-a", tool_capabilities={"say": "say"},
        scopes=frozenset({"game:write"}), now=100.0, lifetime_seconds=10.0,
    )
    return replace(value, **changes)


def credential(boot):
    return boot.secret.reveal()


@pytest.mark.asyncio
async def test_local_boot_material_is_authoritative_and_secret_is_one_time():
    now = [100.0]
    boot = material()
    authority = LocalBootAuthority(boot, negotiated_capability_digest=lambda: "digest-a", clock=lambda: now[0])
    secret = credential(boot)
    handle = await authority.establish({"credential": secret, "client": "test"})
    context = await authority.authorize(handle, "say", "say")
    assert context.profile == "local_direct"
    assert context.runtime_ref == boot.runtime_ref
    assert context.game_instance_nonce == boot.game_instance_nonce
    assert context.capability_digest == "digest-a"
    with pytest.raises(McpError) as caught:
        await authority.establish({"credential": secret})
    assert caught.value.code == "conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "runtime_ref", "game_session_ref", "game_instance_nonce", "lease_epoch",
    "capability_digest", "endpoint", "user_id",
])
async def test_initialize_cannot_inject_protected_context(field):
    boot = material()
    authority = LocalBootAuthority(boot, negotiated_capability_digest=lambda: "digest-a", clock=lambda: 100.0)
    with pytest.raises(McpError) as caught:
        await authority.establish({"credential": credential(boot), field: "attacker"})
    assert caught.value.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_wrong_secret_expiry_close_and_restart_invalidate_context():
    now = [100.0]
    boot = material()
    authority = LocalBootAuthority(boot, negotiated_capability_digest=lambda: "digest-a", clock=lambda: now[0])
    with pytest.raises(McpError) as caught:
        await authority.establish({"credential": "wrong-secret"})
    assert caught.value.code == "capability_denied"
    secret = credential(boot)
    assert secret not in str(caught.value)
    handle = await authority.establish({"credential": secret})
    await authority.close(handle)
    assert not (await authority.revalidate(handle)).valid

    restarted = LocalBootAuthority(material(secret=EphemeralSecret("new-secret")), negotiated_capability_digest=lambda: "digest-a", clock=lambda: now[0])
    with pytest.raises(McpError) as caught:
        await restarted.establish({"credential": secret})
    assert caught.value.code == "capability_denied"

    expired = LocalBootAuthority(boot, negotiated_capability_digest=lambda: "digest-a", clock=lambda: 111.0)
    with pytest.raises(McpError) as caught:
        await expired.establish({"credential": secret})
    assert caught.value.code == "context_expired"


@pytest.mark.asyncio
async def test_capability_digest_change_invalidates_handle():
    digest = ["digest-a"]
    boot = material()
    authority = LocalBootAuthority(boot, negotiated_capability_digest=lambda: digest[0], clock=lambda: 100.0)
    handle = await authority.establish({"credential": credential(boot)})
    digest[0] = "digest-b"
    status = await authority.revalidate(handle)
    assert status.valid is False and status.reason == "capability-digest-changed"
    with pytest.raises(McpError) as caught:
        await authority.authorize(handle, "say", "say")
    assert caught.value.code == "context_expired"


@pytest.mark.asyncio
async def test_authority_rejects_capability_substitution_for_allowed_tool():
    boot = material()
    authority = LocalBootAuthority(boot, negotiated_capability_digest=lambda: "digest-a", clock=lambda: 100.0)
    handle = await authority.establish({"credential": credential(boot)})
    with pytest.raises(McpError) as caught:
        await authority.authorize(handle, "say", "proto:tp")
    assert caught.value.code == "capability_denied"


def test_boot_capability_mapping_is_immutable_and_secret_is_redacted_from_repr():
    boot = material()
    with pytest.raises(TypeError):
        boot.tool_capabilities["say"] = "proto:tp"
    assert credential(boot) not in repr(boot)
