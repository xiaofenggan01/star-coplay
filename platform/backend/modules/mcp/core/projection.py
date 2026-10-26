"""Dynamic, session-pinned projection of Skills into MCP tool records."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from modules.mcp.contracts.models import ContextHandle
from modules.mcp.contracts.projection import McpExposurePort, McpSkillExecutionPort, ExposureFacts
from modules.mcp.core.errors import McpError
from modules.mcp.skills.models import ResolvedSkill, ResolvedSkillCatalog, SkillRef, RiskDecision
from modules.mcp.skills.registry import SkillRegistry
from modules.mcp.skills.runtime_models import (
    SkillCancelResult, SkillExecutionAuthorization, SkillExecutionHandle,
    SkillExecutionState, SkillResult,
)
from modules.mcp.capabilities.contracts import JsonSchemaValidationError, validate_json_schema


log = logging.getLogger("starcoplay.mcp.projection")


class ProjectionError(Exception):
    def __init__(self, code: str, safe_message: str | None = None):
        self.code = code
        self.safe_message = safe_message or code
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class McpToolProjection:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    annotations: Mapping[str, Any]
    skill_ref: SkillRef | None
    catalog_digest: str
    mount_revision: str
    policy_revision: str
    capability_digest: str
    binding_ref: str
    binding_version: int
    game_session_ref: str


@dataclass(frozen=True)
class McpExposureSnapshot:
    catalog: ResolvedSkillCatalog
    tools: tuple[McpToolProjection, ...]
    by_name: Mapping[str, McpToolProjection]
    digest: str


@dataclass(frozen=True)
class McpCallMetadata:
    tool_call_id: str
    request_id: str | None = None


@dataclass(frozen=True)
class BackgroundJobCompletion:
    runtime_ref: str
    generation: int
    principal_id: str
    binding_ref: str
    game_session_ref: str
    job_id: str
    skill_ref: SkillRef
    state: SkillExecutionState
    output: Mapping[str, Any]
    error_code: str | None
    partial_output: Mapping[str, Any]
    side_effects_may_have_occurred: bool


@dataclass
class _BackgroundJob:
    owner_handle: str
    binding_ref: str
    runtime_ref: str
    generation: int
    principal_id: str
    game_session_ref: str
    execution: SkillExecutionHandle
    task: asyncio.Task[None] | None = None
    result: SkillResult | None = None


_JOB_STATUS_REF = SkillRef("starcoplay.runtime.job_status", "1.0.0")
_JOB_CANCEL_REF = SkillRef("starcoplay.runtime.job_cancel", "1.0.0")
_JOB_RECEIPT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string"},
        "skill_id": {"type": "string"},
        "status": {"type": "string", "enum": ["running"]},
    },
    "required": ["job_id", "skill_id", "status"],
    "additionalProperties": False,
}
_JOB_CONTROL_INPUT: Mapping[str, Any] = {
    "type": "object",
    "properties": {"job_id": {"type": "string", "minLength": 1}},
    "required": ["job_id"],
    "additionalProperties": False,
}
_JOB_STATUS_OUTPUT: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string"},
        "skill_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [
                "running", "succeeded", "failed", "partial", "cancelled",
                "timed_out", "unknown",
            ],
        },
        "output": {"type": "object"},
        "partial_output": {"type": "object"},
        "error_code": {"type": ["string", "null"]},
        "side_effects_may_have_occurred": {"type": "boolean"},
    },
    "required": [
        "job_id", "skill_id", "status", "output", "partial_output",
        "error_code", "side_effects_may_have_occurred",
    ],
    "additionalProperties": False,
}
_MAX_TERMINAL_JOBS = 128


def _jsonable(v: Any) -> Any:
    if isinstance(v, Mapping):
        if any(not isinstance(k, str) for k in v):
            raise ProjectionError("invalid_arguments")
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _valid_json(value: Any, active: set[int] | None = None) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (tuple, set, frozenset, bytes)):
        return False
    if isinstance(value, Mapping):
        if active is None:
            active = set()
        if id(value) in active:
            return False
        active.add(id(value))
        try:
            return all(isinstance(k, str) and _valid_json(v, active) for k, v in value.items())
        finally:
            active.remove(id(value))
    if isinstance(value, list):
        if active is None:
            active = set()
        if id(value) in active:
            return False
        active.add(id(value))
        try:
            return all(_valid_json(v, active) for v in value)
        finally:
            active.remove(id(value))
    return False


def _digest(records: list[Mapping[str, Any]], catalog: ResolvedSkillCatalog) -> str:
    payload = {"tools": records, "pins": {
        "mount_revision": catalog.mount_revision, "policy_revision": catalog.policy_revision,
        "capability_digest": catalog.capability_digest, "binding_ref": catalog.binding_ref,
        "binding_version": catalog.binding_version, "game_session_ref": catalog.game_session_ref,
    }}
    raw = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class McpProjection:
    def __init__(self, registry: SkillRegistry, exposure_port: McpExposurePort,
                 execution_port: McpSkillExecutionPort, authority: Any | None = None,
                 implementation_digest_for: Callable[[SkillRef], str] | None = None,
                 background_completion_hook: Callable[
                     [BackgroundJobCompletion], Awaitable[None]
                 ] | None = None):
        self._registry, self._exposure, self._execution, self._authority = registry, exposure_port, execution_port, authority
        self._snapshots: dict[str, McpExposureSnapshot] = {}
        self._handles: dict[tuple[str, str], SkillExecutionHandle] = {}
        self._pending_cancel: dict[tuple[str, str], str] = {}
        self._closed: set[str] = set()
        self._active_start: set[tuple[str, str]] = set()
        self._active_calls: set[tuple[str, str]] = set()
        self._background_jobs: dict[str, _BackgroundJob] = {}
        self._active_job_by_binding: dict[str, str] = {}
        self._background_starting_by_binding: set[str] = set()
        self._implementation_digest_for = implementation_digest_for
        self._background_completion_hook = background_completion_hook
        self._lock = asyncio.Lock()

    async def _valid(self, handle: ContextHandle) -> None:
        if self._authority is not None:
            status = await self._authority.revalidate(handle)
            if not status.valid:
                raise ProjectionError("context_expired")

    @staticmethod
    def _pins(c: ResolvedSkillCatalog) -> tuple[Any, ...]:
        return (c.mount_revision, c.policy_revision, c.capability_digest, c.binding_ref, c.binding_version, c.game_session_ref)

    def _project(
        self,
        catalog: ResolvedSkillCatalog,
        *,
        allowed_tools: frozenset[str] | None = None,
    ) -> McpExposureSnapshot:
        tools: list[McpToolProjection] = []
        records: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for skill in catalog.skills:
            if (not skill.available or skill.reasons or skill.effective_policy.requires_confirmation
                    or any(v is RiskDecision.DENY for v in skill.effective_policy.decisions.values())):
                continue
            m = skill.package.manifest
            name = f"skill:{m.skill_id}@{m.version}"
            if allowed_tools is not None and name not in allowed_tools:
                continue
            if name in seen:
                raise ProjectionError("conflict")
            seen.add(name)
            execution_mode = getattr(m, "execution_mode", "synchronous")
            ann = {"risk": sorted(m.risk), "cancellable": m.cancellable, "side_effects": bool(m.risk), "execution_mode": execution_mode}
            ref = SkillRef(m.skill_id, m.version)
            output_schema = _JOB_RECEIPT_SCHEMA if execution_mode == "background" else m.output_schema
            t = McpToolProjection(name, m.description, m.input_schema, output_schema, ann, ref, "", catalog.mount_revision, catalog.policy_revision, catalog.capability_digest, catalog.binding_ref, catalog.binding_version, catalog.game_session_ref)
            tools.append(t)
            records.append({"name": name, "skill_id": m.skill_id, "version": m.version, "input_schema": m.input_schema, "output_schema": output_schema, "annotations": ann})
        if any(t.annotations.get("execution_mode") == "background" for t in tools):
            controls = (
                ("job_status", "Read a background game job status.", _JOB_STATUS_REF),
                ("job_cancel", "Cancel a background game job owned by this session.", _JOB_CANCEL_REF),
            )
            for name, description, ref in controls:
                ann = {"risk": [], "cancellable": False, "side_effects": name == "job_cancel", "execution_mode": "synchronous"}
                t = McpToolProjection(name, description, _JOB_CONTROL_INPUT, _JOB_STATUS_OUTPUT, ann, None, "", catalog.mount_revision, catalog.policy_revision, catalog.capability_digest, catalog.binding_ref, catalog.binding_version, catalog.game_session_ref)
                tools.append(t)
                records.append({"name": name, "skill_id": ref.skill_id, "version": ref.version, "input_schema": _JOB_CONTROL_INPUT, "output_schema": _JOB_STATUS_OUTPUT, "annotations": ann})
        digest = _digest(records, catalog)
        tools = [McpToolProjection(**{**t.__dict__, "catalog_digest": digest}) for t in tools]
        return McpExposureSnapshot(catalog, tuple(tools), MappingProxyType({t.name: t for t in tools}), digest)

    async def list_tools(self, handle: ContextHandle) -> tuple[McpToolProjection, ...]:
        async with self._lock:
            if handle.value in self._closed:
                raise ProjectionError("context_expired")
            prior = self._snapshots.get(handle.value)
            if prior is not None:
                return prior.tools
            await self._valid(handle)
            facts = await self._exposure.resolve_exposure(handle)
            catalog = self._registry.resolve_catalog(facts.exposure_context, facts.capability_snapshot)
            # Hosted credentials grant final MCP tool identities.  Registry
            # availability is necessary but not sufficient: a mounted Skill
            # must not enter the Agent surface unless the credential grants
            # its projected, version-pinned tool name.  Local Direct retains
            # its boot-material authority semantics.
            allowed_tools = (
                facts.execution_context.allowed_tools
                if facts.execution_context.profile == "hosted"
                else None
            )
            snap = self._project(catalog, allowed_tools=allowed_tools)
            self._snapshots[handle.value] = snap
            return snap.tools

    async def call_tool(self, handle: ContextHandle, name: str, arguments: Mapping[str, Any], *, metadata: McpCallMetadata) -> SkillResult:
        if not isinstance(arguments, Mapping) or not _valid_json(arguments):
            raise ProjectionError("invalid_arguments")
        async with self._lock:
            if handle.value in self._closed:
                raise ProjectionError("context_expired")
            snap = self._snapshots.get(handle.value)
        if snap is None:
            raise ProjectionError("capability_denied")
        tool = snap.by_name.get(name)
        if tool is None:
            raise ProjectionError("capability_denied")
        facts = await self._exposure.revalidate_exposure(handle)
        if facts.execution_context.profile == "hosted":
            if (
                tool.skill_ref is not None
                and name not in facts.execution_context.allowed_tools
            ):
                raise ProjectionError("capability_denied")
            if tool.skill_ref is None and not any(
                candidate.skill_ref is not None
                and candidate.annotations.get("execution_mode") == "background"
                and candidate.name in facts.execution_context.allowed_tools
                for candidate in snap.tools
            ):
                raise ProjectionError("capability_denied")
        current_pins = (facts.exposure_context.mount_revision, facts.exposure_context.policy_revision, facts.capability_snapshot.capability_digest, facts.exposure_context.binding_ref, facts.exposure_context.binding_version, facts.exposure_context.game_session_ref)
        if current_pins != self._pins(snap.catalog):
            raise ProjectionError("conflict")
        if tool.skill_ref is None:
            return await self._call_job_control(handle, name, arguments)
        skill = next((s for s in snap.catalog.skills if SkillRef(s.package.manifest.skill_id, s.package.manifest.version) == tool.skill_ref), None)
        if skill is None or not skill.available or skill.reasons:
            raise ProjectionError("capability_denied")
        protected = {"profile", "principal_id", "binding_ref", "binding_version", "game_session_ref", "capability_digest", "runtime_ref", "user_id"}
        if protected.intersection(arguments):
            raise ProjectionError("invalid_arguments")
        try:
            validate_json_schema(tool.input_schema, arguments)
        except (JsonSchemaValidationError, TypeError, ValueError) as exc:
            raise ProjectionError("invalid_arguments") from exc
        try:
            implementation_digest = (self._implementation_digest_for(tool.skill_ref)
                                     if self._implementation_digest_for is not None
                                     else skill.package.manifest_digest)
        except Exception as exc:
            raise ProjectionError("conflict") from exc
        if (self._implementation_digest_for is not None
                and (not isinstance(implementation_digest, str)
                     or not implementation_digest.startswith("sha256:"))):
            raise ProjectionError("conflict")
        auth = SkillExecutionAuthorization(skill_ref=tool.skill_ref, manifest_digest=skill.package.manifest_digest, implementation_digest=implementation_digest, mount_revision=snap.catalog.mount_revision, policy_revision=snap.catalog.policy_revision, capability_digest=snap.catalog.capability_digest, binding_ref=snap.catalog.binding_ref, binding_version=snap.catalog.binding_version, game_session_ref=snap.catalog.game_session_ref, effective_policy=skill.effective_policy, dependency_bindings=skill.dependency_bindings)
        key = (handle.value, metadata.tool_call_id)
        is_background = getattr(
            skill.package.manifest, "execution_mode", "synchronous",
        ) == "background"
        async with self._lock:
            if handle.value in self._closed:
                raise ProjectionError("context_expired")
            if is_background:
                binding_ref = snap.catalog.binding_ref
                if (
                    binding_ref in self._background_starting_by_binding
                    or binding_ref in self._active_job_by_binding
                ):
                    raise ProjectionError("conflict")
                self._background_starting_by_binding.add(binding_ref)
            self._active_calls.add(key)
            self._active_start.add(key)
        try:
            handle_obj = await self._execution.start(snap.catalog, skill, auth, arguments, facts.execution_context, tool_call_id=metadata.tool_call_id, request_id=metadata.request_id)
        except Exception:
            async with self._lock:
                self._active_start.discard(key)
                self._active_calls.discard(key)
                if is_background:
                    self._background_starting_by_binding.discard(
                        snap.catalog.binding_ref,
                    )
            raise
        else:
            async with self._lock:
                self._active_start.discard(key)
        async with self._lock:
            was_closed = handle.value in self._closed
            if not was_closed:
                self._handles[key] = handle_obj
                pending_reason = self._pending_cancel.pop(key, None)
            else:
                pending_reason = "session_closed"
        if was_closed:
            try:
                await self._execution.cancel(handle_obj, reason="session_closed")
            except Exception:
                pass
            async with self._lock:
                self._active_calls.discard(key)
                if is_background:
                    self._background_starting_by_binding.discard(
                        snap.catalog.binding_ref,
                    )
            raise ProjectionError("context_expired")
        if pending_reason is not None:
            try:
                await self._execution.cancel(handle_obj, reason=pending_reason)
            except Exception:
                async with self._lock:
                    self._handles.pop(key, None)
                    self._active_calls.discard(key)
                    if is_background:
                        self._background_starting_by_binding.discard(
                            snap.catalog.binding_ref,
                        )
                raise
        if is_background:
            async with self._lock:
                self._background_starting_by_binding.discard(
                    snap.catalog.binding_ref,
                )
                active_job = self._active_job_by_binding.get(snap.catalog.binding_ref)
                if (
                    active_job is not None
                    or handle_obj.skill_execution_id in self._background_jobs
                ):
                    self._handles.pop(key, None)
                    self._active_calls.discard(key)
                    conflict = True
                else:
                    conflict = False
                    job = _BackgroundJob(
                        handle.value,
                        snap.catalog.binding_ref,
                        facts.execution_context.runtime_ref,
                        facts.execution_context.generation,
                        facts.execution_context.principal_id,
                        facts.execution_context.game_session_ref,
                        handle_obj,
                    )
                    self._background_jobs[handle_obj.skill_execution_id] = job
                    self._active_job_by_binding[snap.catalog.binding_ref] = handle_obj.skill_execution_id
                    self._handles.pop(key, None)
                    self._active_calls.discard(key)
                    job.task = asyncio.create_task(
                        self._settle_background_job(job),
                        name=f"mcp-background-skill:{handle_obj.skill_execution_id}",
                    )
            if conflict:
                await self._execution.cancel(handle_obj, reason="background_job_conflict")
                raise ProjectionError("conflict")
            return SkillResult(
                handle_obj.skill_execution_id,
                handle_obj.skill_ref,
                SkillExecutionState.SUCCEEDED,
                {"job_id": handle_obj.skill_execution_id, "skill_id": handle_obj.skill_ref.skill_id, "status": "running"},
                None, {}, (), False,
            )
        try:
            return await self._execution.await_result(handle_obj, deadline=handle_obj.deadline_at)
        finally:
            async with self._lock:
                self._handles.pop((handle.value, metadata.tool_call_id), None)
                self._active_calls.discard(key)

    async def _settle_background_job(self, job: _BackgroundJob) -> None:
        try:
            result = await self._execution.await_result(
                job.execution, deadline=job.execution.deadline_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result = SkillResult(
                job.execution.skill_execution_id,
                job.execution.skill_ref,
                SkillExecutionState.FAILED,
                {},
                "internal",
                {},
                (),
                False,
            )
        async with self._lock:
            job.result = result
            if (
                self._active_job_by_binding.get(job.binding_ref)
                == job.execution.skill_execution_id
            ):
                self._active_job_by_binding.pop(job.binding_ref, None)
            self._prune_terminal_jobs_locked()
        if self._background_completion_hook is not None:
            completion = BackgroundJobCompletion(
                runtime_ref=job.runtime_ref,
                generation=job.generation,
                principal_id=job.principal_id,
                binding_ref=job.binding_ref,
                game_session_ref=job.game_session_ref,
                job_id=job.execution.skill_execution_id,
                skill_ref=job.execution.skill_ref,
                state=result.state,
                output=result.output,
                error_code=result.error_code,
                partial_output=result.partial_output,
                side_effects_may_have_occurred=(
                    result.side_effects_may_have_occurred
                ),
            )
            try:
                await self._background_completion_hook(completion)
            except Exception:
                log.exception(
                    "background skill completion hook failed",
                    extra={
                        "event": "mcp.background_job.completion_hook_failed",
                        "runtime_id": job.runtime_ref,
                        "generation": job.generation,
                        "job_id": job.execution.skill_execution_id,
                        "skill_id": job.execution.skill_ref.skill_id,
                        "outcome": "failed",
                    },
                )

    def _prune_terminal_jobs_locked(self) -> None:
        terminal_ids = [
            job_id for job_id, job in self._background_jobs.items()
            if job.result is not None
        ]
        for job_id in terminal_ids[:-_MAX_TERMINAL_JOBS]:
            self._background_jobs.pop(job_id, None)

    async def _call_job_control(
        self, handle: ContextHandle, name: str, arguments: Mapping[str, Any],
    ) -> SkillResult:
        try:
            validate_json_schema(_JOB_CONTROL_INPUT, arguments)
        except (JsonSchemaValidationError, TypeError, ValueError) as exc:
            raise ProjectionError("invalid_arguments") from exc
        job_id = str(arguments["job_id"])
        async with self._lock:
            job = self._background_jobs.get(job_id)
            if job is None or job.owner_handle != handle.value:
                raise ProjectionError("capability_denied")
        if name == "job_cancel" and job.result is None:
            await self._execution.cancel(job.execution, reason="player_requested")
        result = job.result
        status = (
            result.state.value.lower() if result is not None else "running"
        )
        error_code = result.error_code if result is not None else None
        output = dict(result.output) if result is not None else {}
        partial_output = dict(result.partial_output) if result is not None else {}
        side_effects_may_have_occurred = (
            result.side_effects_may_have_occurred if result is not None else False
        )
        ref = _JOB_CANCEL_REF if name == "job_cancel" else _JOB_STATUS_REF
        return SkillResult(
            job_id, ref, SkillExecutionState.SUCCEEDED,
            {"job_id": job_id, "skill_id": job.execution.skill_ref.skill_id,
             "status": status, "output": output,
             "partial_output": partial_output, "error_code": error_code,
             "side_effects_may_have_occurred": side_effects_may_have_occurred},
            None, {}, (), False,
        )

    async def cancel_active(
        self,
        *,
        runtime_id: str,
        generation: int,
        game_session_id: str,
        reason: str,
    ) -> str | None:
        """Cancel the active background job owned by one trusted game session."""
        async with self._lock:
            job = next((
                candidate
                for candidate in self._background_jobs.values()
                if candidate.result is None
                and candidate.runtime_ref == runtime_id
                and candidate.generation == generation
                and candidate.game_session_ref == game_session_id
            ), None)
        if job is None:
            return None
        await self._execution.cancel(job.execution, reason=reason)
        return job.execution.skill_execution_id

    async def cancel(self, handle: ContextHandle, metadata: McpCallMetadata, *, reason: str = "cancelled") -> SkillCancelResult:
        async with self._lock:
            execution = self._handles.get((handle.value, metadata.tool_call_id))
        if execution is None:
            async with self._lock:
                if (handle.value, metadata.tool_call_id) not in self._active_calls:
                    raise ProjectionError("capability_denied")
                self._pending_cancel[(handle.value, metadata.tool_call_id)] = reason
            return SkillCancelResult("pending", True, SkillExecutionState.CANCELLED, "cancelled")
        return await self._execution.cancel(execution, reason=reason)

    async def close(self, handle: ContextHandle) -> None:
        async with self._lock:
            self._closed.add(handle.value)
            self._snapshots.pop(handle.value, None)
            outstanding = [execution for (session, _), execution in self._handles.items() if session == handle.value]
            background = [
                job for job in self._background_jobs.values()
                if job.owner_handle == handle.value and job.result is None
            ]
            owned_job_ids = [
                job_id for job_id, job in self._background_jobs.items()
                if job.owner_handle == handle.value
            ]
            for job_id in owned_job_ids:
                job = self._background_jobs.pop(job_id)
                if self._active_job_by_binding.get(job.binding_ref) == job_id:
                    self._active_job_by_binding.pop(job.binding_ref, None)
            for key in tuple(self._handles):
                if key[0] == handle.value:
                    self._handles.pop(key, None)
            for key in tuple(self._pending_cancel):
                if key[0] == handle.value:
                    self._pending_cancel.pop(key, None)
            for key in tuple(self._active_calls):
                if key[0] == handle.value:
                    self._active_calls.discard(key)
        for execution in outstanding:
            try:
                await self._execution.cancel(execution, reason="session_closed")
            except Exception:
                pass
        background_tasks: list[asyncio.Task[None]] = []
        for job in background:
            try:
                await self._execution.cancel(job.execution, reason="session_closed")
            except Exception:
                pass
            if job.task is not None and not job.task.done():
                job.task.cancel()
                background_tasks.append(job.task)
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await self._exposure.close(handle)
