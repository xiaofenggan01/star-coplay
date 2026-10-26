"""Session-bound exposure facts for Local Direct and Hosted compositions."""
from __future__ import annotations

import hashlib
import inspect
import json
import asyncio
from dataclasses import fields, is_dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from modules.mcp.capabilities.models import (
    CapabilityContractCatalog, CapabilityManifestProvider, CapabilitySnapshot,
    ProtocolCapabilityEntry,
)
from modules.mcp.capabilities.snapshot import build_capability_snapshot
from modules.mcp.contracts.models import ContextHandle, ContextStatus, ExecutionContext
from modules.mcp.contracts.projection import ExposureFacts, McpExposurePort
from modules.mcp.core.errors import McpError
from modules.mcp.skills.models import ExposureContext, RiskDecision, SkillRef


_HOSTED_WRITE_RISK_LABELS = (
    "advances_time",
    "consumes_item",
    "consumes_energy",
    "spends_money",
    "changes_save",
)


class ExposureContextProvider(Protocol):
    async def get(self, context: ExecutionContext) -> ExposureContext: ...


def hosted_risk_policy(context: ExecutionContext) -> dict[str, RiskDecision]:
    """Derive the minimum Hosted risk policy from verified credential scopes.

    ``game:write`` authorizes the release's frozen standard risk labels.  It
    does not authorize any Skill by itself: exact ``allowed_tools`` grants are
    independently enforced by HostedClaimsAuthority and dynamic projection.
    Omitting unknown labels keeps Registry's default-deny behavior intact.
    """
    if "game:write" not in context.scopes:
        return {}
    return {label: RiskDecision.ALLOW for label in _HOSTED_WRITE_RISK_LABELS}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        # Do not use dataclasses.asdict(): it deep-copies nested values and
        # fails for immutable mappingproxy instances used by exposure facts.
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (set, frozenset, tuple, list)):
        return [_jsonable(v) for v in sorted(value, key=lambda x: repr(_jsonable(x)))] if isinstance(value, (set, frozenset)) else [_jsonable(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("modules.mcp"):
        return {k: _jsonable(v) for k, v in vars(value).items() if k != "adapter"}
    return value


def compute_facts_digest(
    execution_context: ExecutionContext,
    exposure_context: ExposureContext,
    snapshot: CapabilitySnapshot,
) -> str:
    """Hash all exposure facts and authority pins; credentials never enter it."""
    payload = {
        "exposure_context": _jsonable(exposure_context),
        "authority": {
            "profile": execution_context.profile,
            "principal_id": execution_context.principal_id,
            "runtime_ref": execution_context.runtime_ref,
            "generation": execution_context.generation,
            "binding_ref": execution_context.binding_ref,
            "binding_version": execution_context.binding_version,
            "game_session_ref": execution_context.game_session_ref,
            "game_instance_nonce": execution_context.game_instance_nonce,
            "lease_epoch": execution_context.lease_epoch,
            "scopes": sorted(execution_context.scopes),
            "allowed_tools": sorted(execution_context.allowed_tools),
            "capability_digest": execution_context.capability_digest,
        },
        "capability_snapshot": _jsonable(snapshot),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class SessionExposurePort(McpExposurePort):
    """Common linearized implementation used by both deployment profiles."""

    def __init__(
        self,
        authority: Any,
        manifest_provider: CapabilityManifestProvider,
        contract_catalog: CapabilityContractCatalog,
        exposure_context_provider: ExposureContextProvider | Callable[[ExecutionContext], Any],
        *,
        negotiated_entries_provider: Callable[[ExecutionContext], Any] | None = None,
    ) -> None:
        self._authority = authority
        self._manifest_provider = manifest_provider
        self._catalog = contract_catalog
        self._context_provider = exposure_context_provider
        self._negotiated_provider = negotiated_entries_provider
        self._facts: dict[str, ExposureFacts] = {}
        self._closed: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    async def _execution_context(self, handle: ContextHandle) -> ExecutionContext:
        # Additive authority hooks are preferred.  The fallback uses existing
        # authorize semantics and therefore does not alter ContextAuthority.
        for name in ("execution_context", "get_execution_context"):
            method = getattr(self._authority, name, None)
            if method is not None:
                result = method(handle)
                return await result if inspect.isawaitable(result) else result
        raise McpError("binding_unavailable", "verified execution context is unavailable")

    async def _call(self, provider: Any, *args: Any) -> Any:
        result = provider(*args) if callable(provider) else provider.get(*args)
        return await result if inspect.isawaitable(result) else result

    async def _compute(self, handle: ContextHandle) -> ExposureFacts:
        status = await self._authority.revalidate(handle)
        if not status.valid:
            raise McpError("context_expired", "context is no longer valid")
        context = await self._execution_context(handle)
        exposure = await self._call(self._context_provider, context)
        if self._negotiated_provider is not None:
            raw = await self._call(self._negotiated_provider, context)
        else:
            raw = await self._call(self._manifest_provider, context)
        if raw is None:
            raise McpError("binding_unavailable", "verified capability manifest is unavailable")
        try:
            snapshot = build_capability_snapshot(raw, self._catalog.contracts, context)
        except Exception as exc:
            if isinstance(exc, McpError):
                raise
            raise McpError("binding_unavailable", "verified capability manifest is unavailable") from exc
        return ExposureFacts(context, exposure, snapshot, compute_facts_digest(context, exposure, snapshot))

    async def resolve_exposure(self, handle: ContextHandle) -> ExposureFacts:
        lock = self._locks.setdefault(handle.value, asyncio.Lock())
        async with lock:
            if handle.value in self._closed:
                raise McpError("context_expired", "context is closed")
            facts = await self._compute(handle)
            if handle.value in self._closed:
                raise McpError("context_expired", "context is closed")
            self._facts[handle.value] = facts
            return facts

    async def revalidate_exposure(self, handle: ContextHandle) -> ExposureFacts:
        return await self.resolve_exposure(handle)

    async def close(self, handle: ContextHandle) -> None:
        lock = self._locks.setdefault(handle.value, asyncio.Lock())
        async with lock:
            self._closed.add(handle.value)
            self._facts.pop(handle.value, None)
            await self._authority.close(handle)
