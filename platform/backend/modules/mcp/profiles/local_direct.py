"""Local Direct launcher and process-lifetime boundary."""
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from modules.mcp.context import LocalBootAuthority, LocalBootMaterial
from modules.mcp.core.errors import McpError
from modules.mcp.routers import (
    LocalModRouter, LocalRouteFence, ModConnection, ThreadedLocalModRouteServer,
)
from modules.mcp.profiles.local_rendezvous import LocalRendezvous


@dataclass(frozen=True)
class LocalLaunchConfig:
    command: Sequence[str]
    readiness_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 5.0
    environment: Mapping[str, str] | None = None
    capability_entries: Sequence[Mapping[str, str]] = ()
    rendezvous_path: Path | None = None


@dataclass
class LocalLaunchHandle:
    endpoint: str
    secret_path: Path
    process: subprocess.Popen[bytes]
    context_ref: str
    generation: int
    authority: LocalBootAuthority
    router: LocalModRouter


def _allocate_loopback_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


class LocalDirectLauncher:
    """Starts one process group and owns all ephemeral launch material."""

    def __init__(
        self,
        config: LocalLaunchConfig,
        *,
        ready: Callable[[str], bool],
        material_factory: Callable[[], LocalBootMaterial],
        mod_connection: ModConnection | None = None,
        negotiated_capability_digest: Callable[[], str],
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ):
        self._config = config
        self._ready = ready
        self._material_factory = material_factory
        self._mod_connection = mod_connection
        self._negotiated_capability_digest = negotiated_capability_digest
        self._clock = clock
        self._sleep = sleeper
        self._popen = popen
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._handle: LocalLaunchHandle | None = None
        self._authority: LocalBootAuthority | None = None
        self._router: LocalModRouter | None = None
        self._material: LocalBootMaterial | None = None
        self._retired_context_refs: set[str] = set()
        self._route_server: ThreadedLocalModRouteServer | None = None
        self._rendezvous: LocalRendezvous | None = None

    def start(self) -> LocalLaunchHandle:
        if self._handle is not None:
            raise McpError("conflict", "local profile is already running")
        endpoint = _allocate_loopback_endpoint()
        host, _, _port = endpoint.partition(":")
        if host != "127.0.0.1":
            raise McpError("internal_error", "local endpoint allocation was not loopback")
        material = self._material_factory()
        if material.context_ref in self._retired_context_refs:
            raise McpError("conflict", "local boot material cannot be reused")
        self._material = material
        authority = LocalBootAuthority(
            material, negotiated_capability_digest=self._negotiated_capability_digest
        )
        self._authority = authority
        route_fence = LocalRouteFence(
            context_ref=material.context_ref,
            principal_ref=material.principal_ref,
            runtime_ref=material.runtime_ref,
            game_session_ref=material.game_session_ref,
            game_instance_nonce=material.game_instance_nonce,
            generation=material.generation,
            binding_ref=material.binding_ref,
            binding_version=material.binding_version,
            lease_epoch=material.lease_epoch,
            capability_digest=material.capability_digest,
        )
        connection = self._mod_connection
        if connection is None:
            self._route_server = ThreadedLocalModRouteServer(
                route_fence, boot_secret=material.secret.reveal()
            )
            endpoint = self._route_server.start()
            connection = self._route_server
        router = LocalModRouter(
            route_fence,
            connection,
            boot_secret=material.secret,
            presented_secret=material.secret.reveal(),
        )
        self._router = router
        try:
            self._tempdir = tempfile.TemporaryDirectory(prefix="starcoplay-mcp-")
            secret_path = Path(self._tempdir.name) / "launch.json"
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
            try:
                os.write(fd, json.dumps({
                    "boot_secret": material.secret.reveal(),
                    "endpoint": endpoint,
                    "context_ref": material.context_ref,
                    "principal_ref": material.principal_ref,
                    "runtime_ref": material.runtime_ref,
                    "generation": material.generation,
                    "binding_ref": material.binding_ref,
                    "binding_version": material.binding_version,
                    "game_session_ref": material.game_session_ref,
                    "game_instance_nonce": material.game_instance_nonce,
                    "lease_epoch": material.lease_epoch,
                    "capability_digest": material.capability_digest,
                    "scopes": sorted(material.scopes),
                    "tool_capabilities": dict(material.tool_capabilities),
                    "issued_at": material.issued_at,
                    "expires_at": material.expires_at,
                }, sort_keys=True).encode())
            finally:
                os.close(fd)
        except Exception:
            self.stop()
            raise
        if self._route_server is not None:
            entries = [dict(entry) for entry in self._config.capability_entries]
            self._rendezvous = LocalRendezvous(self._config.rendezvous_path)
            try:
                self._rendezvous.publish({
                    "endpoint": endpoint,
                    "proof": material.secret.reveal(),
                    "context_ref": material.context_ref,
                    "principal_ref": material.principal_ref,
                    "runtime_ref": material.runtime_ref,
                    "generation": material.generation,
                    "binding_ref": material.binding_ref,
                    "binding_version": material.binding_version,
                    "game_session_ref": material.game_session_ref,
                    "game_instance_nonce": material.game_instance_nonce,
                    "lease_epoch": material.lease_epoch,
                    "capability_entries": entries,
                    "capability_digest": material.capability_digest,
                })
            except Exception:
                self.stop()
                raise
        env = os.environ.copy()
        env.update(self._config.environment or {})
        env["STARCOPLAY_MCP_LAUNCH_MATERIAL"] = str(secret_path)
        try:
            deadline = self._clock() + self._config.readiness_timeout_seconds
            if self._route_server is not None:
                while self._clock() < deadline and not self._route_server.connected:
                    self._sleep(0.05)
                if not self._route_server.connected:
                    raise McpError("route_unavailable", "local Mod handshake timed out")
            process = self._popen(
                list(self._config.command), env=env, stdout=subprocess.DEVNULL,
                stderr=None, start_new_session=True,
            )
            self._handle = LocalLaunchHandle(
                endpoint, secret_path, process, material.context_ref,
                material.generation, authority, router
            )
            deadline = self._clock() + self._config.readiness_timeout_seconds
            while self._clock() < deadline:
                if process.poll() is not None:
                    raise McpError("route_unavailable", "local process exited before readiness")
                mod_ready = self._route_server is None or self._route_server.connected
                if mod_ready and self._ready(endpoint):
                    return self._handle
                self._sleep(0.05)
            raise McpError("route_unavailable", "local process readiness timed out")
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        handle = self._handle
        try:
            if handle is not None and handle.process.poll() is None:
                try:
                    os.killpg(handle.process.pid, signal.SIGTERM)
                    handle.process.wait(timeout=self._config.shutdown_timeout_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(handle.process.pid, signal.SIGKILL)
                        handle.process.wait(timeout=self._config.shutdown_timeout_seconds)
                    except (subprocess.TimeoutExpired, ProcessLookupError):
                        pass
                except ProcessLookupError:
                    pass
        finally:
            if self._authority is not None:
                self._authority.invalidate_boot()
            if self._router is not None:
                self._router.invalidate_boot()
            if self._material is not None:
                self._retired_context_refs.add(self._material.context_ref)
            if handle is not None:
                try:
                    handle.secret_path.unlink(missing_ok=True)
                finally:
                    self._handle = None
            if self._tempdir is not None:
                self._tempdir.cleanup()
                self._tempdir = None
            if self._rendezvous is not None:
                self._rendezvous.cleanup()
                self._rendezvous = None
            if self._route_server is not None:
                self._route_server.stop()
                self._route_server = None
            self._authority = None
            self._router = None
            self._material = None

    def __enter__(self) -> LocalLaunchHandle:
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.stop()
