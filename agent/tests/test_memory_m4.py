"""
tests.test_memory_m4 — 记忆系统 Sprint M4 集成测试。

测试覆盖：
    - TestMemoryE2ESimulation         : 模拟 3 天游戏流程（append → archive → get_context）
    - TestMemoryPersistence           : 重启恢复（创建→写入→销毁→重建→验证数据持久化）
    - TestMemoryPromptIntegration     : 与 PromptAssembler 集成（memory_context 注入 system message）
    - TestDeliberativeLoopMemoryIntegration : 慢环集成点（PlayerInput/EpisodeBoundaryReport）
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── 导入路径修复 ──────────────────────────────────────────────────────────────

AGENT_DIR = Path(__file__).resolve().parent.parent
AGENT_PARENT = AGENT_DIR.parent

if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
if str(AGENT_PARENT) not in sys.path:
    sys.path.insert(0, str(AGENT_PARENT))

_PARENT_PKG = "agent"
if _PARENT_PKG not in sys.modules:
    _pkg = types.ModuleType(_PARENT_PKG)
    _pkg.__path__ = [str(AGENT_DIR)]
    _pkg.__package__ = _PARENT_PKG
    sys.modules[_PARENT_PKG] = _pkg

# 注册 memory 子包
_MEMORY_PKG = f"{_PARENT_PKG}.memory"
if _MEMORY_PKG not in sys.modules:
    _memory_dir = AGENT_DIR / "memory"
    _memory_pkg = types.ModuleType(_MEMORY_PKG)
    _memory_pkg.__path__ = [str(_memory_dir)]
    _memory_pkg.__package__ = _MEMORY_PKG
    sys.modules[_MEMORY_PKG] = _memory_pkg

# 注册 slow_loop 子包
_SLOW_PKG = f"{_PARENT_PKG}.slow_loop"
if _SLOW_PKG not in sys.modules:
    _slow_dir = AGENT_DIR / "slow_loop"
    _slow_pkg = types.ModuleType(_SLOW_PKG)
    _slow_pkg.__path__ = [str(_slow_dir)]
    _slow_pkg.__package__ = _SLOW_PKG
    sys.modules[_SLOW_PKG] = _slow_pkg

# 注册 bt_engine 子包（慢环依赖）
_BT_PKG = f"{_PARENT_PKG}.bt_engine"
if _BT_PKG not in sys.modules:
    _bt_dir = AGENT_DIR / "bt_engine"
    _bt_pkg = types.ModuleType(_BT_PKG)
    _bt_pkg.__path__ = [str(_bt_dir)]
    _bt_pkg.__package__ = _BT_PKG
    sys.modules[_BT_PKG] = _bt_pkg

# 注册 bridge 子包
_BRIDGE_PKG = f"{_PARENT_PKG}.bridge"
if _BRIDGE_PKG not in sys.modules:
    _bridge_dir = AGENT_DIR / "bridge"
    _bridge_pkg = types.ModuleType(_BRIDGE_PKG)
    _bridge_pkg.__path__ = [str(_bridge_dir)]
    _bridge_pkg.__package__ = _BRIDGE_PKG
    sys.modules[_BRIDGE_PKG] = _bridge_pkg

# 注册 messages 子包
_MSG_PKG = f"{_PARENT_PKG}.messages"
if _MSG_PKG not in sys.modules:
    _msg_dir = AGENT_DIR / "messages"
    _msg_pkg = types.ModuleType(_MSG_PKG)
    _msg_pkg.__path__ = [str(_msg_dir)]
    _msg_pkg.__package__ = _MSG_PKG
    sys.modules[_MSG_PKG] = _msg_pkg

# 注册 skills 子包
_SKILLS_PKG = f"{_PARENT_PKG}.skills"
if _SKILLS_PKG not in sys.modules:
    _skills_dir = AGENT_DIR / "skills"
    _skills_pkg = types.ModuleType(_SKILLS_PKG)
    _skills_pkg.__path__ = [str(_skills_dir)]
    _skills_pkg.__package__ = _SKILLS_PKG
    sys.modules[_SKILLS_PKG] = _skills_pkg

# 导入并注册各模块（短名 + 全名）
_ew_mod = importlib.import_module("agent.memory.event_writer")
sys.modules["memory.event_writer"] = _ew_mod

_cw_mod = importlib.import_module("agent.memory.conversation_writer")
sys.modules["memory.conversation_writer"] = _cw_mod

_sg_mod = importlib.import_module("agent.memory.summary_generator")
sys.modules["memory.summary_generator"] = _sg_mod

_fm_mod = importlib.import_module("agent.memory.facts_manager")
sys.modules["memory.facts_manager"] = _fm_mod

_ms_mod = importlib.import_module("agent.memory.memory_system")
sys.modules["memory.memory_system"] = _ms_mod

from memory.memory_system import MemorySystem

# ────────────────────────────────────────────────────────────────────────────
# 辅助：mock litellm 响应
# ────────────────────────────────────────────────────────────────────────────

def _make_litellm_mock(content: str) -> MagicMock:
    """构造一个模拟 litellm 响应对象。"""
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]
    return mock_response


MOCK_SUMMARY = """## Day 1 总结

