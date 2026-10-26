"""
tests.test_memory_system — 记忆系统完整测试套件（Sprint M1）。

测试覆盖：
    - TestEventWriter          : append/read_recent/read_all/freeze_and_move/线程安全/FIFO 淘汰
    - TestEventWriterPinning   : pinned 事件不被淘汰/player_chat 自动钉针/容量上限
    - TestConversationWriter   : append/read_recent/freeze_and_move/独立 FIFO
    - TestMemorySystem         : 初始化目录创建/append_event/append_conversation/get_recent_events/get_conversation
    - TestMemorySystemColdStart: get_facts 返回冷启动模板/get_facts_summary 返回标题行
    - TestMemorySystemArchive  : archive_episode 阶段 1（rename + 新建空文件）
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

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

_ms_mod = importlib.import_module("agent.memory.memory_system")
sys.modules["memory.memory_system"] = _ms_mod

from memory.event_writer import EventWriter
from memory.conversation_writer import ConversationWriter
from memory.memory_system import MemorySystem


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """提供临时目录。"""
    return tmp_path


@pytest.fixture
def event_file(tmp_dir: Path) -> Path:
    """提供临时事件文件路径。"""
    return tmp_dir / "events.jsonl"


@pytest.fixture
def conv_file(tmp_dir: Path) -> Path:
    """提供临时对话文件路径。"""
    return tmp_dir / "conversation.jsonl"


@pytest.fixture
def memory_root(tmp_dir: Path) -> Path:
    """提供临时记忆根目录。"""
    return tmp_dir / "memory"


# ────────────────────────────────────────────────────────────────────────────
# TestEventWriter
# ────────────────────────────────────────────────────────────────────────────


class TestEventWriter:
    """EventWriter 基础功能测试。"""

    def test_append_creates_file(self, event_file: Path) -> None:
        """append 写入后文件应存在。"""
        writer = EventWriter(event_file)
        writer.append({"type": "test", "ts": "2026-01-01T00:00:00Z", "content": "hello"})
        assert event_file.exists()

    def test_append_increments_count(self, event_file: Path) -> None:
        """append 应正确更新 count。"""
        writer = EventWriter(event_file)
        assert writer.count == 0
        writer.append({"type": "test", "ts": "2026-01-01T00:00:00Z"})
        assert writer.count == 1
        writer.append({"type": "test", "ts": "2026-01-01T00:00:01Z"})
        assert writer.count == 2

    def test_read_recent_returns_correct_count(self, event_file: Path) -> None:
        """read_recent(n) 应返回最近 n 条事件。"""
        writer = EventWriter(event_file)
        for i in range(10):
            writer.append({"type": "test", "ts": f"2026-01-01T00:00:0{i}Z", "idx": i})

        recent = writer.read_recent(5)
        assert len(recent) == 5
        # 最后 5 条的 idx 应为 5~9
        assert recent[-1]["idx"] == 9
        assert recent[0]["idx"] == 5

    def test_read_recent_fewer_than_n(self, event_file: Path) -> None:
        """事件少于 n 时应返回全部。"""
        writer = EventWriter(event_file)
        writer.append({"type": "test", "ts": "2026-01-01T00:00:00Z"})
        writer.append({"type": "test", "ts": "2026-01-01T00:00:01Z"})

        recent = writer.read_recent(10)
        assert len(recent) == 2

    def test_read_all_returns_all_events(self, event_file: Path) -> None:
        """read_all 应返回所有事件。"""
        writer = EventWriter(event_file)
        for i in range(5):
            writer.append({"type": "test", "ts": f"2026-01-01T00:00:0{i}Z"})

        all_events = writer.read_all()
        assert len(all_events) == 5

    def test_read_recent_empty_file(self, event_file: Path) -> None:
        """空文件时 read_recent 应返回空列表。"""
        writer = EventWriter(event_file)
        assert writer.read_recent(10) == []

    def test_fifo_eviction_removes_oldest(self, event_file: Path) -> None:
        """FIFO 淘汰应移除最旧的普通事件。"""
        writer = EventWriter(event_file, max_events=3, max_pinned=0)
        for i in range(5):
            writer.append({"type": "test", "ts": f"2026-01-01T00:00:0{i}Z", "idx": i})

        events = writer.read_all()
        # 应只剩 3 条（最新的 3 条：idx=2,3,4）
        assert len(events) == 3
        idxs = [e["idx"] for e in events]
        assert 2 in idxs
        assert 3 in idxs
        assert 4 in idxs

    def test_freeze_and_move(self, tmp_dir: Path, event_file: Path) -> None:
        """freeze_and_move 应原子移动文件并创建新空文件。"""
        writer = EventWriter(event_file)
        writer.append({"type": "test", "ts": "2026-01-01T00:00:00Z", "content": "x"})

        dest = tmp_dir / "archive" / "events.jsonl"
        writer.freeze_and_move(dest)

        # 目标文件应存在且有内容
        assert dest.exists()
        with open(dest, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1

        # 原始文件应重置为空
        assert event_file.exists()
        assert writer.count == 0

    def test_freeze_and_move_nonexistent_source(self, tmp_dir: Path) -> None:
        """源文件不存在时 freeze_and_move 应创建空目标文件（无事件的 Episode 合法）。"""
        writer = EventWriter(tmp_dir / "nonexistent.jsonl")
        dest = tmp_dir / "archive" / "events.jsonl"
        result = writer.freeze_and_move(dest)
        assert result is True  # 应成功
        assert dest.exists()  # 空目标文件应被创建
        assert dest.stat().st_size == 0  # 内容为空

    def test_count_restored_on_init(self, event_file: Path) -> None:
        """重新初始化时应从文件恢复 count。"""
        writer1 = EventWriter(event_file)
        for i in range(3):
            writer1.append({"type": "test", "ts": f"2026-01-01T00:00:0{i}Z"})

        writer2 = EventWriter(event_file)
        assert writer2.count == 3

    def test_thread_safety(self, event_file: Path) -> None:
        """多线程并发写入不应产生竞态或数据损坏。"""
        writer = EventWriter(event_file, max_events=1000)
        errors: list[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(20):
                    writer.append(
                        {
                            "type": "test",
                            "ts": f"2026-01-01T00:00:{worker_id:02d}Z",
                            "worker": worker_id,
                            "seq": i,
                        }
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"线程异常: {errors}"

        # 验证所有事件都是有效 JSON
        all_events = writer.read_all()
        assert len(all_events) == 100  # 5 线程 × 20 条


# ────────────────────────────────────────────────────────────────────────────
# TestEventWriterPinning
# ────────────────────────────────────────────────────────────────────────────


class TestEventWriterPinning:
    """EventWriter 钉针机制测试。"""

    def test_player_chat_auto_pinned(self, event_file: Path) -> None:
        """player_chat 类型事件应自动设置 pinned: true。"""
        writer = EventWriter(event_file)
        writer.append({"type": "player_chat", "ts": "2026-01-01T00:00:00Z", "content": "hi"})

        events = writer.read_all()
        assert len(events) == 1
        assert events[0].get("pinned") is True

    def test_pinned_event_not_evicted(self, event_file: Path) -> None:
        """pinned 事件不应被普通 FIFO 淘汰。"""
        writer = EventWriter(event_file, max_events=3, max_pinned=10)

        # 先写入一条 pinned 事件
        writer.append(
            {"type": "player_chat", "ts": "2026-01-01T00:00:00Z", "content": "keep me"}
        )

        # 写入 5 条普通事件（超出 max_events=3）
        for i in range(5):
            writer.append(
                {"type": "normal", "ts": f"2026-01-01T00:00:0{i+1}Z", "idx": i}
            )

        events = writer.read_all()

        # pinned 事件应仍然存在
        pinned = [e for e in events if e.get("pinned")]
        assert len(pinned) == 1
        assert pinned[0]["content"] == "keep me"

        # 普通事件应被淘汰到 3 条
        normal = [e for e in events if not e.get("pinned")]
        assert len(normal) <= 3

    def test_pinned_capacity_limit(self, event_file: Path) -> None:
        """pinned 事件超出 max_pinned 时应淘汰最旧的 pinned 事件。"""
        writer = EventWriter(event_file, max_events=100, max_pinned=3)

        # 写入 5 条 pinned 事件
        for i in range(5):
            writer.append(
                {
                    "type": "player_chat",
                    "ts": f"2026-01-01T00:00:0{i}Z",
                    "idx": i,
                }
            )

        events = writer.read_all()
        pinned = [e for e in events if e.get("pinned")]
        # 应只剩 3 条（最新的 3 条）
        assert len(pinned) == 3
        idxs = [e["idx"] for e in pinned]
        assert 2 in idxs
        assert 3 in idxs
        assert 4 in idxs

    def test_explicit_pinned_field_respected(self, event_file: Path) -> None:
        """显式设置 pinned: true 的事件也应受保护。"""
        writer = EventWriter(event_file, max_events=2, max_pinned=10)

        writer.append(
            {"type": "important", "ts": "2026-01-01T00:00:00Z", "pinned": True, "content": "vip"}
        )
        for i in range(4):
            writer.append({"type": "normal", "ts": f"2026-01-01T00:00:0{i+1}Z"})

        events = writer.read_all()
        pinned = [e for e in events if e.get("pinned")]
        assert len(pinned) == 1
        assert pinned[0]["content"] == "vip"


# ────────────────────────────────────────────────────────────────────────────
# TestConversationWriter
# ────────────────────────────────────────────────────────────────────────────


class TestConversationWriter:
    """ConversationWriter 测试。"""

    def test_append_creates_file(self, conv_file: Path) -> None:
        """append 写入后文件应存在。"""
        writer = ConversationWriter(conv_file)
        writer.append("player", "你好")
        assert conv_file.exists()

    def test_append_format_is_correct(self, conv_file: Path) -> None:
        """每行应为有效 JSON 且包含 ts/role/content 字段。"""
        writer = ConversationWriter(conv_file)
        writer.append("player", "你好")

        with open(conv_file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["role"] == "player"
        assert record["content"] == "你好"
        assert "ts" in record

    def test_read_recent_returns_recent_turns(self, conv_file: Path) -> None:
        """read_recent(n) 应返回最近 n 轮对话（约 2n 条消息）。"""
        writer = ConversationWriter(conv_file, max_turns=100)
        for i in range(6):
            writer.append("player", f"问题{i}")
            writer.append("agent", f"回答{i}")

        # 读取最近 3 轮
        recent = writer.read_recent(3)
        # 3 轮 * 2 = 6 条，但总共有 12 条
        assert len(recent) == 6
        # 最后一条应是第 5 轮的 agent 回答
        assert recent[-1]["content"] == "回答5"

    def test_read_recent_fewer_than_n(self, conv_file: Path) -> None:
        """消息少于 n 轮时应返回全部。"""
        writer = ConversationWriter(conv_file)
        writer.append("player", "hi")
        writer.append("agent", "hello")

        recent = writer.read_recent(10)
        assert len(recent) == 2

    def test_fifo_eviction(self, conv_file: Path) -> None:
        """超出 max_turns 时应淘汰最旧的对话。"""
        writer = ConversationWriter(conv_file, max_turns=2)

        # 写入 4 轮对话（超出 max_turns=2）
        for i in range(4):
            writer.append("player", f"问{i}")
            writer.append("agent", f"答{i}")

        recent = writer.read_recent(100)
        # 应只剩 2 轮 = 4 条消息
        assert len(recent) == 4
        # 最后一轮是第 3 轮
        assert recent[-1]["content"] == "答3"
        assert recent[0]["content"] == "问2"

    def test_freeze_and_move(self, tmp_dir: Path, conv_file: Path) -> None:
        """freeze_and_move 应原子移动文件并创建新空文件。"""
        writer = ConversationWriter(conv_file)
        writer.append("player", "你好")
        writer.append("agent", "嗨！")

        dest = tmp_dir / "archive" / "conversation.jsonl"
        writer.freeze_and_move(dest)

        # 目标文件应存在
        assert dest.exists()
        with open(dest, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 2

        # 原始文件应重置为空
        assert conv_file.exists()
        assert conv_file.stat().st_size == 0

    def test_thread_safety(self, conv_file: Path) -> None:
        """多线程并发写入不应崩溃。"""
        writer = ConversationWriter(conv_file, max_turns=1000)
        errors: list[Exception] = []

        def worker(wid: int) -> None:
            try:
                for i in range(10):
                    writer.append("player", f"worker{wid}-msg{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        all_records = writer.read_all()
        assert len(all_records) == 40


# ────────────────────────────────────────────────────────────────────────────
# TestMemorySystem
# ────────────────────────────────────────────────────────────────────────────


class TestMemorySystem:
    """MemorySystem 基础功能测试。"""

    def test_init_creates_directory_structure(self, memory_root: Path) -> None:
        """初始化应创建完整的目录结构。"""
        ms = MemorySystem(memory_root)

        assert (memory_root / "current").is_dir()
        assert (memory_root / "episodes").is_dir()
        assert (memory_root / "facts").is_dir()
        assert (memory_root / "facts" / "history").is_dir()

    def test_append_event_writes_to_file(self, memory_root: Path) -> None:
        """append_event 应写入 current/events.jsonl。"""
        ms = MemorySystem(memory_root)
        ms.append_event(
            {"type": "player_action", "ts": "2026-01-01T00:00:00Z", "content": "浇水"}
        )

        events_path = memory_root / "current" / "events.jsonl"
        assert events_path.exists()

        with open(events_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["content"] == "浇水"

    def test_append_event_auto_ts(self, memory_root: Path) -> None:
        """缺少 ts 的事件应自动补充时间戳。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "test", "content": "no ts"})

        events = ms.get_recent_events(1)
        assert len(events) == 1
        assert "ts" in events[0]

    def test_append_conversation(self, memory_root: Path) -> None:
        """append_conversation 应写入 current/conversation.jsonl。"""
        ms = MemorySystem(memory_root)
        ms.append_conversation("player", "你好")
        ms.append_conversation("agent", "嗨！")

        conv_path = memory_root / "current" / "conversation.jsonl"
        assert conv_path.exists()

        with open(conv_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 2

    def test_get_recent_events(self, memory_root: Path) -> None:
        """get_recent_events 应返回最近 N 条事件。"""
        ms = MemorySystem(memory_root)
        for i in range(10):
            ms.append_event({"type": "test", "ts": f"2026-01-01T00:00:0{i}Z", "idx": i})

        events = ms.get_recent_events(5)
        assert len(events) == 5
        assert events[-1]["idx"] == 9

    def test_get_conversation(self, memory_root: Path) -> None:
        """get_conversation 应返回最近 N 轮对话。"""
        ms = MemorySystem(memory_root)
        for i in range(6):
            ms.append_conversation("player", f"问{i}")
            ms.append_conversation("agent", f"答{i}")

        conv = ms.get_conversation(3)
        # 最近 3 轮 = 6 条
        assert len(conv) == 6
        assert conv[-1]["content"] == "答5"

    def test_get_recent_events_empty(self, memory_root: Path) -> None:
        """无事件时 get_recent_events 应返回空列表。"""
        ms = MemorySystem(memory_root)
        assert ms.get_recent_events() == []

    def test_get_conversation_empty(self, memory_root: Path) -> None:
        """无对话时 get_conversation 应返回空列表。"""
        ms = MemorySystem(memory_root)
        assert ms.get_conversation() == []

    def test_player_chat_event_pinned(self, memory_root: Path) -> None:
        """player_chat 类型事件应被自动钉针。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "player_chat", "content": "要去钓鱼"})

        events = ms.get_recent_events(10)
        assert len(events) == 1
        assert events[0].get("pinned") is True


# ────────────────────────────────────────────────────────────────────────────
# TestMemorySystemColdStart
# ────────────────────────────────────────────────────────────────────────────


class TestMemorySystemColdStart:
    """MemorySystem 冷启动测试。"""

    def test_get_facts_returns_cold_start_when_no_file(self, memory_root: Path) -> None:
        """facts/current.md 不存在时应返回冷启动模板。"""
        ms = MemorySystem(memory_root)
        facts = ms.get_facts()

        assert facts.strip() != ""
        # 冷启动模板应包含关键段落
        assert "## 关于搭子" in facts
        assert "## 我们的关系" in facts
        assert "## 世界状态" in facts

    def test_get_facts_returns_file_content_when_exists(self, memory_root: Path) -> None:
        """facts/current.md 存在时应返回文件内容。"""
        ms = MemorySystem(memory_root)

        # 手动写入 facts
        facts_content = "## 关于搭子\n- 名字：小明\n\n## 我们的关系\n- 阶段：熟悉\n"
        facts_path = memory_root / "facts" / "current.md"
        with open(facts_path, "w", encoding="utf-8") as f:
            f.write(facts_content)

        result = ms.get_facts()
        assert "小明" in result
        assert "熟悉" in result

    def test_get_facts_summary_returns_headers(self, memory_root: Path) -> None:
        """get_facts_summary 应只返回 ## 标题行。"""
        ms = MemorySystem(memory_root)
        summary = ms.get_facts_summary()

        # 冷启动模板有 3 个 ## 标题
        headers = [line for line in summary.splitlines() if line.startswith("## ")]
        assert len(headers) == 3
        assert any("关于搭子" in h for h in headers)
        assert any("我们的关系" in h for h in headers)
        assert any("世界状态" in h for h in headers)

    def test_get_facts_summary_concise_format(self, memory_root: Path) -> None:
        """get_facts_summary 应比 get_facts 全文更短（渐进式裁剪用）。

        M3 更新：摘要包含 ## 标题行 + 每节第一个 bullet，
        比全量 Facts 更短，可用于 token 紧张时的降级场景。
        """
        ms = MemorySystem(memory_root)
        full_facts = ms.get_facts()
        summary = ms.get_facts_summary()
        # 摘要应比全量更短
        assert len(summary) <= len(full_facts)

    def test_get_recent_summaries_returns_empty_m1(self, memory_root: Path) -> None:
        """M1 阶段 get_recent_summaries 对没有 summary.md 的 episode 返回空列表。"""
        ms = MemorySystem(memory_root)
        # 创建 episodes 目录但没有 summary.md
        ep_dir = memory_root / "episodes" / "ep_001"
        ep_dir.mkdir(parents=True)

        result = ms.get_recent_summaries()
        assert result == []


# ────────────────────────────────────────────────────────────────────────────
# TestMemorySystemArchive
# ────────────────────────────────────────────────────────────────────────────


class TestMemorySystemArchive:
    """MemorySystem archive_episode 测试（阶段 1 同步归档）。"""

    def test_archive_creates_episode_dir(self, memory_root: Path) -> None:
        """archive_episode 应创建 ep_NNN 目录。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "test", "ts": "2026-01-01T00:00:00Z"})

        ep_id = ms.archive_episode(trigger="day_end", game_context="春季第1天")

        assert ep_id == "ep_001"
        assert (memory_root / "episodes" / "ep_001").is_dir()

    def test_archive_moves_events_file(self, memory_root: Path) -> None:
        """archive_episode 应将 current/events.jsonl 移动到归档目录。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "player_action", "ts": "2026-01-01T00:00:00Z"})
        ms.append_event({"type": "player_action", "ts": "2026-01-01T00:00:01Z"})

        ms.archive_episode(trigger="day_end")

        # 归档目录应有事件文件
        archived = memory_root / "episodes" / "ep_001" / "events.jsonl"
        assert archived.exists()

        with open(archived, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 2

    def test_archive_moves_conversation_file(self, memory_root: Path) -> None:
        """archive_episode 应将 current/conversation.jsonl 移动到归档目录。"""
        ms = MemorySystem(memory_root)
        ms.append_conversation("player", "今天好开心")
        ms.append_conversation("agent", "是的！")

        ms.archive_episode(trigger="day_end")

        archived = memory_root / "episodes" / "ep_001" / "conversation.jsonl"
        assert archived.exists()

    def test_archive_resets_current_files(self, memory_root: Path) -> None:
        """归档后 current/ 目录应有新的空文件。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "test", "ts": "2026-01-01T00:00:00Z"})

        ms.archive_episode(trigger="day_end")

        # current/events.jsonl 应存在但为空
        current_events = memory_root / "current" / "events.jsonl"
        assert current_events.exists()
        assert ms._event_writer.count == 0

        # 归档后可以继续写入
        ms.append_event({"type": "new_day", "ts": "2026-01-02T00:00:00Z"})
        events = ms.get_recent_events()
        assert len(events) == 1
        assert events[0]["type"] == "new_day"

    def test_archive_sequential_episode_ids(self, memory_root: Path) -> None:
        """多次归档应生成递增的 episode_id。"""
        ms = MemorySystem(memory_root)

        ms.append_event({"type": "test", "ts": "2026-01-01T00:00:00Z"})
        ep1 = ms.archive_episode(trigger="day_end")

        ms.append_event({"type": "test", "ts": "2026-01-02T00:00:00Z"})
        ep2 = ms.archive_episode(trigger="day_end")

        ms.append_event({"type": "test", "ts": "2026-01-03T00:00:00Z"})
        ep3 = ms.archive_episode(trigger="day_end")

        assert ep1 == "ep_001"
        assert ep2 == "ep_002"
        assert ep3 == "ep_003"

    def test_archive_writes_meta_json(self, memory_root: Path) -> None:
        """archive_episode 应写入 meta.json 元数据文件。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "test", "ts": "2026-01-01T00:00:00Z"})

        ms.archive_episode(trigger="sleep", game_context="春季第5天入睡")

        meta_path = memory_root / "episodes" / "ep_001" / "meta.json"
        assert meta_path.exists()

        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

        assert meta["episode_id"] == "ep_001"
        assert meta["trigger"] == "sleep"
        assert "春季第5天" in meta["game_context"]
        assert meta["summary_status"] == "pending"

    def test_archive_enqueues_pending(self, memory_root: Path) -> None:
        """archive_episode 应将任务加入 pending_archive_queue.json。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "test", "ts": "2026-01-01T00:00:00Z"})
        ms.archive_episode(trigger="day_end")

        queue_path = memory_root / "pending_archive_queue.json"
        assert queue_path.exists()

        with open(queue_path, encoding="utf-8") as f:
            queue = json.load(f)

        assert len(queue) == 1
        assert queue[0]["episode_id"] == "ep_001"
        assert queue[0]["retry_count"] == 0

    def test_archive_empty_current(self, memory_root: Path) -> None:
        """空的工作记忆也可以归档（归档操作不应崩溃）。"""
        ms = MemorySystem(memory_root)
        ep_id = ms.archive_episode(trigger="shutdown")
        assert ep_id == "ep_001"

    def test_get_context_for_prompt_structure(self, memory_root: Path) -> None:
        """get_context_for_prompt 应返回包含各层记忆的结构化文本。"""
        ms = MemorySystem(memory_root)
        ms.append_event({"type": "player_action", "ts": "2026-01-01T00:00:00Z", "content": "浇水"})
        ms.append_conversation("player", "今天天气真好")
        ms.append_conversation("agent", "是的！")

        context = ms.get_context_for_prompt()

        # 应包含各层标题
        assert "关于搭子" in context
        assert "今天发生的事" in context
        assert "对话历史" in context
        # 事件内容应在上下文中
        assert "浇水" in context
        # 对话内容应在上下文中
        assert "今天天气真好" in context
