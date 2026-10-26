"""
tests.test_say_emote — say + emote 合并派发的单元测试。

验证：
1. say(message="hi") → 只派发 1 条 say 命令
2. say(message="hi", emote="happy") → 先派发 emote 命令，再派发 say 命令
3. say(message="hi", emote="invalid") → emote 跳过，say 正常执行

覆盖 deliberative_loop.py 中 _handle_say 的实现逻辑。
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# 导入 _handle_say 所在类。
# 为避免重复构造完整的 DeliberativeLoop（依赖较重），直接测试方法本身：
# 构造最小化的 loop 实例，只注入 mock bridge，验证 write_command 调用序列。
# ---------------------------------------------------------------------------


def _make_minimal_loop():
    """构造一个只含 bridge mock 的最小 DeliberativeLoop 实例，
    绕过 __init__ 中的重依赖（LLM、文件桥、内存系统等）。
    """
    import importlib
    # conftest 已注册 agent.* 虚拟父包，直接导入
    mod = importlib.import_module("agent.slow_loop.deliberative_loop")
    cls = mod.DeliberativeLoop

    # 用 object.__new__ 跳过 __init__
    loop = object.__new__(cls)

    # 注入最小属性
    bridge = MagicMock()
    loop._bridge = bridge
    loop._dispatcher = MagicMock()
    loop._memory_system = None
    loop._memory = MagicMock()

    return loop, bridge


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


class TestSayOnly:
    """无 emote 参数时只发 say 命令。"""

    def test_say_message_only(self):
        loop, bridge = _make_minimal_loop()
        loop._handle_say({"message": "hi"})

        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "hi"}, wait_result=False)

    def test_say_empty_emote_string(self):
        """emote 为空字符串等价于不传 emote。"""
        loop, bridge = _make_minimal_loop()
        loop._handle_say({"message": "hello", "emote": ""})

        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "hello"}, wait_result=False)

    def test_say_none_emote(self):
        """emote=None 等价于不传。"""
        loop, bridge = _make_minimal_loop()
        loop._handle_say({"message": "hello", "emote": None})

        # None 被视作 falsy，不触发 emote
        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "hello"}, wait_result=False)

    def test_say_empty_message_no_call(self):
        """message 为空字符串时不发任何命令。"""
        loop, bridge = _make_minimal_loop()
        loop._handle_say({"message": ""})

        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_not_called()

    def test_say_missing_message_no_call(self):
        """message 缺失时不发任何命令。"""
        loop, bridge = _make_minimal_loop()
        loop._handle_say({})

        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_not_called()


class TestSayWithEmote:
    """有合法 emote 参数时：先发 emote，再发 say。"""

    @pytest.mark.parametrize("emote", [
        "happy", "sad", "angry", "heart",
        "exclamation", "question", "laugh", "thinking",
    ])
    def test_say_with_valid_emote(self, emote):
        loop, bridge = _make_minimal_loop()
        loop._handle_say({"message": "嗨", "emote": emote})

        bridge.write_command.assert_called_once_with("emote", {"type": emote})
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "嗨"}, wait_result=False)

    def test_emote_then_say_order(self):
        """验证 emote 命令必须在 say 命令之前。"""
        loop, bridge = _make_minimal_loop()
        calls = []
        bridge.write_command.side_effect = lambda action, params: calls.append(("bridge", action, params))
        loop._dispatcher.submit.side_effect = (
            lambda action, params, **kwargs: calls.append(("dispatcher", action, params, kwargs))
        )

        loop._handle_say({"message": "出发！", "emote": "exclamation"})

        assert len(calls) == 2
        assert calls[0] == ("bridge", "emote", {"type": "exclamation"})
        assert calls[1] == ("dispatcher", "say", {"message": "出发！"}, {"wait_result": False})


class TestSayWithInvalidEmote:
    """非法 emote 值时：emote 跳过，say 正常执行，不抛异常。"""

    def test_invalid_emote_skipped(self):
        loop, bridge = _make_minimal_loop()
        loop._handle_say({"message": "hi", "emote": "invalid_emote_value"})

        # 只有 say，没有 emote
        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "hi"}, wait_result=False)

    def test_invalid_emote_does_not_raise(self):
        """非法 emote 不应导致异常，say 正常送出。"""
        loop, bridge = _make_minimal_loop()
        # 不应抛出
        loop._handle_say({"message": "OK", "emote": "😀"})
        bridge.write_command.assert_not_called()
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "OK"}, wait_result=False)


class TestEmoteFailureDoesNotBlockSay:
    """emote 命令发送失败时，say 命令仍然正常执行。"""

    def test_emote_exception_does_not_block_say(self):
        loop, bridge = _make_minimal_loop()

        # 让 write_command 在第一次调用（emote）时抛出异常
        call_count = []

        def side_effect(cmd, params):
            call_count.append(cmd)
            if cmd == "emote":
                raise RuntimeError("bridge disconnected")
            # say 正常

        bridge.write_command.side_effect = side_effect

        # 不应抛出，say 仍被调用
        loop._handle_say({"message": "你好", "emote": "happy"})

        assert "emote" in call_count
        loop._dispatcher.submit.assert_called_once_with("say", {"message": "你好"}, wait_result=False)
