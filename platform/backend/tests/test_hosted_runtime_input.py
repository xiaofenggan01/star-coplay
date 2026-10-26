from types import MappingProxyType, SimpleNamespace
import asyncio
import time

import pytest

from modules.gateway.hosted_runtime_input import (
    HostedRuntimeInputDispatcher,
    HostedSayReflectionRegistry,
)
from modules.gateway.hosted_session import HostedHelloFacts, WebSocketHostedSessionRoute
from modules.mcp.contracts.models import GameCommand, SideEffect
from modules.mcp.core.projection import BackgroundJobCompletion
from modules.mcp.skills.models import SkillRef
from modules.mcp.skills.runtime_models import SkillExecutionState
from modules.runtime_manager.domain import ResourceExhausted


class Coordinator:
    def __init__(self):
        self.calls = []

    async def submit_input_async(self, runtime_id, generation, value):
        self.calls.append((runtime_id, generation, value))
        return SimpleNamespace(operation_id=value.request_id)


def session(*, user_id="tenant-1", game_session_id="game-1",
            target_player_id="42", role="controller"):
    sent = []
    async def send(frame):
        sent.append(frame)
    return SimpleNamespace(
        instance=SimpleNamespace(user_id=user_id),
        hosted_game_session_id=game_session_id,
        hosted_connection_role=role,
        hosted_runtime_spec=SimpleNamespace(runtime_id=f"runtime-{user_id}", generation=3),
        hosted_facts=SimpleNamespace(
            agent_control_target_player_id=target_player_id,
            game_instance_nonce="nonce-1",
        ),
        send=send,
        sent=sent,
    )


def chat(seq, *, player_id="7", player_name="Player", message="hello",
         origin="human", **extra):
    data = {
        "player_id": player_id,
        "player_name": player_name,
        "message": message,
        "origin": origin,
        **extra,
    }
    if player_id is None:
        data.pop("player_id")
    return {"type": "game_event", "event": {"seq": seq, "type": "chat_received", "data": data}}


def completion(*, job_id="job-1", status=SkillExecutionState.SUCCEEDED):
    return BackgroundJobCompletion(
        runtime_ref="runtime-tenant-1",
        generation=3,
        principal_id="tenant-1",
        binding_ref="binding-1",
        game_session_ref="game-1",
        job_id=job_id,
        skill_ref=SkillRef("water_crops", "1.0.0"),
        state=status,
        output=MappingProxyType({"watered": 12, "plots": ("north", "south")}),
        error_code=None,
        partial_output={},
        side_effects_may_have_occurred=True,
    )


@pytest.mark.asyncio
async def test_human_chat_is_submitted_once_with_stable_runtime_identity():
    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, HostedSayReflectionRegistry())

    await dispatcher.dispatch_game_event(session(), chat(9, message="Hi there"))
    await dispatcher.dispatch_game_event(session(), chat(9, message="Hi there"))

    assert len(coordinator.calls) == 1
    runtime_id, generation, value = coordinator.calls[0]
    assert (runtime_id, generation) == ("runtime-tenant-1", 3)
    assert value.request_id == "hosted-chat:game-1:nonce-1:9"
    assert value.content == "Player: Hi there"
    assert value.trace_context["source"] == "game_chat"


@pytest.mark.asyncio
async def test_stop_chat_cancels_active_background_job_before_submitting_reply_turn():
    class BackgroundJobs:
        def __init__(self):
            self.calls = []

        async def cancel_active(self, *, runtime_id, generation, game_session_id, reason):
            self.calls.append({
                "runtime_id": runtime_id,
                "generation": generation,
                "game_session_id": game_session_id,
                "reason": reason,
            })
            return "job-nav-1"

    coordinator = Coordinator()
    background_jobs = BackgroundJobs()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator,
        HostedSayReflectionRegistry(),
        background_jobs=background_jobs,
    )

    await dispatcher.dispatch_game_event(session(), chat(10, message="停下，先别去了"))

    assert background_jobs.calls == [{
        "runtime_id": "runtime-tenant-1",
        "generation": 3,
        "game_session_id": "game-1",
        "reason": "player_requested",
    }]
    assert len(coordinator.calls) == 1
    assert coordinator.calls[0][2].content == "Player: 停下，先别去了"
    assert coordinator.calls[0][2].trace_context["cancelled_background_job_id"] == "job-nav-1"


