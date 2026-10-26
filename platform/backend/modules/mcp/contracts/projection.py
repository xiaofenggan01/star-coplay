"""Protocol-neutral exposure and Skill execution ports (M3.5).

The MCP adapter consumes these contracts; this module intentionally has no
transport or MCP DTO dependency.  Implementations own the ContextHandle and
must treat it as a linear, session-scoped capability lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from modules.mcp.contracts.models import ContextHandle, ExecutionContext
from modules.mcp.capabilities.models import CapabilitySnapshot
from modules.mcp.skills.models import ExposureContext, ResolvedSkill, ResolvedSkillCatalog
from modules.mcp.skills.runtime_models import (
    SkillCancelResult, SkillExecutionAuthorization, SkillExecutionHandle,
    SkillResult,
)


@dataclass(frozen=True)
class ExposureFacts:
    """Immutable, secret-free facts for one authenticated session."""

    execution_context: ExecutionContext
    exposure_context: ExposureContext
    capability_snapshot: CapabilitySnapshot
    facts_digest: str


class McpExposurePort(Protocol):
    async def resolve_exposure(self, handle: ContextHandle) -> ExposureFacts: ...
    async def revalidate_exposure(self, handle: ContextHandle) -> ExposureFacts: ...
    async def close(self, handle: ContextHandle) -> None: ...


class McpSkillExecutionPort(Protocol):
    async def start(
        self, catalog: ResolvedSkillCatalog, resolved_skill: ResolvedSkill,
        authorization: SkillExecutionAuthorization, arguments: Mapping[str, Any],
        execution_context: ExecutionContext, *, tool_call_id: str,
        request_id: str | None,
    ) -> SkillExecutionHandle: ...

    async def await_result(
        self, handle: SkillExecutionHandle, *, deadline: float | None = None,
    ) -> SkillResult: ...

    async def cancel(
        self, handle: SkillExecutionHandle, *, reason: str,
    ) -> SkillCancelResult: ...
