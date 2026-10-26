"""One-boot authority for the ``local_direct`` profile.

The caller supplies only the opaque boot credential.  Every identity and fence
is copied from launcher-created material, never from initialize metadata.
"""
from __future__ import annotations

import hmac
import contextvars
import secrets
import time
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from modules.mcp.contracts.models import ContextHandle, ContextStatus, ExecutionContext
from modules.mcp.core.errors import McpError
from common.observability import new_log_id


_PROTECTED_METADATA = frozenset({
    "profile", "principal_id", "runtime_ref", "runtime_id", "generation",
    "binding_ref", "binding_id", "binding_version", "game_session_ref",
    "game_session_id", "game_instance_nonce", "lease_epoch",
    "capability_digest", "allowed_tools", "scopes", "endpoint", "user_id",
    "agentee_id",
})


class EphemeralSecret:
    """A single mutable secret cell that can be irreversibly destroyed."""

    def __init__(self, value: str):
        if not value:
            raise ValueError("ephemeral secret cannot be empty")
        self._bytes = bytearray(value.encode("utf-8"))
        self._destroyed = False

    @classmethod
    def generate(cls) -> "EphemeralSecret":
        return cls(secrets.token_urlsafe(48))

    @property
    def destroyed(self) -> bool:
        return self._destroyed

    def reveal(self) -> str:
        if self._destroyed:
            raise McpError("context_expired", "local boot secret has been destroyed")
        return self._bytes.decode("utf-8")

    def matches(self, candidate: str) -> bool:
        if self._destroyed or not isinstance(candidate, str):
            return False
        return hmac.compare_digest(bytes(self._bytes), candidate.encode("utf-8"))

    def destroy(self) -> None:
        for index in range(len(self._bytes)):
            self._bytes[index] = 0
        self._bytes.clear()
        self._destroyed = True

    def __repr__(self) -> str:
        return "EphemeralSecret(<destroyed>)" if self._destroyed else "EphemeralSecret(<redacted>)"


@dataclass(frozen=True)
class LocalBootMaterial:
    secret: EphemeralSecret = field(repr=False)
    context_ref: str
    principal_ref: str
    runtime_ref: str
    generation: int
    binding_ref: str
    binding_version: int
    game_session_ref: str
    game_instance_nonce: str
    lease_epoch: int
    scopes: frozenset[str]
    allowed_tools: frozenset[str]
    tool_capabilities: Mapping[str, str]
    capability_digest: str
    issued_at: float
    expires_at: float

    def __post_init__(self) -> None:
        capabilities = dict(self.tool_capabilities)
        if not isinstance(self.secret, EphemeralSecret) or not self.capability_digest or not capabilities:
            raise ValueError("local boot material is incomplete")
        if self.allowed_tools != frozenset(capabilities):
            raise ValueError("allowed_tools must match tool_capabilities")
        if any(tool_name != capability for tool_name, capability in capabilities.items()):
            raise ValueError("local tool capabilities must use canonical identity")
        if self.generation < 1 or self.binding_version < 1 or self.lease_epoch < 1:
            raise ValueError("local fencing versions must be positive")
        if self.expires_at <= self.issued_at:
            raise ValueError("local boot material expiry must follow issuance")
        object.__setattr__(self, "tool_capabilities", MappingProxyType(capabilities))

    @classmethod
    def create(
        cls,
        *,
        capability_digest: str,
        tool_capabilities: Mapping[str, str],
        scopes: frozenset[str],
        lifetime_seconds: float = 300.0,
        generation: int = 1,
        now: float | None = None,
    ) -> "LocalBootMaterial":
        issued = time.time() if now is None else now
        boot = uuid.uuid4().hex
        return cls(
            secret=EphemeralSecret.generate(),
            context_ref=f"local-context:{boot}",
            principal_ref=f"local-principal:{boot}",
            runtime_ref=f"local-runtime:{boot}",
            generation=generation,
            binding_ref=f"local-binding:{boot}",
            binding_version=1,
            game_session_ref=f"local-game:{boot}",
            game_instance_nonce=secrets.token_urlsafe(32),
            lease_epoch=1,
            scopes=scopes,
            allowed_tools=frozenset(tool_capabilities),
            tool_capabilities=dict(tool_capabilities),
            capability_digest=capability_digest,
            issued_at=issued,
            expires_at=issued + lifetime_seconds,
        )


