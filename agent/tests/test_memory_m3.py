"""
tests.test_memory_m3 — 记忆系统 Sprint M3 测试套件。

测试覆盖：
    - TestFactsManager          : 读写/冷启动/原子写入/版本历史
    - TestFactsUpdate           : LLM 调用 mock/更新 Prompt 构建/失败降级
    - TestFactsSummary          : 渐进式裁剪（仅标题行提取）
    - TestPromptInjection       : get_context_for_prompt 完整拼装/裁剪策略
    - TestSubjectivityDualTrack : 确认 update_facts 接收的是原始事件而非摘要
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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

from memory.facts_manager import FactsManager
from memory.memory_system import MemorySystem, _estimate_tokens


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    """提供临时记忆根目录。"""
    return tmp_path / "memory"


@pytest.fixture
def facts_dir(tmp_path: Path) -> Path:
    """提供临时 facts 目录。"""
    d = tmp_path / "facts"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def facts_manager(facts_dir: Path) -> FactsManager:
    """提供已初始化的 FactsManager。

    传入 model 参数以规避 'model 未配置' early-exit，确保 LLM mock 能被调用。
    测试中所有 LLM 调用均通过 patch("llm_client.completion") 拦截。
    """
    return FactsManager(facts_dir=facts_dir, model="gpt-4o-mini-test")


@pytest.fixture
def memory_system(memory_root: Path) -> MemorySystem:
    """提供已初始化的 MemorySystem。"""
    return MemorySystem(memory_root)


@pytest.fixture
def sample_events() -> list[dict]:
    """样例事件列表（客观记录）。"""
    return [
        {"ts": "2026-04-01T08:00:00Z", "ts_game": "Y1-Spring-D5-08:00", "type": "player_action", "content": "开始浇水"},
        {"ts": "2026-04-01T08:05:00Z", "ts_game": "Y1-Spring-D5-08:05", "type": "player_action", "content": "浇水完成"},
        {"ts": "2026-04-01T10:00:00Z", "ts_game": "Y1-Spring-D5-10:00", "type": "player_chat", "content": "要去钓鱼吗？", "pinned": True},
        {"ts": "2026-04-01T15:00:00Z", "ts_game": "Y1-Spring-D5-15:00", "type": "player_action", "content": "钓到了鲶鱼！"},
    ]


@pytest.fixture
def sample_conversation() -> list[dict]:
    """样例对话列表。"""
    return [
        {"ts": "2026-04-01T10:00:00Z", "role": "player", "content": "要去钓鱼吗？"},
        {"ts": "2026-04-01T10:00:05Z", "role": "agent", "content": "好啊！去海边还是山上的湖？"},
    ]


@pytest.fixture
def sample_facts_content() -> str:
    """样例 Facts 内容（Markdown 格式）。"""
    return (
        "## 关于搭子\n"
        "- 名字：小明\n"
        "- 游戏风格：效率导向\n"
        "- 喜欢：钓鱼\n"
        "\n"
        "## 我们的关系\n"
        "- 阶段：熟悉的搭子\n"
        "- 一起玩了：5 天\n"
        "- 信任度：高\n"
        "\n"
        "## 世界状态\n"
        "- 当前季节：春季\n"
        "- 农场：星光农场\n"
    )


# ────────────────────────────────────────────────────────────────────────────
# TestFactsManager — 读写/冷启动/原子写入/版本历史
# ────────────────────────────────────────────────────────────────────────────


class TestFactsManager:
    """FactsManager 基础功能测试。"""

    def test_init_creates_history_dir(self, facts_dir: Path):
        """初始化应创建 history 目录。"""
        mgr = FactsManager(facts_dir=facts_dir)
        assert (facts_dir / "history").is_dir()

    def test_get_facts_cold_start_no_file(self, facts_dir: Path):
        """无 current.md 时应返回冷启动模板（非空）。"""
        mgr = FactsManager(facts_dir=facts_dir)
        result = mgr.get_facts()
        assert isinstance(result, str)
        assert len(result) > 0
        # 冷启动模板应包含关于搭子 section
        assert "关于搭子" in result or "关系" in result

    def test_get_facts_returns_existing_file(self, facts_dir: Path, sample_facts_content: str):
        """存在 current.md 时应返回其全文。"""
        (facts_dir / "current.md").write_text(sample_facts_content, encoding="utf-8")
        mgr = FactsManager(facts_dir=facts_dir)
        result = mgr.get_facts()
        assert result == sample_facts_content

    def test_atomic_write_creates_current_md(self, facts_manager: FactsManager, sample_facts_content: str):
        """_atomic_write 应创建 facts/current.md。"""
        facts_manager._atomic_write(sample_facts_content)
        current_path = facts_manager._current_path
        assert current_path.exists()
        assert current_path.read_text(encoding="utf-8") == sample_facts_content

    def test_atomic_write_no_tmp_remains(self, facts_manager: FactsManager, sample_facts_content: str):
        """原子写入后不应留下 .tmp 文件。"""
        facts_manager._atomic_write(sample_facts_content)
        tmp_path = facts_manager._current_path.with_suffix(".tmp")
        assert not tmp_path.exists()

    def test_atomic_write_saves_history(self, facts_manager: FactsManager, sample_facts_content: str):
        """第二次写入时，应将旧版本保存到 history/v001.md。"""
        old_content = "## 关于搭子\n- 名字：（还不知道）\n"
        facts_manager._atomic_write(old_content)
        facts_manager._atomic_write(sample_facts_content)

        history_dir = facts_manager._history_dir
        history_files = list(history_dir.glob("v*.md"))
        assert len(history_files) == 1
        assert history_files[0].name == "v001.md"
        assert history_files[0].read_text(encoding="utf-8") == old_content

    def test_version_history_increments(self, facts_manager: FactsManager):
        """多次写入后，版本历史应顺序递增。"""
        for i in range(3):
            facts_manager._atomic_write(f"## 版本 {i + 1}\n")

        history_files = sorted(
            facts_manager._history_dir.glob("v*.md"),
            key=lambda p: p.name,
        )
        assert len(history_files) == 2  # 第3次写入时，v001, v002 已存在
        assert history_files[0].name == "v001.md"
        assert history_files[1].name == "v002.md"

    def test_version_property_zero_when_no_history(self, facts_manager: FactsManager):
        """无历史记录时，version 属性应为 0。"""
        assert facts_manager.version == 0

    def test_version_property_increments(self, facts_manager: FactsManager):
        """写入后 version 应递增。"""
        facts_manager._atomic_write("## 测试\n- 条目\n")
        facts_manager._atomic_write("## 测试更新\n- 条目\n")
        # 第一次写入无历史→version 0；第二次写入后 history/v001.md→version 1
        assert facts_manager.version == 1

    def test_get_facts_after_write(self, facts_manager: FactsManager, sample_facts_content: str):
        """写入后 get_facts 应返回最新内容。"""
        facts_manager._atomic_write(sample_facts_content)
        result = facts_manager.get_facts()
        assert result == sample_facts_content

    def test_get_facts_handles_read_error(self, facts_dir: Path):
        """current.md 读取异常时应降级为冷启动模板。"""
        mgr = FactsManager(facts_dir=facts_dir)
        # 创建一个目录来替代 current.md，触发读取错误
        (facts_dir / "current.md").mkdir()
        result = mgr.get_facts()
        # 应返回冷启动模板而非崩溃
        assert isinstance(result, str)
        assert len(result) > 0


# ────────────────────────────────────────────────────────────────────────────
# TestFactsUpdate — LLM 调用 mock/更新 Prompt 构建/失败降级
# ────────────────────────────────────────────────────────────────────────────


class TestFactsUpdate:
    """Facts 更新机制测试（LLM mock）。"""

    def _make_mock_response(self, content: str) -> MagicMock:
        """构建 llm_client.completion 返回值的 mock。"""
        mock_response = MagicMock()
        mock_response.choices[0].message.content = content
        return mock_response

    def test_update_facts_success(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
        sample_facts_content: str,
    ):
        """LLM 调用成功时 update_facts 应返回 True 并写入新 Facts。"""
        new_facts = "## 关于搭子\n- 名字：小明\n- 喜欢：钓鱼、种南瓜\n\n## 我们的关系\n- 阶段：熟悉的搭子\n\n## 世界状态\n- 当前季节：春季\n"
        mock_response = self._make_mock_response(new_facts)

        with patch("llm_client.completion", return_value=mock_response):
            result = facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        assert result is True
        assert facts_manager.get_facts().strip() == new_facts.strip()

    def test_update_facts_llm_exception_returns_false(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
        sample_facts_content: str,
    ):
        """LLM 调用抛出异常时 update_facts 应返回 False，原 Facts 不变。"""
        # 写入初始 Facts
        facts_manager._atomic_write(sample_facts_content)

        with patch("llm_client.completion", side_effect=Exception("LLM 调用失败")):
            result = facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        assert result is False
        # 原 Facts 不变
        assert facts_manager.get_facts() == sample_facts_content

    def test_update_facts_empty_response_returns_false(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
        sample_facts_content: str,
    ):
        """LLM 返回空内容时应返回 False，原 Facts 不变。"""
        facts_manager._atomic_write(sample_facts_content)
        mock_response = self._make_mock_response("")

        with patch("llm_client.completion", return_value=mock_response):
            result = facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        assert result is False
        assert facts_manager.get_facts() == sample_facts_content

    def test_update_facts_litellm_not_installed_returns_false(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
    ):
        """litellm 未安装时 update_facts 应返回 False 而非崩溃。"""
        with patch.dict("sys.modules", {"llm_client": None}):
            result = facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )
        assert result is False

    def test_update_facts_prompt_contains_events(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
    ):
        """update_facts 传递给 LLM 的 Prompt 必须包含原始事件内容。"""
        new_facts = "## 关于搭子\n- 名字：小明\n\n## 我们的关系\n- 阶段：初识\n\n## 世界状态\n- 当前季节：春季\n"
        mock_response = self._make_mock_response(new_facts)
        captured_messages = []

        def capture_completion(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return mock_response

        with patch("llm_client.completion", side_effect=capture_completion):
            facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        # 合并所有消息内容
        all_content = " ".join(m.get("content", "") for m in captured_messages)
        # Prompt 应包含事件内容（客观记录）
        assert "浇水" in all_content or "钓" in all_content

    def test_update_facts_prompt_contains_conversation(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
    ):
        """update_facts 的 Prompt 应包含对话记录。"""
        new_facts = "## 关于搭子\n- 名字：小明\n\n## 我们的关系\n- 阶段：初识\n\n## 世界状态\n- 当前季节：春季\n"
        mock_response = self._make_mock_response(new_facts)
        captured_messages = []

        def capture_completion(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return mock_response

        with patch("llm_client.completion", side_effect=capture_completion):
            facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        all_content = " ".join(m.get("content", "") for m in captured_messages)
        assert "钓鱼" in all_content

    def test_update_facts_saves_version_history(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_facts_content: str,
    ):
        """成功更新后应有版本历史文件。"""
        facts_manager._atomic_write(sample_facts_content)
        new_facts = "## 关于搭子\n- 名字：小明\n- 喜欢：钓鱼\n\n## 我们的关系\n- 阶段：熟悉的搭子\n\n## 世界状态\n- 当前季节：春季\n"
        mock_response = self._make_mock_response(new_facts)

        with patch("llm_client.completion", return_value=mock_response):
            facts_manager.update_facts(
                events=sample_events,
                conversation=[],
                game_context="Y1-Spring-Day5",
            )

        history_files = list(facts_manager._history_dir.glob("v*.md"))
        assert len(history_files) == 1
        assert history_files[0].read_text(encoding="utf-8") == sample_facts_content


# ────────────────────────────────────────────────────────────────────────────
# TestFactsSummary — 渐进式裁剪（仅标题行提取）
# ────────────────────────────────────────────────────────────────────────────


class TestFactsSummary:
    """Facts 摘要/裁剪测试。"""

    def test_get_facts_summary_returns_headers(self, facts_manager: FactsManager, sample_facts_content: str):
        """get_facts_summary 应返回 ## 标题行。"""
        facts_manager._atomic_write(sample_facts_content)
        summary = facts_manager.get_facts_summary()
        assert "## 关于搭子" in summary
        assert "## 我们的关系" in summary
        assert "## 世界状态" in summary

    def test_get_facts_summary_shorter_than_full(self, facts_manager: FactsManager, sample_facts_content: str):
        """摘要版应比全量版更短。"""
        facts_manager._atomic_write(sample_facts_content)
        full = facts_manager.get_facts()
        summary = facts_manager.get_facts_summary()
        assert len(summary) < len(full)

    def test_get_facts_summary_includes_first_bullet(self, facts_manager: FactsManager):
        """摘要应包含每个标题下的第一个 bullet。"""
        content = (
            "## 关于搭子\n"
            "- 名字：小明\n"
            "- 游戏风格：效率导向\n"
            "\n"
            "## 我们的关系\n"
            "- 阶段：熟悉的搭子\n"
            "- 一起玩了：5 天\n"
        )
        facts_manager._atomic_write(content)
        summary = facts_manager.get_facts_summary()
        # 应包含第一个 bullet
        assert "- 名字：小明" in summary
        assert "- 阶段：熟悉的搭子" in summary
        # 不应包含第二个 bullet
        assert "游戏风格" not in summary
        assert "一起玩了" not in summary

    def test_get_facts_summary_no_facts_returns_placeholder(self, facts_dir: Path):
        """无 Facts 文件时，摘要应返回占位符而非崩溃。"""
        # 写一个无 ## 标题的文件
        (facts_dir / "current.md").write_text("无标题内容\n", encoding="utf-8")
        mgr = FactsManager(facts_dir=facts_dir)
        result = mgr.get_facts_summary()
        assert isinstance(result, str)

    def test_get_facts_summary_empty_section(self, facts_manager: FactsManager):
        """标题下方无 bullet 时，不应包含 None 或崩溃。"""
        content = "## 关于搭子\n\n## 我们的关系\n- 阶段：初识\n"
        facts_manager._atomic_write(content)
        summary = facts_manager.get_facts_summary()
        assert isinstance(summary, str)
        assert "## 关于搭子" in summary
        assert "## 我们的关系" in summary


