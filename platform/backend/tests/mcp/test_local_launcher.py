from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import time
import threading

import pytest

from modules.mcp.context import LocalBootMaterial
from modules.mcp.contracts.models import CancelResult, CommandState, GameCommandResult, RouteHealth
from modules.mcp.core.errors import McpError
from modules.mcp.profiles import LocalDirectLauncher, LocalLaunchConfig
from modules.mcp.routers.local_mod_connection import capability_snapshot_digest


class LaunchMod:
    connected = True

    async def dispatch(self, _command): pass
    async def result(self, command_id, _deadline):
        return GameCommandResult(command_id, CommandState.SUCCEEDED)
    async def status(self, _command_id): return CommandState.UNKNOWN
    async def cancel(self, command_id): return CancelResult(command_id, True, CommandState.CANCELLED)
    async def health(self): return RouteHealth(True)


def new_material(*, generation=1):
    return LocalBootMaterial.create(
        capability_digest="digest-launch",
        tool_capabilities={"observe": "observe", "say": "say"},
        scopes=frozenset({"game:read", "game:write"}), generation=generation,
    )


def make_launcher(*, ready, command=None, material_factory=new_material, timeout=10):
    return LocalDirectLauncher(
        LocalLaunchConfig(
            command or [sys.executable, "-c", "import time; time.sleep(60)"],
            readiness_timeout_seconds=timeout, shutdown_timeout_seconds=1,
        ),
        ready=ready,
        material_factory=material_factory,
        mod_connection=LaunchMod(),
        negotiated_capability_digest=lambda: "digest-launch",
    )


def test_launcher_atomically_composes_full_material_authority_router_and_cleans_it():
    material = new_material()
    secret = material.secret.reveal()
    launcher = make_launcher(
        ready=lambda endpoint: endpoint.startswith("127.0.0.1:"),
        material_factory=lambda: material,
    )
    handle = launcher.start()
    path = handle.secret_path
    pid = handle.process.pid
    payload = json.loads(path.read_text())
    assert payload["boot_secret"] == secret
    assert payload["endpoint"] == handle.endpoint
    assert payload["generation"] == material.generation
    assert payload["binding_ref"] == material.binding_ref
    assert payload["binding_version"] == material.binding_version
    assert payload["game_session_ref"] == material.game_session_ref
    assert payload["game_instance_nonce"] == material.game_instance_nonce
    assert payload["lease_epoch"] == material.lease_epoch
    assert payload["capability_digest"] == material.capability_digest
    assert payload["scopes"] == sorted(material.scopes)
    assert payload["tool_capabilities"] == material.tool_capabilities
    assert handle.router._fence.context_ref == material.context_ref
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert handle.endpoint.startswith("127.0.0.1:")

    context_handle = asyncio.run(handle.authority.establish({"credential": secret}))
    context = asyncio.run(handle.authority.authorize(context_handle, "say", "say"))
    assert secret not in repr(handle)
    assert secret not in repr(handle.router._fence)
    launcher.stop()
    assert asyncio.run(handle.authority.revalidate(context_handle)).valid is False
    with pytest.raises(McpError) as caught:
        asyncio.run(handle.router.health(context))
    assert caught.value.code == "context_expired"
    assert not path.exists()
    assert not hasattr(handle, "boot_secret") and not hasattr(handle, "material")
    with pytest.raises(McpError) as caught:
        material.secret.reveal()
    assert caught.value.code == "context_expired"
    assert handle.process.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_launcher_restart_requires_fresh_boot_material_and_invalidates_old_handle():
    materials = [new_material(generation=1), new_material(generation=2)]
    material_iterator = iter(materials)
    old_secret = materials[0].secret.reveal()
    launcher = make_launcher(ready=lambda _endpoint: True,
                             material_factory=lambda: next(material_iterator))
    first = launcher.start()
    old_handle = asyncio.run(first.authority.establish({"credential": old_secret}))
    launcher.stop()
    second = launcher.start()
    try:
        assert second.context_ref != first.context_ref
        assert second.generation == 2
        assert asyncio.run(first.authority.revalidate(old_handle)).valid is False
        with pytest.raises(McpError):
            materials[0].secret.reveal()
    finally:
        launcher.stop()


