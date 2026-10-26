"""Adapter from the neutral :class:`SkillRuntime` to the MCP execution port."""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from modules.mcp.contracts.models import ExecutionContext
from modules.mcp.skills.models import ResolvedSkill, ResolvedSkillCatalog
from modules.mcp.skills.runtime import SkillRuntime
from modules.mcp.skills.runtime_models import (
    SkillCancelResult, SkillExecutionAuthorization, SkillExecutionHandle, SkillResult,
)


class SkillRuntimeAdapter:
    """Thin protocol adapter; it owns no persistent request correlation state."""

    def __init__(self, runtime: SkillRuntime):
        self._runtime = runtime

    async def start(
        self, catalog: ResolvedSkillCatalog, resolved_skill: ResolvedSkill,
        authorization: SkillExecutionAuthorization, arguments: Mapping,
        execution_context: ExecutionContext, *, tool_call_id: str,
        request_id: str | None,
    ) -> SkillExecutionHandle:
        # SkillRuntime intentionally authorizes one exact target catalog.  MCP
        # projection resolves the complete mounted catalog, so narrow it at
        # this adapter boundary while preserving every release/policy pin.
        target_catalog = replace(catalog, skills=(resolved_skill,))
        # ``request_id`` is transport-owned call metadata, not session-owned
        # authority material.  Bind it exactly once at this adapter boundary
        # so Skill audit and every derived Capability context share the same
        # correlation value.  Removing a stale value when metadata is absent
        # also prevents a reusable session context from attributing a later
        # call to an earlier request.
        trace_context = dict(execution_context.trace_context)
        if request_id is None:
            trace_context.pop("request_id", None)
        else:
            trace_context["request_id"] = request_id
        correlated_context = replace(
            execution_context,
            trace_context=trace_context,
        )
        return await self._runtime.start(
            target_catalog, resolved_skill, authorization, arguments, correlated_context,
            tool_call_id=tool_call_id,
        )

    async def await_result(self, handle: SkillExecutionHandle, *, deadline: float | None = None) -> SkillResult:
        return await self._runtime.await_result(handle, deadline=deadline)

    async def cancel(self, handle: SkillExecutionHandle, *, reason: str) -> SkillCancelResult:
        return await self._runtime.cancel(handle, reason=reason)
