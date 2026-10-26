"""
wiki_page — Wiki 页面的内存表示与 Markdown 文件解析。

每个 .md 文件包含 YAML frontmatter（title/category/summary/keywords/sections）
和按 ## / ### 切分的 section 内容。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WikiPage:
    """一个 wiki 页面的内存表示。"""

    name: str  # 页面标题（如 "Pufferfish"）
    category: str  # Overview / NPC / Location / System / Detail
    summary: str  # 一句话摘要
    keywords: list[str] = field(default_factory=list)  # 搜索关键词
    sections: dict[str, str] = field(default_factory=dict)  # section_name → content
    section_order: list[str] = field(default_factory=list)  # section 顺序
    intro: str = ""  # h2 之前的引言内容
    full_text: str = ""  # 完整原始 markdown（用于全文搜索兜底）
    full_text_lower: str = ""  # 预计算的小写全文（搜索优化）

    @property
    def is_large(self) -> bool:
        """页面是否超大（>8000 字符），需要强制 section 级读取。"""
        return len(self.full_text) > 8000

    def get_section(self, section_name: str) -> str | None:
        """获取指定 section 内容（模糊匹配）。"""
        # 精确匹配
        if section_name in self.sections:
            return self.sections[section_name]

        # 大小写不敏感匹配
        lower_name = section_name.lower()
        for key, content in self.sections.items():
            if key.lower() == lower_name:
                return content

        # 部分匹配（section_name 是 key 的子串或反之）
        for key, content in self.sections.items():
            if lower_name in key.lower() or key.lower() in lower_name:
                return content

        return None

    def get_section_list(self) -> str:
        """返回 section 名称列表（带字符数提示）。"""
        lines = []
        for name in self.section_order:
            content = self.sections.get(name, "")
            lines.append(f"- {name} ({len(content)} chars)")
        return "\n".join(lines)


def parse_wiki_file(file_path: Path) -> WikiPage | None:
    """从 markdown 文件解析 WikiPage。

    文件格式：
        ---
        title: Pufferfish
        category: Detail
        summary: ...
        keywords: ["pufferfish", ...]
        sections: ["Map", "Gifting", ...]
        ---

        # Pufferfish
        ...

    Returns:
        WikiPage 或 None（解析失败时）。
    """
    try:
        text = file_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except Exception:
        return None

    # 解析 YAML frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not fm_match:
        return None

    fm_text = fm_match.group(1)
    body = text[fm_match.end():]

    # 简单解析 frontmatter（避免引入 PyYAML 依赖）
    title = ""
    category = ""
    summary = ""
    keywords: list[str] = []

    for line in fm_text.split("\n"):
        if line.startswith("title: "):
            title = line[7:].strip()
        elif line.startswith("category: "):
            category = line[10:].strip()
        elif line.startswith("summary: "):
            summary = line[9:].strip()
        elif line.startswith("keywords: "):
            try:
                keywords = json.loads(line[10:].strip())
            except (json.JSONDecodeError, TypeError):
                keywords = []

    if not title:
        return None

    # 按 ## / ### 切分 body 为 sections
    sections: dict[str, str] = {}
    section_order: list[str] = []
    intro_lines: list[str] = []
    current_heading = ""
    current_lines: list[str] = []
    in_intro = True

    for line in body.split("\n"):
        heading_match = re.match(r"^(#{2,3})\s+(.+)$", line)
        if heading_match:
            if in_intro:
                intro_lines = current_lines
                in_intro = False
            elif current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
                section_order.append(current_heading)

            current_heading = heading_match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 最后一个 section
    if in_intro:
        intro_lines = current_lines
    elif current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()
        section_order.append(current_heading)

    intro = "\n".join(intro_lines).strip()

    return WikiPage(
        name=title,
        category=category,
        summary=summary,
        keywords=keywords,
        sections=sections,
        section_order=section_order,
        intro=intro,
        full_text=body,
        full_text_lower=body.lower(),
    )
