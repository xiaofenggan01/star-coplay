from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from modules.runtime_manager.contracts import PersonaArtifact
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from tests.runtime_manager.conftest import make_spec


@pytest.mark.asyncio
async def test_stop_cleans_transient_state_preserves_logs_and_spec_rebuilds(workspace_root: Path):
    payload = "You are a careful farm companion."
    artifact = PersonaArtifact(
        agentee_id="agentee-1",
        revision="persona-r7",
        content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        instruction_payload=payload,
    )
    adapter = FakeAgentRuntimeAdapter()
    spec = make_spec(workspace_root, runtime_id="rt-rebuild", generation=1)
    spec = type(spec)(**{**spec.__dict__, "persona_artifact": artifact})
    prepared = await adapter.prepare(spec)
    persona_path = prepared.workdir / "persona-artifact.json"
    source_before = artifact
    snapshot = json.loads(persona_path.read_text())
    assert snapshot["revision"] == "persona-r7"
    assert snapshot["content_hash"] == artifact.content_hash
    assert oct(persona_path.stat().st_mode & 0o777) == "0o444"

    handle = await adapter.start(prepared)
    await adapter.wait_ready(handle, deadline=5.0)
    await adapter.checkpoint(handle, deadline=2.0)
    logs = prepared.workdir / "logs"
    await adapter.stop(handle, reason="test-cleanup", grace_period=2.0)
    second_stop = await adapter.stop(handle, reason="test-cleanup-again", grace_period=2.0)
    assert second_stop.escalation == "already_stopped"

    assert not (prepared.workdir / "mcp.json").exists()
    assert not (prepared.workdir / "scratch").exists()
    assert not persona_path.exists()
    assert logs.exists()
    assert (prepared.workdir / "runtime-owner.json").exists()
    assert artifact == source_before

    rebuilt = await adapter.prepare(spec)
    rebuilt_snapshot = json.loads((rebuilt.workdir / "persona-artifact.json").read_text())
    assert rebuilt_snapshot == snapshot
    rebuilt_handle = await adapter.start(rebuilt)
    try:
        assert (await adapter.wait_ready(rebuilt_handle, deadline=5.0)).ready is True
    finally:
        await adapter.stop(rebuilt_handle, reason="test-teardown", grace_period=2.0)
