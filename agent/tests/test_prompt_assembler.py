"""
tests.test_prompt_assembler — PromptAssembler 单元测试。

覆盖：基本组装、全段有内容、EscalateRequest 触发、PlayerInput 触发、
超长工作记忆裁剪、能力列表出现在 system message 中。
"""

from __future__ import annotations

import pytest

from memory.working_memory import WorkingMemory
from messages.types import EscalateRequest, PlayerInput
from slow_loop.prompt_assembler import PromptAssembler


@pytest.fixture
def assembler(tmp_path):
    """创建 PromptAssembler，使用临时模板目录。"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # system_prompt.txt
    (prompts_dir / "system_prompt.txt").write_text(
        "你是{agent_name}，和{player_name}一起冒险。", encoding="utf-8"
    )

    # output_schema.txt
    (prompts_dir / "output_schema.txt").write_text(
        "请输出 JSON，包含 monologue 和 behavior_tree。", encoding="utf-8"
    )

    # trigger_templates/
    trigger_dir = prompts_dir / "trigger_templates"
    trigger_dir.mkdir()

    (trigger_dir / "escalate.txt").write_text(
        "【当前触发：快环需要帮助】\n原因：{escalate_reason}\n节点：{current_bt_node}",
        encoding="utf-8",
    )
    (trigger_dir / "player_chat.txt").write_text(
        "【当前触发：玩家说话了】\n{player_name} 说：\"{player_message}\"",
        encoding="utf-8",
    )
    (trigger_dir / "periodic.txt").write_text(
        "【定期检查】", encoding="utf-8"
    )

    return PromptAssembler(
        prompts_dir=prompts_dir,
        config={"agent_name": "小星", "player_name": "玩家"},
    )


@pytest.fixture
def simple_game_state():
    return {
        "time": "10:00",
        "weather": "sunny",
        "player": {"location": "Farm", "energy": 200, "max_energy": 270},
    }


@pytest.fixture
def simple_caps():
    return [
        {"name": "move_to", "description": "移动"},
        {"name": "say", "description": "说话"},
    ]


@pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
class TestAssembleBasic:
    def test_assemble_basic(self, assembler, simple_game_state, simple_caps, working_memory):
        """基本组装 → 返回 [system, user] 格式。"""
        trigger = "periodic_check"
        result = assembler.assemble(
            trigger_msg=trigger,
            game_state=simple_game_state,
            working_memory=working_memory,
            capabilities=simple_caps,
        )
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_assemble_with_all_segments(
        self, assembler, simple_game_state, simple_caps, working_memory_with_events
    ):
        """所有 7 段有内容 → 各段出现在 user content 中。"""
        persistent_facts = {
            "player": {"name": "小明", "likes": "钓鱼"},
            "relationship": {"stage": "好友", "days_together": 30},
        }
        episode_summaries = ["昨天一起挖矿", "前天种了南瓜"]

        result = assembler.assemble(
            trigger_msg="periodic",
            game_state=simple_game_state,
            working_memory=working_memory_with_events,
            capabilities=simple_caps,
            persistent_facts=persistent_facts,
            episode_summaries=episode_summaries,
        )
        user_content = result[1]["content"]

        # 验证各段内容存在
        assert "小明" in user_content  # persistent facts
        assert "挖矿" in user_content  # episode summaries
        assert "浇了南瓜" in user_content  # working memory events
        assert "Farm" in user_content  # game state
        # output_schema 在 FC 模式下已跳过，不再检查 JSON

    def test_assemble_includes_dispatcher_tasks(self, assembler, simple_game_state, simple_caps, working_memory):
        result = assembler.assemble(
            trigger_msg="periodic",
            game_state=simple_game_state,
            working_memory=working_memory,
            capabilities=simple_caps,
            dispatcher_snapshot={
                "active_job": {
                    "action": "move_to",
                    "job_id": "job_active",
                    "status": "running",
                    "running_for": 12.0,
                    "trigger_label": "PlayerInput:test",
                },
                "pending_jobs": [
                    {
                        "action": "go_bed",
                        "job_id": "job_pending",
                        "status": "pending",
                        "trigger_label": "PlayerInput:test",
                    }
                ],
            },
        )
        user_content = result[1]["content"]
        assert "当前后台任务" in user_content
        assert "move_to" in user_content
        assert "go_bed" in user_content


@pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
class TestTriggerTypes:
    def test_trigger_escalate(self, assembler, simple_game_state, simple_caps, working_memory):
        """EscalateRequest 触发 → 包含"快环需要帮助"。"""
        trigger = EscalateRequest(
            reason="不知道做什么",
            correlation_id="test-001",
            game_state=simple_game_state,
        )
        result = assembler.assemble(
            trigger_msg=trigger,
            game_state=simple_game_state,
            working_memory=working_memory,
            capabilities=simple_caps,
        )
        user_content = result[1]["content"]
        assert "快环需要帮助" in user_content
        assert "不知道做什么" in user_content

    def test_trigger_player_chat(self, assembler, simple_game_state, simple_caps, working_memory):
        """PlayerInput 触发 → 包含"玩家说话了"。"""
        trigger = PlayerInput(text="你好啊小星！")
        result = assembler.assemble(
            trigger_msg=trigger,
            game_state=simple_game_state,
            working_memory=working_memory,
            capabilities=simple_caps,
        )
        user_content = result[1]["content"]
        assert "玩家说话了" in user_content
        assert "你好啊小星" in user_content


@pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
class TestBudgetTrimming:
    def test_budget_trimming(self, assembler, simple_game_state, simple_caps):
        """超长工作记忆 → 被裁剪到预算内。"""
        wm = WorkingMemory(max_events=200)
        # 注入大量事件以触发裁剪
        long_events = [
            {"type": f"event_{i}", "content": f"这是一段很长的事件描述文本" * 10, "ts": float(i)}
            for i in range(100)
        ]
        wm.remember(simple_game_state, long_events)

        result = assembler.assemble(
            trigger_msg="periodic",
            game_state=simple_game_state,
            working_memory=wm,
            capabilities=simple_caps,
            persistent_facts={"player": {"name": "测试", "likes": "长文本" * 50}},
            episode_summaries=[f"Episode {i}: 很长很长" * 20 for i in range(10)],
        )
        # 应该返回有效结果而不崩溃
        assert isinstance(result, list)
        assert len(result) == 2
        user_content = result[1]["content"]
        # 验证裁剪后事件数量减少（不是全部 100 条）
        assert user_content.count("event_") < 100


@pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
class TestCapabilitiesInSystem:
    def test_capabilities_in_system(self, assembler, simple_game_state, simple_caps, working_memory):
        """能力列表出现在 system message 中。

        v2.4.x: capabilities 参数保留向后兼容但不再用于 system message；
        system message 改从 core_tools 读取（含 go_to/interact 而非 move_to）。
        """
        result = assembler.assemble(
            trigger_msg="periodic",
            game_state=simple_game_state,
            working_memory=working_memory,
            capabilities=simple_caps,
        )
        system_content = result[0]["content"]
        # v2.4.x core_tools 中的实际工具名
        assert "go_to" in system_content
        assert "interact" in system_content
        assert "say" in system_content
        assert "可用能力" in system_content
