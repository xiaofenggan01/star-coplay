from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from aiohttp import WSMsgType

import modules.mcp.transports.hosted_server as hosted_server
from modules.mcp.contracts.models import CommandState, ContextHandle, GameCommandResult
from modules.mcp.transports.hosted_server import HostedMcpJsonRpcSession, HostedMcpWebSocketEndpoint


class _Request(dict):
    def __init__(self):
        super().__init__()
        self.headers = {"Authorization": "Bearer test-credential"}


class _FakeWebSocket:
    def __init__(self, frames, *, block_send=False, pace_input=False,
                 allow_sends_before_block=0):
        self.headers = {}
        self.closed = False
        self.close_code = None
        self.close_message = None
        self.sent = []
        self._frames = list(frames)
        self._pace_input = pace_input
        self._send_gate = asyncio.Event()
        self._send_count = 0
        self._allow_sends_before_block = allow_sends_before_block
        if not block_send:
            self._send_gate.set()

    async def prepare(self, _request):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            if self._pace_input:
                await asyncio.sleep(0)
            return self._frames.pop(0)
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def send_json(self, frame):
        self._send_count += 1
        if self._send_count > self._allow_sends_before_block:
            await self._send_gate.wait()
        self.sent.append(dict(frame))

    async def close(self, *, code, message):
        self.closed = True
        self.close_code = code
        self.close_message = message


class _Authority:
    def __init__(self):
        self.establish_count = 0

    async def establish(self, _metadata):
        self.establish_count += 1
        return ContextHandle("hosted-test")

    async def close(self, _handle):
        pass


class _Core:
    catalog_digest = "empty"
    _projection = None

    def __init__(self):
        self.list_count = 0

    async def list_tools(self, _handle):
        self.list_count += 1
        return []

    async def call_tool(self, *_args, **_kwargs):
        raise AssertionError("overflow frames must not reach ToolCall dispatch")


class _BlockingCallCore(_Core):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def call_tool(self, *_args, **_kwargs):
        self.started.set()
        await self.release.wait()
        return GameCommandResult("command", CommandState.SUCCEEDED, {"ok": True})


def _text(frame):
    return SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(frame))


def _install_fake_websocket(monkeypatch, websocket):
    monkeypatch.setattr("aiohttp.web.WebSocketResponse", lambda **_kwargs: websocket)


def _assert_no_hosted_bridge_tasks(baseline):
    remaining = {
        task for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    }
    names = {task.get_name() for task in remaining}
    assert not names.intersection({
        "hosted-ws-reader", "hosted-ws-writer", "hosted-mcp-serve",
        "hosted-ws-overflow-signal",
    })
    assert not (remaining - baseline), "Hosted endpoint leaked unnamed request/control work"


@pytest.mark.asyncio
async def test_hosted_tool_call_gate_releases_after_true_task_cancellation():
    core, authority = _BlockingCallCore(), _Authority()
    session = HostedMcpJsonRpcSession(core, authority, "credential")
    assert "result" in await session.request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    frame = {
        "jsonrpc": "2.0", "id": "first", "method": "tools/call",
        "params": {"name": "test", "arguments": {},
                   "_meta": {"toolCallId": "first", "commandId": "first"}},
    }
    first = asyncio.create_task(session.request(frame))
    await core.started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    core.release.set()
    after = await session.request({
        **frame, "id": "after",
        "params": {"name": "test", "arguments": {},
                   "_meta": {"toolCallId": "after", "commandId": "after"}},
    })
    assert after["result"]["isError"] is False
    await session.close()


@pytest.mark.asyncio
async def test_hosted_inbound_n_plus_one_overflow_warns_metrics_closes_1013_and_dispatches_nothing(
    monkeypatch, caplog,
):
    websocket = _FakeWebSocket([
        _text({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _text({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ])
    _install_fake_websocket(monkeypatch, websocket)
    metrics = []
    monkeypatch.setattr(hosted_server, "emit_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    authority, core = _Authority(), _Core()
    endpoint = HostedMcpWebSocketEndpoint(core, authority, queue_size=1)
    baseline = set(asyncio.all_tasks())

    with caplog.at_level("WARNING", logger="mcp.hosted.transport"):
        await endpoint.handle(_Request())

    assert websocket.close_code == 1013
    assert websocket.close_message == b"resource_exhausted"
    # The first accepted frame may run concurrently; the overflow frame and
    # all subsequent queued work must not dispatch.
    assert authority.establish_count <= 1
    assert core.list_count == 0
    overflow = [record for record in caplog.records
                if getattr(record, "event", "") == "mcp.hosted.websocket.overflow"]
    assert len(overflow) == 1
    assert sum(record.levelno >= logging.WARNING for record in caplog.records) == 1
    assert overflow[0].limit_kind == "hosted_mcp_input_frames"
    assert len(metrics) == 1
    assert metrics[0][1]["limit_kind"] == "hosted_mcp_input_frames"
    _assert_no_hosted_bridge_tasks(baseline)


@pytest.mark.asyncio
async def test_hosted_outbound_n_plus_one_overflow_warns_metrics_closes_1013_without_orphans(
    monkeypatch, caplog,
):
    # Initialize is flushed normally.  The writer then takes the first async
    # tools/list response and blocks in send_json; the next response fills the
    # one-frame queue and N+1 overflows from a background request task.  The
    # reader sends no frame after that burst and remains open, proving the
    # explicit overflow signal (not reader completion) wakes the supervisor.
    websocket = _FakeWebSocket([
        _text({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _text({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        _text({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}),
        _text({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}),
    ], block_send=True, pace_input=True, allow_sends_before_block=1)
    _install_fake_websocket(monkeypatch, websocket)
    metrics = []
    monkeypatch.setattr(hosted_server, "emit_metric", lambda *args, **kwargs: metrics.append((args, kwargs)))
    authority, core = _Authority(), _Core()
    endpoint = HostedMcpWebSocketEndpoint(core, authority, queue_size=1)
    baseline = set(asyncio.all_tasks())

    with caplog.at_level("WARNING", logger="mcp.hosted.transport"):
        await asyncio.wait_for(endpoint.handle(_Request()), timeout=1)

    assert websocket.close_code == 1013
    assert websocket.close_message == b"resource_exhausted"
    assert authority.establish_count == 1
    dispatched_at_close = core.list_count
    assert dispatched_at_close >= 2
    await asyncio.sleep(0)
    assert core.list_count == dispatched_at_close
    overflow = [record for record in caplog.records
                if getattr(record, "event", "") == "mcp.hosted.websocket.overflow"]
    assert len(overflow) == 1
    assert sum(record.levelno >= logging.WARNING for record in caplog.records) == 1
    assert overflow[0].limit_kind == "hosted_mcp_output_frames"
    assert len(metrics) == 1
    assert metrics[0][1]["limit_kind"] == "hosted_mcp_output_frames"
    _assert_no_hosted_bridge_tasks(baseline)
