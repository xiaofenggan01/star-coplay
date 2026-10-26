"""Manager-owned Binding projection validation injected into GatewayRouter."""
from __future__ import annotations

from typing import Optional, Protocol

from modules.gateway.runtime_contracts import GameSessionSnapshot
from modules.mcp.contracts.models import ExecutionContext
from modules.mcp.core.errors import McpError
from modules.runtime_manager.domain import BindingState, RuntimeBinding


class AsyncBindingProjectionReader(Protocol):
    async def get(self, binding_id: str) -> Optional[RuntimeBinding]: ...


class RuntimeBindingProjectionValidator:
    def __init__(self, bindings: AsyncBindingProjectionReader) -> None:
        self.bindings = bindings

    async def validate(self, context: ExecutionContext, snapshot: GameSessionSnapshot) -> None:
        binding = await self.bindings.get(context.binding_ref)
        if binding is None or binding.status is not BindingState.ACTIVE:
            raise McpError("binding_unavailable", "binding is not active")
        expected = (
            binding.runtime_id,
            binding.generation,
            binding.binding_id,
            binding.binding_version,
            binding.lease_epoch,
            binding.game_session_id,
            binding.game_instance_nonce,
            binding.capability_digest,
            binding.user_id,
        )
        actual = (
            context.runtime_ref,
            context.generation,
            context.binding_ref,
            context.binding_version,
            context.lease_epoch,
            context.game_session_ref,
            context.game_instance_nonce,
            context.capability_digest,
            context.principal_id,
        )
        if actual != expected:
            raise McpError("binding_unavailable", "execution context does not match current Binding")
        if (
            snapshot.game_session_id != context.game_session_ref
            or snapshot.game_instance_nonce != context.game_instance_nonce
            or snapshot.capability_digest != context.capability_digest
            or snapshot.owner_user_id != context.principal_id
        ):
            raise McpError("binding_unavailable", "execution context does not match current GameSession")