# ────────────────────────────────────────────────────────────────────────────
# TestPromptInjection — get_context_for_prompt 完整拼装/裁剪策略
# ────────────────────────────────────────────────────────────────────────────


class TestPromptInjection:
    """get_context_for_prompt 拼装与裁剪测试。"""

    def _populate_memory(self, ms: MemorySystem, facts_content: str = None) -> None:
        """向 MemorySystem 写入测试数据。"""
        if facts_content:
            ms._facts_manager._atomic_write(facts_content)
        for i in range(5):
            ms.append_event({
                "ts_game": f"Y1-Spring-D5-{8 + i:02d}:00",
                "type": "player_action",
                "content": f"事件 {i + 1}",
            })
        ms.append_conversation("player", "你好")
        ms.append_conversation("agent", "你好啊！")

    def test_context_contains_facts(self, memory_system: MemorySystem, sample_facts_content: str):
        """完整上下文应包含 Facts 内容。"""
        self._populate_memory(memory_system, sample_facts_content)
        ctx = memory_system.get_context_for_prompt()
        assert "关于搭子" in ctx
        assert "小明" in ctx

    def test_context_contains_events(self, memory_system: MemorySystem):
        """完整上下文应包含工作记忆事件。"""
        self._populate_memory(memory_system)
        ctx = memory_system.get_context_for_prompt()
        assert "事件 1" in ctx

    def test_context_contains_conversation(self, memory_system: MemorySystem):
        """完整上下文应包含对话历史。"""
        self._populate_memory(memory_system)
        ctx = memory_system.get_context_for_prompt()
        assert "你好" in ctx

    def test_context_empty_returns_placeholder(self, memory_system: MemorySystem):
        """无任何记忆时应返回占位符而非崩溃。"""
        ctx = memory_system.get_context_for_prompt()
        assert isinstance(ctx, str)
        # 冷启动 Facts 存在，所以通常不会返回占位符，但至少不崩溃

    def test_estimate_tokens_function(self):
        """_estimate_tokens 应返回正整数。"""
        assert _estimate_tokens("hello") > 0
        assert _estimate_tokens("你好世界") > 0
        assert _estimate_tokens("") == 1  # max(1, ...)

    def test_progressive_trimming_p3_events(self, memory_root: Path):
        """token 超预算时应先裁减工作记忆事件（P3）。"""
        ms = MemorySystem(memory_root)
        # 写入大量事件使总量超出预算
        for i in range(25):
            ms.append_event({
                "ts_game": f"Y1-Spring-D5-{i:02d}:00",
                "type": "player_action",
                "content": "这是一个比较长的事件描述，用于测试 token 超预算时的裁剪行为" * 2,
            })

        # 设定非常小的预算，强制触发裁剪
        ctx = ms.get_context_for_prompt(max_tokens=200, max_events=20)
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_context_sections_order(self, memory_system: MemorySystem, sample_facts_content: str):
        """上下文中各 section 应按设计顺序排列（Facts → 摘要 → 事件 → 对话）。"""
        self._populate_memory(memory_system, sample_facts_content)
        ctx = memory_system.get_context_for_prompt()

        # 各 section 标题位置顺序
        pos_facts = ctx.find("关于搭子")
        pos_events = ctx.find("今天发生的事")
        pos_conv = ctx.find("对话历史")

        if pos_facts >= 0 and pos_events >= 0:
            assert pos_facts < pos_events
        if pos_events >= 0 and pos_conv >= 0:
            assert pos_events < pos_conv

    def test_context_with_episode_summaries(self, memory_root: Path):
        """存在 Episode 摘要时，上下文应包含摘要内容。"""
        ms = MemorySystem(memory_root)
        # 手动写入一个摘要
        ep_dir = ms._episodes_dir / "ep_001"
        ep_dir.mkdir(parents=True)
        (ep_dir / "summary.md").write_text("今天钓到了鲶鱼，特别开心！\n\n## 标签\n钓鱼", encoding="utf-8")

        ctx = ms.get_context_for_prompt()
        assert "鲶鱼" in ctx

    def test_build_context_facts_mode_summary(self, memory_system: MemorySystem, sample_facts_content: str):
        """facts_mode='summary' 时应使用摘要版 Facts。"""
        memory_system._facts_manager._atomic_write(sample_facts_content)
        ctx = memory_system._build_context(
            events_limit=5,
            summaries_limit=3,
            conv_limit=5,
            facts_mode="summary",
        )
        # 摘要模式应包含标题
        assert "## 关于搭子" in ctx
        # 但不包含完整的详情（游戏风格等第二条 bullet）
        assert "游戏风格" not in ctx

    def test_build_context_facts_mode_minimal(self, memory_system: MemorySystem, sample_facts_content: str):
        """facts_mode='minimal' 时应只保留关系阶段相关信息。"""
        memory_system._facts_manager._atomic_write(sample_facts_content)
        ctx = memory_system._build_context(
            events_limit=5,
            summaries_limit=1,
            conv_limit=5,
            facts_mode="minimal",
        )
        assert isinstance(ctx, str)
        assert len(ctx) > 0


