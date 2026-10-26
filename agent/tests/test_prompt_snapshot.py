"""
tests.test_prompt_snapshot — Sprint A 核心路径快照测试。

覆盖三大区域：
    1. core_tools 完整性（工具名、废弃工具不存在、FC 格式、中文 description）
    2. bt_validator 白名单（DEFAULT_CAPABILITIES、build_allowed_capabilities、validate_action_plan）
    3. system_prompt 无工具名泄漏

独立可运行，不依赖 Mod / WS 连接。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 导入路径修复（与 conftest.py 保持一致，但独立可运行）
# ---------------------------------------------------------------------------

AGENT_DIR = Path(__file__).resolve().parent.parent
AGENT_PARENT = AGENT_DIR.parent

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
if str(AGENT_PARENT) not in sys.path:
    sys.path.insert(0, str(AGENT_PARENT))

import types as _types

_PARENT_PKG = "agent"
if _PARENT_PKG not in sys.modules:
    _pkg = _types.ModuleType(_PARENT_PKG)
    _pkg.__path__ = [str(AGENT_DIR)]
    _pkg.__package__ = _PARENT_PKG
    sys.modules[_PARENT_PKG] = _pkg

_SUB_PACKAGES = ["slow_loop"]
for _sub in _SUB_PACKAGES:
    _fqn = f"{_PARENT_PKG}.{_sub}"
    if _fqn not in sys.modules:
        _sub_dir = AGENT_DIR / _sub
        _sub_pkg = _types.ModuleType(_fqn)
        _sub_pkg.__path__ = [str(_sub_dir)]
        _sub_pkg.__package__ = _fqn
        sys.modules[_fqn] = _sub_pkg

from agent.slow_loop.core_tools import (  # noqa: E402
    get_core_tool_names,
    get_core_tool_schemas,
    get_local_tool_names,
)
from agent.protocol.capability_registry import iter_llm_exposed_specs  # noqa: E402
from agent.slow_loop.bt_validator import (  # noqa: E402
    DEFAULT_CAPABILITIES,
    build_allowed_capabilities,
    validate_action_plan,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EXPECTED_CORE_TOOLS = frozenset(spec.tool_name for spec in iter_llm_exposed_specs())

DEPRECATED_TOOLS = [
    "do",
    "tp",
    "observe",
    "search_wiki",
    "read_wiki",
    "remember",
]

SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "slow_loop"
    / "prompts"
    / "system_prompt.txt"
)

# system_prompt 中不应出现的工具名（独立单词匹配，避免误报）
FORBIDDEN_IN_SYSTEM_PROMPT = [
    "move_to",
    "use_tool",
    "equip",
    "go_to",
    "observe",
    "query_",       # 任何 query_ 前缀
    "water_crops",
    "harvest_crops",
    "plant_crops",
    "chop_trees",
    "mine_rocks",
    "emote",
    "face",
    "tp",
    "do",           # 独立单词 "do"（\bdo\b，不含 undo）
]

# ---------------------------------------------------------------------------
# 1. core_tools 完整性测试
# ---------------------------------------------------------------------------


class TestCoreTools:
    """core_tools 模块返回的工具列表完整性验证。"""

    def test_core_tool_names(self):
        """验证 get_core_tool_names() 恰好返回 protocol registry 暴露工具名。"""
        names = get_core_tool_names()
        assert isinstance(names, list), "get_core_tool_names() 应返回 list"
        assert frozenset(names) == EXPECTED_CORE_TOOLS, (
            f"工具名集合不匹配。\n"
            f"  期望: {sorted(EXPECTED_CORE_TOOLS)}\n"
            f"  实际: {sorted(names)}"
        )

    def test_no_deprecated_tools(self):
        """验证 core_tool_names 不包含任何已废弃工具名。"""
        names = set(get_core_tool_names())
        for deprecated in DEPRECATED_TOOLS:
            assert deprecated not in names, (
                f"废弃工具 '{deprecated}' 仍存在于 core_tool_names 中"
            )

    def test_local_tools_are_not_default_core_tools(self):
        """验证 Agent-local tools 不混入默认 protocol core tools。"""
        names = set(get_core_tool_names())
        for local_name in get_local_tool_names():
            assert local_name not in names, (
                f"local tool '{local_name}' 不应出现在默认 core_tool_names 中"
            )

    def test_core_tool_schemas_format(self):
        """验证每个 schema 符合 OpenAI Function Calling 格式。"""
        schemas = get_core_tool_schemas()
        assert len(schemas) == len(EXPECTED_CORE_TOOLS), (
            f"get_core_tool_schemas() 应返回 {len(EXPECTED_CORE_TOOLS)} 个 schema，"
            f"实际 {len(schemas)} 个"
        )
        for schema in schemas:
            assert schema.get("type") == "function", (
                f"schema 缺少 type='function': {schema}"
            )
            fn = schema.get("function")
            assert isinstance(fn, dict), (
                f"schema['function'] 应为 dict: {schema}"
            )
            assert "name" in fn, f"function 缺少 'name' 字段: {fn}"
            assert "description" in fn, f"function 缺少 'description' 字段: {fn}"
            assert "parameters" in fn, f"function 缺少 'parameters' 字段: {fn}"
            params = fn["parameters"]
            assert isinstance(params, dict), (
                f"function['parameters'] 应为 dict: {fn}"
            )
            assert params.get("type") == "object", (
                f"parameters['type'] 应为 'object': {fn['name']} -> {params}"
            )

    def test_core_tool_schemas_are_protocol_compatible(self):
        """验证旧 core_tools 入口暴露的是 protocol-compatible 参数形态。"""
        schemas = {schema["function"]["name"]: schema for schema in get_core_tool_schemas()}
        assert "tp" not in schemas
        assert {"observe", "search_wiki", "read_wiki", "remember"}.isdisjoint(schemas)

        go_to_params = schemas["go_to"]["function"]["parameters"]["properties"]
        assert {"map_id", "x", "y", "arrival_mode"}.issubset(go_to_params)
        assert "target" not in go_to_params
        assert "target" in schemas["go_to"]["function"]["description"]

        move_to_params = schemas["move_to"]["function"]["parameters"]["properties"]
        assert {"mode", "x", "y"}.issubset(move_to_params)
        assert "跨地图" in schemas["move_to"]["function"]["description"]

    def test_descriptions_are_chinese(self):
        """验证所有 description 都包含中文字符。"""
        schemas = get_core_tool_schemas()
        # 中文 Unicode 范围：CJK 统一汉字 (U+4E00–U+9FFF)
        chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
        for schema in schemas:
            fn = schema["function"]
            desc = fn.get("description", "")
            assert chinese_pattern.search(desc), (
                f"工具 '{fn['name']}' 的 description 不含中文字符: {desc[:80]!r}"
            )


# ---------------------------------------------------------------------------
# 2. bt_validator 白名单测试
# ---------------------------------------------------------------------------


class TestBtValidator:
    """bt_validator 白名单逻辑验证。"""

    def test_default_capabilities_matches_core_tools(self):
        """DEFAULT_CAPABILITIES 应包含且仅包含 protocol core_tools 名称。"""
        assert isinstance(DEFAULT_CAPABILITIES, frozenset), (
            "DEFAULT_CAPABILITIES 应为 frozenset"
        )
        assert DEFAULT_CAPABILITIES == EXPECTED_CORE_TOOLS, (
            f"DEFAULT_CAPABILITIES 与 core_tools 不一致。\n"
            f"  期望: {sorted(EXPECTED_CORE_TOOLS)}\n"
            f"  实际: {sorted(DEFAULT_CAPABILITIES)}"
        )

    def test_build_allowed_with_skills(self):
        """build_allowed_capabilities 传入 Skill 名时应扩展为 core+N 个。"""
        skill_names = ["skill:water_crops", "skill:pet_animals"]
        allowed = build_allowed_capabilities(skill_names)
        assert isinstance(allowed, frozenset), (
            "build_allowed_capabilities() 应返回 frozenset"
        )
        expected_count = len(EXPECTED_CORE_TOOLS) + len(skill_names)
        assert len(allowed) == expected_count, (
            f"期望 {expected_count} 个能力，实际 {len(allowed)} 个: {sorted(allowed)}"
        )
        # 确保 core_tools 全部保留
        for name in EXPECTED_CORE_TOOLS:
            assert name in allowed, (
                f"core_tool '{name}' 在 build_allowed_capabilities 结果中丢失"
            )
        # 确保 Skill 已加入
        for skill in skill_names:
            assert skill in allowed, (
                f"Skill '{skill}' 未加入 build_allowed_capabilities 结果"
            )

    def test_build_allowed_without_skills(self):
        """build_allowed_capabilities() 不传参数时与 DEFAULT_CAPABILITIES 等价。"""
        allowed = build_allowed_capabilities()
        assert allowed == DEFAULT_CAPABILITIES

    def test_validate_action_plan_accepts_new_tools(self):
        """validate_action_plan 应接受 core_tools 中的新工具名。"""
        for tool_name in EXPECTED_CORE_TOOLS:
            actions = [{"action": tool_name}]
            is_valid, err = validate_action_plan(
                actions, allowed_capabilities=DEFAULT_CAPABILITIES
            )
            assert is_valid, (
                f"validate_action_plan 拒绝了合法工具 '{tool_name}': {err}"
            )

    def test_validate_action_plan_rejects_deprecated_do(self):
        """validate_action_plan 应拒绝废弃工具 'do'。"""
        actions = [{"action": "do"}]
        is_valid, err = validate_action_plan(
            actions, allowed_capabilities=DEFAULT_CAPABILITIES
        )
        assert not is_valid, (
            "validate_action_plan 未拒绝废弃工具 'do'"
        )
        assert "do" in err, f"错误信息中未提及 'do': {err}"

    def test_validate_action_plan_accepts_protocol_move_to(self):
        """validate_action_plan 应接受 protocol registry 中的 move_to。"""
        actions = [{"action": "move_to"}]
        is_valid, err = validate_action_plan(
            actions, allowed_capabilities=DEFAULT_CAPABILITIES
        )
        assert is_valid, (
            f"validate_action_plan 拒绝了 protocol 工具 'move_to': {err}"
        )

    def test_validate_action_plan_rejects_all_deprecated(self):
        """validate_action_plan 应拒绝所有非默认工具名。"""
        for deprecated in DEPRECATED_TOOLS:
            actions = [{"action": deprecated}]
            is_valid, err = validate_action_plan(
                actions, allowed_capabilities=DEFAULT_CAPABILITIES
            )
            assert not is_valid, (
                f"validate_action_plan 未拒绝废弃工具 '{deprecated}'"
            )

    def test_validate_action_plan_rejects_unknown_query_prefix(self):
        """validate_action_plan 应拒绝未在 protocol registry 中暴露的 query_* 工具。"""
        query_tools = ["query_objects", "query_npcs", "query_animals", "query_state"]
        for tool in query_tools:
            actions = [{"action": tool}]
            is_valid, _ = validate_action_plan(
                actions, allowed_capabilities=DEFAULT_CAPABILITIES
            )
            assert not is_valid, (
                f"validate_action_plan 未拒绝隐藏工具 '{tool}'"
            )


# ---------------------------------------------------------------------------
# 3. system_prompt 无工具名测试
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """system_prompt.txt 内容守护测试，确保不泄漏工具名。"""

    @pytest.fixture(scope="class")
    def system_prompt(self):
        """读取 system_prompt.txt 内容。"""
        assert SYSTEM_PROMPT_PATH.exists(), (
            f"system_prompt.txt 不存在: {SYSTEM_PROMPT_PATH}"
        )
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    def test_system_prompt_no_tool_names(self, system_prompt):
        """system_prompt 中不应包含任何工具名（独立单词匹配）。

        规则：
        - move_to, use_tool, equip, go_to, observe, query_* 等不应出现
        - 'do' 使用 \\bdo\\b 匹配（排除 undo 等误报）
        - 'say' 允许出现（角色说话的日常用词，不作限制）
        """
        violations = []
        for token in FORBIDDEN_IN_SYSTEM_PROMPT:
            if token == "do":
                # 独立单词匹配，避免误报 "undo"、"做" 等
                pattern = re.compile(r'\bdo\b', re.IGNORECASE)
            elif token.endswith("_"):
                # query_ 类前缀：查找任何以该前缀开头的词
                pattern = re.compile(
                    re.escape(token) + r'\w+',
                    re.IGNORECASE
                )
            else:
                # 其他工具名：独立单词匹配（含下划线的工具名不在单词边界内）
                # 使用字符边界检测：前后非字母数字下划线
                pattern = re.compile(
                    r'(?<![a-zA-Z0-9_])' + re.escape(token) + r'(?![a-zA-Z0-9_])',
                    re.IGNORECASE
                )
            matches = pattern.findall(system_prompt)
            if matches:
                violations.append(f"  '{token}' 出现 {len(matches)} 次: {matches[:3]}")

        assert not violations, (
            "system_prompt.txt 中出现了不应存在的工具名：\n"
            + "\n".join(violations)
            + f"\n\nsystem_prompt 路径: {SYSTEM_PROMPT_PATH}"
        )

    def test_system_prompt_has_say(self, system_prompt):
        """system_prompt.txt 应包含 'say' 相关内容（允许且鼓励出现）。"""
        # say 是角色行为的核心，system_prompt 必须说明 say 工具的语义
        assert "say" in system_prompt.lower(), (
            "system_prompt.txt 中未找到 'say' 相关内容，请确认工具说明是否完整"
        )

    def test_system_prompt_not_empty(self, system_prompt):
        """system_prompt.txt 不应为空。"""
        stripped = system_prompt.strip()
        assert len(stripped) > 100, (
            f"system_prompt.txt 内容过短（{len(stripped)} 字符），可能文件有问题"
        )

    def test_system_prompt_is_valid_utf8(self):
        """system_prompt.txt 应能以 UTF-8 正常读取。"""
        assert SYSTEM_PROMPT_PATH.exists(), (
            f"system_prompt.txt 不存在: {SYSTEM_PROMPT_PATH}"
        )
        try:
            content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
            assert len(content) > 0
        except UnicodeDecodeError as e:
            pytest.fail(f"system_prompt.txt UTF-8 解码失败: {e}")
