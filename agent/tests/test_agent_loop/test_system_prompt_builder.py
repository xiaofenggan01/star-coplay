"""
test_system_prompt_builder.py — SystemPromptBuilder 单元测试。

测试 SystemPromptBuilder.build() 的完整性、容错性、幂等性。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# 由于 SystemPromptBuilder 尚未实现，测试中通过 try/except 优雅降级


# ── 测试用例 ────────────────────────────────────────────────────────────────


class TestSystemPromptBuilder:
    """SystemPromptBuilder 测试套件"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前尝试导入 SystemPromptBuilder"""
        try:
            from agent.slow_loop.system_prompt_builder import SystemPromptBuilder
            self.SystemPromptBuilder = SystemPromptBuilder
        except ImportError:
            pytest.skip("SystemPromptBuilder 尚未实现")

    @pytest.mark.p0
    def test_a1_build_returns_complete_prompt(self, fake_memory_system, loop_config):
        """A1. build() 返回完整 system prompt [P0]

        验证 build() 输出包含角色定义 + 记忆 + 行为约束。
        断言：返回非空字符串，包含 Facts 中的玩家信息。
        """
        builder = self.SystemPromptBuilder(fake_memory_system, loop_config)
        prompt = builder.build()

        # 验证返回非空字符串
        assert isinstance(prompt, str), "build() 应返回字符串"
        assert len(prompt) > 0, "build() 返回值不应为空"

        # 验证包含记忆中的玩家信息
        assert "测试玩家" in prompt, "prompt 应包含 Facts 中的玩家名"
        assert "Sebastian" in prompt, "prompt 应包含 Facts 中的好感度信息"

        # 验证包含最近摘要
        assert "第1天" in prompt, "prompt 应包含最近摘要"

    @pytest.mark.p0
    def test_a2_keeps_tool_call_contract_without_action_tool_schema(self, fake_memory_system, loop_config):
        """A2. build() 保留 FC 输出契约，但不包含动作工具 schema [P0]

        断言：返回值包含 tool-calling agent loop 的通用约束；
        具体动作工具清单和参数仍只由 tools schema 承载。
        """
        builder = self.SystemPromptBuilder(fake_memory_system, loop_config)
        prompt = builder.build()

        assert "tool_calls" in prompt
        assert "content" in prompt
        assert "玩家看不到" in prompt
        assert "say" in prompt.lower()
        assert "没有 tool_calls 的回复会被视为没有可执行行为" in prompt

        # 验证不包含具体动作工具名；say 是玩家可见输出契约，允许出现。
        tool_names = ["move_to", "interact", "go_to", "use_tool", "equip"]
        for tool_name in tool_names:
            assert tool_name not in prompt.lower(), (
                f"prompt 不应包含具体动作工具名 '{tool_name}'"
            )

        # 验证不包含工具清单/schema 描述关键词
        assert "可用工具" not in prompt, "prompt 不应包含 '可用工具' 字样"
        assert "parameters" not in prompt.lower(), "prompt 不应包含工具参数 schema"

    @pytest.mark.p1
    def test_a3_graceful_degradation_empty_memory(self, loop_config):
        """A3. build() 记忆为空时优雅降级 [P1]

        Mock: get_facts() → "", get_recent_summaries() → []
        断言：不抛异常，返回包含角色定义的字符串。
        """
        empty_memory = MagicMock()
        empty_memory.get_facts.return_value = ""
        empty_memory.get_recent_summaries.return_value = []

        builder = self.SystemPromptBuilder(empty_memory, loop_config)

        # 不应抛异常
        prompt = builder.build()

        # 验证返回非空字符串（至少包含角色定义）
        assert isinstance(prompt, str), "build() 应返回字符串"
        assert len(prompt) > 0, "即使记忆为空，build() 也应返回非空字符串"

    @pytest.mark.p1
    def test_a4_resilient_to_memory_system_error(self, loop_config):
        """A4. build() MemorySystem 异常时不崩溃 [P1]

        Mock: get_facts() raises RuntimeError
        断言：不抛异常，返回包含角色定义的字符串。
        """
        broken_memory = MagicMock()
        broken_memory.get_facts.side_effect = RuntimeError("Memory system error")
        broken_memory.get_recent_summaries.return_value = []

        builder = self.SystemPromptBuilder(broken_memory, loop_config)

        # 不应抛异常
        prompt = builder.build()

        # 验证返回非空字符串
        assert isinstance(prompt, str), "build() 应返回字符串"
        assert len(prompt) > 0, "即使 MemorySystem 异常，build() 也应返回非空字符串"

    @pytest.mark.p0
    def test_a5_build_output_idempotent(self, fake_memory_system, loop_config):
        """A5. build() 输出 session 内不变 [P0]

        连续调用 build() 两次。
        断言：两次返回值完全相同。
        """
        builder = self.SystemPromptBuilder(fake_memory_system, loop_config)

        # 连续调用两次
        prompt1 = builder.build()
        prompt2 = builder.build()

        # 验证完全相同
        assert prompt1 == prompt2, (
            "build() 应幂等：同一 builder 实例的两次调用应返回相同结果"
        )
        assert isinstance(prompt1, str), "build() 应返回字符串"
        assert len(prompt1) > 0, "build() 返回值不应为空"

    def test_a6_injects_l4_routing_principles_and_skill_metadata(self, fake_memory_system, loop_config):
        builder = self.SystemPromptBuilder(
            fake_memory_system,
            loop_config,
            skill_routing_summaries=[
                {
                    "name": "water_crops",
                    "description": "浇灌当前地图所有未浇水的作物",
                    "category": "farming",
                    "routing": {
                        "owns_discovery": True,
                        "handles_precheck": True,
                        "verifies_result": True,
                        "execution_scope": "current_map",
                        "ambiguity_policy": "execute_when_clear",
                    },
                }
            ],
        )

        prompt = builder.build()

        assert "L4 编排原则" in prompt
        assert "不要先 say 启动确认" in prompt
        assert "不能凭 observe 或记忆宣称任务完成" in prompt
        assert "内部调度状态不得进入游戏聊天" in prompt
        assert "skill-water_crops" in prompt
        assert "owns_discovery=true" in prompt
        assert "verifies_result=true" in prompt