今天是春天第1天。完成了浇水任务，与玩家进行了友好交流。

### 关键事件
- 完成了所有作物的浇水
- 玩家询问了农场状况

### 体力消耗
从满体力降至50点。
"""

MOCK_FACTS = """## 关于搭子

- 名字：（待确认）
- 关系阶段：初识

## 玩家偏好

- 偏好早上安排农业任务
- 关注作物生长状态

## 我们的关系

- 阶段：初识（第1天）
"""


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    """提供临时记忆根目录。"""
    return tmp_path / "memory"


@pytest.fixture
def memory_system(memory_root: Path) -> MemorySystem:
    """提供带 mock LLM 的 MemorySystem 实例。"""
    with patch("llm_client.completion", return_value=_make_litellm_mock(MOCK_SUMMARY)):
        ms = MemorySystem(memory_root=memory_root)
    return ms


# ────────────────────────────────────────────────────────────────────────────
# TestMemoryE2ESimulation: 模拟 3 天游戏流程
# ────────────────────────────────────────────────────────────────────────────


class TestMemoryE2ESimulation:
    """模拟 3 天游戏过程，验证端到端记忆流程。"""

    def test_day1_archive_creates_summary(self, memory_root: Path) -> None:
        """Day 1：append 事件 + 对话 → archive_episode → summary.md 存在（mock LLM）。"""
        ms = MemorySystem(memory_root=memory_root)

        # Day 1 活动
        ms.append_event({"type": "crop_watered", "content": "浇水完成", "ts_game": "Day1 08:00"})
        ms.append_event({"type": "energy_change", "content": "体力变化 -20", "ts_game": "Day1 09:00"})
        ms.append_conversation("player", "今天天气怎么样？")
        ms.append_conversation("agent", "今天是晴天，适合农业劳动！")

        # 归档 Day 1
        episode_id = ms.archive_episode(trigger="day_end", game_context="Day 1, Spring")

        assert episode_id == "ep_001"

        # 验证目录结构存在
        ep_dir = memory_root / "episodes" / "ep_001"
        assert ep_dir.is_dir(), "ep_001 目录应该存在"
        assert (ep_dir / "events.jsonl").exists(), "events.jsonl 应已归档"
        assert (ep_dir / "meta.json").exists(), "meta.json 应已写入"

        # 验证 pending_archive_queue.json
        queue_path = memory_root / "pending_archive_queue.json"
        assert queue_path.exists(), "pending_archive_queue 应存在"
        with open(queue_path) as f:
            queue = json.load(f)
        assert len(queue) == 1
        assert queue[0]["episode_id"] == "ep_001"

    def test_day1_process_pending_creates_summary_md(self, memory_root: Path) -> None:
        """Day 1：process_pending_archives 调用 LLM → summary.md 被写入。"""
        ms = MemorySystem(memory_root=memory_root)

        ms.append_event({"type": "crop_watered", "content": "浇水完成"})
        ms.append_conversation("player", "今天有什么计划？")
        ms.archive_episode(trigger="day_end", game_context="Day 1")

        # mock LLM 调用，触发 process_pending_archives
        with patch("llm_client.completion", return_value=_make_litellm_mock(MOCK_SUMMARY)):
            count = ms.process_pending_archives()

        assert count == 1

        summary_path = memory_root / "episodes" / "ep_001" / "summary.md"
        assert summary_path.exists(), "summary.md 应该被创建"
        content = summary_path.read_text(encoding="utf-8")
        assert len(content) > 0, "summary.md 不应为空"

    def test_day2_archive_increments_episode_id(self, memory_root: Path) -> None:
        """Day 2：归档后 episode_id 正确递增。"""
        ms = MemorySystem(memory_root=memory_root)

        # Day 1
        ms.append_event({"type": "crop_watered", "content": "Day 1 浇水"})
        ep1 = ms.archive_episode(trigger="day_end", game_context="Day 1")

        # Day 2
        ms.append_event({"type": "fishing", "content": "Day 2 钓鱼"})
        ep2 = ms.archive_episode(trigger="day_end", game_context="Day 2")

        assert ep1 == "ep_001"
        assert ep2 == "ep_002"

    @pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
    def test_day3_get_context_contains_summaries_and_facts(self, memory_root: Path) -> None:
        """Day 3：get_context_for_prompt 包含 Day 1-2 的摘要和 Facts。"""
        ms = MemorySystem(memory_root=memory_root)

        # Day 1
        ms.append_event({"type": "crop_watered", "content": "Day 1 浇水完成"})
        ms.append_conversation("player", "今天很充实")
        ms.archive_episode(trigger="day_end", game_context="Day 1")

        with patch("llm_client.completion", return_value=_make_litellm_mock(MOCK_SUMMARY)):
            ms.process_pending_archives()

        # Day 2
        ms.append_event({"type": "fishing", "content": "Day 2 钓到鱼"})
        ms.archive_episode(trigger="day_end", game_context="Day 2")

        with patch("llm_client.completion", return_value=_make_litellm_mock(MOCK_SUMMARY)):
            ms.process_pending_archives()

        # Day 3 活动（当前工作记忆）
        ms.append_event({"type": "shopping", "content": "Day 3 购物"})
        ms.append_conversation("player", "今天去商店")

        # 获取上下文
        context = ms.get_context_for_prompt()

        assert context, "context 不应为空"
        # 应包含最近的摘要或工作记忆
        assert "Day 3 购物" in context or "今天发生的事" in context, "应包含 Day 3 的工作记忆"


# ────────────────────────────────────────────────────────────────────────────
# TestMemoryPersistence: 重启恢复
# ────────────────────────────────────────────────────────────────────────────


class TestMemoryPersistence:
    """验证 MemorySystem 重启后数据持久化。"""

    def test_restart_preserves_summary(self, memory_root: Path) -> None:
        """创建 MemorySystem，写入数据，archive，重启后 get_recent_summaries 返回数据。"""
        # 第一次会话：写入并归档
        ms1 = MemorySystem(memory_root=memory_root)
        ms1.append_event({"type": "crop_watered", "content": "浇水"})
        ms1.append_conversation("player", "很好")
        ms1.archive_episode(trigger="day_end", game_context="Day 1")

        with patch("llm_client.completion", return_value=_make_litellm_mock(MOCK_SUMMARY)):
            ms1.process_pending_archives()

        # 验证 summary.md 已落盘
        summary_path = memory_root / "episodes" / "ep_001" / "summary.md"
        assert summary_path.exists(), "第一次会话后 summary.md 应存在"

        # 销毁实例，重新创建（模拟重启）
        del ms1

        # 第二次会话：重新创建 MemorySystem
        ms2 = MemorySystem(memory_root=memory_root)

        # 验证数据持久化
        summaries = ms2.get_recent_summaries(n=5)
        assert len(summaries) == 1, f"重启后应有 1 个摘要，实际: {len(summaries)}"
        assert MOCK_SUMMARY.strip()[:50] in summaries[0] or len(summaries[0]) > 0

    def test_restart_preserves_facts(self, memory_root: Path) -> None:
        """写入 Facts 后重启，get_facts 返回之前的内容。"""
        # 手动写入 facts/current.md
        facts_dir = memory_root / "facts"
        facts_dir.mkdir(parents=True, exist_ok=True)
        (facts_dir / "current.md").write_text(MOCK_FACTS, encoding="utf-8")

        # 创建 MemorySystem（应该加载已有 facts）
        ms = MemorySystem(memory_root=memory_root)

        facts = ms.get_facts()
        assert len(facts) > 0, "Facts 应该被加载"
        assert "初识" in facts, "Facts 内容应包含已写入的内容"

    def test_restart_episode_id_increments_correctly(self, memory_root: Path) -> None:
        """重启后新的 archive_episode 的 ID 基于已有 episodes 正确递增。"""
        # 第一次会话
        ms1 = MemorySystem(memory_root=memory_root)
        ms1.append_event({"type": "event1", "content": "第一天"})
        ep1 = ms1.archive_episode(trigger="day_end", game_context="Day 1")

        # 第二次会话（重建实例）
        ms2 = MemorySystem(memory_root=memory_root)
        ms2.append_event({"type": "event2", "content": "第二天"})
        ep2 = ms2.archive_episode(trigger="day_end", game_context="Day 2")

        assert ep1 == "ep_001"
        assert ep2 == "ep_002", f"重启后新 episode 应为 ep_002，实际: {ep2}"

    def test_restart_pending_queue_is_restored(self, memory_root: Path) -> None:
        """重启后 pending_archive_queue 中的任务可以被新实例继续处理。"""
        # 第一次会话：归档但不处理
        ms1 = MemorySystem(memory_root=memory_root)
        ms1.append_event({"type": "event1", "content": "未处理事件"})
        ms1.archive_episode(trigger="day_end", game_context="Day 1")

        # pending_archive_queue 此时有 1 个任务
        queue_path = memory_root / "pending_archive_queue.json"
        assert queue_path.exists()

        # 第二次会话：重建并处理 pending
        ms2 = MemorySystem(memory_root=memory_root)
        with patch("llm_client.completion", return_value=_make_litellm_mock(MOCK_SUMMARY)):
            count = ms2.process_pending_archives()

        assert count == 1, f"重建后应处理 1 个 pending 任务，实际: {count}"


# ────────────────────────────────────────────────────────────────────────────
# TestMemoryPromptIntegration: 与 PromptAssembler 集成
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
class TestMemoryPromptIntegration:
    """验证 PromptAssembler 正确接收并注入 memory_context。"""

    @pytest.fixture(autouse=True)
    def _setup_imports(self):
        """确保 slow_loop.prompt_assembler 已真正加载（含 PromptAssembler 类）。

        test_skill_library.py 在模块级别将 agent.slow_loop.prompt_assembler 注册为空 stub，
        导致 importlib.import_module 从缓存返回无属性的模块。
        修复方式：检测缓存模块是否为空 stub，如是则清除缓存强制重新加载。
        """
        for mod_name in ["slow_loop.prompt_assembler", "slow_loop.planner"]:
            full_name = f"agent.{mod_name}"
            cached = sys.modules.get(full_name)
            # 如果缓存的是无属性的空 stub（如 test_skill_library.py 注册的），清除缓存
            is_stub = (
                cached is not None
                and mod_name == "slow_loop.prompt_assembler"
                and not hasattr(cached, "PromptAssembler")
            )
            if is_stub:
                del sys.modules[full_name]
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
            if full_name not in sys.modules:
                try:
                    mod = importlib.import_module(full_name)
                    sys.modules[full_name] = mod
                    sys.modules[mod_name] = mod
                except ImportError:
                    pass

    def test_memory_context_injected_in_system_message(self) -> None:
        """传入 memory_context 后，system message 应包含该内容。"""
        # 动态导入 PromptAssembler
        try:
            pa_mod = importlib.import_module("agent.slow_loop.prompt_assembler")
        except ImportError:
            pytest.skip("PromptAssembler 导入失败，跳过")

        PromptAssembler = pa_mod.PromptAssembler

        # 创建 mock prompts 目录（使空模板）
        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()
            (prompts_dir / "system_prompt.txt").write_text(
                "你是{agent_name}，一个友好的游戏搭子。", encoding="utf-8"
            )
            (prompts_dir / "output_schema.txt").write_text("输出 JSON。", encoding="utf-8")
            trigger_dir = prompts_dir / "trigger_templates"
            trigger_dir.mkdir()
            (trigger_dir / "periodic.txt").write_text("【当前触发】定时检查。", encoding="utf-8")

            assembler = PromptAssembler(prompts_dir=prompts_dir)

            # 构造 memory_context
            memory_context = """### 关于搭子

