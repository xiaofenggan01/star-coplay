from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from modules.mcp.core.errors import McpError
from modules.mcp.profiles.local_stdio import (
    LocalDirectStdioApplication,
    LocalDirectStdioConfig,
    registered_capability_entries,
)
from modules.mcp.__main__ import build_parser


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_controlled_mutation_cli_requires_explicit_process_start_flag():
    assert not build_parser().parse_args(["--profile", "local_direct"]).enable_controlled_mutations
    assert build_parser().parse_args([
        "--profile", "local_direct", "--enable-controlled-mutations",
    ]).enable_controlled_mutations


class FakeRouteServer:
    def __init__(self, events):
        self.events = events
        self.connected = False
        self.endpoint = "ws://127.0.0.1:43123"
        self.stopped = False

    async def start(self):
        self.events.append("server")
        return self.endpoint

    async def stop(self):
        self.events.append("stop-server")
        self.connected = False
        self.stopped = True

    async def dispatch(self, _command):
        raise AssertionError("not used")

    async def result(self, *_args):
        raise AssertionError("not used")

    async def status(self, *_args):
        raise AssertionError("not used")

    async def cancel(self, *_args):
        raise AssertionError("not used")

    async def health(self):
        raise AssertionError("not used")


class FakeRendezvous:
    def __init__(self, events):
        self.events = events
        self.material = None
        self.cleaned = False

    def publish(self, material):
        self.events.append("publish")
        self.material = dict(material)

    def cleanup(self):
        self.events.append("cleanup-rendezvous")
        self.cleaned = True


class EmptyReader:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def application(events, *, timeout=0.2, reader_factory=None):
    server = FakeRouteServer(events)
    rendezvous = FakeRendezvous(events)
    app = LocalDirectStdioApplication(
        LocalDirectStdioConfig(
            repo_root=REPO_ROOT,
            readiness_timeout_seconds=timeout,
            readiness_poll_seconds=0.001,
            audit_path=Path(
                f"/tmp/starcoplay-local-audit-test-{os.getpid()}"
            ) / f"{id(events)}.jsonl",
        ),
        reader_factory=reader_factory or (lambda: EmptyReader()),
        route_server_factory=lambda _fence, _secret: server,
        rendezvous_factory=lambda _path: rendezvous,
    )
    return app, server, rendezvous


@pytest.mark.asyncio
async def test_run_orders_server_publish_authenticated_ready_then_stdin_and_cleans():
    events = []

    def reader_factory():
        events.append("stdin")
        return EmptyReader()

    app, server, rendezvous = application(events, reader_factory=reader_factory)
    task = asyncio.create_task(app.run())
    while events != ["server", "publish"]:
        await asyncio.sleep(0)
    assert "stdin" not in events
    assert rendezvous.material["endpoint"].startswith("ws://127.0.0.1:")
    server.connected = True  # represents a fully authenticated LocalModRouteServer handshake
    await task
    assert events == ["server", "publish", "stdin", "cleanup-rendezvous", "stop-server"]
    assert rendezvous.cleaned and server.stopped
    assert app.material.secret.destroyed


@pytest.mark.asyncio
async def test_composition_has_one_catalog_core_authority_router_and_full_surface():
    events = []
    app, server, _ = application(events)
    assert app.core._catalog is app.catalog
    assert app.core._authority is app.authority
    assert app.core._router is app.router
    assert app.router._connection is app.route_server is server
    assert set(app.material.tool_capabilities) == set(app.catalog._tools)
    assert len(app.catalog._tools) > 20
    assert app.capability_entries == registered_capability_entries(REPO_ROOT)
    capability_ids = {entry["capability_id"] for entry in app.capability_entries}
    assert all(entry["source_surface"].startswith("protocol-") for entry in app.capability_entries)
    assert "tp" in capability_ids
    assert "tp" not in app.catalog._tools
    await app.close()


@pytest.mark.asyncio
async def test_readiness_timeout_never_constructs_reader_and_rolls_back_every_owner():
    events = []
    app, server, rendezvous = application(
        events, timeout=0.005,
        reader_factory=lambda: (_ for _ in ()).throw(AssertionError("stdin opened before ready")),
    )
    with pytest.raises(McpError) as caught:
        await app.run()
    assert caught.value.code == "route_unavailable"
    assert rendezvous.cleaned and server.stopped and app.material.secret.destroyed
    assert events == ["server", "publish", "cleanup-rendezvous", "stop-server"]


@pytest.mark.asyncio
async def test_shutdown_during_startup_is_sigterm_equivalent_and_idempotently_cleans():
    events = []
    app, server, rendezvous = application(events, timeout=5)
    task = asyncio.create_task(app.run())
    while events != ["server", "publish"]:
        await asyncio.sleep(0)
    app.request_shutdown()
    with pytest.raises(McpError) as caught:
        await task
    assert caught.value.code == "route_unavailable"
    await app.close()
    assert events.count("cleanup-rendezvous") == 1
    assert events.count("stop-server") == 1
    assert rendezvous.cleaned and server.stopped and app.material.secret.destroyed


def test_module_cli_is_executable_and_timeout_cleans_real_rendezvous(tmp_path):
    rendezvous = tmp_path / "local-direct" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    result = subprocess.run(
        [
            sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
            "--readiness-timeout", "0.05", "--rendezvous-path", str(rendezvous),
        ],
        cwd=REPO_ROOT / "platform/backend", env=env,
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 1
    assert "authenticated local Mod readiness timed out" in result.stderr
    assert result.stdout == ""
    assert not rendezvous.exists()
    assert not list(rendezvous.parent.glob(".rendezvous-*"))


def test_module_cli_sigterm_cleans_rendezvous_and_socket_owner(tmp_path):
    rendezvous = tmp_path / "sigterm" / "rendezvous.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "platform/backend")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "modules.mcp", "--profile", "local_direct",
            "--readiness-timeout", "30", "--rendezvous-path", str(rendezvous),
        ],
        cwd=REPO_ROOT / "platform/backend", env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 5
    while not rendezvous.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rendezvous.exists()
    process.terminate()
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 1
    assert stdout == ""
    assert "local direct startup was terminated" in stderr
    assert not rendezvous.exists()
    assert not list(rendezvous.parent.glob(".rendezvous-*"))
