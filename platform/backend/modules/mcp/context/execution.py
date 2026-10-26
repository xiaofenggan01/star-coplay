"""Composition-neutral Skill execution port declarations."""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from modules.mcp.contracts.models import ExecutionContext
from modules.mcp.skills.models import ResolvedSkill, ResolvedSkillCatalog
from modules.mcp.skills.runtime_models import (
    SkillCancelResult, SkillExecutionAuthorization, SkillExecutionHandle,
    SkillResult,
)


class SkillExecutionPort(Protocol):
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

