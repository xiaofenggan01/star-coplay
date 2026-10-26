"""M4 production composition for the protocol-neutral MCP graph.

This module is the only place that assembles the release-bound Skill graph.
Profile code supplies authenticated exposure and execution dependencies; this
factory never creates a fake router, capability provider, or authority.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from modules.mcp.capabilities.models import CapabilityRuntime
from modules.mcp.core.audit import MemoryAuditSink
from modules.mcp.core.projection import BackgroundJobCompletion, McpProjection
from modules.mcp.contracts.projection import McpExposurePort
from modules.mcp.release_manifest import load_release_manifest
from modules.mcp.skills.loader import LoadedSkillProgram, load_skill_programs
from modules.mcp.skills.models import SkillRef
from modules.mcp.skills.registry import SkillRegistry
from modules.mcp.skills.runtime import SkillRuntime
from modules.mcp.skills.runtime_models import SkillAuditSink
from modules.mcp.core.runtime_adapter import SkillRuntimeAdapter


class McpCompositionError(RuntimeError):
    """Neutral construction failure; no filesystem or dependency detail leaks."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class McpProductionComposition:
    """Immutable process graph shared by Local Direct and Hosted profiles."""

    registry: SkillRegistry
    programs: Mapping[SkillRef, LoadedSkillProgram]
    runtime: SkillRuntime
    execution_port: SkillRuntimeAdapter
    projection: McpProjection
    release_digest: str
    controlled_enabled: bool


def build_mcp_composition(
    *,
    repo_root: Path,
    exposure_port: McpExposurePort,
    capability_runtime: CapabilityRuntime,
    authority: Any | None = None,
    controlled_enabled: bool = False,
    audit: SkillAuditSink | None = None,
    clock: Callable[[], float] = time.time,
    uuid_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    wait: Callable[[float], Any] | None = None,
    background_completion_hook: Callable[
        [BackgroundJobCompletion], Awaitable[None]
    ] | None = None,
) -> McpProductionComposition:
    """Construct the complete, release-bound Skill execution graph.

    ``capability_runtime`` is mandatory so a profile cannot silently fall
    back to a fake or legacy implementation.  Controlled packages are only
    enabled for an explicit local setting and the exact audited repository
    root; Hosted callers must leave ``controlled_enabled`` false.
    """
    if not isinstance(repo_root, Path) or not repo_root.is_absolute():
        raise McpCompositionError("repository_root_invalid")
    # Runtime protocols are structural and intentionally not runtime-checkable;
    # reject only an absent dependency and let the first call surface a typed
    # boundary error for malformed implementations.
    if exposure_port is None:
        raise McpCompositionError("exposure_port_missing")
    if capability_runtime is None:
        raise McpCompositionError("capability_runtime_missing")
    if not isinstance(controlled_enabled, bool):
        raise McpCompositionError("controlled_flag_invalid")

    builtin_root = repo_root / "platform/backend/modules/mcp/skills/builtin"
    controlled_root = repo_root / "platform/backend/modules/mcp/skills/controlled-m4"
    manifest_path = repo_root / "platform/backend/modules/mcp/release-manifest.yaml"
    if controlled_enabled:
        try:
            controlled_root.relative_to(repo_root)
        except ValueError as exc:
            raise McpCompositionError("controlled_root_invalid") from exc
    try:
        # Verifies both package digests and exact release inventory before any
        # implementation code is loaded.
        release = load_release_manifest(
            manifest_path,
            builtin_root=builtin_root,
            # The checked-in Release Manifest is the complete release
            # inventory, including controlled packages.  Hosted still verifies
            # that inventory against the audited root, then excludes controlled
            # packages from its process index below.
            controlled_root=controlled_root,
        )
        registry = SkillRegistry.index(
            builtin_root,
            controlled_root if controlled_enabled else None,
        )
        programs = load_skill_programs(registry.packages)
    except Exception as exc:
        # Keep a neutral public boundary while preserving the private chain.
        if isinstance(exc, McpCompositionError):
            raise
        raise McpCompositionError("release_composition_invalid") from exc

    audit_sink = audit or MemoryAuditSink()
    runtime = SkillRuntime(
        programs,
        capability_runtime,
        audit_sink,
        clock,
        uuid_factory,
        wait or _default_wait,
    )
    execution_port = SkillRuntimeAdapter(runtime)
    projection = McpProjection(
        registry,
        exposure_port,
        execution_port,
        authority=authority,
        implementation_digest_for=lambda ref: programs[ref].implementation_digest,
        background_completion_hook=background_completion_hook,
    )
    return McpProductionComposition(
        registry=registry,
        programs=programs,
        runtime=runtime,
        execution_port=execution_port,
        projection=projection,
        release_digest=str(release["release_digest"]),
        controlled_enabled=controlled_enabled,
    )


async def _default_wait(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


__all__ = ["McpCompositionError", "McpProductionComposition", "build_mcp_composition"]
