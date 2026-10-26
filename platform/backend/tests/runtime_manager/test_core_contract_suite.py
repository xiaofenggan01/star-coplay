"""One parameterized core Contract Suite shared by Fake and real Claude."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from modules.runtime_manager.adapters.claude.adapter import ClaudeAgentRuntimeAdapter
from modules.runtime_manager.contracts import RuntimeInput, RuntimeSpec
from modules.runtime_manager.fake.adapter import FakeAgentRuntimeAdapter
from modules.runtime_manager.harness import RuntimeHarness
from tests.runtime_manager.conftest import make_spec


def _provider_cases():
    return [
        pytest.param("fake", FakeAgentRuntimeAdapter, False, id="fake"),
        pytest.param(
            "claude",
            ClaudeAgentRuntimeAdapter,
            True,
            id="claude-real",
            marks=pytest.mark.skipif(
                os.environ.get("RUN_REAL_CLAUDE_TESTS") != "1",
                reason="real Claude contract case is opt-in",
            ),
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,adapter_factory,is_real", _provider_cases())
async def test_core_adapter_contract(provider, adapter_factory, is_real, tmp_path: Path):
    metadata = {}
    if is_real:
        metadata = {
            "model": "claude-sonnet-5",
            "ready_deadline_seconds": 60.0,
            "turn_deadline_seconds": 60.0,
        }
    spec = RuntimeSpec(
        runtime_id=f"rt-contract-{provider}",
        generation=1,
        user_id="contract-user",
        agentee_id="contract-agentee",
        provider=provider,
        workspace_root=tmp_path,
        provider_private_metadata=metadata,
    )
    adapter = adapter_factory()
    prepared = await adapter.prepare(spec)
    handle = await adapter.start(prepared)
    try:
        before = await adapter.inspect(handle)
        assert before.pid is not None and before.returncode is None
        assert before.ready is False
        ready = await adapter.wait_ready(handle, deadline=60.0 if is_real else 5.0)
        assert ready.ready is True
        turn = await adapter.submit_input(
            handle,
            RuntimeInput(request_id="contract-turn-1", content="Reply with exactly OK." if is_real else "OK"),
        )
        assert turn.runtime_id == spec.runtime_id
        assert turn.generation == spec.generation
        assert turn.turn == 1
        assert (await adapter.inspect(handle)).pid == before.pid
    finally:
        first = await adapter.stop(handle, reason="contract-stop", grace_period=10.0 if is_real else 2.0)
        second = await adapter.stop(handle, reason="contract-stop-again", grace_period=2.0)
        assert first.stopped and second.stopped
        assert second.escalation == "already_stopped"


class _ProtocolOnlyAdapter:
    """Adapter that raises if any non-``AgentRuntimeAdapter`` method is called.

    Proves ``RuntimeHarness`` never reaches outside the public Protocol.
    """

    def __init__(self) -> None:
        self._inner = FakeAgentRuntimeAdapter()

    def __getattr__(self, name: str):
        if name in (
            "prepare",
            "start",
            "wait_ready",
            "submit_input",
            "inspect",
            "checkpoint",
            "stop",
            "reconcile",
        ):
            return getattr(self._inner, name)
        raise RuntimeError(
            f"RuntimeHarness called {name}() which is not an AgentRuntimeAdapter Protocol member"
        )


@pytest.mark.asyncio
async def test_harness_uses_only_protocol_members(workspace_root: Path):
    """Prove ``RuntimeHarness`` drives the adapter through only its public
    ``AgentRuntimeAdapter`` Protocol interface. If any code path in the
    Harness calls ``mcp_call`` or another non-Protocol method, the
    ``_ProtocolOnlyAdapter.__getattr__`` trap raises ``RuntimeError``."""
    adapter = _ProtocolOnlyAdapter()
    harness = RuntimeHarness(adapter)
    spec = make_spec(workspace_root, runtime_id="rt-proto-only", generation=1)
    entry = await harness.start_runtime(spec, ready_timeout=5.0)
    from modules.runtime_manager.contracts import RuntimeActualState

    assert entry.actual_state == RuntimeActualState.READY
    turn = await harness.submit_input(
        "rt-proto-only", 1, RuntimeInput(request_id="proto-1", content="hi")
    )
    assert turn.turn == 1
    await harness.report_effective_activity("rt-proto-only", 1)
    await harness._adapter.stop(entry.handle, reason="test-teardown", grace_period=2.0)
