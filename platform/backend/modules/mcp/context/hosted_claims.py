"""Hosted credential authority implementing the shared MCP ContextAuthority."""
from __future__ import annotations

import hmac
import contextvars
import time
import uuid
from datetime import datetime, timezone
from typing import Mapping, Optional, Protocol

from modules.mcp.contracts.models import ContextHandle, ContextStatus, ExecutionContext
from modules.mcp.core.errors import McpError
from modules.runtime_manager.authority import AuthorityMaterial
from modules.runtime_manager.domain import BindingState, RuntimeBinding
from common.observability import new_log_id

_PROTECTED = frozenset({
    "profile", "principal_id", "runtime_ref", "runtime_id", "generation",
    "binding_ref", "binding_id", "binding_version", "game_session_ref",
    "game_session_id", "game_instance_nonce", "lease_epoch", "capability_digest",
    "allowed_tools", "scopes", "user_id", "agentee_id", "jti",
})


class HostedCredentialVerificationPort(Protocol):
    async def verify(self, credential: str) -> AuthorityMaterial: ...
    async def is_revoked(self, material: AuthorityMaterial) -> bool: ...


class HostedBindingReader(Protocol):
    async def get(self, binding_id: str) -> Optional[RuntimeBinding]: ...


class HostedClaimsAuthority:
    def __init__(self, verifier: HostedCredentialVerificationPort, bindings: HostedBindingReader,
                 *, clock=time.time, turn_correlation_registry=None) -> None:
        self._verifier = verifier
        self._bindings = bindings
        self._clock = clock
        self._turn_correlation_registry = turn_correlation_registry
        self._claims: dict[str, AuthorityMaterial] = {}
        self._closed: set[str] = set()
        self._tool_trace: contextvars.ContextVar[Mapping[str, str] | None] = (
            contextvars.ContextVar("hosted_mcp_tool_trace", default=None)
        )

    def _correlation_for(self, material: AuthorityMaterial) -> dict[str, str]:
        active = (
            self._turn_correlation_registry.lookup(material.runtime_id, material.generation)
            if self._turn_correlation_registry is not None else None
        )
        if active is None:
            return {"log_id": new_log_id()}
        return {"log_id": active.log_id, "operation_id": active.operation_id}

    async def begin_tool_call(self, handle: ContextHandle):
        """Bind server-derived correlation for exactly one Hosted ToolCall."""
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "hosted credential is no longer valid")
        material = self._claims[handle.value]
        active = (
            self._turn_correlation_registry.lookup(material.runtime_id, material.generation)
            if self._turn_correlation_registry is not None else None
        )
        if active is not None and active.interrupted_reason is not None:
            raise McpError(
                "player_interrupted",
                "the player superseded this turn; no further tool calls are allowed",
            )
        trace = self._correlation_for(material)
        return trace, self._tool_trace.set(trace)

    def end_tool_call(self, token) -> None:
        self._tool_trace.reset(token)

    def resume_tool_call(self, trace_context: Mapping[str, str]):
        """Resume a server-owned ToolCall scope for its cancellation task."""
        return self._tool_trace.set(dict(trace_context))

    def _current_trace(self, material: AuthorityMaterial) -> Mapping[str, str]:
        return self._tool_trace.get() or self._correlation_for(material)

    async def establish(self, connection_metadata: Mapping[str, str]) -> ContextHandle:
        if _PROTECTED.intersection(connection_metadata):
            raise McpError("invalid_arguments", "hosted authority context cannot be client supplied")
        credential = connection_metadata.get("credential", "")
        if not credential:
            raise McpError("capability_denied", "hosted credential is required")
        try:
            material = await self._verifier.verify(credential)
        except McpError:
            raise
        except Exception as exc:
            raise McpError("capability_denied", "hosted credential is invalid") from exc
        await self._validate(material)
        handle = ContextHandle(f"hosted:{uuid.uuid4()}")
        self._claims[handle.value] = material
        return handle

    async def authorize(self, handle: ContextHandle, tool_name: str,
                        capability: str) -> ExecutionContext:
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "hosted credential is no longer valid")
        material = self._claims[handle.value]
        # Capability identity north of Router is canonical and equals the MCP
        # tool name. Wire/version mapping belongs to HostedSessionRoute.
        if tool_name not in material.allowed_tools or capability != tool_name:
            raise McpError("capability_denied", "tool is not authorized")
        return ExecutionContext(
            profile="hosted", principal_id=material.user_id,
            runtime_ref=material.runtime_id, generation=material.generation,
            binding_ref=material.binding_id, binding_version=material.binding_version,
            game_session_ref=material.game_session_id,
            game_instance_nonce=material.game_instance_nonce,
            lease_epoch=material.lease_epoch, scopes=frozenset(material.scopes),
            allowed_tools=frozenset(material.allowed_tools),
            capability_digest=material.capability_digest,
            issued_at=self._clock(), expires_at=material.expires_at.timestamp(),
            trace_context=self._current_trace(material),
        )

    async def execution_context(self, handle: ContextHandle) -> ExecutionContext:
        """Additive verified-context read for exposure composition."""
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "hosted credential is no longer valid")
        material = self._claims[handle.value]
        return ExecutionContext(
            profile="hosted", principal_id=material.user_id,
            runtime_ref=material.runtime_id, generation=material.generation,
            binding_ref=material.binding_id, binding_version=material.binding_version,
            game_session_ref=material.game_session_id,
            game_instance_nonce=material.game_instance_nonce,
            lease_epoch=material.lease_epoch, scopes=frozenset(material.scopes),
            allowed_tools=frozenset(material.allowed_tools),
            capability_digest=material.capability_digest,
            issued_at=self._clock(), expires_at=material.expires_at.timestamp(),
            trace_context=self._current_trace(material),
        )

    async def revalidate(self, handle: ContextHandle) -> ContextStatus:
        material = self._claims.get(handle.value)
        if material is None or handle.value in self._closed:
            return ContextStatus(False, "closed")
        try:
            await self._validate(material)
        except McpError as error:
            return ContextStatus(False, error.code)
        return ContextStatus(True)

    async def close(self, handle: ContextHandle) -> None:
        self._closed.add(handle.value)
        self._claims.pop(handle.value, None)

    async def _validate(self, material: AuthorityMaterial) -> RuntimeBinding:
        if (
            material.iss != "starcoplay-runtime-manager"
            or material.aud != "starcoplay-mcp"
            or not hmac.compare_digest(material.sub, material.runtime_id)
            or not all((material.jti, material.runtime_id, material.binding_id,
                        material.game_session_id, material.game_instance_nonce,
                        material.capability_digest, material.user_id, material.agentee_id))
            or material.generation < 1 or material.binding_version < 1 or material.lease_epoch < 1
            or material.expires_at.tzinfo is None
            or material.expires_at <= datetime.fromtimestamp(self._clock(), timezone.utc)
        ):
            raise McpError("context_expired", "hosted claims are invalid or expired")
        if await self._verifier.is_revoked(material):
            raise McpError("context_expired", "hosted credential has been revoked")
        binding = await self._bindings.get(material.binding_id)
        if binding is None or binding.status is not BindingState.ACTIVE:
            raise McpError("binding_unavailable", "binding is not active")
        actual = (
            material.runtime_id, material.generation, material.binding_id,
            material.binding_version, material.lease_epoch, material.game_session_id,
            material.game_instance_nonce, material.capability_digest,
            material.user_id, material.agentee_id,
        )
        expected = (
            binding.runtime_id, binding.generation, binding.binding_id,
            binding.binding_version, binding.lease_epoch, binding.game_session_id,
            binding.game_instance_nonce, binding.capability_digest,
            binding.user_id, binding.agentee_id,
        )
        if not all(hmac.compare_digest(str(a), str(b)) for a, b in zip(actual, expected)):
            raise McpError("binding_unavailable", "hosted claims do not match current Binding")
        if not material.scopes or not material.allowed_tools:
            raise McpError("capability_denied", "hosted capability grant is empty")
        return binding
