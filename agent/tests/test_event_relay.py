"""EventRelay 事件路由测试。"""
import pytest
from unittest.mock import MagicMock, patch
from game_monitor import EventRelay
from messages.types import EpisodeBoundaryReport, ReportEvent, PlayerInput


@pytest.fixture
def relay():
    mock_bus = MagicMock()
    mock_bridge = MagicMock()
    mock_bridge.state_path = "/tmp/test_state.json"
    mock_bridge.read_new_events.return_value = []
    with patch("game_monitor.GameStateCache"):
        r = EventRelay(message_bus=mock_bus, bridge=mock_bridge)
    r._last_event_seq = 0
    return r


def _make_event(evt_type, data=None, seq=1):
    return {"type": evt_type, "data": data or {}, "seq": seq}


class TestDayEnding:
    def test_creates_episode_boundary_report(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("day_ending", {"day": 5, "season": "spring"})
        ]
        relay._poll_game_events()
        relay._bus.send_to_slow.assert_called_once()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, EpisodeBoundaryReport)
        assert msg.trigger == "day_end"
        assert msg.game_context["day"] == 5
        assert msg.game_context["season"] == "spring"
        assert relay._bus.send_to_slow.call_args.kwargs["priority"] == 4


class TestDayStarted:
    def test_creates_report_event_not_player_input(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("day_started", {"day": 6, "season": "spring", "weather": "sunny"})
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, ReportEvent), "day_started should be ReportEvent, not PlayerInput"
        assert not isinstance(msg, PlayerInput)
        assert msg.significance == 8
        assert relay._bus.send_to_slow.call_args.kwargs["priority"] == 7


class TestOtherEvents:
    def test_chat_mojibake_repaired_before_player_input(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("chat_received", {"player_name": "test", "message": "鎴戞槸璇翠綘"}, seq=1)
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, PlayerInput)
        assert "我是说你" in msg.text
        assert relay._bus.send_to_slow.call_args.kwargs["priority"] == 2

    def test_chat_from_target_player_is_ignored(self, relay):
        relay._target_player_id = 42
        relay._bridge.read_new_events.return_value = [
            _make_event(
                "chat_received",
                {"player_id": 42, "player_name": "AI", "message": "thinking"},
                seq=1,
            )
        ]
        relay._poll_game_events()
        relay._bus.send_to_slow.assert_not_called()

    def test_chat_from_other_player_forwarded_when_target_configured(self, relay):
        relay._target_player_id = 42
        relay._bridge.read_new_events.return_value = [
            _make_event(
                "chat_received",
                {"player_id": "7", "player_name": "Player", "message": "hello"},
                seq=1,
            )
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, PlayerInput)
        assert msg.text == "Player: hello"

    def test_chat_without_player_id_remains_backward_compatible(self, relay):
        relay._target_player_id = 42
        relay._bridge.read_new_events.return_value = [
            _make_event(
                "chat_received",
                {"player_name": "LegacyPlayer", "message": "old event"},
                seq=1,
            )
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, PlayerInput)
        assert msg.text == "LegacyPlayer: old event"

    def test_energy_low_triggers_llm(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("energy_low", {"current": 20, "max": 100, "percent": 0.2})
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, ReportEvent)
        assert msg.significance >= 7

    def test_npc_nearby_no_llm_trigger(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("npc_nearby", {"name": "Abigail", "distance": 5})
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, ReportEvent)
        assert msg.significance < 7

    def test_time_changed_silently_dropped(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("time_changed", {"time": 800})
        ]
        relay._poll_game_events()
        relay._bus.send_to_slow.assert_not_called()

    def test_inventory_changed_without_high_value_dropped(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("inventory_changed", {"added": ["stone"], "removed": []})
        ]
        relay._poll_game_events()
        relay._bus.send_to_slow.assert_not_called()

    def test_inventory_changed_with_high_value_reported(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("inventory_changed", {"added": ["diamond"], "high_value_item": "diamond"})
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, ReportEvent)
        assert msg.event_type == "inventory_changed"

    def test_dialogue_opened_triggers_llm(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("dialogue_opened", {"type": "npc", "npc_name": "Abigail"})
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, ReportEvent)
        assert msg.significance >= 7

    def test_season_changed_high_significance(self, relay):
        relay._bridge.read_new_events.return_value = [
            _make_event("season_changed", {"from": "spring", "to": "summer", "year": 1})
        ]
        relay._poll_game_events()
        msg = relay._bus.send_to_slow.call_args[0][0]
        assert isinstance(msg, ReportEvent)
        assert msg.significance == 8
