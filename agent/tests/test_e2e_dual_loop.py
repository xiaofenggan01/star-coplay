"""
tests.test_e2e_dual_loop — 端到端双环（FC）：EscalateRequest → write_command。
"""

from __future__ import annotations

import time

from bt_engine.message_bus import MessageBus
from memory.working_memory import WorkingMemory
from messages.types import EscalateRequest
from slow_loop.deliberative_loop import DeliberativeLoop
from tests.test_fc_migration import make_fc_llm_fn, make_fc_response


class TestEscalateE2E:
    def test_escalate_e2e(self, mock_bridge, working_memory):
        bus = MessageBus()
        resp = make_fc_response(
            tool_calls=[{"name": "move_to", "arguments": {"location": "Farm"}}],
            content="去农场",
        )
        llm_fn = make_fc_llm_fn([resp])

        config = {
            "slow_loop": {
                "min_call_interval_s": 0.0,
                "player_input_debounce_s": 0.5,
                "max_bt_depth": 3,
                "max_bt_nodes": 15,
            }
        }
        loop = DeliberativeLoop(
            message_bus=bus,
            bridge=mock_bridge,
            working_memory=working_memory,
            config=config,
        )
        loop._llm_fn = llm_fn

        loop.start()
        try:
            escalate_msg = EscalateRequest(
                reason="发现新季节，需要调整种植计划",
                correlation_id="e2e-test-001",
                game_state={
                    "time": "8:00",
                    "player": {"location": "Farm", "energy": 250},
                },
                current_bt_node="WaterCrops",
            )
            bus.send_to_slow(escalate_msg, priority=1)

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if mock_bridge.write_command.called:
                    break
                time.sleep(0.05)

            assert mock_bridge.write_command.called
            first = mock_bridge.write_command.call_args_list[0]
            assert first[0][0] == "move_to"
            assert first[0][1].get("location") == "Farm"
        finally:
            loop.stop()
