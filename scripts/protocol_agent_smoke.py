from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO_ROOT / "agent"
for path in (str(REPO_ROOT), str(AGENT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from bt_engine.message_bus import MessageBus  # noqa: E402
from memory.working_memory import WorkingMemory  # noqa: E402
from messages.types import TaskCompleted  # noqa: E402
from slow_loop.action_dispatcher import ActionDispatcher  # noqa: E402

from agent.protocol.testing.fake_mod_bridge import FakeProtocolModBridge  # noqa: E402


def main() -> int:
    bus = MessageBus()
    memory = WorkingMemory()
    stop = threading.Event()
    bridge = FakeProtocolModBridge(
        game_state={
            "time": "09:00",
            "player": {
                "location": "Farm",
                "x": 64,
                "y": 15,
                "facing": "down",
                "energy": 270,
            },
        }
    )
    dispatcher = ActionDispatcher(bridge, bus, memory, stop)
    dispatcher.start()
    try:
        print("== Fake Mod adapter capabilities ==")
        print_json(sorted(bridge.capability_names()))

        run_action(
            dispatcher,
            bus,
            bridge,
            "query_location",
            {},
            wait_result=True,
        )
        run_action(
            dispatcher,
            bus,
            bridge,
            "use_tool",
            {"x": 10, "y": 11, "charge_level": 0},
            wait_result=True,
        )
        run_action(
            dispatcher,
            bus,
            bridge,
            "say",
            {"message": "Hi from fake protocol smoke", "emote": "happy"},
            wait_result=False,
        )
    finally:
        dispatcher.stop()
    return 0


def run_action(
    dispatcher: ActionDispatcher,
    bus: MessageBus,
    bridge: FakeProtocolModBridge,
    action: str,
    params: dict[str, Any],
    *,
    wait_result: bool,
) -> None:
    print()
    print(f"== Agent action: {action} ==")
    print_json({"params": params, "wait_result": wait_result})

    job_id = dispatcher.submit(
        action,
        params,
        wait_result=wait_result,
        timeout_s=2.0,
        trigger_label="protocol-agent-smoke",
        needs_followup=wait_result,
    )
    if job_id is None:
        raise RuntimeError(f"dispatcher rejected action {action}")
    dispatcher.wait_until_job_writes(job_id, timeout_s=2.0)

    exchange = bridge.last_exchange
    if exchange is None:
        raise RuntimeError(f"action {action} did not produce a protocol exchange")
    print("-- Protocol exchange --")
    print_json(bridge.protocol_summary(exchange))

    if wait_result:
        msg = wait_for_task_completed(bus)
        if msg is None:
            raise RuntimeError(f"action {action} did not emit TaskCompleted")
        print("-- Agent-consumed TaskCompleted --")
        print_json(
            {
                "node_id": msg.node_id,
                "observation": msg.observation,
                "duration_s": msg.duration_s,
                "result": msg.result,
            }
        )
    else:
        print("-- Agent feedback --")
        print("instant action accepted; no TaskCompleted is emitted by ActionDispatcher")


def wait_for_task_completed(bus: MessageBus, timeout_s: float = 2.0) -> TaskCompleted | None:
    deadline = __import__("time").monotonic() + timeout_s
    while __import__("time").monotonic() < deadline:
        msg = bus.get_from_fast_blocking(timeout=0.1)
        if isinstance(msg, TaskCompleted):
            return msg
    return None


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