- 名字：（待确认）
- 关系阶段：初识

### 今天发生的事

- [Day1 08:00] crop_watered: 浇水完成"""

            # mock WorkingMemory
            from memory.working_memory import WorkingMemory
            wm = WorkingMemory()

            # 组装 Prompt
            trigger_mock = MagicMock()
            trigger_mock.__class__ = object  # 触发 periodic 模板

            messages = assembler.assemble(
                trigger_msg=trigger_mock,
                game_state={"time": "08:00", "weather": "晴", "player": {}},
                working_memory=wm,
                capabilities=[],
                memory_context=memory_context,
            )

            # 验证 system message 包含 memory_context
            assert len(messages) == 2
            system_content = messages[0]["content"]
            assert "关于搭子" in system_content, "system message 应包含记忆上下文标题"
            assert "crop_watered" in system_content, "system message 应包含记忆上下文事件内容"

    def test_no_memory_context_does_not_inject(self) -> None:
        """不传入 memory_context 时，system message 不应有意外内容。"""
        try:
            pa_mod = importlib.import_module("agent.slow_loop.prompt_assembler")
        except ImportError:
            pytest.skip("PromptAssembler 导入失败，跳过")

        PromptAssembler = pa_mod.PromptAssembler

        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()
            (prompts_dir / "system_prompt.txt").write_text("你是{agent_name}。", encoding="utf-8")
            (prompts_dir / "output_schema.txt").write_text("输出 JSON。", encoding="utf-8")
            trigger_dir = prompts_dir / "trigger_templates"
            trigger_dir.mkdir()
            (trigger_dir / "periodic.txt").write_text("【当前触发】定时检查。", encoding="utf-8")

            assembler = PromptAssembler(prompts_dir=prompts_dir)

            from memory.working_memory import WorkingMemory
            wm = WorkingMemory()
            trigger_mock = MagicMock()

            messages = assembler.assemble(
                trigger_msg=trigger_mock,
                game_state={"time": "08:00", "weather": "晴", "player": {}},
                working_memory=wm,
                capabilities=[],
                memory_context=None,
            )

            system_content = messages[0]["content"]
            # 不应有记忆相关内容（memory_context 为 None）
            assert "### 关于搭子" not in system_content

    def test_memory_context_from_memory_system(self, memory_root: Path) -> None:
        """MemorySystem.get_context_for_prompt() 的输出可以直接传入 PromptAssembler。"""
        try:
            pa_mod = importlib.import_module("agent.slow_loop.prompt_assembler")
        except ImportError:
            pytest.skip("PromptAssembler 导入失败，跳过")

        PromptAssembler = pa_mod.PromptAssembler

        ms = MemorySystem(memory_root=memory_root)
        ms.append_event({"type": "crop_watered", "content": "浇水完成", "ts_game": "Day1 08:00"})
        ms.append_conversation("player", "今天怎么样？")

        context = ms.get_context_for_prompt()
        assert isinstance(context, str)
        assert len(context) > 0

        # 验证可以传入 PromptAssembler
        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()
            (prompts_dir / "system_prompt.txt").write_text("你是{agent_name}。", encoding="utf-8")
            (prompts_dir / "output_schema.txt").write_text("输出 JSON。", encoding="utf-8")
            trigger_dir = prompts_dir / "trigger_templates"
            trigger_dir.mkdir()
            (trigger_dir / "periodic.txt").write_text("【当前触发】。", encoding="utf-8")

            assembler = PromptAssembler(prompts_dir=prompts_dir)
            from memory.working_memory import WorkingMemory
            wm = WorkingMemory()
            trigger_mock = MagicMock()

            messages = assembler.assemble(
                trigger_msg=trigger_mock,
                game_state={"player": {}},
                working_memory=wm,
                capabilities=[],
                memory_context=context,
            )

            system_content = messages[0]["content"]
            assert len(system_content) > 10, "system message 应有内容"


# ────────────────────────────────────────────────────────────────────────────
# TestDeliberativeLoopMemoryIntegration: 慢环集成点
# ────────────────────────────────────────────────────────────────────────────


class TestDeliberativeLoopMemoryIntegration:
    """验证 DeliberativeLoop 与 MemorySystem 的集成点。"""

    @pytest.fixture
    def loop_with_memory(self, memory_root: Path):
        """创建带 MemorySystem 的 DeliberativeLoop。"""
        # 注册所需模块
        mods_to_register = [
            ("agent.bt_engine.message_bus", "bt_engine.message_bus"),
            ("agent.memory.working_memory", "memory.working_memory"),
            ("agent.messages.types", "messages.types"),
            ("agent.slow_loop.planner", "slow_loop.planner"),
            ("agent.slow_loop.prompt_assembler", "slow_loop.prompt_assembler"),
            ("agent.slow_loop.deliberative_loop", "slow_loop.deliberative_loop"),
        ]
        for full_name, short_name in mods_to_register:
            if short_name not in sys.modules:
                try:
                    mod = importlib.import_module(full_name)
                    sys.modules[short_name] = mod
                except ImportError:
                    pass

        try:
            from bt_engine.message_bus import MessageBus
            from memory.working_memory import WorkingMemory
            from slow_loop.deliberative_loop import DeliberativeLoop
        except ImportError as e:
            pytest.skip(f"慢环模块导入失败: {e}")

        ms = MemorySystem(memory_root=memory_root)
        bus = MessageBus()
        wm = WorkingMemory()
        bridge = MagicMock()
        bridge.read_game_state.return_value = {}
        bridge.capabilities.return_value = []
        bridge.capability_names.return_value = []

        config = {
            "slow_loop": {
                "min_call_interval_s": 0.0,
                "player_input_debounce_s": 0.01,
                "max_bt_depth": 3,
                "max_bt_nodes": 15,
            }
        }

        loop = DeliberativeLoop(
            message_bus=bus,
            bridge=bridge,
            working_memory=wm,
            config=config,
            memory_system=ms,
        )

        return loop, bus, ms

    def test_player_input_appends_conversation(self, loop_with_memory, memory_root: Path) -> None:
        """PlayerInput 消息触发 MemorySystem.append_conversation。"""
        try:
            from messages.types import PlayerInput
        except ImportError:
            pytest.skip("messages.types 导入失败")

        loop, bus, ms = loop_with_memory

        # 直接调用 _handle_message（不启动线程）
        msg = PlayerInput(text="你好，今天怎么样？")
        loop._handle_message(msg)

        # 验证对话已写入
        conversation = ms.get_conversation(n=10)
        assert len(conversation) >= 1
        player_msgs = [m for m in conversation if m.get("role") == "player"]
        assert len(player_msgs) >= 1
        assert "你好，今天怎么样？" in player_msgs[-1].get("content", "")

    def test_episode_boundary_triggers_archive(self, loop_with_memory, memory_root: Path) -> None:
        """EpisodeBoundaryReport 消息触发 MemorySystem.archive_episode。"""
        try:
            from messages.types import EpisodeBoundaryReport
        except ImportError:
            pytest.skip("messages.types 导入失败")

        loop, bus, ms = loop_with_memory

        # 先写入一些事件
        ms.append_event({"type": "crop_watered", "content": "浇水"})

        # mock _process_trigger，避免实际 LLM 调用
        loop._process_trigger = MagicMock()

        msg = EpisodeBoundaryReport(
            trigger="day_end",
            game_context={"date": "Spring 1 Year 1", "day": 1},
        )
        loop._handle_message(msg)

        # 验证 archive_episode 已被触发（ep_001 目录存在）
        ep_dir = memory_root / "episodes" / "ep_001"
        assert ep_dir.is_dir(), "EpisodeBoundaryReport 应触发 archive_episode，ep_001 应存在"

        # 验证 _process_trigger 被调用
        loop._process_trigger.assert_called_once_with(msg)

    def test_report_event_appends_to_memory_system(self, loop_with_memory, memory_root: Path) -> None:
        """ReportEvent 消息触发 MemorySystem.append_event。"""
        try:
            from messages.types import ReportEvent
        except ImportError:
            pytest.skip("messages.types 导入失败")

        loop, bus, ms = loop_with_memory

        # 低重要性事件（不触发 LLM）
        msg = ReportEvent(event_type="crop_ready", data={"crop": "parsnip"}, significance=3)
        loop._handle_message(msg)

        # 验证事件已写入工作记忆（MemorySystem）
        events = ms.get_recent_events(n=10)
        assert len(events) >= 1
        crop_events = [e for e in events if e.get("type") == "crop_ready"]
        assert len(crop_events) >= 1

    def test_memory_system_none_does_not_crash(self, memory_root: Path) -> None:
        """memory_system=None 时慢环应正常运行（降级模式）。"""
        mods_to_register = [
            ("agent.bt_engine.message_bus", "bt_engine.message_bus"),
            ("agent.memory.working_memory", "memory.working_memory"),
            ("agent.messages.types", "messages.types"),
            ("agent.slow_loop.planner", "slow_loop.planner"),
            ("agent.slow_loop.prompt_assembler", "slow_loop.prompt_assembler"),
            ("agent.slow_loop.deliberative_loop", "slow_loop.deliberative_loop"),
        ]
        for full_name, short_name in mods_to_register:
            if short_name not in sys.modules:
                try:
                    mod = importlib.import_module(full_name)
                    sys.modules[short_name] = mod
                except ImportError:
                    pass

        try:
            from bt_engine.message_bus import MessageBus
            from memory.working_memory import WorkingMemory
            from messages.types import PlayerInput
            from slow_loop.deliberative_loop import DeliberativeLoop
        except ImportError as e:
            pytest.skip(f"模块导入失败: {e}")

        bus = MessageBus()
        wm = WorkingMemory()
        bridge = MagicMock()

        config = {
            "slow_loop": {
                "min_call_interval_s": 0.0,
                "player_input_debounce_s": 0.01,
            }
        }

        # memory_system=None — 降级模式
        loop = DeliberativeLoop(
            message_bus=bus,
            bridge=bridge,
            working_memory=wm,
            config=config,
            memory_system=None,
        )

        # 不应崩溃
        msg = PlayerInput(text="测试消息")
        loop._handle_message(msg)  # 不应抛出异常

        assert loop._memory_system is None


# ────────────────────────────────────────────────────────────────────────────
# TestGetContextForPrompt: get_context_for_prompt 输出格式验证
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.skip(reason="[DEPRECATED] stateless 模式已被 Agent Loop 替代，agent-loop-mode 迭代后应当彻底删除")
class TestGetContextForPrompt:
    """验证 get_context_for_prompt 在不同场景下的输出。"""

    def test_empty_memory_returns_placeholder(self, memory_root: Path) -> None:
        """空记忆时返回占位符字符串（非空）。"""
        ms = MemorySystem(memory_root=memory_root)
        context = ms.get_context_for_prompt()
        assert isinstance(context, str)
        assert len(context) > 0

    def test_with_events_includes_events_section(self, memory_root: Path) -> None:
        """有工作记忆事件时，context 应包含事件段。"""
        ms = MemorySystem(memory_root=memory_root)
        ms.append_event({"type": "crop_watered", "content": "浇水了玉米", "ts_game": "Day1 08:00"})

        context = ms.get_context_for_prompt()
        assert "crop_watered" in context or "浇水了玉米" in context

    def test_with_conversation_includes_dialogue(self, memory_root: Path) -> None:
        """有对话时，context 应包含对话段。"""
        ms = MemorySystem(memory_root=memory_root)
        ms.append_conversation("player", "你好！")
        ms.append_conversation("agent", "你好，今天有什么需要帮忙的？")

        context = ms.get_context_for_prompt()
        assert "你好" in context

    def test_with_summaries_includes_summaries(self, memory_root: Path) -> None:
        """有 Episode 摘要时，context 应包含摘要段。"""
        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True, exist_ok=True)
        (ep_dir / "events.jsonl").write_text(
            '{"type":"noop","content":"x","ts":"2026-01-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        (ep_dir / "conversation.jsonl").write_text("", encoding="utf-8")
        (ep_dir / "meta.json").write_text(
            json.dumps(
                {
                    "episode_id": "ep_001",
                    "trigger": "test",
                    "game_context": "",
                    "archived_at": "2026-01-01T00:00:00+00:00",
                    "summary_status": "done",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (ep_dir / "summary.md").write_text(
            "# Day 1 总结\n\n今天完成了浇水任务。", encoding="utf-8"
        )

        ms = MemorySystem(memory_root=memory_root)
        context = ms.get_context_for_prompt()
        assert "Day 1 总结" in context or "浇水任务" in context

    def test_token_budget_respected(self, memory_root: Path) -> None:
        """get_context_for_prompt 的 token 预算裁剪不崩溃。"""
        ms = MemorySystem(memory_root=memory_root)

        # 写入大量事件
        for i in range(50):
            ms.append_event({
                "type": "event",
                "content": f"第{i}个事件，内容很长：{'x' * 100}",
                "ts_game": f"Day1 {i:02d}:00",
            })

        # max_tokens 非常小，应触发裁剪而不崩溃
        context = ms.get_context_for_prompt(max_tokens=500)
        assert isinstance(context, str)
        assert len(context) > 0


# ────────────────────────────────────────────────────────────────────────────
# TestRememberPersistence: _handle_remember 持久化验证
# ────────────────────────────────────────────────────────────────────────────


class TestRememberPersistence:
    """_handle_remember 应同时写入 WorkingMemory 和 MemorySystem。"""

    def test_remember_persists_to_memory_system(self, tmp_path: Path) -> None:
        """remember 工具调用后，事件应出现在 MemorySystem 的 events.jsonl 中。"""
        ms = MemorySystem(memory_root=tmp_path / "memory")

        # 模拟 _handle_remember 的持久化逻辑
        mem_type = "player_preference"
        content = "玩家喜欢钓鱼"
        ts = time.time()

        ms.append_event({
            "type": f"remember_{mem_type}",
            "content": content,
            "ts": ts,
            "pinned": True,
            "source": "llm_remember_tool",
        })

        # 验证事件存在
        events = ms.get_recent_events(50)
        remember_events = [e for e in events if e.get("source") == "llm_remember_tool"]
        assert len(remember_events) == 1
        assert remember_events[0]["content"] == "玩家喜欢钓鱼"
        assert remember_events[0]["pinned"] is True

    def test_remember_survives_restart(self, tmp_path: Path) -> None:
        """重启 MemorySystem 后 remember 事件仍然存在。"""
        root = tmp_path / "memory"
        ms1 = MemorySystem(memory_root=root)
        ms1.append_event({
            "type": "remember_player_preference",
            "content": "玩家喜欢钓鱼",
            "ts": 1000.0,
            "pinned": True,
            "source": "llm_remember_tool",
        })

        # 模拟重启
        ms2 = MemorySystem(memory_root=root)
        events = ms2.get_recent_events(50)
        remember_events = [e for e in events if e.get("source") == "llm_remember_tool"]
        assert len(remember_events) == 1, "remember 事件应在重启后仍然存在"
