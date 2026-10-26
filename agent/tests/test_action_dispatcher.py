from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from bridge.bridge_result import BridgeResult
from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import TaskCompleted
from slow_loop.action_dispatcher import ActionDispatcher


def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _bridge() -> MagicMock:
    bridge = MagicMock()
    seq = {"n": 0}

    def write_command(action, params):
        seq["n"] += 1
        return f"act_{seq['n']:04d}_{action}"

    bridge.write_command.side_effect = write_command
    bridge.read_command_result.return_value = None
    return bridge


def test_long_job_completion_emits_task_completed():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()

    result = BridgeResult(
        success=True,
        status="ok",
        data={"arrived": True},
        observation="arrived",
        side_effects=[],
        error_code=None,
        message=None,
        action_id="act_0001_move_to",
        seq=1,
    )
    bridge.read_command_result.return_value = result

    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        job_id = dispatcher.submit("move_to", {"location": "Farm"}, timeout_s=1.0, trigger_label="test", needs_followup=True)
        assert job_id
        msg = bus.get_from_fast_blocking(timeout=2.0)
        assert isinstance(msg, TaskCompleted)
        assert msg.result["success"] is True
        assert msg.result["job_id"] == job_id
        assert msg.node_id == job_id
        assert msg.result["action_id"] == "act_0001_move_to"
        assert msg.result["error_code"] is None
        assert msg.result["needs_followup"] is True
        assert any(e["type"] == "action_move_to" for e in memory.recent_events(10))
    finally:
        dispatcher.stop()


def test_fast_action_writes_while_long_job_waits():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()
    bridge.read_command_result.return_value = None

    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        dispatcher.submit("move_to", {"location": "Farm"}, timeout_s=1.0)
        assert _wait_until(lambda: any(c[0][0] == "move_to" for c in bridge.write_command.call_args_list))
        dispatcher.submit("say", {"message": "还在路上"}, wait_result=False)
        assert _wait_until(lambda: any(c[0][0] == "say" for c in bridge.write_command.call_args_list))
    finally:
        dispatcher.stop()


def test_queue_limit_rejects_excess_long_jobs():
    stop = __import__("threading").Event()
    stop.set()
    dispatcher = ActionDispatcher(_bridge(), MessageBus(), WorkingMemory(), stop, max_long_queue=1)

    first = dispatcher.submit("move_to", {"location": "Farm"})
    second = dispatcher.submit("go_bed", {})

    assert first is not None
    assert second is None
    dispatcher.stop()


def test_cancel_pending_emits_cancelled_task_completed():
    bus = MessageBus()
    stop = __import__("threading").Event()
    stop.set()
    dispatcher = ActionDispatcher(_bridge(), bus, WorkingMemory(), stop)

    job_id = dispatcher.submit("move_to", {"location": "Farm"})
    count = dispatcher.cancel_pending()

    assert count == 1
    msg = bus.get_from_fast_blocking(timeout=1.0)
    assert isinstance(msg, TaskCompleted)
    assert msg.result["job_id"] == job_id
    assert msg.result["success"] is False
    assert msg.node_id == job_id
    assert msg.result["action_id"] is None
    assert msg.result["error_code"] == "CANCELLED"
    assert dispatcher.snapshot()["long_queue_size"] == 0
    dispatcher.stop()


def test_long_job_timeout_emits_task_completed():
    bus = MessageBus()
    stop = __import__("threading").Event()
    bridge = _bridge()
    bridge.read_command_result.return_value = None
    dispatcher = ActionDispatcher(bridge, bus, WorkingMemory(), stop)
    dispatcher.start()
    try:
        job_id = dispatcher.submit("move_to", {"location": "Farm"}, timeout_s=0.01)
        assert job_id
        msg = bus.get_from_fast_blocking(timeout=2.0)
        assert isinstance(msg, TaskCompleted)
        assert msg.node_id == job_id
        assert msg.result["job_id"] == job_id
        assert msg.result["action_id"] == "act_0001_move_to"
        assert msg.result["success"] is False
        assert msg.result["error_code"] == "TIMEOUT"
        bridge.cancel_command.assert_called_once_with("act_0001_move_to")
    finally:
        dispatcher.stop()


