from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import EscalateRequest, PlayerInput, TaskCompleted
from slow_loop.action_policy import MAX_ASYNC_ACTIONS_PER_ROUND, timeout_for_action
from slow_loop.deliberative_loop import DeliberativeLoop, _player_preemption_reason
from slow_loop.plan_state import PlanItem, PlanState
from tests.test_fc_migration import make_fc_response


def _loop(bridge: MagicMock, memory: WorkingMemory) -> DeliberativeLoop:
    return DeliberativeLoop(
        message_bus=MessageBus(),
        bridge=bridge,
        working_memory=memory,
        config={"slow_loop": {"min_call_interval_s": 0.0, "player_input_debounce_s": 0.1}},
    )


def _entry(description: str) -> SimpleNamespace:
    return SimpleNamespace(timeout=1.0, metadata={"description": description})


def _assert_no_internal_chat_terms(text: str) -> None:
    lowered = text.lower()
    for term in ("skill-", "job_id", "queued", "planned", "taskcompleted"):
        assert term not in lowered


def test_successful_task_completed_without_followup_does_not_call_llm(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock()

    loop._handle_message(TaskCompleted(
        node_id="job_1",
        result={"success": True, "needs_followup": False, "action": "move_to"},
        observation="arrived",
    ))

    loop._llm_fn.assert_not_called()
    assert any(e["type"] == "task_completed" for e in working_memory.recent_events(5))


def test_failed_task_completed_calls_llm(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(side_effect=RuntimeError("stop after call"))

    loop._handle_message(TaskCompleted(
        node_id="job_1",
        result={"success": False, "needs_followup": False, "action": "move_to", "error_code": "TIMEOUT"},
        observation="",
    ))

    loop._llm_fn.assert_called_once()


def test_failed_task_completed_without_tool_calls_still_surfaces_llm_monologue(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(tool_calls=None, content="我需要重新判断一下"))

    loop._handle_message(TaskCompleted(
        node_id="job_1",
        result={"success": False, "needs_followup": True, "action": "move_to", "error_code": "TIMEOUT"},
        observation="timeout",
    ))

    say_calls = [
        call for call in mock_bridge.write_command.call_args_list
        if call.args and call.args[0] == "say"
    ]
    assert len(say_calls) == 1


def test_task_completed_monologue_is_forwarded_when_no_tool_calls(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(tool_calls=[], content="都处理好了，我们接下来去镇上逛逛？"))

    loop._handle_message(TaskCompleted(
        node_id="job_1",
        result={"success": True, "needs_followup": True, "action": "skill-water_crops"},
        observation="ok",
    ))

    say_calls = [
        call for call in mock_bridge.write_command.call_args_list
        if call.args and call.args[0] == "say"
    ]
    assert len(say_calls) == 1
    assert "镇上逛逛" in say_calls[0].args[1]["message"]


def test_player_input_bypasses_llm_throttle(mock_bridge, working_memory):
    loop = DeliberativeLoop(
        message_bus=MessageBus(),
        bridge=mock_bridge,
        working_memory=working_memory,
        config={"slow_loop": {"min_call_interval_s": 999.0, "player_input_debounce_s": 0.1}},
    )
    loop._last_call_at = __import__("time").monotonic()
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[{"name": "say", "arguments": {"message": "在"}}],
    ))

    loop._process_trigger(PlayerInput(text="你在吗"))

    loop._llm_fn.assert_called_once()


