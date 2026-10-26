"""Complete hosted authority claims and revocation boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Protocol, Tuple

from modules.runtime_manager.domain import RuntimeBinding


@dataclass(frozen=True)
class AuthorityMaterial:
    credential_ref: str
    iss: str
    aud: str
    sub: str
    jti: str
    runtime_id: str
    generation: int
    binding_id: str
    binding_version: int
    lease_epoch: int
    game_session_id: str
    game_instance_nonce: str
    capability_digest: str
    user_id: str
    agentee_id: str
    scopes: Tuple[str, ...]
    allowed_tools: Tuple[str, ...]
    expires_at: datetime


class AuthorityMaterialIssuer(Protocol):
    def issue(
        self,
        binding: RuntimeBinding,
        scopes: Tuple[str, ...] = ("game:read", "game:write"),
        allowed_tools: Tuple[str, ...] = (),
        jti: str = "",
    ) -> AuthorityMaterial: ...
    def revoke(self, binding_id: str, through_binding_version: int, through_lease_epoch: int) -> None: ...


class InMemoryAuthorityMaterialIssuer:
    def __init__(self) -> None:
        self.material: Dict[str, AuthorityMaterial] = {}
        self.revocations: Dict[str, Tuple[int, int]] = {}

    def issue(
        self,
        binding: RuntimeBinding,
        scopes: Tuple[str, ...] = ("game:read", "game:write"),
        allowed_tools: Tuple[str, ...] = (),
        jti: str = "",
    ) -> AuthorityMaterial:
        required = (
            binding.game_session_id,
            binding.game_instance_nonce,
            binding.capability_digest,
        )
        if not all(required):
            raise ValueError("active authority requires complete GameSession fencing claims")
        stable_jti = jti or f"{binding.binding_id}:{binding.binding_version}:{binding.lease_epoch}"
        existing = self.material.get(binding.binding_id)
        if existing is not None and existing.jti == stable_jti:
            return existing
        material = AuthorityMaterial(
            credential_ref=f"authority:{binding.binding_id}:{binding.binding_version}:{binding.lease_epoch}",
            iss="starcoplay-runtime-manager",
            aud="starcoplay-mcp",
            sub=binding.runtime_id,
            jti=stable_jti,
            runtime_id=binding.runtime_id,
            generation=binding.generation,
            binding_id=binding.binding_id,
            binding_version=binding.binding_version,
            lease_epoch=binding.lease_epoch,
            game_session_id=binding.game_session_id or "",
            game_instance_nonce=binding.game_instance_nonce or "",
            capability_digest=binding.capability_digest or "",
            user_id=binding.user_id,
            agentee_id=binding.agentee_id,
            scopes=scopes,
            allowed_tools=allowed_tools,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.material[binding.binding_id] = material
        return material

    def revoke(self, binding_id: str, through_binding_version: int, through_lease_epoch: int) -> None:
        current = self.revocations.get(binding_id, (0, 0))
        self.revocations[binding_id] = (
            max(current[0], through_binding_version),
            max(current[1], through_lease_epoch),
        )
        self.material.pop(binding_id, None)

    def accepts(self, material: AuthorityMaterial, now: datetime | None = None) -> bool:
        revoked_version, revoked_epoch = self.revocations.get(material.binding_id, (0, 0))
        return (
            material.expires_at > (now or datetime.now(timezone.utc))
            and material.binding_version > revoked_version
            and material.lease_epoch >= revoked_epoch
        )
