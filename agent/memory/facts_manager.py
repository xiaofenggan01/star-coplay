"""
memory.facts_manager — Persistent Facts 管理器。

负责 facts/current.md 的读取、更新、版本历史管理。
调用 LLM 基于原始事件（非摘要）更新 Facts（主观性双轨设计）。

设计原则：
    - Markdown 格式存储（渐进式披露）
    - 原子写入（tmp→rename）
    - 版本历史保留（history/vNNN.md）
    - 基于客观事件更新，不基于摘要，防止主观偏差累积
    - LLM 失败时原 Facts 保持不变（安全降级）
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

from memory._formatters import format_events, format_conversation
from report import events as report_events
import llm_client as _llm_client

log = logging.getLogger(__name__)

# 冷启动 Facts 模板文件路径（只读数据，从 bundle 加载）
import sys as _sys
if getattr(_sys, 'frozen', False):
    _COLD_START_FACTS_PATH = (Path(_sys._MEIPASS) / "agent" / "memory" / "cold_start_facts.md"
                               if hasattr(_sys, '_MEIPASS')
                               else Path(_sys.executable).parent / "memory" / "cold_start_facts.md")
else:
    _COLD_START_FACTS_PATH = Path(__file__).parent / "cold_start_facts.md"

# Facts 更新用的系统 Prompt
_FACTS_UPDATE_SYSTEM_PROMPT = """你是一个游戏中的 AI 搭子。根据最新发生的事件，更新你对搭子和世界的认知。"""

# Facts 更新用的用户 Prompt 模板
_FACTS_UPDATE_USER_PROMPT = """当前认知：
{current_facts}

最新事件（客观记录）：
{events_text}

最新对话：
{conversation_text}

游戏上下文：{game_context}

请输出更新后的完整认知（保持 Markdown 格式），包含三个部分：
## 关于搭子
- 名字、游戏风格、喜好、习惯等

## 我们的关系
- 阶段、信任度、共同高光、印象等

## 世界状态
- 农场、季节、动物、已解锁区域等