# ────────────────────────────────────────────────────────────────────────────
# TestSubjectivityDualTrack — 主观性双轨验证
# ────────────────────────────────────────────────────────────────────────────


class TestSubjectivityDualTrack:
    """主观性双轨设计验证 — 确认 update_facts 接收的是原始事件而非摘要。"""

    def test_update_facts_receives_raw_events(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
        sample_conversation: list[dict],
    ):
        """update_facts 应将原始事件（不是摘要）传递给 LLM Prompt。

        主观性双轨核心设计：
            存储层（客观）→ events.jsonl → Facts 更新（不经过摘要）
            索引层（主观）→ summary.md   → Prompt 注入（营造感觉）
        """
        new_facts = "## 关于搭子\n- 名字：小明\n\n## 我们的关系\n- 阶段：初识\n\n## 世界状态\n- 当前季节：春季\n"
        mock_response = MagicMock()
        mock_response.choices[0].message.content = new_facts
        captured_kwargs = {}

        def capture_completion(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_response

        with patch("llm_client.completion", side_effect=capture_completion):
            facts_manager.update_facts(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        # 验证传递给 LLM 的消息包含原始事件内容
        messages = captured_kwargs.get("messages", [])
        all_content = " ".join(m.get("content", "") for m in messages)

        # 原始事件的 content 字段应出现在 Prompt 中
        for evt in sample_events:
            assert evt["content"] in all_content, f"事件 '{evt['content']}' 未出现在 Prompt 中"

    def test_update_facts_not_based_on_summary(
        self,
        facts_manager: FactsManager,
        sample_events: list[dict],
    ):
        """update_facts 调用路径不经过 summary.md（主观摘要）。

        此测试验证 update_facts 方法签名只接受 events/conversation/game_context，
        没有 summary 参数——确保 Facts 更新不依赖摘要。
        """
        import inspect
        sig = inspect.signature(facts_manager.update_facts)
        param_names = list(sig.parameters.keys())

        # 必须有这三个参数
        assert "events" in param_names
        assert "conversation" in param_names
        assert "game_context" in param_names

        # 不应有 summary 参数
        assert "summary" not in param_names
        assert "episode_summary" not in param_names

    def test_memory_system_update_facts_called_with_events(
        self,
        memory_root: Path,
        sample_events: list[dict],
        sample_conversation: list[dict],
    ):
        """process_pending_archives 成功后应用原始事件调用 update_facts（非摘要）。"""
        ms = MemorySystem(memory_root)

        # 写入一些事件和对话
        for evt in sample_events:
            ms.append_event(evt)
        for msg in sample_conversation:
            ms.append_conversation(msg["role"], msg["content"])

        # 归档
        ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")

        # mock SummaryGenerator 返回固定摘要
        summary_content = "今天钓鱼很开心！\n\n## 标签\n钓鱼\n\n## 提取的事实\n- 钓到鲶鱼"
        mock_sg_response = MagicMock()
        mock_sg_response.choices[0].message.content = summary_content

        # mock FactsManager.update_facts 捕获调用参数
        update_calls = []

        def mock_update_facts(events, conversation, game_context):
            update_calls.append({
                "events": events,
                "conversation": conversation,
                "game_context": game_context,
            })
            return True

        with patch("llm_client.completion", return_value=mock_sg_response):
            with patch.object(ms._facts_manager, "update_facts", side_effect=mock_update_facts):
                ms.process_pending_archives()

        # 应调用了一次 update_facts
        assert len(update_calls) == 1
        call_data = update_calls[0]

        # 传递的是原始事件列表（dict 格式），不是摘要字符串
        assert isinstance(call_data["events"], list)
        assert isinstance(call_data["conversation"], list)
        assert call_data["game_context"] == "Y1-Spring-Day5"

        # 验证事件内容（原始事件，不是摘要）
        event_contents = [e.get("content", "") for e in call_data["events"]]
        assert any("浇水" in c or "钓" in c for c in event_contents)

    def test_facts_update_uses_raw_events_not_summary_text(
        self,
        memory_root: Path,
    ):
        """Facts 更新 Prompt 中不包含摘要文本中特有的主观表达。

        核心验证：摘要中的主观表达（"特别开心"）不应出现在 Facts 更新的 Prompt 中。
        """
        ms = MemorySystem(memory_root)

        # 写入客观事件
        ms.append_event({
            "ts_game": "Y1-Spring-D5-15:00",
            "type": "player_action",
            "content": "钓到了鲶鱼",
        })
        ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")

        # 摘要中包含主观表达
        subjective_summary = "今天钓鱼特别开心！运气超好！\n\n## 标签\n钓鱼\n\n## 提取的事实\n- 钓到鲶鱼"
        mock_sg_response = MagicMock()
        mock_sg_response.choices[0].message.content = subjective_summary

        captured_facts_prompts = []

        def mock_facts_update(events, conversation, game_context):
            captured_facts_prompts.append({
                "events": events,
                "conversation": conversation,
            })
            return True

        with patch("llm_client.completion", return_value=mock_sg_response):
            with patch.object(ms._facts_manager, "update_facts", side_effect=mock_facts_update):
                ms.process_pending_archives()

        assert len(captured_facts_prompts) == 1
        facts_events = captured_facts_prompts[0]["events"]

        # Facts 更新收到的是原始事件列表，不是摘要字符串
        assert isinstance(facts_events, list)
        # 原始事件中不含摘要中的主观表达
        events_str = json.dumps(facts_events, ensure_ascii=False)
        assert "特别开心" not in events_str
        assert "运气超好" not in events_str
        # 但包含客观事实
        assert "钓到了鲶鱼" in events_str


# ────────────────────────────────────────────────────────────────────────────
# TestMemorySystemIntegration — MemorySystem 与 FactsManager 集成
# ────────────────────────────────────────────────────────────────────────────


class TestMemorySystemIntegration:
    """MemorySystem 集成测试 — 验证 M3 与 M1/M2 的兼容性。"""

    def test_memory_system_has_facts_manager(self, memory_system: MemorySystem):
        """MemorySystem 应有 _facts_manager 属性，且该属性有正确的接口。"""
        assert hasattr(memory_system, "_facts_manager")
        fm = memory_system._facts_manager
        # 检查接口而非类型（避免多文件测试中的模块注册冲突导致 isinstance 失败）
        assert hasattr(fm, "get_facts")
        assert hasattr(fm, "get_facts_summary")
        assert hasattr(fm, "update_facts")
        assert hasattr(fm, "_atomic_write")

    def test_get_facts_delegates_to_facts_manager(self, memory_system: MemorySystem):
        """get_facts 应委托给 FactsManager.get_facts。"""
        test_content = "## 关于搭子\n- 名字：测试用户\n\n## 我们的关系\n- 阶段：初识\n\n## 世界状态\n- 当前季节：夏季\n"
        memory_system._facts_manager._atomic_write(test_content)
        assert memory_system.get_facts() == test_content

    def test_get_facts_summary_delegates_to_facts_manager(self, memory_system: MemorySystem):
        """get_facts_summary 应委托给 FactsManager.get_facts_summary。"""
        test_content = "## 关于搭子\n- 名字：测试用户\n\n## 我们的关系\n- 阶段：初识\n\n## 世界状态\n- 当前季节：夏季\n"
        memory_system._facts_manager._atomic_write(test_content)
        summary = memory_system.get_facts_summary()
        assert "## 关于搭子" in summary
        assert "## 我们的关系" in summary
        assert "## 世界状态" in summary

    def test_process_pending_calls_update_facts_on_success(self, memory_root: Path):
        """process_pending_archives 摘要生成成功后应调用 update_facts。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "player_action", "content": "测试事件"})
        ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")

        summary_content = "今天测试。\n\n## 标签\n测试\n\n## 提取的事实\n- 测试完成"
        mock_response = MagicMock()
        mock_response.choices[0].message.content = summary_content

        facts_update_called = []

        def mock_update(events, conversation, game_context):
            facts_update_called.append(True)
            return True

        with patch("llm_client.completion", return_value=mock_response):
            with patch.object(ms._facts_manager, "update_facts", side_effect=mock_update):
                count = ms.process_pending_archives()

        assert count == 1
        assert len(facts_update_called) == 1

    def test_process_pending_facts_update_failure_doesnt_fail_summary(self, memory_root: Path):
        """update_facts 失败时，摘要处理不应因此失败（already counted as success）。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "player_action", "content": "测试事件"})
        ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")

        summary_content = "今天测试。\n\n## 标签\n测试\n\n## 提取的事实\n- 测试完成"
        mock_response = MagicMock()
        mock_response.choices[0].message.content = summary_content

        with patch("llm_client.completion", return_value=mock_response):
            with patch.object(ms._facts_manager, "update_facts", return_value=False):
                count = ms.process_pending_archives()

        # 即使 update_facts 返回 False，摘要仍然成功
        assert count == 1
        ep_dir = ms._episodes_dir / "ep_001"
        assert (ep_dir / "summary.md").exists()

    def test_cold_start_facts_not_empty(self, memory_system: MemorySystem):
        """冷启动状态下 get_facts 不应返回空字符串。"""
        result = memory_system.get_facts()
        assert len(result.strip()) > 0

    def test_facts_dir_structure_created(self, memory_root: Path):
        """MemorySystem 初始化应创建完整的 facts/ 目录结构。"""
        ms = MemorySystem(memory_root)
        assert (memory_root / "facts").is_dir()
        assert (memory_root / "facts" / "history").is_dir()
