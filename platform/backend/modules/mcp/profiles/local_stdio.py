"""Production composition root for the single-process Local Direct MCP profile."""
from __future__ import annotations

import asyncio
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, TextIO

from modules.mcp.context import LocalBootAuthority, LocalBootMaterial, SessionExposurePort
from modules.mcp.skills.models import ExposureContext, RiskDecision
from modules.mcp.capabilities.contracts import build_capability_contract_catalog
from modules.mcp.capabilities.runtime import RouterBackedCapabilityRuntime
from modules.mcp.composition import build_mcp_composition
from modules.mcp.core import McpCore, ToolCatalog
from modules.mcp.core.errors import McpError
from modules.mcp.core.audit import DurableJsonlAuditSink
from modules.mcp.manifest import build_manifest, build_proto_capability_entries, build_tool_catalog
from modules.mcp.release_manifest import default_mounted_skill_refs
from modules.mcp.profiles.local_rendezvous import LocalRendezvous, default_rendezvous_path
from modules.mcp.routers import LocalModRouteServer, LocalModRouter, LocalRouteFence
from modules.mcp.routers.local_mod_connection import capability_snapshot_digest
from modules.mcp.transports import StdioMcpTransport


@dataclass(frozen=True)
class LocalDirectStdioConfig:
    repo_root: Path
    rendezvous_path: Path | None = None
    readiness_timeout_seconds: float = 10.0
    boot_lifetime_seconds: float = 300.0
    readiness_poll_seconds: float = 0.02
    # Controlled packages remain indexed for Local Direct, but mutating
    # samples are exposed only after this process-start opt-in.  There is no
    # prompt-time or per-call bypass of the default-deny Registry policy.
    enable_controlled_mutations: bool = False
    audit_path: Path | None = None


def local_controlled_risk_policy(enabled: bool) -> dict[str, RiskDecision]:
    """Return the exact process-start policy for the audited Local samples."""
    if not isinstance(enabled, bool):
        raise ValueError("controlled_mutation_flag_invalid")
    if not enabled:
        return {}
    return {
        "changes_save": RiskDecision.ALLOW,
        "consumes_energy": RiskDecision.ALLOW,
    }


class AsyncStdinLineReader:
    """Async stdin adapter which can be cancelled without a blocking worker thread."""

    def __init__(self, reader: asyncio.StreamReader, pipe_transport: asyncio.BaseTransport):
        self._reader = reader
        self._pipe_transport = pipe_transport

    @classmethod
    async def create(cls) -> "AsyncStdinLineReader":
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        pipe = getattr(sys.stdin, "buffer", sys.stdin)
        transport, _ = await loop.connect_read_pipe(lambda: protocol, pipe)
        return cls(reader, transport)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        line = await self._reader.readline()
        if not line:
            raise StopAsyncIteration
        return line.decode("utf-8")

    def close(self) -> None:
        self._pipe_transport.close()


def registered_capability_entries(repo_root: Path) -> list[dict[str, str]]:
    """Return exactly the Proto capabilities the current Mod registers at runtime."""
    return build_proto_capability_entries(repo_root)