def test_long_job_lower_priority_value_runs_first():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()

    ok = BridgeResult(
        success=True,
        status="ok",
        data={},
        observation="",
        side_effects=[],
        error_code=None,
        message=None,
        action_id="act_done",
        seq=1,
    )
    bridge.read_command_result.return_value = ok

    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        dispatcher.submit("move_to", {"which": "low"}, timeout_s=2.0, priority=200)
        dispatcher.submit("move_to", {"which": "high"}, timeout_s=2.0, priority=10)
        assert _wait_until(lambda: bridge.write_command.call_count >= 2, timeout_s=3.0)
        first_params = bridge.write_command.call_args_list[0][0][1]
        assert first_params.get("which") == "high"
        second_params = bridge.write_command.call_args_list[1][0][1]
        assert second_params.get("which") == "low"
    finally:
        dispatcher.stop()


def test_cancel_active_running_skill_calls_context_cancel_and_emits_cancelled():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()
    ctx = MagicMock()

    class Runner:
        async def execute(self, skill_entry, params, on_context=None):
            if on_context:
                on_context(ctx)
            while not ctx.cancel.called:
                await asyncio.sleep(0.01)
            return {
                "success": True,
                "status": "partial",
                "observation": "skill 被取消",
                "error": {"code": "CANCELLED"},
            }

    entry = MagicMock()
    entry.timeout = 1.0
    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        job_id = dispatcher.submit_skill(
            "skill:test",
            {},
            skill_runner=Runner(),
            skill_entry=entry,
        )
        assert job_id
        assert _wait_until(
            lambda: (dispatcher.snapshot()["active_job"] or {}).get("has_skill_context")
        )
        assert dispatcher.cancel_active() is True
        assert _wait_until(lambda: ctx.cancel.called)

        msg = bus.get_from_fast_blocking(timeout=2.0)
        assert isinstance(msg, TaskCompleted)
        assert msg.result["job_id"] == job_id
        assert msg.result["action_id"] is None
        assert msg.result["success"] is False
        assert msg.result["error_code"] == "CANCELLED"
    finally:
        dispatcher.stop()


def test_wait_until_job_writes_returns_for_running_skill():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()

    class Runner:
        async def execute(self, skill_entry, params, on_context=None):
            if on_context:
                on_context(MagicMock())
            await asyncio.sleep(0.2)
            return {"success": True, "status": "ok"}

    entry = MagicMock()
    entry.timeout = 1.0
    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        job_id = dispatcher.submit_skill(
            "skill:test",
            {},
            skill_runner=Runner(),
            skill_entry=entry,
        )
        assert job_id
        start = time.monotonic()
        dispatcher.wait_until_job_writes(job_id, timeout_s=1.0)
        assert time.monotonic() - start < 0.5
    finally:
        dispatcher.stop()


def test_skill_partial_status_is_preserved_in_snapshot_and_completion():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()

    class Runner:
        async def execute(self, skill_entry, params, on_context=None):
            return {
                "success": True,
                "status": "partial",
                "observation": "petted=1/2",
                "data": {"petted": 1, "skipped": 1},
            }

    entry = MagicMock()
    entry.timeout = 1.0
    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        job_id = dispatcher.submit_skill("skill:test", {}, skill_runner=Runner(), skill_entry=entry)
        assert job_id
        msg = bus.get_from_fast_blocking(timeout=2.0)
        assert isinstance(msg, TaskCompleted)
        assert msg.result["job_id"] == job_id
        assert msg.result["status"] == "partial"
        assert msg.result["success"] is True
        recent = dispatcher.snapshot()["recent_jobs"]
        matched = next(job for job in recent if job["job_id"] == job_id)
        assert matched["status"] == "partial"
    finally:
        dispatcher.stop()


def test_skill_blocked_status_is_preserved_in_snapshot_and_completion():
    bus = MessageBus()
    memory = WorkingMemory()
    stop = __import__("threading").Event()
    bridge = _bridge()

    class Runner:
        async def execute(self, skill_entry, params, on_context=None):
            return {
                "success": False,
                "status": "blocked",
                "observation": "no reachable targets",
                "error": {"code": "NO_PATH", "message": "no reachable targets"},
            }

    entry = MagicMock()
    entry.timeout = 1.0
    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        job_id = dispatcher.submit_skill("skill:test", {}, skill_runner=Runner(), skill_entry=entry)
        assert job_id
        msg = bus.get_from_fast_blocking(timeout=2.0)
        assert isinstance(msg, TaskCompleted)
        assert msg.result["job_id"] == job_id
        assert msg.result["status"] == "blocked"
        assert msg.result["success"] is False
        assert msg.result["error_code"] == "NO_PATH"
        recent = dispatcher.snapshot()["recent_jobs"]
        matched = next(job for job in recent if job["job_id"] == job_id)
        assert matched["status"] == "blocked"
    finally:
        dispatcher.stop()
