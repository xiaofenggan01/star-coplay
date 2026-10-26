"""
slow_loop.bt_validator — 行为树拓扑验证器。

对 LLM 生成的 BT JSON 执行三层校验：
    L2：拓扑约束（深度、节点数、节点类型合法性）
    L3：能力白名单（action 节点的 call 值必须在允许列表中）

独立于 Planner 使用，也可被测试或其他模块单独调用。
"""

from __future__ import annotations

import logging
from typing import Optional

from .core_tools import get_core_tool_names, get_local_tool_names

log = logging.getLogger("slow_loop.bt_validator")


def build_allowed_capabilities(
    skill_names: list[str] | None = None,
) -> frozenset[str]:
    """动态构建允许的能力集合。

    合并 core_tools 定义的基本能力 + SkillLibrary 注册的技能名称。

    Args:
        skill_names: SkillLibrary 动态注册的技能名称列表，可为 None。

    Returns:
        包含所有允许 action call 值的 frozenset。
    """
    allowed: set[str] = set(get_core_tool_names())
    if skill_names:
        allowed.update(skill_names)
    return frozenset(allowed)


# 向后兼容：保留 DEFAULT_CAPABILITIES 常量，基于 core_tools 动态构建。
# 注意：仅包含 core_tools（9 个基本能力），不含 SkillLibrary 动态注册的技能。
# 运行时应通过 build_allowed_capabilities(skill_names=...) 构建包含 Skill 的完整白名单。
DEFAULT_CAPABILITIES: frozenset[str] = build_allowed_capabilities()

# 合法的 BT 节点类型枚举
VALID_NODE_TYPES: frozenset[str] = frozenset({
    "sequence",
    "reactive_sequence",
    "selector",
    "priority_selector",
    "parallel",
    "repeat",
    "retry",
    "timeout",
    "inverter",
    "succeeder",
    "condition",
    "action",
    "observe",
    "report",
    "escalate",
    "set_var",
})


class BtValidator:
    """行为树拓扑验证器（L2 + L3）。

    L2 检查拓扑约束：节点类型合法性、最大深度、最大节点数。
    L3 检查能力白名单：所有 action 节点的 call 值必须在允许列表中。
    """

    def __init__(self, max_depth: int = 3, max_nodes: int = 15) -> None:
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def validate(
        self,
        bt_json: dict,
        allowed_capabilities: frozenset[str] | None = None,
    ) -> tuple[bool, str]:
        """验证 BT JSON。

        Args:
            bt_json: 行为树 JSON 根节点。
            allowed_capabilities: 允许的 action call 值集合，
                                  默认使用 DEFAULT_CAPABILITIES。

        Returns:
            (is_valid, error_message)
            is_valid=True 时 error_message 为空字符串。
        """
        if allowed_capabilities is None:
            allowed_capabilities = build_allowed_capabilities(get_local_tool_names())

        # 根节点必须包含 type 字段
        if "type" not in bt_json:
            return False, "根节点缺少 'type' 字段"

        # 检测 child + children 共存（LLM 生成的非法结构）
        conflict = self._find_child_children_conflict(bt_json)
        if conflict:
            return False, f"节点同时包含 'child' 和 'children': {conflict}"

        # L2: 节点类型合法性
        invalid_types = self._find_invalid_types(bt_json)
        if invalid_types:
            return False, f"非法节点类型: {', '.join(sorted(invalid_types))}"

        # L2: 深度检查
        depth = self.calc_max_depth(bt_json)
        if depth > self.max_depth:
            return False, f"BT 深度 {depth} 超过上限 {self.max_depth}"

        # L2: 节点数检查
        nodes = self.count_nodes(bt_json)
        if nodes > self.max_nodes:
            return False, f"BT 节点数 {nodes} 超过上限 {self.max_nodes}"

        # L3: 能力白名单
        action_calls = self.extract_action_calls(bt_json)
        unknown = action_calls - allowed_capabilities
        if unknown:
            return False, f"未知的 action call: {', '.join(sorted(unknown))}"

        return True, ""

    def count_nodes(self, node: dict) -> int:
        """递归计算节点总数。"""
        count = 1
        for child in node.get("children", []):
            count += self.count_nodes(child)
        # repeat/inverter 等装饰器节点可能用 "child" 而非 "children"
        if "child" in node and isinstance(node["child"], dict):
            count += self.count_nodes(node["child"])
        return count

    def calc_max_depth(self, node: dict, current: int = 0) -> int:
        """递归计算最大深度。

        根节点深度为 0，每下一层 +1。
        """
        children: list[dict] = list(node.get("children", []))
        if "child" in node and isinstance(node["child"], dict):
            children.append(node["child"])
        if not children:
            return current
        return max(self.calc_max_depth(c, current + 1) for c in children)

    def extract_action_calls(self, node: dict) -> set[str]:
        """递归提取所有 action 节点的 call 值。"""
        calls: set[str] = set()
        if node.get("type") == "action" and "call" in node:
            calls.add(node["call"])
        for child in node.get("children", []):
            calls.update(self.extract_action_calls(child))
        if "child" in node and isinstance(node["child"], dict):
            calls.update(self.extract_action_calls(node["child"]))
        return calls

    def _find_child_children_conflict(self, node: dict) -> str | None:
        """递归查找同时包含 child 和 children 的节点。"""
        if "child" in node and "children" in node and node["children"]:
            return node.get("name", node.get("type", "unknown"))
        for child in node.get("children", []):
            result = self._find_child_children_conflict(child)
            if result:
                return result
        if "child" in node and isinstance(node["child"], dict):
            result = self._find_child_children_conflict(node["child"])
            if result:
                return result
        return None

    def _find_invalid_types(self, node: dict) -> set[str]:
        """递归查找所有非法节点类型。"""
        invalid: set[str] = set()
        node_type = node.get("type")
        if node_type is not None and node_type not in VALID_NODE_TYPES:
            invalid.add(node_type)
        for child in node.get("children", []):
            invalid.update(self._find_invalid_types(child))
        if "child" in node and isinstance(node["child"], dict):
            invalid.update(self._find_invalid_types(node["child"]))
        return invalid


# ---------------------------------------------------------------
# action_plan 列表校验（独立于 BtValidator 类）
# ---------------------------------------------------------------

def validate_action_plan(
    actions: list[dict],
    allowed_capabilities: frozenset[str] | None = None,
) -> tuple[bool, str]:
    """校验 action_plan 列表。

    Args:
        actions: action_plan 数组，每个元素应为 dict 且包含 action 字段。
        allowed_capabilities: 允许的 action 名称集合，
                              未提供时跳过白名单检查。

    Returns:
        (is_valid, error_message)
        is_valid=True 时 error_message 为空字符串。
    """
    if not isinstance(actions, list):
        return False, "action_plan 必须是数组"
    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            return False, f"action_plan[{i}] 不是对象"
        action_name = act.get("action")
        if not action_name or not isinstance(action_name, str):
            return False, f"action_plan[{i}] 缺少 action 字段"
        if allowed_capabilities and action_name not in allowed_capabilities:
            return False, f"action_plan[{i}]: 未知能力 '{action_name}'"
    return True, ""