class LocalDirectStdioApplication:
    """Own the complete Local Direct object graph for exactly one process boot.

    The route is listening and its one-time rendezvous is published before the
    application waits for an authenticated Mod.  Stdin is attached only after
    that handshake succeeds.
    """

    def __init__(
        self,
        config: LocalDirectStdioConfig,
        *,
        writer: TextIO | None = None,
        reader_factory: Callable[[], object] | None = None,
        route_server_factory: Callable[[LocalRouteFence, str], LocalModRouteServer] | None = None,
        rendezvous_factory: Callable[[Path | None], LocalRendezvous] = LocalRendezvous,
    ):
        self.config = config
        self.writer = writer or sys.stdout
        self._reader_factory = reader_factory or AsyncStdinLineReader.create
        self._route_server_factory = route_server_factory or (
            lambda fence, secret: LocalModRouteServer(fence, boot_secret=secret)
        )
        self._rendezvous_factory = rendezvous_factory
        self.shutdown_requested = asyncio.Event()

        self.capability_entries = build_proto_capability_entries(config.repo_root)
        self.capability_digest = capability_snapshot_digest(self.capability_entries)
        manifest = build_manifest(config.repo_root)
        self.catalog: ToolCatalog = build_tool_catalog(manifest)
        tool_capabilities = {name: name for name in self.catalog._tools}
        scopes = frozenset(tool.required_scope for tool in self.catalog._tools.values())
        self.material = LocalBootMaterial.create(
            capability_digest=self.capability_digest,
            tool_capabilities=tool_capabilities,
            scopes=scopes,
            lifetime_seconds=config.boot_lifetime_seconds,
        )
        self.authority = LocalBootAuthority(
            self.material, negotiated_capability_digest=lambda: self.capability_digest
        )
        self.fence = LocalRouteFence(
            context_ref=self.material.context_ref,
            principal_ref=self.material.principal_ref,
            runtime_ref=self.material.runtime_ref,
            game_session_ref=self.material.game_session_ref,
            game_instance_nonce=self.material.game_instance_nonce,
            generation=self.material.generation,
            binding_ref=self.material.binding_ref,
            binding_version=self.material.binding_version,
            lease_epoch=self.material.lease_epoch,
            capability_digest=self.material.capability_digest,
        )
        secret = self.material.secret.reveal()
        self.route_server = self._route_server_factory(self.fence, secret)
        # Additive, post-handshake facts port.  It reads only the exact Proto
        # entries retained by LocalModRouteServer; it does not alter McpCore's
        # existing ToolCatalog path.
        contract_catalog = build_capability_contract_catalog(config.repo_root)
        mounted_skill_refs = default_mounted_skill_refs(
            config.repo_root / "platform/backend/modules/mcp/skills/builtin",
            config.repo_root / "platform/backend/modules/mcp/skills/controlled-m4",
        )
        controlled_risk_policy = local_controlled_risk_policy(
            config.enable_controlled_mutations,
        )
        self.exposure_port = SessionExposurePort(
            self.authority,
            lambda _context: getattr(self.route_server, "negotiated_capability_entries", None),
            contract_catalog,
            lambda context: ExposureContext(
                game="stardew-valley", mounted_skill_refs=mounted_skill_refs,
                risk_policy=controlled_risk_policy, confirmation_available=False,
                mount_revision="empty", policy_revision="empty",
                binding_ref=context.binding_ref, binding_version=context.binding_version,
                game_session_ref=context.game_session_ref,
            ),
        )
        self.router = LocalModRouter(
            self.fence, self.route_server,
            boot_secret=self.material.secret, presented_secret=secret,
        )
        class _LocalManifestProvider:
            async def entries(_self, _context):
                return getattr(self.route_server, "negotiated_capability_entries", ())

        audit_sink = DurableJsonlAuditSink(
            config.audit_path
            or (config.rendezvous_path or default_rendezvous_path()).parent
               / "audit" / f"{self.material.context_ref}.jsonl"
        )
        capability_runtime = RouterBackedCapabilityRuntime(
            self.router,
            _LocalManifestProvider(),
            contract_catalog,
            context_ref=lambda _context: self.material.context_ref,
            audit=audit_sink,
        )
        self.composition = build_mcp_composition(
            repo_root=config.repo_root,
            exposure_port=self.exposure_port,
            capability_runtime=capability_runtime,
            authority=self.authority,
            controlled_enabled=True,
            audit=audit_sink,
        )
        self.core = McpCore(
            self.authority, self.router, self.catalog,
            adapt_results=True, projection=self.composition.projection,
        )
        self.transport = StdioMcpTransport(
            self.core, self.authority, writer=self.writer,
            connection_metadata={"credential": secret}, exposure_port=self.exposure_port,
        )
        self.rendezvous = self._rendezvous_factory(config.rendezvous_path)
        self.endpoint: str | None = None
        self._started = False
        self._closed = False
        self._stdin_reader: object | None = None

    def _rendezvous_material(self) -> dict[str, object]:
        m = self.material
        return {
            "endpoint": self.endpoint,
            "proof": m.secret.reveal(),
            "context_ref": m.context_ref,
            "principal_ref": m.principal_ref,
            "runtime_ref": m.runtime_ref,
            "generation": m.generation,
            "binding_ref": m.binding_ref,
            "binding_version": m.binding_version,
            "game_session_ref": m.game_session_ref,
            "game_instance_nonce": m.game_instance_nonce,
            "lease_epoch": m.lease_epoch,
            "capability_entries": self.capability_entries,
            "capability_digest": m.capability_digest,
        }

    async def start(self) -> None:
        if self._started or self._closed:
            raise McpError("conflict", "local direct application cannot be started again")
        try:
            self.endpoint = await self.route_server.start()
            self.rendezvous.publish(self._rendezvous_material())
            deadline = asyncio.get_running_loop().time() + self.config.readiness_timeout_seconds
            while not self.route_server.connected:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise McpError("route_unavailable", "authenticated local Mod readiness timed out")
                try:
                    await asyncio.wait_for(
                        self.shutdown_requested.wait(),
                        timeout=min(self.config.readiness_poll_seconds, remaining),
                    )
                except asyncio.TimeoutError:
                    continue
                raise McpError("route_unavailable", "local direct startup was terminated")
            self._started = True
        except BaseException:
            await self.close()
            raise

    async def run(self) -> None:
        try:
            await self.start()
            # This is deliberately after authenticated Mod readiness.
            created = self._reader_factory()
            self._stdin_reader = await created if hasattr(created, "__await__") else created
            serve_task = asyncio.create_task(self.transport.serve(self._stdin_reader))
            shutdown_task = asyncio.create_task(self.shutdown_requested.wait())
            try:
                done, _ = await asyncio.wait(
                    {serve_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if shutdown_task in done and not serve_task.done():
                    serve_task.cancel()
                    await asyncio.gather(serve_task, return_exceptions=True)
                else:
                    await serve_task
            finally:
                if not shutdown_task.done():
                    shutdown_task.cancel()
                    await asyncio.gather(shutdown_task, return_exceptions=True)
        finally:
            await self.close()

    def request_shutdown(self) -> None:
        self.shutdown_requested.set()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.rendezvous.cleanup()
            await self.route_server.stop()
        finally:
            self.router.invalidate_boot()
            self.authority.invalidate_boot()
            close = getattr(self._stdin_reader, "close", None)
            if callable(close):
                close()


async def run_local_direct_stdio(config: LocalDirectStdioConfig) -> None:
    application = LocalDirectStdioApplication(config)
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, application.request_shutdown)
            installed.append(signum)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await application.run()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[5]
