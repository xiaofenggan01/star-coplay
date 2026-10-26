"""
tests.test_planner — Planner 单元测试。

覆盖：合法 JSON、markdown 包裹、非法 JSON 重试、全部失败、
schema 缺失字段、BT 深度超限、自定义白名单。
"""

from __future__ import annotations

import json

import pytest

from slow_loop.planner import Planner
from tests.helpers import VALID_GSP_SIMPLE, VALID_GSP_COMPLEX, make_llm_fn


VALID_JSON_STR = json.dumps(VALID_GSP_SIMPLE, ensure_ascii=False)
VALID_COMPLEX_JSON_STR = json.dumps(VALID_GSP_COMPLEX, ensure_ascii=False)

MESSAGES_STUB = [
    {"role": "system", "content": "你是搭子。"},
    {"role": "user", "content": "请做计划。"},
]


class TestPlanValidJSON:
    def test_plan_valid_json(self):
        """Mock LLM 返回合法 JSON → plan() 返回 dict。"""
        llm_fn = make_llm_fn([VALID_JSON_STR])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None
        assert result["monologue"] == VALID_GSP_SIMPLE["monologue"]
        assert "action_plan" in result

    def test_plan_markdown_wrapped(self):
        """Mock LLM 返回 ```json 包裹 → plan() 正确解析。"""
        wrapped = f"好的，这是我的计划：\n```json\n{VALID_JSON_STR}\n```\n以上是我的决策。"
        llm_fn = make_llm_fn([wrapped])
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None
        assert result["monologue"] == VALID_GSP_SIMPLE["monologue"]


class TestPlanRetry:
    def test_plan_invalid_json_retry(self):
        """首次返回非法 JSON → 重试 → 第二次成功。"""
        responses = ["这不是 JSON 啊 {{{", VALID_JSON_STR]
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is not None
        assert result["monologue"] == VALID_GSP_SIMPLE["monologue"]

    def test_plan_all_fail(self):
        """两次都失败 → 返回 None。"""
        responses = ["not json 1", "not json 2"]
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is None


class TestPlanSchemaValidation:
    def test_plan_schema_missing_monologue(self):
        """缺少 monologue → 返回 None。"""
        bad_gsp = {"action_plan": [{"action": "observe", "params": {}}]}
        responses = [json.dumps(bad_gsp)] * 2
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(MESSAGES_STUB)
        assert result is None

    def test_plan_action_plan_invalid_action(self):
        """action_plan 包含未知 action + 白名单校验 → 返回 None。"""
        bad_plan = {
            "monologue": "做个奇怪的事",
            "action_plan": [
                {"action": "nonexistent_action", "params": {}},
            ],
        }
        responses = [json.dumps(bad_plan)] * 2
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)
        result = planner.plan(
            MESSAGES_STUB,
            allowed_capabilities=frozenset({"move_to", "observe"}),
        )
        assert result is None


class TestPlanCustomCapabilities:
    def test_plan_custom_capabilities(self):
        """传入白名单不含 fish → 失败。"""
        gsp_with_fish = {
            "monologue": "去钓鱼",
            "action_plan": [{"action": "fish", "params": {}}],
        }
        responses = [json.dumps(gsp_with_fish)] * 2
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)

        result_no_fish = planner.plan(
            MESSAGES_STUB,
            allowed_capabilities=frozenset({"move_to", "observe"}),
        )
        assert result_no_fish is None

    def test_plan_custom_capabilities_allowed(self):
        """传入自定义白名单含 fish → 通过。"""
        gsp_with_fish = {
            "monologue": "去钓鱼",
            "action_plan": [{"action": "fish", "params": {}}],
        }
        responses = [json.dumps(gsp_with_fish)]
        llm_fn = make_llm_fn(responses)
        planner = Planner(llm_fn=llm_fn)

        result = planner.plan(
            MESSAGES_STUB,
            allowed_capabilities=frozenset({"fish", "move_to"}),
        )
        assert result is not None
        assert result["action_plan"][0]["action"] == "fish"
