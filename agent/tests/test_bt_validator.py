"""
tests.test_bt_validator — BtValidator 单元测试。

覆盖：合法简单/复杂 BT、深度超限、节点数超限、未知能力、非法节点类型、
缺少 type 字段、自定义白名单、child/children 冲突、装饰器 child 格式。
"""

from __future__ import annotations

import pytest

from slow_loop.bt_validator import BtValidator, DEFAULT_CAPABILITIES


@pytest.fixture
def validator():
    return BtValidator(max_depth=3, max_nodes=15)


# ── 合法 BT ──────────────────────────────────────────────────────────


class TestValidBT:
    def test_valid_simple_bt(self, validator):
        """简单合法 BT：单个 action 节点。"""
        bt = {"type": "action", "call": "go_to", "params": {"target": "player"}}
        ok, err = validator.validate(bt)
        assert ok is True
        assert err == ""

    def test_valid_complex_bt(self, validator):
        """多层合法 BT：深度=3，节点=10。

        v2.4.x 工具面变更：move_to → go_to，emote 合并到 say，do → interact。
        """
        bt = {
            "type": "selector",
            "name": "Root",
            "children": [
                {
                    "type": "sequence",
                    "name": "Branch1",
                    "children": [
                        {"type": "condition", "check": "energy < 30"},
                        {
                            "type": "sequence",
                            "name": "SubBranch",
                            "children": [
                                {"type": "action", "call": "go_to", "params": {"target": "home"}},
                                {"type": "action", "call": "say", "params": {"message": "回家"}},
                            ],
                        },
                    ],
                },
                {
                    "type": "sequence",
                    "name": "Branch2",
                    "children": [
                        {"type": "action", "call": "observe", "params": {}},
                        {"type": "action", "call": "interact", "params": {}},
                    ],
                },
                {"type": "action", "call": "remember", "params": {"type": "strategy_lesson", "content": "idle"}},
            ],
        }
        # 验证节点数和深度
        assert validator.count_nodes(bt) == 10
        assert validator.calc_max_depth(bt) == 3

        ok, err = validator.validate(bt)
        assert ok is True
        assert err == ""


# ── 拒绝非法 BT ─────────────────────────────────────────────────────


class TestRejectBT:
    def test_reject_depth_exceeded(self, validator):
        """深度=4 → 拒绝。"""
        bt = {
            "type": "sequence",
            "children": [
                {
                    "type": "sequence",
                    "children": [
                        {
                            "type": "sequence",
                            "children": [
                                {
                                    "type": "sequence",
                                    "children": [
                                        {"type": "action", "call": "observe"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        assert validator.calc_max_depth(bt) == 4
        ok, err = validator.validate(bt)
        assert ok is False
        assert "深度" in err

    def test_reject_nodes_exceeded(self, validator):
        """节点=16 → 拒绝。"""
        children = [
            {"type": "action", "call": "observe"} for _ in range(15)
        ]
        bt = {"type": "sequence", "children": children}
        assert validator.count_nodes(bt) == 16
        ok, err = validator.validate(bt)
        assert ok is False
        assert "节点数" in err

    def test_reject_unknown_capability(self, validator):
        """call="fish" 不在默认白名单 → 拒绝。"""
        bt = {"type": "action", "call": "fish", "params": {}}
        ok, err = validator.validate(bt)
        assert ok is False
        assert "fish" in err

    def test_reject_invalid_node_type(self, validator):
        """type="random" 不在合法类型 → 拒绝。"""
        bt = {"type": "random", "children": []}
        ok, err = validator.validate(bt)
        assert ok is False
        assert "random" in err

    def test_reject_missing_type(self, validator):
        """无 type 字段 → 拒绝。"""
        bt = {"call": "move_to", "params": {}}
        ok, err = validator.validate(bt)
        assert ok is False
        assert "type" in err


# ── 自定义白名单 ─────────────────────────────────────────────────────


class TestCustomCapabilities:
    def test_accept_custom_capabilities(self, validator):
        """自定义白名单含 "fish" → 通过。"""
        bt = {"type": "action", "call": "fish", "params": {}}
        custom_caps = frozenset({"fish", "move_to"})
        ok, err = validator.validate(bt, allowed_capabilities=custom_caps)
        assert ok is True
        assert err == ""


# ── child/children 冲突 ──────────────────────────────────────────────


class TestChildConflict:
    def test_child_children_conflict(self, validator):
        """同时有 child 和 children → 拒绝。"""
        bt = {
            "type": "repeat",
            "child": {"type": "action", "call": "observe"},
            "children": [{"type": "action", "call": "move_to"}],
        }
        ok, err = validator.validate(bt)
        assert ok is False
        assert "child" in err and "children" in err

    def test_decorator_child_format(self, validator):
        """repeat 节点用 "child" → 通过。"""
        bt = {
            "type": "repeat",
            "times": 3,
            "child": {"type": "action", "call": "observe"},
        }
        ok, err = validator.validate(bt)
        assert ok is True
        assert err == ""