@pytest.mark.asyncio
async def test_background_job_cancel_failure_does_not_drop_player_reply_turn():
    class BackgroundJobs:
        async def cancel_active(self, **_kwargs):
            raise RuntimeError("cancel unavailable")

    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator,
        HostedSayReflectionRegistry(),
        background_jobs=BackgroundJobs(),
    )

    await dispatcher.dispatch_game_event(session(), chat(11, message="停下，我想聊聊"))

    assert len(coordinator.calls) == 1
    value = coordinator.calls[0][2]
    assert value.content == "Player: 停下，我想聊聊"
    assert "cancelled_background_job_id" not in value.trace_context


@pytest.mark.asyncio
async def test_busy_runtime_steers_preview_and_keeps_player_chat_for_next_turn():
    class BusyCoordinator:
        def __init__(self):
            self.calls = 0
            self.steered = []
            self.accepted = []
            self.available = asyncio.Event()

        async def submit_input_async(self, runtime_id, generation, value):
            self.calls += 1
            if self.calls == 1:
                raise ResourceExhausted("runtime input is already in flight")
            self.accepted.append((runtime_id, generation, value))
            return SimpleNamespace(operation_id=value.request_id)

        async def steer_input(self, runtime_id, generation, value):
            self.steered.append((runtime_id, generation, value))
            return True

        async def wait_input_available(self, runtime_id, generation, *, timeout):
            await asyncio.wait_for(self.available.wait(), timeout=timeout)
            return True

    coordinator = BusyCoordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator, HostedSayReflectionRegistry(), retry_delay_seconds=.001,
    )

    queued = await dispatcher.dispatch_game_event(
        session(), chat(10, message="second message"),
    )
    await asyncio.sleep(.01)
    assert coordinator.calls == 1
    coordinator.available.set()
    for _ in range(100):
        if coordinator.accepted:
            break
        await asyncio.sleep(.001)

    assert queued is not None
    assert queued.operation_id == "hosted-chat:game-1:nonce-1:10"
    assert queued.status == "queued"
    assert len(coordinator.steered) == 1
    assert coordinator.steered[0][2].content.startswith("[Steering preview; do not reply")
    assert coordinator.steered[0][2].trace_context["source"] == "game_chat_steering_preview"
    assert len(coordinator.accepted) == 1
    assert coordinator.accepted[0][2].content == "Player: second message"
    assert coordinator.accepted[0][2].trace_context["source"] == "game_chat"
    assert coordinator.calls == 2


@pytest.mark.asyncio
async def test_busy_runtime_interrupts_old_turn_without_steering_or_compensation():
    class InterruptingCoordinator:
        def __init__(self):
            self.calls = []
            self.interrupts = []
            self.available = asyncio.Event()

        async def submit_input_async(self, runtime_id, generation, value):
            self.calls.append((runtime_id, generation, value))
            if len(self.calls) == 2:
                raise ResourceExhausted("runtime input is already in flight")
            return SimpleNamespace(operation_id=value.request_id)

        async def interrupt_input(self, runtime_id, generation, reason):
            self.interrupts.append((runtime_id, generation, reason))
            return "hosted-chat:game-1:nonce-1:1"

        async def steer_input(self, *_args):
            raise AssertionError("native interrupt must not also steer the old turn")

        async def wait_input_available(self, _runtime_id, _generation, *, timeout):
            await asyncio.wait_for(self.available.wait(), timeout=timeout)
            return True

    coordinator = InterruptingCoordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator, HostedSayReflectionRegistry(), retry_delay_seconds=.001,
    )
    controller = session()
    await dispatcher.dispatch_game_event(controller, chat(1, message="old plan"))
    old_value = coordinator.calls[0][2]

    queued = await dispatcher.dispatch_game_event(
        controller, chat(2, message="stop and talk to me"),
    )
    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, old_value, SimpleNamespace(content=""),
        RuntimeError("interrupted"),
    )
    coordinator.available.set()
    for _ in range(100):
        if len(coordinator.calls) >= 3:
            break
        await asyncio.sleep(.001)

    assert queued is not None
    assert coordinator.interrupts == [
        ("runtime-tenant-1", 3, "player_superseded"),
    ]
    assert len(coordinator.calls) == 3
    assert coordinator.calls[-1][2].content == "Player: stop and talk to me"
    assert controller.sent == []