class TestPersonaSection:
    """Persona 注入测试套件"""

    @pytest.fixture(autouse=True)
    def setup(self):
        try:
            from agent.slow_loop.system_prompt_builder import SystemPromptBuilder
            self.SystemPromptBuilder = SystemPromptBuilder
        except ImportError:
            pytest.skip("SystemPromptBuilder 尚未实现")

    def _make_builder(self, persona: dict | None = None) -> "SystemPromptBuilder":
        memory = MagicMock()
        memory.get_facts.return_value = ""
        memory.get_recent_summaries.return_value = []
        config: dict = {"slow_loop": {"agent_name": "小星", "player_name": "玩家"}}
        if persona is not None:
            config["persona"] = persona
        return self.SystemPromptBuilder(memory, config)

    def test_no_persona_returns_empty(self):
        """config 无 persona 时 _build_persona_section 返回空"""
        builder = self._make_builder(persona=None)
        assert builder._build_persona_section() == ""

    def test_persona_with_description_only(self):
        """只有 description 时生成有效段落"""
        builder = self._make_builder(persona={
            "description": "活泼话多的钓鱼佬",
            "axes": {"speech_style": 50, "game_style": 50, "initiative": 50},
            "traits": [],
        })
        section = builder._build_persona_section()
        assert "【性格】" in section
        assert "活泼话多的钓鱼佬" in section

    def test_persona_axis_labels(self):
        """三轴标签正确映射"""
        builder = self._make_builder(persona={
            "description": "测试",
            "axes": {"speech_style": 90, "game_style": 10, "initiative": 50},
            "traits": [],
        })
        section = builder._build_persona_section()
        assert "活泼不羁" in section
        assert "效率至上" in section
        assert "适中" in section

    def test_persona_trait_tags(self):
        """特质标签正确映射"""
        builder = self._make_builder(persona={
            "description": "测试",
            "axes": {"speech_style": 50, "game_style": 50, "initiative": 50},
            "traits": ["Tag_Fisher", "Tag_NightOwl"],
        })
        section = builder._build_persona_section()
        assert "钓鱼" in section
        assert "夜猫子" in section
        assert "偏好" in section

    def test_persona_in_full_build(self):
        """persona 段落出现在完整 build() 输出中"""
        builder = self._make_builder(persona={
            "description": "安安静静的陪玩",
            "axes": {"speech_style": 20, "game_style": 70, "initiative": 10},
            "traits": [],
        })
        prompt = builder.build()
        assert "安安静静的陪玩" in prompt
        assert "【性格】" in prompt

    def test_persona_section_token_budget(self):
        """人格段落 token 预算 < 50 tokens (~100 中文字符)"""
        builder = self._make_builder(persona={
            "description": "一个话很多的钓鱼佬，看到水就要去钓鱼，整天叽叽喳喳找我聊天，对农活和进度一点不上心，开心最重要。休息时喜欢到处闲逛看风景，偶尔也会去矿洞冒险。",
            "axes": {"speech_style": 95, "game_style": 85, "initiative": 90},
            "traits": ["Tag_Fisher", "Tag_NightOwl"],
        })
        section = builder._build_persona_section()
        cn_chars = sum(1 for c in section if '一' <= c <= '鿿')
        assert cn_chars <= 100, f"人格段落中文字符数 {cn_chars} 超过 100 字预算"

    def test_persona_idempotent(self):
        """多次 build 返回相同结果"""
        builder = self._make_builder(persona={
            "description": "测试",
            "axes": {"speech_style": 50, "game_style": 50, "initiative": 50},
            "traits": [],
        })
        assert builder.build() == builder.build()

    def test_persona_invalid_axes_ignored(self):
        """axes 不是 dict 时不崩溃"""
        builder = self._make_builder(persona={
            "description": "测试",
            "axes": "invalid",
            "traits": [],
        })
        section = builder._build_persona_section()
        assert "测试" in section

    def test_persona_empty_description_skipped(self):
        """description 为空时仍能基于 axes 生成"""
        builder = self._make_builder(persona={
            "description": "",
            "axes": {"speech_style": 90, "game_style": 90, "initiative": 90},
            "traits": ["Tag_Farmer"],
        })
        section = builder._build_persona_section()
        assert "活泼不羁" in section
        assert "种地" in section
