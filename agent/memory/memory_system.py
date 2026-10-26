"""
memory.memory_system — V2 记忆系统。

管理三层记忆的存储、读取和维护：
    Layer 1 — 工作记忆（current/ 目录）：当前 Episode 的实时事件和对话缓冲
    Layer 2 — 情节日志（episodes/ 目录）：已归档 Episode 的原始事件 + LLM 摘要
    Layer 3 — 持久事实（facts/ 目录）：跨 Episode 的结构化认知（Markdown）

设计原则：
    - 存储层不可变（events.jsonl 追加写入）
    - 索引层可迭代（summary.md / facts/current.md 由 LLM 生成，可重建）
    - 对话与事件分离（不同 FIFO 策略）
    - 单写者 EventWriter 消除竞态
    - 两阶段归档（阶段 1 同步 rename，阶段 2 异步 LLM — M2 实现）
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from memory.conversation_writer import ConversationWriter
from memory.event_writer import EventWriter
from memory.facts_manager import FactsManager
from memory.summary_generator import SummaryGenerator

log = logging.getLogger(__name__)


class MemorySystem:
    """V2 记忆系统。

    管理三层记忆的存储、读取和维护。

    目录结构：
        memory_root/
        ├── current/
        │   ├── events.jsonl          # 当前 Episode 游戏事件
        │   └── conversation.jsonl    # 当前对话历史
        ├── episodes/
        │   ├── ep_001/
        │   │   ├── events.jsonl      # 原始事件（不可变）
        │   │   ├── conversation.jsonl
        │   │   └── summary.md        # LLM 摘要（M2 实现）
        │   └── ...
        ├── facts/
        │   ├── current.md            # 当前 Facts 快照
        │   └── history/
        │       ├── v001.md
        │       └── ...
        └── pending_archive_queue.json

    Args:
        memory_root: 记忆数据根目录（如 memory/save_001/）。
        max_events: 工作记忆事件最大保留数量（默认 200）。
        max_pinned: pinned 事件最大保留数量（默认 50）。
        max_conversation_turns: 对话最大保留轮数（默认 20）。
    """

    # 每个 pending 任务最大重试次数
    MAX_RETRY_COUNT: int = 5

    def __init__(
        self,
        memory_root: Path,
        max_events: int = 200,
        max_pinned: int = 50,
        max_conversation_turns: int = 20,
        summary_model: str = "",
        facts_model: str = "",
    ) -> None:
        self._root = Path(memory_root)

        # 创建目录结构
        self._current_dir = self._root / "current"
        self._episodes_dir = self._root / "episodes"
        self._facts_dir = self._root / "facts"
        self._facts_history_dir = self._facts_dir / "history"
        self._pending_queue_path = self._root / "pending_archive_queue.json"

        for d in [
            self._current_dir,
            self._episodes_dir,
            self._facts_dir,
            self._facts_history_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        # 初始化写入器
        self._event_writer = EventWriter(
            file_path=self._current_dir / "events.jsonl",
            max_events=max_events,
            max_pinned=max_pinned,
        )
        self._conversation_writer = ConversationWriter(
            file_path=self._current_dir / "conversation.jsonl",
            max_turns=max_conversation_turns,
        )

        # 初始化摘要生成器（M2）
        self._summary_generator = SummaryGenerator(model=summary_model)

        # 初始化 Facts 管理器（M3）
        self._facts_manager = FactsManager(
            facts_dir=self._facts_dir,
            model=facts_model,
        )

        log.info("MemorySystem 初始化完成: %s", self._root)
        # 并发保护
        self._archive_lock = threading.Lock()
        self._queue_lock = threading.Lock()

        self._check_integrity_on_startup()

    def _check_integrity_on_startup(self) -> None:
        """启动时检查 episode 目录完整性，修复中间状态。"""
        if not self._episodes_dir.exists():
            return

        for ep_dir in sorted(self._episodes_dir.iterdir()):
            if not ep_dir.is_dir():
                continue

            events_exists = (ep_dir / "events.jsonl").exists()
            conv_exists = (ep_dir / "conversation.jsonl").exists()
            meta_exists = (ep_dir / "meta.json").exists()

            if not events_exists and not conv_exists:
                # 完全空的 episode 目录 → 清理
                log.warning("MemorySystem: 空 episode 目录，清理: %s", ep_dir.name)
                shutil.rmtree(ep_dir, ignore_errors=True)
            elif not events_exists and conv_exists:
                # 只有 conversation 无 events → 不完整，清理
                log.warning("MemorySystem: episode 缺少 events，清理: %s", ep_dir.name)
                shutil.rmtree(ep_dir, ignore_errors=True)
            elif events_exists and not meta_exists:
                # 有 events 但无 meta → 补写 meta + 入队
                log.warning("MemorySystem: episode 缺少 meta.json，补写: %s", ep_dir.name)
                if not conv_exists:
                    (ep_dir / "conversation.jsonl").touch()
                self._write_episode_meta(ep_dir.name, "recovered", "")
                self._enqueue_pending_archive(ep_dir.name, "recovered", "")
            elif events_exists and not conv_exists:
                # 有 events 有 meta 但无 conversation → 创建空 conversation
                log.warning("MemorySystem: episode 缺少 conversation，创建空文件: %s", ep_dir.name)
                (ep_dir / "conversation.jsonl").touch()

    # ── 写入 ───────────────────────────────────────────────────────────────

    def append_event(self, event: dict) -> None:
        """追加游戏事件到 current/events.jsonl。

        player_chat 类型事件自动设置 pinned: true（钉针机制）。

        Args:
            event: 事件字典，推荐包含 ts、ts_game、type、content 字段。
        """
        # 补充 ts 字段（如缺失）
        if "ts" not in event:
            event = {**event, "ts": datetime.now(timezone.utc).isoformat()}

        self._event_writer.append(event)

    def append_conversation(self, role: str, content: str) -> None:
        """追加对话到 current/conversation.jsonl。

        Args:
            role: 说话方，"player" 或 "agent"。
            content: 对话内容文本。
        """
        self._conversation_writer.append(role, content)

    def archive_episode(self, trigger: str, game_context: str = "") -> str:
        """Episode 结束，归档当前工作记忆。

        M1 实现阶段 1（同步归档）：
            1. 生成 episode_id
            2. 原子 rename 事件文件和对话文件到归档目录
            3. 立即开启新的工作记忆

        阶段 2（异步摘要生成）将在 M2 实现。

        Args:
            trigger: 触发原因（如 "day_end"、"sleep"、"shutdown"）。
            game_context: 当前游戏上下文（如游戏日期、位置等，字符串格式）。

        Returns:
            episode_id（如 "ep_001"），归档失败时返回空字符串 ""（falsy）。
        """
        with self._archive_lock:
            # Parse game_context for episode naming
            ctx = {}
            try:
                ctx = ast.literal_eval(game_context) if isinstance(game_context, str) else (game_context if isinstance(game_context, dict) else {})
            except Exception:
                pass
            season = str(ctx.get("season", ""))[:3].lower()
            day = int(ctx.get("day", 0))
            episode_id = self._next_episode_id(season=season, day=day)
            ep_dir = self._episodes_dir / episode_id

            log.info("MemorySystem: 开始归档 Episode %s (trigger=%s)", episode_id, trigger)

            # 阶段 1：同步归档（shutil.move，支持跨文件系统）
            events_ok = self._event_writer.freeze_and_move(ep_dir / "events.jsonl")
            if not events_ok:
                log.error("MemorySystem: 事件归档失败，中止 Episode %s", episode_id)
                # 清理可能已创建的空目录
                if ep_dir.exists() and not any(ep_dir.iterdir()):
                    ep_dir.rmdir()
                return ""

            conv_ok = self._conversation_writer.freeze_and_move(ep_dir / "conversation.jsonl")
            if not conv_ok:
                # 回滚：把 events 移回 current/，并恢复 EventWriter 状态
                archived_events = ep_dir / "events.jsonl"
                if archived_events.exists():
                    current_events = self._current_dir / "events.jsonl"
                    shutil.move(str(archived_events), str(current_events))
                    # 恢复 _count（freeze_and_move 已重置为 0）
                    try:
                        with open(current_events, "r", encoding="utf-8") as f:
                            self._event_writer._count = sum(1 for line in f if line.strip())
                    except OSError:
                        self._event_writer._count = 0
                log.error("MemorySystem: 对话归档失败，已回滚事件，中止 Episode %s", episode_id)
                return ""

            # 写入元数据（不含 LLM 摘要，M2 会补充 summary.md）
            self._write_episode_meta(episode_id, trigger, game_context)

            # 将此 episode 加入 pending_archive_queue（M2 异步摘要用）
            self._enqueue_pending_archive(episode_id, trigger, game_context)

            log.info("MemorySystem: 阶段 1 归档完成，Episode %s 已落盘", episode_id)
            return episode_id

    def _next_episode_id(self, season: str = "", day: int = 0) -> str:
        """生成下一个 episode_id，用 mkdir 原子性保证唯一。"""
        suffix = f"_{season}_d{day:02d}" if season else ""
        for _attempt in range(100):
            existing = [d.name for d in self._episodes_dir.iterdir() if d.is_dir()]
            max_num = 0
            for name in existing:
                m = re.match(r"ep_(\d+)", name)
                if m:
                    max_num = max(max_num, int(m.group(1)))

            candidate = f"ep_{max_num + 1:03d}{suffix}"
            try:
                (self._episodes_dir / candidate).mkdir(parents=False, exist_ok=False)
                return candidate
            except FileExistsError:
                continue
        raise RuntimeError("无法生成唯一 episode_id（重试 100 次仍冲突）")

    def _write_episode_meta(
        self, episode_id: str, trigger: str, game_context: str
    ) -> None:
        """写入 Episode 元数据文件（用于 M2 异步处理时识别上下文）。"""
        ep_dir = self._episodes_dir / episode_id
        meta = {
            "episode_id": episode_id,
            "trigger": trigger,
            "game_context": game_context,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "summary_status": "pending",  # M2 完成后更新为 "done"
        }
        try:
            with open(ep_dir / "meta.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.warning("MemorySystem: 写入元数据失败: %s", e)

    def _enqueue_pending_archive(
        self, episode_id: str, trigger: str, game_context: str
    ) -> None:
        """将 episode 加入 pending_archive_queue.json（M2 异步摘要用）。"""
        with self._queue_lock:
            queue = []
            if self._pending_queue_path.exists():
                try:
                    with open(self._pending_queue_path, "r", encoding="utf-8") as f:
                        queue = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    log.error("MemorySystem: pending queue 损坏，重置为空列表: %s", e)
                    queue = []

            queue.append(
                {
                    "episode_id": episode_id,
                    "trigger": trigger,
                    "game_context": game_context,
                    "enqueued_at": datetime.now(timezone.utc).isoformat(),
                    "retry_count": 0,
                }
            )

            try:
                tmp_path = self._pending_queue_path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(queue, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._pending_queue_path)
            except OSError as e:
                log.warning("MemorySystem: 写入 pending_archive_queue 失败: %s", e)

    def _generate_summary_sync(
        self,
        episode_id: str,
        events: list[dict],
        conversation: list[dict],
        trigger: str,
        game_context: str,
    ) -> str:
        """同步生成 Episode 摘要（Markdown 格式）。

        调用 LLM 生成第一人称摘要。
        如果 LLM 不可用或调用失败，生成降级版摘要（纯事件列表）。

        Args:
            episode_id: Episode ID（日志用）。
            events: 原始事件列表（来自归档的 events.jsonl）。
            conversation: 对话记录列表（来自归档的 conversation.jsonl）。
            trigger: 触发原因。
            game_context: 游戏上下文字符串。

        Returns:
            Markdown 格式的摘要文本。
        """
        log.info("MemorySystem: 开始为 %s 生成摘要（LLM）", episode_id)
        current_facts = self.get_facts()
        summary = self._summary_generator.generate(
            events=events,
            conversation=conversation,
            game_context=game_context,
            current_facts=current_facts,
        )
        log.info("MemorySystem: %s 摘要生成完成，%d 字符", episode_id, len(summary))
        return summary

    def process_pending_archives(self) -> int:
        """处理所有 pending 的归档任务。返回成功处理数量。

        读取 pending_archive_queue.json，对每个 pending 任务：
        1. 读取归档目录中的原始事件和对话
        2. 调用 _generate_summary_sync 生成摘要
        3. 写入 episodes/ep_XXX/summary.md
        4. 更新 meta.json 的 summary_status 为 "done"
        5. 从队列中移除已完成任务

        失败时 retry_count +1，超过 MAX_RETRY_COUNT 则标记为 "failed"。

        Returns:
            本次成功处理的任务数量。
        """
        with self._queue_lock:
            if not self._pending_queue_path.exists():
                return 0

            try:
                with open(self._pending_queue_path, "r", encoding="utf-8") as f:
                    queue: list[dict] = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                log.warning("MemorySystem: 读取 pending_archive_queue 失败: %s", e)
                return 0

            if not queue:
                return 0

        processed_count = 0
        updated_queue: list[dict] = []

        for task in queue:
            episode_id = task.get("episode_id", "")
            retry_count = task.get("retry_count", 0)
            trigger = task.get("trigger", "unknown")
            game_context = task.get("game_context", "")

            # 已标记 failed 的任务跳过
            if task.get("status") == "failed":
                updated_queue.append(task)
                continue

            # 超过最大重试次数，标记 failed
            if retry_count >= self.MAX_RETRY_COUNT:
                log.warning(
                    "MemorySystem: %s 已重试 %d 次，标记为 failed",
                    episode_id,
                    retry_count,
                )
                task = {**task, "status": "failed"}
                updated_queue.append(task)
                continue

            ep_dir = self._episodes_dir / episode_id
            if not ep_dir.is_dir():
                log.warning("MemorySystem: episode 目录不存在: %s", ep_dir)
                task = {**task, "retry_count": retry_count + 1}
                updated_queue.append(task)
                continue

            # 读取原始事件和对话
            events = self._read_jsonl(ep_dir / "events.jsonl")
            conversation = self._read_jsonl(ep_dir / "conversation.jsonl")

            try:
                summary = self._generate_summary_sync(
                    episode_id=episode_id,
                    events=events,
                    conversation=conversation,
                    trigger=trigger,
                    game_context=game_context,
                )

                # 写入 summary.md
                summary_path = ep_dir / "summary.md"
                tmp_path = summary_path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(summary)
                os.replace(tmp_path, summary_path)

                # 更新 meta.json summary_status
                self._update_meta_status(ep_dir, "done")

                # M3：摘要生成成功后，更新 Persistent Facts
                # 主观性双轨：接收原始事件（客观），不基于摘要
                facts_updated = self._facts_manager.update_facts(
                    events=events,
                    conversation=conversation,
                    game_context=game_context,
                )
                if facts_updated:
                    log.info("MemorySystem: %s Facts 更新成功", episode_id)
                else:
                    log.warning("MemorySystem: %s Facts 更新失败，标记 facts_pending", episode_id)
                    self._update_meta_status(ep_dir, "done_facts_pending")

                processed_count += 1
                log.info("MemorySystem: %s 摘要处理完成", episode_id)
                # 成功的任务不再加入队列（等于从队列移除）

            except Exception as e:
                log.warning(
                    "MemorySystem: %s 摘要生成失败（retry %d）: %s",
                    episode_id,
                    retry_count + 1,
                    e,
                )
                task = {**task, "retry_count": retry_count + 1}
                updated_queue.append(task)

        # 清理过多的 failed 任务（保留最近 10 个）
        MAX_FAILED_RETAIN = 10
        failed = [t for t in updated_queue if t.get("status") == "failed"]
        active = [t for t in updated_queue if t.get("status") != "failed"]
        if len(failed) > MAX_FAILED_RETAIN:
            updated_queue = active + failed[-MAX_FAILED_RETAIN:]

        with self._queue_lock:
            # 合并并发新增的条目（处理期间 _enqueue 可能追加了新任务）
            processed_ids = {t.get("episode_id") for t in queue}
            try:
                if self._pending_queue_path.exists():
                    with open(self._pending_queue_path, "r", encoding="utf-8") as f:
                        current_queue = json.load(f)
                    # 找出处理期间新增的条目（不在原始 queue 中）
                    new_entries = [
                        t for t in current_queue
                        if t.get("episode_id") not in processed_ids
                    ]
                    if new_entries:
                        updated_queue.extend(new_entries)
                        log.info("MemorySystem: 合并 %d 条并发新增的 pending 任务", len(new_entries))
            except (OSError, json.JSONDecodeError):
                pass  # 合并失败不影响已处理结果的写回

            # 持久化更新后的队列
            try:
                tmp_path = self._pending_queue_path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(updated_queue, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._pending_queue_path)
            except OSError as e:
                log.warning("MemorySystem: 写回 pending_archive_queue 失败: %s", e)

        return processed_count

    def retry_pending_facts(self) -> int:
        """重试所有 facts_pending 状态的 Episode 的 Facts 更新。"""
        if not self._episodes_dir.exists():
            return 0
        retried = 0
        for ep_dir in sorted(self._episodes_dir.iterdir()):
            if not ep_dir.is_dir():
                continue
            meta_path = ep_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("summary_status") != "done_facts_pending":
                    continue
                events = self._read_jsonl(ep_dir / "events.jsonl")
                conversation = self._read_jsonl(ep_dir / "conversation.jsonl")
                game_context = meta.get("game_context", "")
                facts_updated = self._facts_manager.update_facts(
                    events=events,
                    conversation=conversation,
                    game_context=game_context,
                )
                if facts_updated:
                    self._update_meta_status(ep_dir, "done")
                    retried += 1
                    log.info("retry_pending_facts: %s Facts 更新成功", ep_dir.name)
                else:
                    log.debug("retry_pending_facts: %s Facts 更新仍失败", ep_dir.name)
            except Exception:
                log.debug("retry_pending_facts: %s 异常", ep_dir.name, exc_info=True)
        return retried

    def _read_jsonl(self, path: Path) -> list[dict]:
        """读取 JSONL 文件，返回有效 JSON 行的列表。"""
        if not path.exists():
            return []
        records: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        log.warning("MemorySystem: 跳过损坏的 JSONL 行: %s (文件: %s)", line[:80], path)
        except OSError as e:
            log.warning("MemorySystem: 读取 %s 失败: %s", path, e)
        return records

    def _update_meta_status(self, ep_dir: Path, status: str) -> None:
        """更新 ep_dir/meta.json 的 summary_status 字段。"""
        meta_path = ep_dir / "meta.json"
        if not meta_path.exists():
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["summary_status"] = status
            tmp_path = meta_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, meta_path)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("MemorySystem: 更新 meta.json 失败: %s", e)

    # ── 读取 ───────────────────────────────────────────────────────────────

    def get_recent_events(self, n: int = 20) -> list[dict]:
        """从 current/events.jsonl 读取最近 N 条事件。

        Args:
            n: 返回的最大事件数量（默认 20）。

        Returns:
            最近 n 条事件列表（最旧的在最前）。
        """
        return self._event_writer.read_recent(n)

    def get_conversation(self, n: int = 10) -> list[dict]:
        """从 current/conversation.jsonl 读取最近 N 轮对话。

        Args:
            n: 返回的轮数上限（默认 10 轮）。

        Returns:
            最近 n 轮对话消息列表（每条含 ts/role/content）。
        """
        return self._conversation_writer.read_recent(n)

    def get_recent_summaries(self, n: int = 5) -> list[str]:
        """读取最近 N 个 Episode 的 summary.md 内容。

        扫描 episodes/ 目录下所有有 summary.md 的 Episode，
        按 ID 倒序（最新优先）取前 n 个，返回各自 summary.md 全文。
        没有 summary.md 的 Episode 跳过。

        Args:
            n: 返回的最大 Episode 摘要数量（默认 5）。

        Returns:
            每个元素为一个 Episode 的 summary.md 全文（按时间从旧到新排列）。
            如果所有 Episode 都没有 summary.md，返回空列表。
        """
        # M2 实现：扫描 episodes/ 目录，读取最近 n 个 summary.md
        summaries = []
        def _ep_num(d: Path) -> int:
            m = re.search(r"\d+", d.name)
            return int(m.group()) if m else 0

        ep_dirs = sorted(
            [d for d in self._episodes_dir.iterdir() if d.is_dir()],
            key=_ep_num,
            reverse=True,
        )

        for ep_dir in ep_dirs[:n]:
            summary_path = ep_dir / "summary.md"
            if summary_path.exists():
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        summaries.append(f.read())
                except OSError as e:
                    log.warning(
                        "MemorySystem: 读取 summary.md 失败 (%s): %s", ep_dir.name, e
                    )

        # 返回按时间顺序排列（最旧的在最前）
        return list(reversed(summaries))

    def get_facts(self) -> str:
        """读取 facts/current.md 全文。

        M3：委托给 FactsManager，不存在时返回冷启动模板。

        Returns:
            Facts Markdown 全文（如不存在则返回冷启动模板）。
        """
        return self._facts_manager.get_facts()

    def get_facts_summary(self) -> str:
        """仅返回 Facts 标题行（降级用，token 紧张时使用）。

        M3：委托给 FactsManager，返回 ## 标题行 + 紧跟的第一个 bullet。

        Returns:
            Facts 标题行摘要，用换行分隔。
        """
        return self._facts_manager.get_facts_summary()

    def _load_cold_start_template(self) -> str:
        """加载冷启动 Facts 模板（兼容旧调用路径）。"""
        return self._facts_manager.get_facts()

    def get_context_for_prompt(
        self,
        max_tokens: int = 3500,
        max_events: int = 20,
        max_conversation_turns: int = 10,
        max_summaries: int = 5,
    ) -> str:
        """一站式拼装完整记忆上下文，保留给旧调用方和测试使用。

        Agent Loop 新路径由 SystemPromptBuilder 组装记忆上下文，但历史调用方仍会
        直接调用此方法。这里复用现有 `_build_context`，保持旧渐进裁剪策略。
        """
        result = self._build_context(
            events_limit=max_events,
            summaries_limit=max_summaries,
            conv_limit=max_conversation_turns,
            facts_mode="full",
        )
        if _estimate_tokens(result) <= max_tokens:
            return result

        result = self._build_context(
            events_limit=10,
            summaries_limit=max_summaries,
            conv_limit=max_conversation_turns,
            facts_mode="full",
        )
        if _estimate_tokens(result) <= max_tokens:
            log.debug("MemorySystem: P3 裁剪后 token=%d（工作记忆→10）", _estimate_tokens(result))
            return result

        result = self._build_context(
            events_limit=5,
            summaries_limit=max_summaries,
            conv_limit=max_conversation_turns,
            facts_mode="full",
        )
        if _estimate_tokens(result) <= max_tokens:
            log.debug("MemorySystem: P3 裁剪后 token=%d（工作记忆→5）", _estimate_tokens(result))
            return result

        result = self._build_context(
            events_limit=5,
            summaries_limit=3,
            conv_limit=max_conversation_turns,
            facts_mode="summary",
        )
        if _estimate_tokens(result) <= max_tokens:
            log.debug("MemorySystem: P2 裁剪后 token=%d（摘要→3，Facts 标题）", _estimate_tokens(result))
            return result

        result = self._build_context(
            events_limit=5,
            summaries_limit=1,
            conv_limit=max_conversation_turns,
            facts_mode="summary",
        )
        if _estimate_tokens(result) <= max_tokens:
            log.debug("MemorySystem: P2 裁剪后 token=%d（摘要→1）", _estimate_tokens(result))
            return result

        for conv_limit in (max_conversation_turns, 5, 3):
            result = self._build_context(
                events_limit=5,
                summaries_limit=1,
                conv_limit=conv_limit,
                facts_mode="minimal",
            )
            if _estimate_tokens(result) <= max_tokens:
                log.debug(
                    "MemorySystem: P1 裁剪后 token=%d（Facts 最小化，对话→%d）",
                    _estimate_tokens(result),
                    conv_limit,
                )
                return result

        result = self._build_context(
            events_limit=3,
            summaries_limit=0,
            conv_limit=2,
            facts_mode="minimal",
        )
        estimated = _estimate_tokens(result)
        if estimated <= max_tokens:
            log.debug("MemorySystem: P0 极限裁剪后 token=%d", estimated)
            return result

        if max_tokens > 0:
            ratio = max_tokens / max(estimated, 1)
            max_chars = int(len(result) * ratio * 0.95)
            result = result[:max_chars] + "\n\n（...记忆已截断）"
            log.warning("MemorySystem: P0 后仍超限，硬截断至 %d 字符", max_chars)

        return result

    def _build_context(
        self,
        events_limit: int,
        summaries_limit: int,
        conv_limit: int,
        facts_mode: str,
    ) -> str:
        """构建记忆上下文，按指定限制。

        Args:
            events_limit: 工作记忆事件最大数量。
            summaries_limit: Episode 摘要最大数量。
            conv_limit: 对话最大轮数。
            facts_mode: "full"（全量）/ "summary"（仅标题行）/ "minimal"（仅关系行）。

        Returns:
            拼装好的 Markdown 格式记忆上下文字符串。
        """
        sections: list[str] = []

        # Layer 3 — 持久事实
        if facts_mode == "full":
            facts = self.get_facts()
        elif facts_mode == "summary":
            facts = self.get_facts_summary()
        else:  # minimal
            facts = self._get_facts_minimal()

        if facts.strip():
            sections.append("### 关于搭子\n\n" + facts.strip())

        # Layer 2 — Episode 摘要
        summaries = self.get_recent_summaries(summaries_limit)
        if summaries:
            summaries_text = "\n\n---\n\n".join(summaries)
            sections.append("### 最近的经历\n\n" + summaries_text)

        # Layer 1 — 工作记忆事件
        events = self.get_recent_events(events_limit)
        if events:
            event_lines = []
            for evt in events:
                ts_game = evt.get("ts_game", "")
                etype = evt.get("type", "event")
                content = evt.get("content", "")
                pinned_mark = " [重要]" if evt.get("pinned") else ""
                if ts_game:
                    event_lines.append(f"- [{ts_game}] {etype}: {content}{pinned_mark}")
                else:
                    event_lines.append(f"- {etype}: {content}{pinned_mark}")
            sections.append("### 今天发生的事\n\n" + "\n".join(event_lines))

        # Layer 1 — 对话历史
        conversation = self.get_conversation(conv_limit)
        if conversation:
            conv_lines = []
            for msg in conversation:
                role_label = "玩家" if msg.get("role") == "player" else "我"
                content = msg.get("content", "")
                conv_lines.append(f"**{role_label}**：{content}")
            sections.append("### 对话历史\n\n" + "\n\n".join(conv_lines))

        if not sections:
            return "（暂无记忆上下文）"

        return "\n\n".join(sections)

    def _get_facts_minimal(self) -> str:
        """获取 Facts 最小化版本（仅关系阶段 + 名字）。"""
        facts_content = self.get_facts()
        lines = facts_content.splitlines()
        minimal_lines: list[str] = []
        in_relationship = False
        for line in lines:
            if line.startswith("## 我们的关系"):
                in_relationship = True
                minimal_lines.append(line)
            elif line.startswith("## "):
                in_relationship = False
                # 提取关于搭子的名字行
                if "关于搭子" in line:
                    minimal_lines.append(line)
            elif in_relationship and (line.startswith("- 阶段") or line.startswith("- 名字")):
                minimal_lines.append(line)
        return "\n".join(minimal_lines) if minimal_lines else self.get_facts_summary()

    # ── 维护 ────────────────────────────────────────────────────────────────

    def rebuild_indexes(self) -> None:
        """重建所有索引（模型升级后可调用以重新生成所有摘要和 Facts）。

        实现：将所有 episodes/ 下 summary.md 缺失或 summary_status 非 done 的 Episode
        重新入 pending 队列，然后调用 process_pending_archives()。
        """
        log.info("MemorySystem: 开始重建索引...")
        rebuilt = 0
        if not self._episodes_dir.exists():
            return

        for ep_dir in sorted(self._episodes_dir.iterdir()):
            if not ep_dir.is_dir():
                continue
            episode_id = ep_dir.name
            summary_path = ep_dir / "summary.md"
            if summary_path.exists():
                continue  # 已有摘要，跳过

            # 读取元数据
            meta_path = ep_dir / "meta.json"
            trigger = "unknown"
            game_context = ""
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    trigger = meta.get("trigger", "unknown")
                    game_context = meta.get("game_context", "")
                except (OSError, json.JSONDecodeError):
                    pass

            self._enqueue_pending_archive(episode_id, trigger, game_context)
            rebuilt += 1

        if rebuilt > 0:
            log.info("MemorySystem: %d 个 Episode 重新入 pending 队列", rebuilt)
            self.process_pending_archives()
        else:
            log.info("MemorySystem: 所有 Episode 已有摘要，无需重建")

    # ── 未来预留（MVP 空实现）────────────────────────────────────────────

    def search(self, query: str) -> list:
        """语义检索（V3 实现，当前返回空列表）。

        Args:
            query: 检索查询字符串。

        Returns:
            空列表（V3 实现语义检索）。
        """
        return []

    def forget(self, criteria: dict) -> None:
        """按条件遗忘（V2.1+ 实现）。

        Args:
            criteria: 遗忘条件（如按时间、类型等）。
        """
        log.info("MemorySystem: forget 暂未实现（V2.1+）")

    def self_edit(self) -> None:
        """自编辑（V3 实现：LLM 定期审视 Facts，合并冗余、删除过时）。"""
        log.info("MemorySystem: self_edit 暂未实现（V3）")

    # ── 属性 ───────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        """记忆数据根目录。"""
        return self._root

    @property
    def event_writer(self) -> EventWriter:
        """EventWriter 实例（供测试访问）。"""
        return self._event_writer

    @property
    def conversation_writer(self) -> ConversationWriter:
        """ConversationWriter 实例（供测试访问）。"""
        return self._conversation_writer


def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数。

    设计决策：Agent 内部思考使用英文以优化 token 效率，
    仅在玩家交互时使用玩家语言。因此存储内容以英文为主。

    英文文本：~0.25 tokens/char（4 chars per token）。
    中文文本：~2.5 tokens/char。
    混合内容按字符类型分别估算。
    """
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    ascii_chars = len(text) - cjk
    return max(1, int(cjk * 2.5 + ascii_chars * 0.25))
