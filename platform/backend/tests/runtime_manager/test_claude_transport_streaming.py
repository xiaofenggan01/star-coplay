"""Claude CLI stream transport regression tests."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from modules.runtime_manager.adapters.claude.adapter import (
    ClaudeAgentRuntimeAdapter,
    _ClaudeHandleState,
)
from modules.runtime_manager.adapters.claude.errors import ClaudeTransportError
from modules.runtime_manager.adapters.claude.protocol import ClaudeLaunchConfig
from modules.runtime_manager.adapters.claude.transport import ClaudeProcessTransport
from modules.runtime_manager.contracts import (
    ProviderStatus,
    RuntimeHandle,
    RuntimeInput,
    RuntimeStoppingError,
)


@pytest.mark.asyncio
async def test_active_turn_accepts_soft_user_input_on_same_stdin(tmp_path: Path):
    script = "\n".join((
        "import json, sys",
        "first = json.loads(sys.stdin.readline())",
        "second = json.loads(sys.stdin.readline())",
        "text = first['message']['content'] + ' -> ' + second['message']['content']",
        "print(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', "
        "'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn'}}), flush=True)",
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False}), flush=True)",
    ))
    transport = ClaudeProcessTransport("runtime-soft-input", 1)
    await transport.start(
        command=(sys.executable, "-c", script),
        cwd=tmp_path,
        env={},
    )

    turn = asyncio.create_task(transport.run_turn("walk to town", deadline_seconds=2.0))
    try:
        for _ in range(100):
            if transport.turn_inflight:
                break
            await asyncio.sleep(0)

        assert await transport.steer_turn("go to the town square") is True
        outcome = await turn
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await transport.stop(grace_period=0.2)

    assert outcome.text == "walk to town -> go to the town square"


@pytest.mark.asyncio
async def test_active_turn_accepts_native_interrupt_control_request(tmp_path: Path):
    script = "\n".join((
        "import json, sys",
        "json.loads(sys.stdin.readline())",
        "control = json.loads(sys.stdin.readline())",
        "print(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', "
        "'content': [{'type': 'text', 'text': json.dumps(control)}], "
        "'stop_reason': 'end_turn'}}), flush=True)",
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False}), flush=True)",
    ))
    transport = ClaudeProcessTransport("runtime-interrupt", 1)
    await transport.start(command=(sys.executable, "-c", script), cwd=tmp_path, env={})

    turn = asyncio.create_task(transport.run_turn("old plan", deadline_seconds=2.0))
    try:
        for _ in range(100):
            if transport.turn_inflight:
                break
            await asyncio.sleep(0)
        assert await transport.interrupt_turn() is True
        outcome = await turn
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await transport.stop(grace_period=0.2)

    control = json.loads(outcome.text)
    assert control["type"] == "control_request"
    assert control["request_id"].startswith("interrupt-")
    assert control["request"] == {"subtype": "interrupt"}


@pytest.mark.asyncio
async def test_terminal_turn_rejects_soft_user_input_before_result(tmp_path: Path):
    script = "\n".join((
        "import json, sys, time",
        "sys.stdin.readline()",
        "print(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', "
        "'content': [{'type': 'text', 'text': 'done'}], 'stop_reason': 'end_turn'}}), flush=True)",
        "time.sleep(0.2)",
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False}), flush=True)",
    ))
    transport = ClaudeProcessTransport("runtime-terminal-input", 1)
    await transport.start(command=(sys.executable, "-c", script), cwd=tmp_path, env={})

    turn = asyncio.create_task(transport.run_turn("go", deadline_seconds=2.0))
    try:
        for _ in range(100):
            if transport.turn_inflight:
                break
            await asyncio.sleep(0)
        assert transport.turn_inflight is True
        for _ in range(100):
            if not transport.turn_inflight and not turn.done():
                break
            await asyncio.sleep(.002)

        assert transport.turn_inflight is False
        assert turn.done() is False
        assert await transport.steer_turn("too late") is False
        await turn
    finally:
        if not turn.done():
            turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await transport.stop(grace_period=0.2)


@pytest.mark.asyncio
async def test_large_tool_result_frame_does_not_hide_terminal_result(tmp_path: Path):
    script = "\n".join((
        "import json, sys",
        "sys.stdin.readline()",
        "tool_result = {'type': 'user', 'message': {'role': 'user', 'content': "
        "[{'type': 'tool_result', 'content': 'x' * (128 * 1024)}]}}",
        "print(json.dumps(tool_result), flush=True)",
        "print(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', "
        "'content': [{'type': 'text', 'text': 'done'}], 'stop_reason': 'end_turn'}}), flush=True)",
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False}), flush=True)",
    ))
    transport = ClaudeProcessTransport("runtime-large-frame", 1)
    await transport.start(
        command=(sys.executable, "-c", script),
        cwd=tmp_path,
        env={},
    )

    try:
        outcome = await transport.run_turn("go", deadline_seconds=2.0)
    finally:
        await transport.stop(grace_period=0.2)

    assert outcome.text == "done"
    assert outcome.result_frame["subtype"] == "success"


@pytest.mark.asyncio
async def test_stdout_pump_failure_fails_turn_and_health_instead_of_hanging(tmp_path: Path):
    script = "\n".join((
        "import json, sys",
        "sys.stdin.readline()",
        "tool_result = {'type': 'user', 'message': {'role': 'user', 'content': "
        "[{'type': 'tool_result', 'content': 'x' * (9 * 1024 * 1024)}]}}",
        "print(json.dumps(tool_result), flush=True)",
    ))
    transport = ClaudeProcessTransport("runtime-broken-pump", 1)
    await transport.start(
        command=(sys.executable, "-c", script),
        cwd=tmp_path,
        env={},
    )

    try:
        with pytest.raises(ClaudeTransportError, match="stdout pump failed"):
            await transport.run_turn("go", deadline_seconds=2.0)
        status = await transport.inspect()
    finally:
        await transport.stop(grace_period=0.2)

    assert status.healthy is False
    assert status.detail["stdout_pump_failed"] is True
    assert status.detail["stdout_pump_error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_stderr_pump_failure_also_fails_turn_and_health(tmp_path: Path):
    script = "\n".join((
        "import sys",
        "sys.stdin.readline()",
        "print('x' * (9 * 1024 * 1024), file=sys.stderr, flush=True)",
    ))
    transport = ClaudeProcessTransport("runtime-broken-stderr", 1)
    await transport.start(
        command=(sys.executable, "-c", script),
        cwd=tmp_path,
        env={},
    )

    try:
        with pytest.raises(ClaudeTransportError, match="stderr pump failed"):
            await transport.run_turn("go", deadline_seconds=2.0)
        status = await transport.inspect()
    finally:
        await transport.stop(grace_period=0.2)

    assert status.healthy is False
    assert status.detail["stderr_pump_failed"] is True
    assert status.detail["stderr_pump_error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_adapter_stops_desynchronized_process_after_transport_failure(tmp_path: Path):
    transport = AsyncMock()
    transport.inspect.return_value = ProviderStatus(
        pid=123,
        returncode=None,
        ready=True,
        healthy=True,
        last_frame_monotonic=None,
    )
    transport.run_turn.side_effect = ClaudeTransportError("stdout pump failed")
    state = _ClaudeHandleState(
        transport=transport,
        launch_config=ClaudeLaunchConfig.__new__(ClaudeLaunchConfig),
        bootstrapped=True,
        bootstrap_ok=True,
        is_hosted_mcp=True,
    )
    handle = RuntimeHandle("runtime-broken-pump", 1, tmp_path, state)

    with pytest.raises(ClaudeTransportError, match="stdout pump failed"):
        await ClaudeAgentRuntimeAdapter().submit_input(
            handle, RuntimeInput("request-1", "hello"),
        )

    transport.stop.assert_awaited_once_with(grace_period=0.5)


@pytest.mark.asyncio
async def test_adapter_rejects_input_when_transport_pump_is_already_unhealthy(tmp_path: Path):
    transport = AsyncMock()
    transport.inspect.return_value = ProviderStatus(
        pid=123,
        returncode=None,
        ready=True,
        healthy=False,
        last_frame_monotonic=None,
        detail={"stdout_pump_failed": True},
    )
    state = _ClaudeHandleState(
        transport=transport,
        launch_config=ClaudeLaunchConfig.__new__(ClaudeLaunchConfig),
        bootstrapped=True,
        bootstrap_ok=True,
        is_hosted_mcp=True,
    )
    handle = RuntimeHandle("runtime-unhealthy-pump", 1, tmp_path, state)

    with pytest.raises(RuntimeStoppingError, match="transport is unhealthy"):
        await ClaudeAgentRuntimeAdapter().submit_input(
            handle, RuntimeInput("request-2", "hello again"),
        )

    transport.run_turn.assert_not_awaited()
    transport.stop.assert_awaited_once_with(grace_period=0.5)
