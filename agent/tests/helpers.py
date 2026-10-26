"""
tests.helpers — 测试用的共享常量和辅助函数。

从 conftest.py 分离出来，供测试模块显式导入。
"""

from __future__ import annotations

from types import SimpleNamespace

# ── 有效的 GSP JSON 示例 ───────────────────────────────────────────────

VALID_GSP_SIMPLE: dict = {
    "monologue": "我应该跟着玩家走。",
    "action_plan": [
        {"action": "move_to", "params": {"location": "Farm"}},
    ],
}

VALID_GSP_COMPLEX: dict = {
    "monologue": "天色晚了，先回家休息。",
    "action_plan": [
        {"action": "move_to", "params": {"location": "FarmHouse"}},
        {"action": "say", "params": {"message": "我先回家了"}},
    ],
    "dialogue": {"content": "今天辛苦了！", "tone": "warm"},
}

VALID_GSP_WITH_DIALOGUE: dict = {
    "monologue": "玩家在跟我打招呼。",
    "action_plan": [
        {"action": "emote", "params": {"type": "happy"}},
    ],
    "dialogue": {"content": "你好呀！"},
}


# ── Mock LLM Response ──────────────────────────────────────────────────


def make_llm_response(content: str):
    """构造一个模拟的 llm_client.completion 返回值。"""
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def make_llm_fn(responses: list[str]):
    """创建一个按顺序返回多个响应的 mock llm_fn。"""
    call_count = {"n": 0}

    def _fn(**kwargs):
        idx = call_count["n"] % len(responses)
        call_count["n"] += 1
        return make_llm_response(responses[idx])

    return _fn