@pytest.mark.asyncio
async def test_background_completion_enters_same_runtime_generation():
    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator, HostedSayReflectionRegistry(),
    )

    await dispatcher.dispatch_background_completion(completion())

    assert len(coordinator.calls) == 1
    runtime_id, generation, value = coordinator.calls[0]
    assert (runtime_id, generation) == ("runtime-tenant-1", 3)
    assert value.request_id == "hosted-job-completed:job-1"
    assert value.trace_context["source"] == "background_job_completion"
    assert "exactly one concise say call" in value.content
    assert '"watered":12' in value.content
    assert '"plots":["north","south"]' in value.content


@pytest.mark.asyncio
async def test_player_queue_drains_before_background_completion_queue():
    class BusyCoordinator:
        def __init__(self):
            self.busy = True
            self.accepted = []
            self.available = asyncio.Event()

        async def submit_input_async(self, runtime_id, generation, value):
            if self.busy:
                raise ResourceExhausted("runtime input is already in flight")
            self.accepted.append(value)
            return SimpleNamespace(operation_id=value.request_id)

        async def wait_input_available(self, runtime_id, generation, *, timeout):
            await asyncio.wait_for(self.available.wait(), timeout=timeout)
            return True

    coordinator = BusyCoordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator,
        HostedSayReflectionRegistry(),
        retry_delay_seconds=.001,
    )

    await dispatcher.dispatch_background_completion(completion())
    await dispatcher.dispatch_game_event(
        session(), chat(12, message="what are you doing?"),
    )
    coordinator.busy = False
    coordinator.available.set()
    for _ in range(100):
        if len(coordinator.accepted) == 2:
            break
        await asyncio.sleep(.001)

    assert [
        value.trace_context["source"] for value in coordinator.accepted
    ] == ["game_chat", "background_job_completion"]


@pytest.mark.asyncio
async def test_queue_orders_compensation_before_player_before_background_without_polling():
    class EventCoordinator:
        def __init__(self):
            self.busy = False
            self.calls = 0
            self.accepted = []
            self.available = asyncio.Event()

        async def submit_input_async(self, runtime_id, generation, value):
            self.calls += 1
            if self.busy:
                raise ResourceExhausted("runtime input is already in flight")
            self.accepted.append(value)
            return SimpleNamespace(operation_id=value.request_id)

        async def steer_input(self, runtime_id, generation, value):
            return False

        async def wait_input_available(self, runtime_id, generation, *, timeout):
            await asyncio.wait_for(self.available.wait(), timeout=timeout)
            return True

    coordinator = EventCoordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator, HostedSayReflectionRegistry(), retry_delay_seconds=.001,
    )
    controller = session()
    await dispatcher.dispatch_game_event(controller, chat(30, message="first"))
    original = coordinator.accepted.pop()

    coordinator.busy = True
    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, original, SimpleNamespace(content="silent"), None,
    )
    await dispatcher.dispatch_background_completion(completion(job_id="queued-job"))
    await dispatcher.dispatch_game_event(controller, chat(31, message="second"))
    calls_while_queued = coordinator.calls
    await asyncio.sleep(.01)
    assert coordinator.calls == calls_while_queued

    coordinator.busy = False
    coordinator.available.set()
    for _ in range(100):
        if len(coordinator.accepted) == 3:
            break
        await asyncio.sleep(.001)

    assert [value.trace_context["source"] for value in coordinator.accepted] == [
        "say_compensation", "game_chat", "background_job_completion",
    ]
    diagnostics = dispatcher.diagnostics_snapshot()
    assert diagnostics["queue"]["total"] == 0
    assert diagnostics["delivery"]["silent_turns"] == 1


@pytest.mark.asyncio
async def test_terminal_steering_race_falls_back_to_bounded_queue():
    class TerminalCoordinator:
        def __init__(self):
            self.calls = 0
            self.accepted = []

        async def submit_input_async(self, runtime_id, generation, value):
            self.calls += 1
            if self.calls == 1:
                raise ResourceExhausted("runtime input is already in flight")
            self.accepted.append((runtime_id, generation, value))
            return SimpleNamespace(operation_id=value.request_id)

        async def steer_input(self, runtime_id, generation, value):
            return False

    coordinator = TerminalCoordinator()
    dispatcher = HostedRuntimeInputDispatcher(
        coordinator, HostedSayReflectionRegistry(), retry_delay_seconds=.001,
    )

    queued = await dispatcher.dispatch_game_event(
        session(), chat(11, message="next turn"),
    )
    for _ in range(100):
        if coordinator.accepted:
            break
        await asyncio.sleep(.001)

    assert queued is not None
    assert queued.status == "queued"
    assert len(coordinator.accepted) == 1


