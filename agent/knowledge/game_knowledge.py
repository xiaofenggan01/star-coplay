"""
game_knowledge — 游戏事实性知识查询层。

启动时加载 wiki markdown 文件，构建搜索索引。
提供 search_wiki / read_wiki 两个 LLM Tool 接口。

三层渐进式知识库：
    Layer 0: _index.md — 分类索引（LLM 读索引即知"wiki 里有什么"）
    Layer 1: 总览索引页 — 单页即可回答该类目大部分查询
    Layer 2: 详情页 — section 级寻址，按需加载
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .wiki_page import WikiPage, parse_wiki_file

log = logging.getLogger("knowledge.game_knowledge")

# ── Tool 定义（OpenAI function calling 格式）────────────────────────────

KNOWLEDGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": (
                "Search the Stardew Valley wiki knowledge base. "
                "Returns matching page names with summary snippets. "
                "Use when you need to find which page contains certain information. "
                "Always search in English."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search keywords (English). "
                            "e.g. 'Pufferfish', 'Fall crops profit', 'Abigail gifts'"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_wiki",
            "description": (
                "Read a specific wiki page or section. "
                "Use after search_wiki to get detailed content. "
                "Always specify a section for large pages (NPCs, Bundles) "
                "to avoid excessive token usage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "description": "Page name. e.g. 'Pufferfish', 'Abigail', 'Fish'",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "Optional section heading to read only that part. "
                            "e.g. 'Gift Tastes', 'Schedule', 'Fish Tank'. "
                            "Omit to read full page (only for short pages)."
                        ),
                    },
                },
                "required": ["page"],
            },
        },
    },
]

# 大页面截断阈值（超过此长度强制要求指定 section）
_MAX_FULL_PAGE_CHARS = 8000


class GameKnowledge:
    """游戏事实性知识查询层。

    启动时加载 wiki markdown 文件，构建搜索索引。
    提供 search_wiki / read_wiki 两个 tool 接口。
    """

    def __init__(self, wiki_dir: Path) -> None:
        self._wiki_dir = wiki_dir
        self._pages: dict[str, WikiPage] = {}  # normalized_name → WikiPage
        self._name_aliases: dict[str, str] = {}  # alias → normalized_name

    def load(self) -> int:
        """加载所有 wiki markdown 文件，构建索引。

        Returns:
            加载的页面数。
        """
        if not self._wiki_dir.exists():
            log.warning("Wiki 目录不存在: %s", self._wiki_dir)
            return 0

        # 加载所有 .md 文件
        count = 0
        for md_file in sorted(self._wiki_dir.glob("*.md")):
            if md_file.name == "_index.md":
                continue

            page = parse_wiki_file(md_file)
            if page is None:
                log.debug("解析失败，跳过: %s", md_file.name)
                continue

            norm = self._normalize(page.name)
            self._pages[norm] = page

            # 建立别名索引
            self._name_aliases[norm] = norm
            # 文件名（去扩展名）
            file_stem = md_file.stem.lower().replace("_", " ").replace("'", "'")
            self._name_aliases[file_stem] = norm
            # 带下划线的形式
            self._name_aliases[md_file.stem.lower()] = norm
            # keywords
            for kw in page.keywords:
                kw_norm = kw.lower().strip()
                if kw_norm and kw_norm not in self._name_aliases:
                    self._name_aliases[kw_norm] = norm

            count += 1

        log.info("GameKnowledge 加载完成: %d 页面, %d 别名", count, len(self._name_aliases))
        return count

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # ── Tool 接口 ─────────────────────────────────────────────────────────

    def get_tool_definitions(self) -> list[dict]:
        """返回 OpenAI tools 格式的 search_wiki + read_wiki 定义。"""
        return list(KNOWLEDGE_TOOLS)

    def execute_tool(self, tool_name: str, args: dict) -> str:
        """执行 tool 调用，返回 markdown 字符串结果。"""
        if tool_name == "search_wiki":
            return self.search(args.get("query", ""))
        elif tool_name == "read_wiki":
            return self.read_page(args.get("page", ""), args.get("section"))
        else:
            return f"Unknown tool: {tool_name}"

    # ── 搜索 ──────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 8) -> str:
        """关键词搜索，返回匹配页面 + 摘要片段的 markdown 列表。"""
        if not query:
            return "Please provide a search query."

        query_terms = [t.lower() for t in query.split() if t]
        if not query_terms:
            return "Please provide a search query."

        # 评分每个页面
        scored: list[tuple[float, WikiPage]] = []
        for page in self._pages.values():
            score = self._score_page(page, query_terms)
            if score > 0:
                scored.append((score, page))

        # 排序
        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            return f"No results found for '{query}'. Try different keywords."

        # 格式化结果
        results = scored[:limit]
        total = len(scored)
        shown = len(results)
        header = f"### Search results for '{query}' (showing top {shown} of {total} matches)\n" if total > shown else f"### Search results for '{query}' ({total} matches)\n"
        lines = [header]
        for score, page in results:
            lines.append(f"- **{page.name}** [{page.category}]: {page.summary[:150]}")

        return "\n".join(lines)

    def _score_page(self, page: WikiPage, query_terms: list[str]) -> float:
        """计算页面与查询的相关性分数。"""
        score = 0.0
        name_lower = page.name.lower()

        for term in query_terms:
            # 标题精确匹配（最高权重）
            if term == name_lower:
                score += 10.0
            elif term in name_lower:
                score += 5.0

            # keywords 匹配
            for kw in page.keywords:
                if term == kw:
                    score += 3.0
                elif term in kw or kw in term:
                    score += 1.5

            # summary 匹配
            if term in page.summary.lower():
                score += 2.0

            # section 标题匹配
            for sec_name in page.section_order:
                if term in sec_name.lower():
                    score += 1.0

            # 全文匹配（低权重，使用预计算的小写全文）
            if term in page.full_text_lower:
                score += 0.5

        return score

    # ── 读取 ──────────────────────────────────────────────────────────────

    def read_page(self, page_name: str, section: str | None = None) -> str:
        """读取页面或指定 section 的 markdown 内容。"""
        if not page_name:
            return "Please specify a page name."

        page = self._find_page(page_name)
        if page is None:
            # 尝试搜索建议
            suggestion = self.search(page_name, limit=3)
            return f"Page '{page_name}' not found.\n\n{suggestion}"

        # 指定 section
        if section:
            content = page.get_section(section)
            if content is None:
                available = page.get_section_list()
                return (
                    f"Section '{section}' not found in '{page.name}'.\n\n"
                    f"Available sections:\n{available}"
                )
            return f"## {page.name} — {section}\n\n{content}"

        # 全页读取
        if page.is_large:
            # 大页面：返回摘要 + section 列表，提示 LLM 指定 section
            available = page.get_section_list()
            intro_preview = page.intro[:500] if page.intro else page.summary
            return (
                f"## {page.name}\n\n"
                f"{intro_preview}\n\n"
                f"**This is a large page ({len(page.full_text):,} chars). "
                f"Please specify a section to read:**\n{available}"
            )

        # 小页面：返回完整内容（截断保护）
        content = page.full_text[:_MAX_FULL_PAGE_CHARS]
        if len(page.full_text) > _MAX_FULL_PAGE_CHARS:
            available = page.get_section_list()
            content += (
                f"\n\n**[Truncated — full page is {len(page.full_text):,} chars. "
                f"Specify a section for more detail.]**\n{available}"
            )
        return content

    def _find_page(self, page_name: str) -> WikiPage | None:
        """通过页面名查找 WikiPage（支持别名和模糊匹配）。"""
        norm = self._normalize(page_name)

        # 直接匹配
        if norm in self._pages:
            return self._pages[norm]

        # 别名匹配
        if norm in self._name_aliases:
            target = self._name_aliases[norm]
            return self._pages.get(target)

        # 部分匹配（名字包含查询词或反之），优先最短名（避免 "fish" 匹配到 "catfish"）
        candidates = []
        for key, page in self._pages.items():
            if norm in key or key in norm:
                candidates.append((len(key), page))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]

        return None

    @staticmethod
    def _normalize(name: str) -> str:
        """标准化页面名称用于匹配。"""
        return name.lower().replace("_", " ").replace("'", "'").replace("'", "'").strip()