def test_launcher_kills_descendant_process_group(tmp_path):
    child_pid = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"open({str(child_pid)!r},'w').write(str(p.pid)); time.sleep(60)"
    )
    launcher = make_launcher(command=[sys.executable, "-c", code],
                             ready=lambda _endpoint: child_pid.exists())
    launcher.start()
    descendant = int(child_pid.read_text())
    launcher.stop()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant)], capture_output=True, text=True
        ).stdout.strip()
        if not status or status.startswith("Z"):
            break
        time.sleep(0.02)
    else:
        pytest.fail("launcher left a running descendant process")


def test_readiness_failure_rolls_back_secret_process_and_authority_handle():
    captured = {}
    material = new_material()
    secret = material.secret.reveal()
    launcher = None

    def never_ready(endpoint):
        captured["endpoint"] = endpoint
        if "handle" not in captured:
            captured["authority"] = launcher._authority
            captured["handle"] = asyncio.run(
                launcher._authority.establish({"credential": secret})
            )
        return False

    launcher = make_launcher(ready=never_ready, material_factory=lambda: material, timeout=0.01)
    with pytest.raises(McpError) as caught:
        launcher.start()
    assert caught.value.code == "route_unavailable"
    assert captured["endpoint"].startswith("127.0.0.1:")
    assert asyncio.run(captured["authority"].revalidate(captured["handle"])).valid is False
    assert launcher._handle is None and launcher._tempdir is None
    with pytest.raises(McpError) as caught:
        launcher.start()
    assert caught.value.code == "conflict"  # failed boot material is retired, not reusable


def test_second_start_is_rejected_without_replacing_owner():
    launcher = make_launcher(ready=lambda _endpoint: True)
    first = launcher.start()
    try:
        with pytest.raises(McpError) as caught:
            launcher.start()
        assert caught.value.code == "conflict"
        assert first.process.poll() is None
    finally:
        launcher.stop()


def test_real_launcher_orders_server_rendezvous_mod_handshake_then_process_and_cleans_all(tmp_path):
    entries = [{
        "source_surface": "protocol-2.4.5",
        "capability_id": "say",
        "capability_contract_version": "2.4.5",
    }]
    digest = capability_snapshot_digest(entries)
    material = LocalBootMaterial.create(
        capability_digest=digest,
        tool_capabilities={"say": "say"},
        scopes=frozenset({"game:write"}),
    )
    rendezvous_path = tmp_path / "owner" / "rendezvous.json"
    claimed = threading.Event()

    def mod_worker():
        async def run():
            deadline = time.monotonic() + 3
            while not rendezvous_path.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            payload = json.loads(rendezvous_path.read_text())
            rendezvous_path.unlink()
            async with __import__("websockets").connect(payload["endpoint"]) as ws:
                await ws.send(json.dumps({
                    "type": "local_direct_hello", "profile": "local_direct",
                    "proof": payload["proof"], "generation": payload["generation"],
                    "binding_ref": payload["binding_ref"],
                    "binding_version": payload["binding_version"],
                    "game_session_ref": payload["game_session_ref"],
                    "game_instance_nonce": payload["game_instance_nonce"],
                    "lease_epoch": payload["lease_epoch"],
                    "capability_entries": payload["capability_entries"],
                    "capability_digest": payload["capability_digest"],
                }))
                assert json.loads(await ws.recv())["type"] == "local_direct_ready"
                claimed.set()
                while not material.secret.destroyed:
                    await asyncio.sleep(0.01)
        asyncio.run(run())

    worker = threading.Thread(target=mod_worker, daemon=True)
    worker.start()
    launcher = LocalDirectLauncher(
        LocalLaunchConfig(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            readiness_timeout_seconds=3,
            shutdown_timeout_seconds=1,
            capability_entries=entries,
            rendezvous_path=rendezvous_path,
        ),
        ready=lambda _endpoint: claimed.is_set(),
        material_factory=lambda: material,
        negotiated_capability_digest=lambda: digest,
    )
    handle = launcher.start()
    try:
        assert claimed.is_set()
        assert handle.process.poll() is None
        assert not rendezvous_path.exists()
        assert handle.router._connection.connected
    finally:
        launcher.stop()
        worker.join(timeout=2)
    assert handle.process.poll() is not None
    assert not rendezvous_path.exists()