@pytest.mark.asyncio
async def test_player_queue_timeout_has_visible_degraded_outcome():
    class TimeoutCoordinator:
        async def submit_input_async(self, runtime_id, generation, value):
            raise ResourceExhausted("runtime input is already in flight")

        async def steer_input(self, runtime_id, generation, value):
            return False

        async def wait_input_available(self, runtime_id, generation, *, timeout):
            return False

    dispatcher = HostedRuntimeInputDispatcher(
        TimeoutCoordinator(), HostedSayReflectionRegistry(),
    )
    controller = session()

    queued = await dispatcher.dispatch_game_event(
        controller, chat(40, message="will this time out?"),
    )
    for _ in range(20):
        if controller.sent:
            break
        await asyncio.sleep(0)

    assert queued.status == "queued"
    assert controller.sent == [{
        "type": "hosted_status",
        "code": "player_reply_degraded",
        "retryable": False,
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [
    chat(1, player_id="42", player_name="AI"),
    chat(1, player_id=None),
    chat(1, player_id="7", origin="agent"),
    chat(1, player_id="7", origin="system"),
])
async def test_untrusted_or_non_human_chat_is_filtered_before_runtime(frame):
    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, HostedSayReflectionRegistry())

    await dispatcher.dispatch_game_event(session(), frame)

    assert coordinator.calls == []


@pytest.mark.asyncio
async def test_observer_game_chat_never_creates_runtime_input():
    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, HostedSayReflectionRegistry())

    observer = session(role="observer")
    await dispatcher.dispatch_game_event(observer, chat(1))

    assert coordinator.calls == []
    assert observer.sent == [{
        "type": "hosted_status",
        "code": "controller_reconnecting",
        "retryable": True,
    }]


@pytest.mark.asyncio
async def test_player_turn_without_say_gets_one_compensation_then_visible_degraded_status():
    coordinator = Coordinator()
    reflections = HostedSayReflectionRegistry()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, reflections)
    controller = session()

    await dispatcher.dispatch_game_event(controller, chat(20, message="are you there?"))
    original = coordinator.calls[0][2]
    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, original,
        SimpleNamespace(content="assistant final text is not delivered"), None,
    )

    assert len(coordinator.calls) == 2
    compensation = coordinator.calls[1][2]
    assert compensation.request_id == f"{original.request_id}:say-compensation"
    assert compensation.trace_context["source"] == "say_compensation"
    assert "call say exactly once" in compensation.content.lower()

    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, compensation,
        SimpleNamespace(content="still no say"), None,
    )
    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, compensation,
        SimpleNamespace(content="duplicate completion"), None,
    )

    assert len(coordinator.calls) == 2
    assert controller.sent == [{
        "type": "hosted_status",
        "code": "player_reply_degraded",
        "retryable": False,
    }]


@pytest.mark.asyncio
async def test_successful_say_needs_no_compensation_and_background_turn_is_ignored():
    coordinator = Coordinator()
    reflections = HostedSayReflectionRegistry()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, reflections)
    controller = session()

    await dispatcher.dispatch_game_event(controller, chat(21, message="hello"))
    original = coordinator.calls[0][2]
    reflections.mark_delivered(
        owner_user_id="tenant-1", game_session_id="game-1",
        operation_id=original.request_id,
    )
    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, original, SimpleNamespace(content="done"), None,
    )
    background = completion(job_id="job-ignore")
    await dispatcher.dispatch_background_completion(background)
    background_input = coordinator.calls[-1][2]
    await dispatcher.on_turn_completed(
        "runtime-tenant-1", 3, background_input, SimpleNamespace(content="done"), None,
    )

    assert [call[2].trace_context["source"] for call in coordinator.calls] == [
        "game_chat", "background_job_completion",
    ]
    assert controller.sent == []