class LocalBootAuthority:
    """Consumes one boot secret once and invalidates all handles on shutdown."""

    def __init__(
        self,
        material: LocalBootMaterial,
        *,
        negotiated_capability_digest: Callable[[], str],
        clock: Callable[[], float] = time.time,
    ):
        self._material = material
        self._negotiated_digest = negotiated_capability_digest
        self._clock = clock
        self._handle: ContextHandle | None = None
        self._closed = False
        self._tool_trace: contextvars.ContextVar[Mapping[str, str] | None] = (
            contextvars.ContextVar("local_mcp_tool_trace", default=None)
        )

    async def begin_tool_call(self, handle: ContextHandle):
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "local context is no longer valid")
        trace = {"log_id": new_log_id()}
        return trace, self._tool_trace.set(trace)

    def end_tool_call(self, token) -> None:
        self._tool_trace.reset(token)

    def resume_tool_call(self, trace_context: Mapping[str, str]):
        return self._tool_trace.set(dict(trace_context))

    async def establish(self, connection_metadata: Mapping[str, str]) -> ContextHandle:
        if _PROTECTED_METADATA.intersection(connection_metadata):
            raise McpError("invalid_arguments", "authorization context cannot be supplied at initialize")
        if self._closed or self._material.secret.destroyed or self._clock() >= self._material.expires_at:
            raise McpError("context_expired", "local boot authorization has expired")
        supplied = connection_metadata.get("credential", "")
        if not self._material.secret.matches(supplied):
            raise McpError("capability_denied", "local boot credential is invalid")
        if self._handle is not None:
            raise McpError("conflict", "local boot credential has already been consumed")
        if not hmac.compare_digest(self._negotiated_digest(), self._material.capability_digest):
            raise McpError("binding_unavailable", "negotiated capabilities changed")
        self._handle = ContextHandle(self._material.context_ref)
        return self._handle

    async def authorize(self, handle: ContextHandle, tool_name: str, capability: str) -> ExecutionContext:
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "local context is no longer valid")
        if self._material.tool_capabilities.get(tool_name) != capability:
            raise McpError("capability_denied", "tool is not authorized")
        return ExecutionContext(
            profile="local_direct",
            principal_id=self._material.principal_ref,
            runtime_ref=self._material.runtime_ref,
            generation=self._material.generation,
            binding_ref=self._material.binding_ref,
            binding_version=self._material.binding_version,
            game_session_ref=self._material.game_session_ref,
            game_instance_nonce=self._material.game_instance_nonce,
            lease_epoch=self._material.lease_epoch,
            scopes=self._material.scopes,
            allowed_tools=self._material.allowed_tools,
            capability_digest=self._material.capability_digest,
            issued_at=self._material.issued_at,
            expires_at=self._material.expires_at,
            trace_context=self._tool_trace.get() or {},
        )

    async def execution_context(self, handle: ContextHandle) -> ExecutionContext:
        """Additive session-bound context read for exposure composition."""
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "local context is no longer valid")
        m = self._material
        return ExecutionContext(
            profile="local_direct", principal_id=m.principal_ref,
            runtime_ref=m.runtime_ref, generation=m.generation,
            binding_ref=m.binding_ref, binding_version=m.binding_version,
            game_session_ref=m.game_session_ref,
            game_instance_nonce=m.game_instance_nonce, lease_epoch=m.lease_epoch,
            scopes=m.scopes, allowed_tools=m.allowed_tools,
            capability_digest=m.capability_digest, issued_at=m.issued_at,
            expires_at=m.expires_at,
            trace_context=self._tool_trace.get() or {},
        )

    async def revalidate(self, handle: ContextHandle) -> ContextStatus:
        if self._closed or self._material.secret.destroyed or self._handle != handle:
            return ContextStatus(False, "closed")
        if self._clock() >= self._material.expires_at:
            return ContextStatus(False, "expired")
        if not hmac.compare_digest(self._negotiated_digest(), self._material.capability_digest):
            return ContextStatus(False, "capability-digest-changed")
        return ContextStatus(True)

    async def close(self, handle: ContextHandle) -> None:
        if self._handle == handle:
            self._closed = True

    def invalidate_boot(self) -> None:
        """Launcher shutdown/restart hook; all material becomes unusable."""
        self._closed = True
        self._material.secret.destroy()
