from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Mapping

from modules.mcp.contracts.models import ContextHandle, ContextStatus, ExecutionContext
from modules.mcp.core.errors import McpError


class FakeContextAuthority:
    def __init__(self, grants: Mapping[str, ExecutionContext]):
        self._grants = dict(grants)
        self._handles: dict[str, ExecutionContext] = {}
        self._closed: set[str] = set()

    async def establish(self, connection_metadata: Mapping[str, str]) -> ContextHandle:
        credential = connection_metadata.get("credential", "")
        grant = self._grants.get(credential)
        if grant is None:
            raise McpError("capability_denied", "connection credential is invalid")
        handle = ContextHandle(str(uuid.uuid4()))
        self._handles[handle.value] = replace(grant)
        return handle

    async def authorize(self, handle: ContextHandle, tool_name: str, capability: str) -> ExecutionContext:
        status = await self.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "context is no longer valid")
        context = self._handles[handle.value]
        if tool_name not in context.allowed_tools or capability != tool_name:
            raise McpError("capability_denied", "tool is not authorized")
        return context

    async def revalidate(self, handle: ContextHandle) -> ContextStatus:
        context = self._handles.get(handle.value)
        if context is None or handle.value in self._closed:
            return ContextStatus(False, "closed")
        if context.expires_at is not None and context.expires_at <= time.time():
            return ContextStatus(False, "expired")
        return ContextStatus(True)

    async def close(self, handle: ContextHandle) -> None:
        self._closed.add(handle.value)

    def replace_grant(self, handle: ContextHandle, context: ExecutionContext) -> None:
        self._handles[handle.value] = context