@pytest.mark.asyncio
async def test_say_reflection_with_missing_player_id_is_filtered_by_command_provenance():
    coordinator = Coordinator()
    reflections = HostedSayReflectionRegistry()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, reflections)
    reflections.register(
        owner_user_id="tenant-1",
        game_session_id="game-1",
        target_player_id="42",
        message="I am on my way",
        command_id="cmd-say-1",
        tool_call_id="tool-say-1",
    )

    await dispatcher.dispatch_game_event(
        session(),
        chat(2, player_id=None, message="I am on my way", command_id="cmd-say-1"),
    )

    assert coordinator.calls == []


@pytest.mark.asyncio
async def test_same_text_from_another_human_is_not_cross_suppressed():
    coordinator = Coordinator()
    reflections = HostedSayReflectionRegistry()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, reflections)
    reflections.register(
        owner_user_id="tenant-1", game_session_id="game-1", target_player_id="42",
        message="same words", command_id="cmd", tool_call_id="tool",
    )

    await dispatcher.dispatch_game_event(
        session(), chat(3, player_id="7", message="same words"),
    )

    assert len(coordinator.calls) == 1


@pytest.mark.asyncio
async def test_repeated_chat_opens_loop_circuit_before_third_runtime_turn():
    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, HostedSayReflectionRegistry())

    await dispatcher.dispatch_game_event(session(), chat(1, message="repeat"))
    await dispatcher.dispatch_game_event(session(), chat(2, message="repeat"))
    await dispatcher.dispatch_game_event(session(), chat(3, message="repeat"))

    assert len(coordinator.calls) == 2


@pytest.mark.asyncio
async def test_same_text_is_isolated_by_tenant_and_game_session():
    coordinator = Coordinator()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, HostedSayReflectionRegistry())

    await dispatcher.dispatch_game_event(session(user_id="tenant-1"), chat(1, message="hello"))
    await dispatcher.dispatch_game_event(
        session(user_id="tenant-2", game_session_id="game-2"),
        chat(1, message="hello"),
    )

    assert len(coordinator.calls) == 2


@pytest.mark.asyncio
async def test_human_turn_then_hosted_say_reflection_creates_no_second_runtime_turn():
    coordinator = Coordinator()
    reflections = HostedSayReflectionRegistry()
    dispatcher = HostedRuntimeInputDispatcher(coordinator, reflections)
    facts = HostedHelloFacts(
        route_contract_version="starcoplay.hosted-route.v1",
        game_instance_nonce="nonce-1",
        capability_digest="digest-1",
        capability_entries=(("protocol-2.4.5", "say", "2.4.5"),),
        player_id="42",
        agent_control_target_player_id="42",
        agent_control_revision=1,
    )

    class RouteSession:
        def __init__(self):
            self.connection_id = "connection-1"
            self.instance = SimpleNamespace(user_id="tenant-1")
            self.outbound = asyncio.Queue()
            self.hosted_game_session_id = "game-1"
            self.hosted_connection_role = "controller"
            self.hosted_runtime_spec = SimpleNamespace(runtime_id="runtime-tenant-1", generation=3)
            self.hosted_facts = facts
            self._subscribers = []

        def subscribe(self, *, frame_types=None):
            queue = asyncio.Queue()
            self._subscribers.append(queue)
            return queue

        def unsubscribe(self, queue):
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    route_session = RouteSession()
    route = WebSocketHostedSessionRoute(
        "game-1", "resume-1", facts, route_session,
        say_reflections=reflections,
    )

    await dispatcher.dispatch_game_event(route_session, chat(1, message="come with me"))
    command = GameCommand(
        contract_version="starcoplay.game-command.v1",
        message_id="message-1",
        occurred_at=time.time(),
        idempotency_key="idem-1",
        command_id="command-say-1",
        tool_call_id="tool-say-1",
        capability_id="say",
        capability_version="2.4.5",
        capability_digest="digest-1",
        operation=SideEffect.MUTATING,
        validated_payload={"message": "on my way"},
        deadline_at=time.time() + 30,
        execution_context_ref="context-1",
        game_instance_nonce="nonce-1",
        lease_epoch=1,
    )
    await route.dispatch(command)
    await dispatcher.dispatch_game_event(
        route_session,
        chat(2, player_id=None, message="on my way", command_id="command-say-1"),
    )

    assert len(coordinator.calls) == 1
