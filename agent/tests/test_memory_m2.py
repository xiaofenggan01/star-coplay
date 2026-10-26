"""
tests.test_memory_m2 — 记忆系统 Sprint M2 测试套件。

测试覆盖：
    - TestSummaryGenerator      : Prompt 构建、LLM 调用（mock litellm）、降级版摘要
    - TestPendingArchiveQueue   : 队列 CRUD、重试、max retries、failed 标记
    - TestArchiveE2E            : 完整归档流程（append events → archive → process_pending → summary.md 存在）
    - TestGetRecentSummaries    : 有 summary.md 时返回内容、按 ID 倒序、无 summary 时跳过
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
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

# 导入并注册各模块（短名 + 全名）
_ew_mod = importlib.import_module("agent.memory.event_writer")
sys.modules["memory.event_writer"] = _ew_mod

_cw_mod = importlib.import_module("agent.memory.conversation_writer")
sys.modules["memory.conversation_writer"] = _cw_mod

_sg_mod = importlib.import_module("agent.memory.summary_generator")
sys.modules["memory.summary_generator"] = _sg_mod

_ms_mod = importlib.import_module("agent.memory.memory_system")
sys.modules["memory.memory_system"] = _ms_mod

from memory.summary_generator import SummaryGenerator
from memory.memory_system import MemorySystem


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    """提供临时记忆根目录。"""
    return tmp_path / "memory"


@pytest.fixture
def memory_system(memory_root: Path) -> MemorySystem:
    """提供已初始化的 MemorySystem。"""
    return MemorySystem(memory_root)


@pytest.fixture
def sample_events() -> list[dict]:
    """样例事件列表。"""
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
        {"ts": "2026-04-01T10:00:20Z", "role": "player", "content": "去海边吧"},
        {"ts": "2026-04-01T10:00:25Z", "role": "agent", "content": "那走吧！"},
    ]


# ────────────────────────────────────────────────────────────────────────────
# TestSummaryGenerator
# ────────────────────────────────────────────────────────────────────────────


class TestSummaryGenerator:
    """SummaryGenerator 测试。"""

    def test_fallback_returns_markdown(self, sample_events, sample_conversation):
        """降级版摘要应返回 Markdown 格式文本。"""
        gen = SummaryGenerator()
        result = gen.generate_fallback(
            events=sample_events,
            conversation=sample_conversation,
            game_context="Y1-Spring-Day5",
        )
        assert isinstance(result, str)
        assert len(result) > 0
        # 降级版应包含事件记录段
        assert "## 事件记录" in result

    def test_fallback_includes_events(self, sample_events):
        """降级版摘要应包含所有事件内容。"""
        gen = SummaryGenerator()
        result = gen.generate_fallback(
            events=sample_events,
            conversation=[],
            game_context="Y1-Spring-Day5",
        )
        assert "浇水" in result
        assert "钓到了鲶鱼" in result

    def test_fallback_includes_conversation(self, sample_conversation):
        """降级版摘要应包含对话记录。"""
        gen = SummaryGenerator()
        result = gen.generate_fallback(
            events=[],
            conversation=sample_conversation,
            game_context="Y1-Spring-Day5",
        )
        assert "## 对话记录" in result
        assert "钓鱼" in result

    def test_fallback_empty_events(self):
        """空事件列表的降级摘要不应崩溃。"""
        gen = SummaryGenerator()
        result = gen.generate_fallback(
            events=[],
            conversation=[],
            game_context="Y1-Spring-Day5",
        )
        assert isinstance(result, str)
        assert "无事件" in result or "## 事件记录" in result

    def test_fallback_includes_tags_section(self):
        """降级版摘要应包含 ## 标签 段。"""
        gen = SummaryGenerator()
        result = gen.generate_fallback(events=[], conversation=[], game_context="")
        assert "## 标签" in result

    def test_fallback_includes_facts_section(self):
        """降级版摘要应包含 ## 提取的事实 段。"""
        gen = SummaryGenerator()
        result = gen.generate_fallback(events=[], conversation=[], game_context="")
        assert "## 提取的事实" in result

    def test_generate_calls_litellm_when_available(self, sample_events, sample_conversation):
        """有 litellm 时，generate() 应调用 llm_client.completion。"""
        gen = SummaryGenerator(model="claude-haiku-4-5-20251001")

        # 构造 mock 返回值
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "今天和玩家一起去钓鱼！\n\n## 标签\n钓鱼\n\n## 提取的事实\n- 玩家喜欢钓鱼"

        with patch("llm_client.completion", return_value=mock_response) as mock_completion:
            result = gen.generate(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
                current_facts="## 关于搭子\n- 名字：小明",
            )

        # 应调用过 llm_client.completion
        mock_completion.assert_called_once()
        # 返回值应是 LLM 输出
        assert "钓鱼" in result

    def test_generate_uses_correct_model(self, sample_events, sample_conversation):
        """generate() 应使用初始化时指定的模型。"""
        model_name = "claude-haiku-4-5-20251001"
        gen = SummaryGenerator(model=model_name)

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "测试摘要内容"

        with patch("llm_client.completion", return_value=mock_response) as mock_completion:
            gen.generate(events=[], conversation=[], game_context="test")

        call_kwargs = mock_completion.call_args
        assert call_kwargs[1]["model"] == model_name or call_kwargs[0][0] == model_name or mock_completion.call_args.kwargs.get("model") == model_name

    def test_generate_falls_back_on_llm_failure(self, sample_events, sample_conversation):
        """LLM 调用抛出异常时，generate() 应降级为 fallback 摘要。"""
        gen = SummaryGenerator()

        with patch("llm_client.completion", side_effect=Exception("LLM 超时")):
            result = gen.generate(
                events=sample_events,
                conversation=sample_conversation,
                game_context="Y1-Spring-Day5",
            )

        # 降级版应包含事件记录
        assert isinstance(result, str)
        assert len(result) > 0
        # 降级版摘要包含降级标记
        assert "自动生成" in result or "## 事件记录" in result

    def test_generate_falls_back_on_empty_llm_response(self, sample_events):
        """LLM 返回空内容时，generate() 应降级为 fallback 摘要。"""
        gen = SummaryGenerator()

        mock_response = MagicMock()
        mock_response.choices[0].message.content = ""

        with patch("llm_client.completion", return_value=mock_response):
            result = gen.generate(
                events=sample_events,
                conversation=[],
                game_context="Y1-Spring-Day5",
            )

        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_events_empty(self):
        """format_events 空列表应返回占位文本。"""
        from memory._formatters import format_events
        result = format_events([])
        assert "无事件" in result

    def test_format_events_with_ts_game(self):
        """format_events 有 ts_game 时应包含游戏时间戳。"""
        from memory._formatters import format_events
        events = [{"ts_game": "Y1-Spring-D5-08:00", "type": "player_action", "content": "浇水"}]
        result = format_events(events)
        assert "Y1-Spring-D5-08:00" in result
        assert "浇水" in result

    def test_format_conversation_empty(self):
        """format_conversation 空列表应返回占位文本。"""
        from memory._formatters import format_conversation
        result = format_conversation([])
        assert "无对话" in result

    def test_format_conversation_role_mapping(self):
        """format_conversation 应将 player/agent 映射为中文角色名。"""
        from memory._formatters import format_conversation
        conv = [
            {"role": "player", "content": "你好"},
            {"role": "agent", "content": "嗨！"},
        ]
        result = format_conversation(conv)
        assert "玩家" in result
        assert "AI" in result