规则：
- 只更新有变化的条目，其他保持不变
- 新发现的信息追加到对应类别
- 过时的信息直接覆盖（如季节变化）
- 保持简洁，每个类别 5-10 条"""


class FactsManager:
    """Persistent Facts 管理 — Markdown 格式的跨 Episode 持久事实。

    职责：
    - 读取/写入 facts/current.md（Markdown 格式）
    - 原子写入（tmp→rename）
    - 版本历史（每次更新保留旧版本到 facts/history/vNNN.md）
    - 调用 LLM 基于原始事件更新 Facts（主观性双轨：基于客观事件，不基于摘要）
    - 渐进式裁剪（get_facts_summary 仅返回标题行）

    Args:
        facts_dir: facts/ 目录路径。
        model: LLM 模型名（用于 Facts 更新）。
        max_tokens: LLM 生成最大 token 数。
        timeout_s: LLM 调用超时时间（秒）。
    """

    def __init__(
        self,
        facts_dir: Path,
        model: str = "",
        max_tokens: int = 1000,
        timeout_s: float = 30.0,
    ) -> None:
        self._facts_dir = Path(facts_dir)
        self._current_path = self._facts_dir / "current.md"
        self._history_dir = self._facts_dir / "history"
        self._model = model or _llm_client.get_default_model()
        if not self._model:
            log.error(
                "FactsManager: model 未配置。请通过平台注入 LLM_MODEL，"
                " 否则 Facts 更新功能将不可用。"
            )
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._history_dir.mkdir(parents=True, exist_ok=True)

    # ── 读取 ─────────────────────────────────────────────────────────────────

    def get_facts(self) -> str:
        """读取当前 Facts 全文（Markdown）。

        不存在时返回冷启动模板。

        Returns:
            Facts Markdown 全文。
        """
        if self._current_path.exists():
            try:
                with open(self._current_path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                log.warning("FactsManager: 读取 facts/current.md 失败: %s", e)

        return self._load_cold_start_template()

    def get_facts_summary(self) -> str:
        """仅返回 Facts 的标题行（渐进式裁剪降级用）。

        提取所有以 "## " 开头的行，以及紧跟的第一个 "- " 行。

        Returns:
            Facts 标题行（每个 ## 标题 + 紧跟的第一个 bullet），换行分隔。
        """
        facts_content = self.get_facts()
        lines = facts_content.splitlines()
        result_lines: list[str] = []

        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("## "):
                result_lines.append(line)
                # 找下一个 "- " 行（跳过空行）
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and lines[j].startswith("- "):
                    result_lines.append(lines[j])
            i += 1

        return "\n".join(result_lines) if result_lines else "（暂无事实记录）"

    # ── 更新 ─────────────────────────────────────────────────────────────────

    def update_facts(
        self,
        events: list[dict],
        conversation: list[dict],
        game_context: str,
    ) -> bool:
        """基于原始事件（非摘要）更新 Facts。

        主观性双轨设计：Facts 更新 Prompt 接收的是客观事件，不是主观摘要。
        LLM 调用失败时原 Facts 保持不变（安全降级）。

        Args:
            events: 原始事件列表（来自 events.jsonl，客观记录）。
            conversation: 对话记录列表。
            game_context: 当前游戏上下文字符串。

        Returns:
            True 更新成功，False 更新失败（原 Facts 不变）。
        """
        with self._lock:
            if not self._model:
                log.error("FactsManager: model 未配置，跳过 Facts 更新")
                return False
            try:
                import llm_client
            except ImportError:
                log.warning("FactsManager: llm_client 未安装，跳过 Facts 更新")
                return False

            current_facts = self.get_facts()
            events_text = format_events(events)
            conversation_text = format_conversation(conversation)

            user_content = _FACTS_UPDATE_USER_PROMPT.format(
                current_facts=current_facts.strip(),
                events_text=events_text,
                conversation_text=conversation_text,
                game_context=game_context or "（未知）",
            )

            messages = [
                {"role": "system", "content": _FACTS_UPDATE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]

            try:
                t0 = time.monotonic()
                response = llm_client.completion(
                    model=self._model,
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=0.3,
                    timeout=self._timeout_s,
                )
                latency_ms = (time.monotonic() - t0) * 1000.0
                new_facts = response.choices[0].message.content
                if not new_facts or not new_facts.strip():
                    log.warning("FactsManager: LLM 返回空内容，Facts 不变")
                    return False

                # P1-10: 最小结构校验
                required_headers = ["## 关于搭子", "## 我们的关系", "## 世界状态"]
                if not all(h in new_facts for h in required_headers):
                    log.warning("FactsManager: LLM 返回缺少必要结构标题，Facts 不变")
                    return False

                self._atomic_write(new_facts.strip())
                report_events.llm_call(
                    model=self._model,
                    response=response,
                    latency_ms=latency_ms,
                    trigger="memory.facts",
                )
                log.info("FactsManager: Facts 更新成功，版本 v%03d", self.version)
                return True

            except Exception as e:
                log.warning("FactsManager: LLM 调用失败，Facts 不变: %s", e)
                return False

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    def _atomic_write(self, content: str) -> None:
        """原子写入 facts/current.md（tmp→rename）。

        步骤：
        1. 备份当前版本到 history/vNNN.md（如当前存在）
        2. 写入 tmp 文件
        3. os.replace(tmp, current.md)
        """
        # 备份当前版本
        if self._current_path.exists():
            next_version = self._next_version_number()
            history_path = self._history_dir / f"v{next_version:03d}.md"
            try:
                with open(self._current_path, "r", encoding="utf-8") as f:
                    old_content = f.read()
                with open(history_path, "w", encoding="utf-8") as f:
                    f.write(old_content)
                log.debug("FactsManager: 旧版本备份到 %s", history_path.name)
            except OSError as e:
                log.warning("FactsManager: 备份旧版本失败: %s", e)
                # 备份失败不阻止更新

            # 清理过旧的版本历史（保留最近 50 个）
            self._cleanup_old_history()

        # 原子写入
        tmp_path = self._current_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self._current_path)
        except OSError as e:
            log.error("FactsManager: 原子写入失败: %s", e)
            # 清理可能残留的 tmp 文件
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    def _next_version_number(self) -> int:
        """推断下一个版本号（基于 history/ 目录中已有的文件）。"""
        if not self._history_dir.exists():
            return 1
        max_ver = 0
        for path in self._history_dir.iterdir():
            m = re.match(r"v(\d+)\.md$", path.name)
            if m:
                max_ver = max(max_ver, int(m.group(1)))
        return max_ver + 1

    def _load_cold_start_template(self) -> str:
        """加载冷启动 Facts 模板。"""
        if _COLD_START_FACTS_PATH.exists():
            try:
                with open(_COLD_START_FACTS_PATH, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                log.warning("FactsManager: 读取冷启动模板失败: %s", e)

        # 内嵌兜底模板
        return (
            "## 关于搭子\n"
            "- 名字：（还不知道）\n"
            "- 印象：刚认识，还在了解中\n"
            "\n"
            "## 我们的关系\n"
            "- 阶段：初识\n"
            "- 一起玩了：0 天\n"
            "\n"
            "## 世界状态\n"
            "- 当前季节：（从游戏状态读取）\n"
        )

    def _cleanup_old_history(self, max_versions: int = 50) -> None:
        """清理过旧的版本历史文件。"""
        if not self._history_dir.exists():
            return
        history_files = sorted(
            self._history_dir.glob("v*.md"),
            key=lambda p: int(m.group(1)) if (m := re.match(r"v(\d+)", p.name)) else 0,
        )
        if len(history_files) > max_versions:
            for old in history_files[: len(history_files) - max_versions]:
                try:
                    old.unlink()
                    log.debug("FactsManager: 清理旧版本 %s", old.name)
                except OSError:
                    pass

    # ── 属性 ─────────────────────────────────────────────────────────────────

    @property
    def version(self) -> int:
        """当前版本号（基于 history/ 中的文件数量推断）。"""
        return self._next_version_number() - 1