def test_direct_watering_command_routes_to_skill_without_llm(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock()
    entry = MagicMock()
    entry.timeout = 1.0
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.return_value = entry
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(return_value="job_water")

    loop._process_trigger(PlayerInput(text="我刚种了作物，给我浇水吧"))

    loop._llm_fn.assert_not_called()
    loop._dispatcher.submit_skill.assert_called_once()
    assert loop._dispatcher.submit_skill.call_args.args[0] == "skill-water_crops"


def test_multi_waypoint_command_routes_to_walk_route_skill(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock()
    entry = MagicMock()
    entry.timeout = 1.0
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.return_value = entry
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(return_value="job_route")

    loop._process_trigger(PlayerInput(text="去 Farm:64,15，再去 Farm:70,20，然后去 Town:31,67"))

    loop._llm_fn.assert_not_called()
    loop._dispatcher.submit_skill.assert_called_once()
    assert loop._dispatcher.submit_skill.call_args.args[0] == "skill-walk_route"
    assert loop._dispatcher.submit_skill.call_args.args[1] == {
        "waypoints": [
            {"map_id": "Farm", "x": 64, "y": 15},
            {"map_id": "Farm", "x": 70, "y": 20},
            {"map_id": "Town", "x": 31, "y": 67},
        ]
    }


def test_mid_turn_player_input_is_added_before_next_llm_call(mock_bridge, working_memory):
    bus = MessageBus()
    loop = _loop(mock_bridge, working_memory)
    first = make_fc_response(tool_calls=[{"name": "observe", "arguments": {}}])
    first.choices[0].finish_reason = "tool_calls"
    second = make_fc_response(tool_calls=[{"name": "say", "arguments": {"message": "收到"}}])
    calls = []

    def llm_fn(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            bus.send_to_slow(PlayerInput(text="先别继续，听我补一句"), priority=5)
            return first
        return second

    loop._bus = bus
    loop._llm_fn = llm_fn

    loop._process_trigger(PlayerInput(text="先观察一下"))

    assert len(calls) >= 2
    assert any("玩家中途补充" in (m.get("content") or "") for m in calls[1]["messages"])


def test_player_preemption_reason_matches_emergency_and_sleep():
    assert _player_preemption_reason("紧急情况取消后台任务").startswith("urgent:")
    assert _player_preemption_reason("我要去睡觉了").startswith("sleep:")
    assert _player_preemption_reason("hello chat") is None


def test_normal_chat_does_not_cancel_active_plan(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._plan_state = PlanState.from_items(
        source_input="先浇水再收菜",
        items=[PlanItem(label="浇灌当前地图所有未浇水的作物", tool="skill-water_crops")],
    )
    loop._plan_state.start_next(job_id="job_water")
    loop._dispatcher.cancel_pending = MagicMock(return_value=0)
    loop._dispatcher.cancel_active = MagicMock(return_value=True)

    loop._apply_player_preemption(PlayerInput(text="你辛苦啦，先聊两句"))

    loop._dispatcher.cancel_pending.assert_not_called()
    loop._dispatcher.cancel_active.assert_not_called()
    assert loop._plan_state.status == "active"


def test_explicit_cancel_marks_plan_cancelled(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._plan_state = PlanState.from_items(
        source_input="先浇水再收菜",
        items=[
            PlanItem(label="浇灌当前地图所有未浇水的作物", tool="skill-water_crops"),
            PlanItem(label="收割当前地图所有成熟作物", tool="skill-harvest_crops"),
        ],
    )
    loop._plan_state.start_next(job_id="job_water")
    loop._dispatcher.cancel_pending = MagicMock(return_value=1)
    loop._dispatcher.cancel_active = MagicMock(return_value=True)

    loop._apply_player_preemption(PlayerInput(text="取消后面的任务"))

    loop._dispatcher.cancel_pending.assert_called_once()
    loop._dispatcher.cancel_active.assert_called_once()
    assert loop._plan_state.status == "cancelled"
    assert loop._plan_state.blocked_reason.startswith("urgent:")


def test_action_policy_covers_long_timeouts():
    assert MAX_ASYNC_ACTIONS_PER_ROUND == 3
    assert timeout_for_action("execute_on_targets") == 300.0
    assert timeout_for_action("menu_interact") == 120.0
    assert timeout_for_action("unknown_long_action") == 60.0


def test_queue_skill_uses_dispatcher_path(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    entry = MagicMock()
    entry.timeout = 1.0
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.return_value = entry
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(return_value="job_queued")

    result = loop._queue_skill("skill:pet_animals", {"target": "Daisy"}, "test", 10)

    assert result == "skill:pet_animals queued: job_id=job_queued"
    loop._skill_runner.execute.assert_not_called()
    loop._dispatcher.submit_skill.assert_called_once()


def test_chop_trees_skill_waits_for_next_player_command(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[{"name": "skill-chop_trees", "arguments": {}}],
    ))
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.return_value = MagicMock(timeout=1.0)
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(return_value="job_chop")

    loop._process_trigger(PlayerInput(text="砍树"))

    loop._dispatcher.submit_skill.assert_called_once()
    kwargs = loop._dispatcher.submit_skill.call_args.kwargs
    assert kwargs["needs_followup"] is False


def test_queued_skill_status_is_not_sent_to_game_chat(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[{"name": "skill-harvest_crops", "arguments": {}}],
    ))
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.return_value = MagicMock(timeout=1.0)
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(return_value="job_harvest")

    loop._process_trigger(PlayerInput(text="帮我收菜"))

    say_calls = [
        call for call in mock_bridge.write_command.call_args_list
        if call.args and call.args[0] == "say"
    ]
    assert say_calls == []


def test_execute_skill_core_tool_dispatches_synchronously(mock_bridge, working_memory):
    """_execute_skill 走同步路径真实调用 SkillRunner.execute，而非返回 rejected。"""
    import asyncio

    loop = _loop(mock_bridge, working_memory)

    entry = MagicMock()
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.return_value = entry

    async def mock_execute(entry, params):
        return {
            "status": "ok",
            "observation": "位置: Farm (10, 20), 精力: 200/270",
            "data": {"location": "Farm"},
        }

    loop._skill_runner = MagicMock()
    loop._skill_runner.execute = mock_execute

    result = loop._execute_skill("skill:observe", {})

    assert "queued asynchronously" not in result
    assert "rejected" not in result
    # 应包含 observe 返回的真实观测文本
    assert "Farm" in result


def test_long_action_plan_does_not_wait_for_prior_long_job_to_write(mock_bridge, working_memory):
    bridge = mock_bridge
    bridge.read_command_result.return_value = None
    loop = _loop(bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[
            {"name": "move_to", "arguments": {"location": "Farm"}},
            {"name": "do", "arguments": {"verb": "wave"}},
        ],
    ))

    start = time.monotonic()
    try:
        loop._process_trigger(EscalateRequest(reason="queue-test", correlation_id="queue-test"))
        elapsed = time.monotonic() - start
        assert elapsed < 0.5
        snapshot = loop._dispatcher.snapshot()
        active = snapshot.get("active_job")
        pending = snapshot.get("pending_jobs") or []
        assert active is not None or pending
        assert sum(snapshot.get("jobs", {}).values()) == 1
        assert loop._plan_state is not None
        assert loop._plan_state.active is not None
        assert loop._plan_state.active.tool == "move_to"
        assert [step.tool for step in loop._plan_state.remaining] == ["do"]
    finally:
        loop.stop()


def test_query_action_jobs_returns_snapshot_to_fc_followup(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    first = make_fc_response(tool_calls=[{"name": "query_action_jobs", "arguments": {}}])
    first.choices[0].finish_reason = "tool_calls"
    second = make_fc_response(tool_calls=[])
    calls = []

    loop._dispatcher.snapshot = MagicMock(return_value={
        "running": True,
        "fast_queue_size": 0,
        "long_queue_size": 1,
        "jobs": {"running": 1},
        "active_job": {
            "job_id": "job_active",
            "action": "move_to",
            "status": "running",
        },
        "pending_jobs": [],
        "recent_jobs": [],
    })

    def llm_fn(**kwargs):
        calls.append(kwargs)
        return first if len(calls) == 1 else second

    loop._llm_fn = llm_fn

    loop._process_trigger(EscalateRequest(reason="status", correlation_id="status"))

    assert len(calls) == 2
    tool_messages = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
    assert any("action_jobs" in m.get("content", "") and "move_to" in m.get("content", "") for m in tool_messages)


def test_player_triggered_batch_starts_only_first_long_action_and_records_plan(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[
            {"name": "move_to", "arguments": {"location": "Farm"}},
            {"name": "do", "arguments": {"verb": "inspect"}},
        ],
    ))
    submitted = []

    def fake_submit(action, params, **kwargs):
        submitted.append((action, params, kwargs))
        return f"job_{len(submitted)}"

    loop._dispatcher.submit = MagicMock(side_effect=fake_submit)

    loop._process_trigger(PlayerInput(text="先去农场，再检查一下附近"))

    long_calls = [item for item in submitted if item[0] in {"move_to", "do"}]
    assert len(long_calls) == 1
    assert long_calls[0][0] == "move_to"
    assert long_calls[0][2]["needs_followup"] is False
    assert loop._plan_state is not None
    assert loop._plan_state.status == "active"
    assert loop._plan_state.active is not None
    assert loop._plan_state.active.tool == "move_to"
    assert loop._plan_state.active.job_id == "job_1"
    assert [step.tool for step in loop._plan_state.remaining] == ["do"]

    assistant_tool_blocks = [
        message for message in loop._session_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    tool_messages = [
        message for message in loop._session_messages
        if message.get("role") == "tool"
    ]
    assert len(assistant_tool_blocks[-1]["tool_calls"]) == 2
    assert len(tool_messages) >= 2
    say_calls = [
        call for call in mock_bridge.write_command.call_args_list
        if call.args and call.args[0] == "say"
    ]
    assert say_calls == []
    plan_say = [item for item in submitted if item[0] == "say"]
    assert len(plan_say) == 1
    message = plan_say[0][1]["message"]
    assert "去Farm" in message
    assert "完成inspect" in message
    assert "move_to" not in message
    assert "do:" not in message
    _assert_no_internal_chat_terms(message)


def test_plan_state_starts_next_skill_after_first_completion(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[
            {"name": "skill-water_crops", "arguments": {}},
            {"name": "skill-harvest_crops", "arguments": {}},
        ],
    ))
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.side_effect = lambda name: {
        "water_crops": _entry("浇灌当前地图所有未浇水的作物。"),
        "harvest_crops": _entry("收割当前地图所有成熟作物。"),
    }.get(name, _entry(f"{name}。"))
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(side_effect=["job_water", "job_harvest"])

    loop._process_trigger(PlayerInput(text="water then harvest"))

    assert loop._dispatcher.submit_skill.call_count == 1
    assert loop._dispatcher.submit_skill.call_args.args[0] == "skill-water_crops"
    assert loop._plan_state is not None
    assert [step.tool for step in loop._plan_state.remaining] == ["skill-harvest_crops"]

    loop._handle_message(TaskCompleted(
        node_id="job_water",
        result={
            "job_id": "job_water",
            "success": True,
            "needs_followup": False,
            "action": "skill-water_crops",
            "data": {"remaining_unwatered": 0},
        },
        observation="watered",
    ))

    assert loop._dispatcher.submit_skill.call_count == 2
    assert loop._dispatcher.submit_skill.call_args.args[0] == "skill-harvest_crops"
    assert loop._plan_state.status == "active"
    assert loop._plan_state.active is not None
    assert loop._plan_state.active.tool == "skill-harvest_crops"
    assert loop._plan_state.active.job_id == "job_harvest"
    assert loop._plan_state.remaining == []


def test_plan_state_sends_natural_plan_and_progress_feedback(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[
            {"name": "skill-water_crops", "arguments": {}},
            {"name": "skill-harvest_crops", "arguments": {}},
        ],
    ))
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.side_effect = lambda name: {
        "water_crops": _entry("浇灌当前地图所有未浇水的作物。"),
        "harvest_crops": _entry("收割当前地图所有成熟作物。"),
    }.get(name, _entry(f"{name}。"))
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(side_effect=["job_water", "job_harvest"])
    say_messages: list[str] = []
    loop._dispatcher.submit = MagicMock(
        side_effect=lambda action, params, **kwargs: (
            say_messages.append(params["message"]) or f"say_{len(say_messages)}"
            if action == "say"
            else f"job_{action}"
        )
    )

    loop._process_trigger(PlayerInput(text="先浇水再收菜"))

    assert say_messages
    assert "先" in say_messages[0]
    assert "浇灌" in say_messages[0]
    assert "收割" in say_messages[0]
    _assert_no_internal_chat_terms(say_messages[0])

    loop._handle_message(TaskCompleted(
        node_id="job_water",
        result={
            "job_id": "job_water",
            "success": True,
            "needs_followup": False,
            "action": "skill-water_crops",
            "data": {"remaining_unwatered": 0},
            "message": "浇灌了 12/12 棵作物",
        },
        observation="watered",
    ))

    assert len(say_messages) >= 2
    assert "浇灌" in say_messages[-1]
    assert "收割" in say_messages[-1]
    _assert_no_internal_chat_terms(say_messages[-1])


def test_plan_state_context_is_added_to_next_l4_user_turn(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._plan_state = None
    loop._llm_fn = MagicMock(return_value=make_fc_response(tool_calls=[]))
    plan = loop._plan_state = PlanState.from_items(
        source_input="先浇水再收菜",
        items=[PlanItem(
            label="浇灌当前地图所有未浇水的作物",
            tool="skill-water_crops",
        )],
    )
    plan.start_next(job_id="job_water")
    plan.remaining.append(PlanItem(
        label="收割当前地图所有成熟作物",
        tool="skill-harvest_crops",
    ))

    user_turn = loop._build_user_turn(PlayerInput(text="现在进度怎么样"), {})

    assert "当前计划" in user_turn
    assert "active" in user_turn
    assert "remaining" in user_turn
    assert "浇灌当前地图所有未浇水的作物" in user_turn
    assert "收割当前地图所有成熟作物" in user_turn
    assert "job_water" not in user_turn


def test_plan_state_partial_completion_blocks_remaining_skill(mock_bridge, working_memory):
    loop = _loop(mock_bridge, working_memory)
    loop._llm_fn = MagicMock(return_value=make_fc_response(
        tool_calls=[
            {"name": "skill-water_crops", "arguments": {}},
            {"name": "skill-harvest_crops", "arguments": {}},
        ],
    ))
    loop._skill_registry = MagicMock()
    loop._skill_registry.get.side_effect = lambda name: {
        "water_crops": _entry("浇灌当前地图所有未浇水的作物。"),
        "harvest_crops": _entry("收割当前地图所有成熟作物。"),
    }.get(name, _entry(f"{name}。"))
    loop._skill_runner = MagicMock()
    loop._dispatcher.submit_skill = MagicMock(side_effect=["job_water", "job_harvest"])

    loop._process_trigger(PlayerInput(text="water then harvest"))
    loop._handle_message(TaskCompleted(
        node_id="job_water",
        result={
            "job_id": "job_water",
            "success": True,
            "needs_followup": False,
            "action": "skill-water_crops",
            "data": {"remaining_unwatered": 3},
        },
        observation="partial",
    ))

    assert loop._dispatcher.submit_skill.call_count == 1
    assert loop._plan_state is not None
    assert loop._plan_state.status == "blocked"
    assert loop._plan_state.blocked_reason == "partial"
    assert [step.tool for step in loop._plan_state.remaining] == ["skill-harvest_crops"]