# ────────────────────────────────────────────────────────────────────────────
# TestPendingArchiveQueue
# ────────────────────────────────────────────────────────────────────────────


class TestPendingArchiveQueue:
    """pending_archive_queue 管理测试。"""

    def test_process_pending_returns_zero_when_no_queue(self, memory_system: MemorySystem):
        """没有 pending_archive_queue.json 时 process_pending_archives 应返回 0。"""
        result = memory_system.process_pending_archives()
        assert result == 0

    def test_process_pending_returns_zero_when_empty_queue(self, memory_root: Path):
        """空队列时 process_pending_archives 应返回 0。"""
        ms = MemorySystem(memory_root)
        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([], f)

        result = ms.process_pending_archives()
        assert result == 0

    def test_process_pending_skips_missing_episode_dir(self, memory_root: Path):
        """episode 目录不存在时，任务应保留在队列中并增加 retry_count。"""
        ms = MemorySystem(memory_root)
        # 手动写一个指向不存在目录的 pending 任务
        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([{
                "episode_id": "ep_999",
                "trigger": "day_end",
                "game_context": "",
                "retry_count": 0,
            }], f)

        ms.process_pending_archives()

        # 队列任务应仍在，retry_count +1
        with open(queue_path) as f:
            queue = json.load(f)
        assert len(queue) == 1
        assert queue[0]["retry_count"] == 1

    def test_process_pending_marks_failed_after_max_retries(self, memory_root: Path):
        """retry_count >= MAX_RETRY_COUNT 时任务应标记为 failed。"""
        ms = MemorySystem(memory_root)
        max_retries = MemorySystem.MAX_RETRY_COUNT

        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([{
                "episode_id": "ep_999",
                "trigger": "day_end",
                "game_context": "",
                "retry_count": max_retries,  # 已达上限
            }], f)

        ms.process_pending_archives()

        with open(queue_path) as f:
            queue = json.load(f)
        assert len(queue) == 1
        assert queue[0].get("status") == "failed"

    def test_process_pending_skips_already_failed(self, memory_root: Path):
        """已标记 failed 的任务应被跳过，不再修改。"""
        ms = MemorySystem(memory_root)
        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([{
                "episode_id": "ep_999",
                "trigger": "day_end",
                "game_context": "",
                "retry_count": 10,
                "status": "failed",
            }], f)

        result = ms.process_pending_archives()
        assert result == 0

        with open(queue_path) as f:
            queue = json.load(f)
        # 任务应仍在队列中（保留 failed 状态）
        assert len(queue) == 1
        assert queue[0]["status"] == "failed"

    def test_process_pending_removes_task_on_success(self, memory_root: Path, sample_events, sample_conversation):
        """处理成功后任务应从队列中移除。"""
        ms = MemorySystem(memory_root)

        # 手动创建 episode 目录和事件文件
        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True)
        _write_jsonl(ep_dir / "events.jsonl", sample_events)
        _write_jsonl(ep_dir / "conversation.jsonl", sample_conversation)
        _write_meta(ep_dir, "ep_001", "day_end", "Y1-Spring-Day5")

        # 手动入队
        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([{
                "episode_id": "ep_001",
                "trigger": "day_end",
                "game_context": "Y1-Spring-Day5",
                "retry_count": 0,
            }], f)

        # mock LLM 成功返回
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "今天很开心！\n\n## 标签\n钓鱼\n\n## 提取的事实\n- test"

        with patch("llm_client.completion", return_value=mock_response):
            result = ms.process_pending_archives()

        assert result == 1

        # 队列应为空
        with open(queue_path) as f:
            queue = json.load(f)
        assert len(queue) == 0

    def test_process_pending_succeeds_with_fallback_on_llm_failure(self, memory_root: Path, sample_events):
        """LLM 异常时 SummaryGenerator 自动降级为 fallback，任务仍然成功。

        设计原则：fallback 摘要也是有效摘要，process_pending_archives 应视为成功。
        """
        ms = MemorySystem(memory_root)

        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True)
        _write_jsonl(ep_dir / "events.jsonl", sample_events)
        (ep_dir / "conversation.jsonl").touch()
        _write_meta(ep_dir, "ep_001", "day_end", "")

        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([{
                "episode_id": "ep_001",
                "trigger": "day_end",
                "game_context": "",
                "retry_count": 0,
            }], f)

        # LLM 失败，但 SummaryGenerator 内部会降级为 fallback
        with patch("llm_client.completion", side_effect=Exception("timeout")):
            result = ms.process_pending_archives()

        # fallback 摘要算成功，返回 1
        assert result == 1

        # 队列应为空（任务已完成）
        with open(queue_path) as f:
            queue = json.load(f)
        assert len(queue) == 0

        # summary.md 应存在（fallback 内容）
        assert (ep_dir / "summary.md").exists()

    def test_process_pending_multiple_tasks(self, memory_root: Path, sample_events, sample_conversation):
        """多个任务时应逐个处理。"""
        ms = MemorySystem(memory_root)

        # 创建 2 个 episode 目录
        for ep_num in ["001", "002"]:
            ep_dir = memory_root / "episodes" / f"ep_{ep_num}"
            ep_dir.mkdir(parents=True)
            _write_jsonl(ep_dir / "events.jsonl", sample_events)
            _write_jsonl(ep_dir / "conversation.jsonl", sample_conversation)
            _write_meta(ep_dir, f"ep_{ep_num}", "day_end", "")

        queue_path = memory_root / "pending_archive_queue.json"
        with open(queue_path, "w") as f:
            json.dump([
                {"episode_id": "ep_001", "trigger": "day_end", "game_context": "", "retry_count": 0},
                {"episode_id": "ep_002", "trigger": "day_end", "game_context": "", "retry_count": 0},
            ], f)

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "摘要内容\n\n## 标签\ntest\n\n## 提取的事实\n- test"

        with patch("llm_client.completion", return_value=mock_response):
            result = ms.process_pending_archives()

        assert result == 2


# ────────────────────────────────────────────────────────────────────────────
# TestArchiveE2E
# ────────────────────────────────────────────────────────────────────────────


class TestArchiveE2E:
    """完整归档流程 E2E 测试。"""

    def test_archive_then_process_creates_summary(
        self, memory_root: Path, sample_events, sample_conversation
    ):
        """完整流程：append events → archive → process_pending → summary.md 存在。"""
        ms = MemorySystem(memory_root)

        # 写入事件和对话
        for evt in sample_events:
            ms.append_event(evt)
        for msg in sample_conversation:
            ms.append_conversation(msg["role"], msg["content"])

        # 阶段 1 归档
        ep_id = ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")
        assert ep_id == "ep_001"

        # 此时 summary.md 应不存在（pending）
        ep_dir = memory_root / "episodes" / ep_id
        assert not (ep_dir / "summary.md").exists()

        # 处理 pending，mock LLM
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "今天和小明去钓鱼！\n\n## 标签\n钓鱼、合作\n\n## 提取的事实\n- 小明喜欢钓鱼"

        with patch("llm_client.completion", return_value=mock_response):
            processed = ms.process_pending_archives()

        assert processed == 1
        # summary.md 应已创建
        assert (ep_dir / "summary.md").exists()
        # meta.json 的 summary_status 应更新为 done
        with open(ep_dir / "meta.json") as f:
            meta = json.load(f)
        assert meta["summary_status"] in ("done", "done_facts_pending")

    def test_summary_content_is_llm_output(
        self, memory_root: Path, sample_events, sample_conversation
    ):
        """summary.md 的内容应是 LLM 返回的文本。

        传入 summary_model 以规避 'model 未配置' early-exit，确保 LLM mock 能被调用。
        """
        ms = MemorySystem(memory_root, summary_model="gpt-4o-mini-test")
        for evt in sample_events:
            ms.append_event(evt)

        ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")

        expected_content = "今天和小明去钓鱼，很开心！\n\n## 标签\n钓鱼\n\n## 提取的事实\n- 小明喜欢钓鱼"
        mock_response = MagicMock()
        mock_response.choices[0].message.content = expected_content

        with patch("llm_client.completion", return_value=mock_response):
            ms.process_pending_archives()

        ep_dir = memory_root / "episodes" / "ep_001"
        with open(ep_dir / "summary.md", "r", encoding="utf-8") as f:
            content = f.read()

        assert content.strip() == expected_content.strip()

    def test_fallback_summary_written_when_no_litellm(
        self, memory_root: Path, sample_events
    ):
        """litellm 不可用时，降级摘要也应正确写入 summary.md。"""
        ms = MemorySystem(memory_root)
        for evt in sample_events:
            ms.append_event(evt)
        ms.archive_episode(trigger="day_end", game_context="Y1-Spring-Day5")

        with patch("llm_client.completion", side_effect=Exception("unavailable")):
            processed = ms.process_pending_archives()

        assert processed == 1
        ep_dir = memory_root / "episodes" / "ep_001"
        assert (ep_dir / "summary.md").exists()
        with open(ep_dir / "summary.md", "r", encoding="utf-8") as f:
            content = f.read()
        # 降级版应有事件内容
        assert len(content) > 0

    def test_archive_multiple_days_e2e(self, memory_root: Path):
        """多天归档：每天 archive → 最后 process_pending 一次性处理所有。"""
        ms = MemorySystem(memory_root)
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "当天摘要\n\n## 标签\ntest\n\n## 提取的事实\n- test"

        ep_ids = []
        for day in range(3):
            ms.append_event({"type": "player_action", "ts": f"2026-01-0{day+1}T00:00:00Z", "content": f"第{day+1}天行动"})
            ep_id = ms.archive_episode(trigger="day_end", game_context=f"第{day+1}天")
            ep_ids.append(ep_id)

        assert ep_ids == ["ep_001", "ep_002", "ep_003"]

        # 一次性处理所有 pending
        with patch("llm_client.completion", return_value=mock_response):
            processed = ms.process_pending_archives()

        assert processed == 3
        for ep_id in ep_ids:
            assert (memory_root / "episodes" / ep_id / "summary.md").exists()

    def test_pending_queue_empty_after_all_success(self, memory_root: Path):
        """所有任务处理成功后 pending 队列应为空。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "test", "ts": "2026-01-01T00:00:00Z"})
        ms.archive_episode(trigger="day_end")

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "摘要\n\n## 标签\ntest\n\n## 提取的事实\n- test"

        with patch("llm_client.completion", return_value=mock_response):
            ms.process_pending_archives()

        with open(memory_root / "pending_archive_queue.json") as f:
            queue = json.load(f)
        assert len(queue) == 0


# ────────────────────────────────────────────────────────────────────────────
# TestGetRecentSummaries
# ────────────────────────────────────────────────────────────────────────────


class TestGetRecentSummaries:
    """get_recent_summaries 测试。"""

    def test_returns_empty_when_no_episodes(self, memory_system: MemorySystem):
        """无任何 episode 时应返回空列表。"""
        result = memory_system.get_recent_summaries()
        assert result == []

    def test_returns_empty_when_no_summary_md(self, memory_root: Path):
        """有 episode 目录但无 summary.md 时应返回空列表。"""
        ms = MemorySystem(memory_root)
        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True)
        # 只创建 events.jsonl，不创建 summary.md

        result = ms.get_recent_summaries()
        assert result == []

    def test_returns_summary_content_when_exists(self, memory_root: Path):
        """有 summary.md 时应返回其内容。"""
        ms = MemorySystem(memory_root)
        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True)
        content = "今天很开心！\n\n## 标签\n钓鱼\n\n## 提取的事实\n- 小明喜欢钓鱼"
        with open(ep_dir / "summary.md", "w", encoding="utf-8") as f:
            f.write(content)

        result = ms.get_recent_summaries()
        assert len(result) == 1
        assert "今天很开心" in result[0]

    def test_returns_sorted_by_id_ascending(self, memory_root: Path):
        """返回的摘要应按 ep_id 从旧到新排列（最旧在最前）。"""
        ms = MemorySystem(memory_root)
        for i in range(1, 4):
            ep_dir = memory_root / "episodes" / f"ep_00{i}"
            ep_dir.mkdir(parents=True)
            with open(ep_dir / "summary.md", "w", encoding="utf-8") as f:
                f.write(f"第{i}天摘要")

        result = ms.get_recent_summaries(3)
        assert len(result) == 3
        # 应按时间顺序排列（旧→新）
        assert "第1天" in result[0]
        assert "第2天" in result[1]
        assert "第3天" in result[2]

    def test_returns_most_recent_n_when_more_available(self, memory_root: Path):
        """episode 超过 n 时，应返回最近 n 个的摘要。"""
        ms = MemorySystem(memory_root)
        # 创建 5 个 episode，只有最近 3 个有 summary.md
        for i in range(1, 6):
            ep_dir = memory_root / "episodes" / f"ep_00{i}"
            ep_dir.mkdir(parents=True)
            with open(ep_dir / "summary.md", "w", encoding="utf-8") as f:
                f.write(f"第{i}天摘要")

        result = ms.get_recent_summaries(3)
        assert len(result) == 3
        # 应是最新的 3 个（3、4、5），按旧到新排列
        assert "第3天" in result[0]
        assert "第4天" in result[1]
        assert "第5天" in result[2]

    def test_skips_episodes_without_summary(self, memory_root: Path):
        """无 summary.md 的 episode 应被跳过。"""
        ms = MemorySystem(memory_root)

        # ep_001 无摘要
        ep1 = memory_root / "episodes" / "ep_001"
        ep1.mkdir(parents=True)

        # ep_002 有摘要
        ep2 = memory_root / "episodes" / "ep_002"
        ep2.mkdir(parents=True)
        with open(ep2 / "summary.md", "w", encoding="utf-8") as f:
            f.write("第2天摘要")

        # ep_003 无摘要
        ep3 = memory_root / "episodes" / "ep_003"
        ep3.mkdir(parents=True)

        result = ms.get_recent_summaries(5)
        assert len(result) == 1
        assert "第2天摘要" in result[0]

    def test_respects_n_limit(self, memory_root: Path):
        """get_recent_summaries(n) 应最多返回 n 个摘要。"""
        ms = MemorySystem(memory_root)
        for i in range(1, 8):
            ep_dir = memory_root / "episodes" / f"ep_00{i}"
            ep_dir.mkdir(parents=True)
            with open(ep_dir / "summary.md", "w", encoding="utf-8") as f:
                f.write(f"摘要{i}")

        result = ms.get_recent_summaries(3)
        assert len(result) == 3

    def test_get_context_for_prompt_includes_summaries(self, memory_root: Path):
        """get_context_for_prompt 应将 Episode 摘要包含在上下文中。"""
        ms = MemorySystem(memory_root)
        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True)
        with open(ep_dir / "summary.md", "w", encoding="utf-8") as f:
            f.write("今天钓到了鲶鱼！")

        context = ms.get_context_for_prompt()
        assert "最近的经历" in context
        assert "钓到了鲶鱼" in context


# ────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """写入 JSONL 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_meta(ep_dir: Path, episode_id: str, trigger: str, game_context: str) -> None:
    """写入 Episode meta.json。"""
    meta = {
        "episode_id": episode_id,
        "trigger": trigger,
        "game_context": game_context,
        "archived_at": "2026-04-01T00:00:00Z",
        "summary_status": "pending",
    }
    with open(ep_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
