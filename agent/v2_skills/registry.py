"""
v2_skills.registry — v2.4.x FC 模式下的 Skill 加载与注册（权威）

本模块是 v2.4.x FC（Function Calling）体系下唯一有效的 Skill 注册中心。
启动时自动扫描 builtin/ 目录，无需手动注册。

加载约定：
  - 扫描 v2_skills/builtin/<name>/ 目录
  - 每个 Skill 必须有：skill.md（YAML frontmatter + Markdown 正文）+ script.py
  - skill.md frontmatter description + 正文第一段被提取为 FC schema description（~200 tokens）
  - script.py 中必须有与目录同名的 async 函数，第一个参数固定为 ctx

新增 Skill 步骤：
  1. 创建目录：v2_skills/builtin/<my_skill>/
  2. 写 skill.md（参考 pet_animals/ 的格式）
  3. 写 script.py（实现 async def <my_skill>(ctx, ...) 函数）
  4. 启动后自动发现，无需手动注册

历史：替代了旧的 skills/skill_library.py（仅 BT 体系遗留，已废弃）

── 当前已加载 Skill（v2.4.x） ────────────────────────────────────────────────
  builtin/clear_farm/       — 清理农场
  builtin/go_bed/           — 上床睡觉
  builtin/go_shopping/      — 去商店购物
  builtin/harvest_crops/    — 收割作物
  builtin/pet_animals/      — 抚摸动物
  builtin/ship_items/       — 装箱出售物品
  builtin/water_crops/      — 浇水

── 技能包目录结构 ────────────────────────────────────────────────────────────
  builtin/
      pet_animals/
          skill.md       # YAML frontmatter + Markdown 文档
          script.py      # 包含 async def pet_animals(ctx, ...) 函数
          __init__.py    # 空文件（可选）
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from v2_skills.errors import SkillLoadError

log = logging.getLogger("v2_skills.registry")

# 排除出 FC schema 的 Skill（由 core_tools 直接暴露给 LLM，不通过 SkillRegistry 的 skill: 前缀）
# observe 由 core_tools.py 负责 LLM 可见性，执行委托给 v2_skills/builtin/observe/ 实现
SKILLS_EXCLUDED_FROM_FC: frozenset[str] = frozenset({
    "observe",  # observe 在 core_tools 体系中暴露，路由通过 L3_BACKED_CORE_TOOLS 机制
    "inspect",  # inspect 同样由 core_tools 暴露，内部委托 query_inspect_refs
    "find_interactions",  # L3 查询入口，由 core_tools 暴露
    "search_facts",  # L3 当前视野查询入口，由 core_tools 暴露
})

# YAML frontmatter 正则：匹配 --- 包围的内容
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


@dataclass
class SkillEntry:
    """注册的技能条目。

    Attributes:
        name: 技能名称（与目录名一致）。
        func: 技能的 async 函数引用。
        metadata: skill.md frontmatter 解析后的 dict。
        fc_schema: OpenAI FC tool schema dict。
        timeout: 技能最大执行时间（秒），默认 120。
    """

    name: str
    func: Callable
    metadata: dict
    fc_schema: dict
    timeout: float = 120.0

    @property
    def routing(self) -> dict[str, Any]:
        routing = self.metadata.get("routing")
        return routing if isinstance(routing, dict) else {}


class SkillRegistry:
    """技能注册表。

    启动时扫描 builtin/ 下的技能包，加载并注册。

    Args:
        skills_dir: v2_skills 模块的根目录路径。
    """

    def __init__(self, skills_dir: str | Path):
        self._skills: dict[str, SkillEntry] = {}
        self._load_skills(Path(skills_dir))

    def _load_skills(self, base_dir: Path) -> None:
        """扫描 builtin/ 下每个包含 skill.md + script.py 的子文件夹。"""
        builtin_dir = base_dir / "builtin"
        if not builtin_dir.exists():
            log.info("SkillRegistry: builtin/ 不存在，跳过加载")
            return

        for skill_dir in sorted(builtin_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            # 跳过 __pycache__ 等
            if skill_dir.name.startswith("_"):
                continue

            skill_md = skill_dir / "skill.md"
            script_py = skill_dir / "script.py"
            if not (skill_md.exists() and script_py.exists()):
                log.debug("SkillRegistry: 跳过 %s（缺少 skill.md 或 script.py）", skill_dir.name)
                continue

            # 安全：符号链接防护，确保 script.py 不指向 builtin/ 之外
            try:
                if not script_py.resolve().is_relative_to(base_dir.resolve()):
                    log.warning(
                        "SkillRegistry: 跳过 %s（script.py 指向 builtin/ 之外: %s）",
                        skill_dir.name, script_py.resolve(),
                    )
                    continue
            except (OSError, ValueError):
                log.warning("SkillRegistry: 跳过 %s（无法解析 script.py 路径）", skill_dir.name)
                continue

            try:
                entry = self._load_one_skill(skill_dir, skill_md, script_py)
                self._skills[entry.name] = entry
                log.info(
                    "SkillRegistry: 已加载技能 %s (timeout=%.0fs, params=%s)",
                    entry.name,
                    entry.timeout,
                    list((entry.metadata.get("parameters") or {}).keys()),
                )
            except SkillLoadError as e:
                log.warning("SkillRegistry: 跳过技能 %s: %s", skill_dir.name, e)

    def _load_one_skill(
        self,
        skill_dir: Path,
        skill_md_path: Path,
        script_py_path: Path,
    ) -> SkillEntry:
        """加载单个技能包。

        步骤：
        1. 解析 skill.md frontmatter（YAML）
        2. 动态 import script.py
        3. 找到与目录同名的 async 函数
        4. 校验签名：函数参数 >= metadata.parameters 的 key
        5. 生成 FC schema
        6. 构造 SkillEntry
        """
        # 1. 解析 frontmatter
        content = skill_md_path.read_text(encoding="utf-8")
        metadata = self._parse_frontmatter(content)
        if not metadata:
            raise SkillLoadError(f"skill.md 缺少 YAML frontmatter: {skill_md_path}")

        name = metadata.get("name", skill_dir.name)

        # 2. 动态 import script.py
        module_name = f"v2_skills.builtin.{skill_dir.name}.script"
        spec = importlib.util.spec_from_file_location(module_name, script_py_path)
        if spec is None or spec.loader is None:
            raise SkillLoadError(f"无法加载 {script_py_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise SkillLoadError(f"script.py 导入失败: {e}") from e
        # 注册到 sys.modules，确保模块内的相对引用和 pickle 等场景正常工作
        sys.modules[module_name] = module

        # 3. 找到与目录同名的 async 函数
        func = getattr(module, name, None)
        if func is None:
            raise SkillLoadError(
                f"script.py 中未找到函数 '{name}'，"
                f"可用函数: {[n for n in dir(module) if not n.startswith('_')]}"
            )
        if not asyncio.iscoroutinefunction(func):
            raise SkillLoadError(f"函数 '{name}' 不是 async 函数")

        # 4. 校验签名
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())
        # 第一个参数应该是 ctx
        if not param_names or param_names[0] != "ctx":
            raise SkillLoadError(
                f"函数 '{name}' 的第一个参数应为 'ctx'，实际: {param_names}"
            )

        # metadata.parameters 中定义的参数必须在函数签名中存在
        expected_params = set((metadata.get("parameters") or {}).keys())
        actual_params = set(param_names[1:])  # 排除 ctx
        missing = expected_params - actual_params
        if missing:
            raise SkillLoadError(
                f"函数 '{name}' 缺少参数: {missing}，"
                f"函数签名: {param_names}，"
                f"skill.md 定义: {expected_params}"
            )

        # 反向检查：函数有额外参数（skill.md 未声明）且无默认值时警告
        # FC 调用不会传这些参数，若无默认值会导致运行时 TypeError
        extra_params = actual_params - expected_params
        if extra_params:
            for ep in extra_params:
                p = sig.parameters[ep]
                if p.default is inspect.Parameter.empty:
                    log.warning(
                        "SkillRegistry: 技能 '%s' 函数参数 '%s' 未在 skill.md 中声明且无默认值，"
                        "FC 调用时不会传入此参数，可能导致 TypeError",
                        name, ep,
                    )

        # 5. 生成 FC schema（传入 skill_md_path 以提取正文 pushy description）
        fc_schema = self._generate_fc_schema(name, metadata, skill_md_path)

        # 6. 构造 SkillEntry
        timeout = float(metadata.get("timeout", 120.0))

        return SkillEntry(
            name=name,
            func=func,
            metadata=metadata,
            fc_schema=fc_schema,
            timeout=timeout,
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> dict:
        """解析 YAML frontmatter（--- 包围的部分）。

        使用简易解析器避免强依赖 PyYAML，支持基本的 key: value 和嵌套结构。
        如果安装了 yaml 模块则使用 yaml.safe_load。

        Args:
            content: skill.md 的完整文本内容。

        Returns:
            解析后的 dict，解析失败返回空 dict。
        """
        match = _FRONTMATTER_RE.match(content)
        if not match:
            return {}

        yaml_text = match.group(1)

        # 优先使用 PyYAML
        try:
            import yaml
            try:
                result = yaml.safe_load(yaml_text)
                return result if isinstance(result, dict) else {}
            except yaml.YAMLError as e:
                log.warning("YAML 解析失败，降级到简易解析器: %s", e)
                # 不 return，继续尝试简易解析器
        except ImportError:
            pass

        # Fallback：简易 YAML 解析（仅支持 skill.md 场景的子集）
        return _simple_yaml_parse(yaml_text)

    @staticmethod
    def _generate_fc_schema(name: str, metadata: dict, skill_md_path: Path | None = None) -> dict:
        """从 metadata.parameters 生成 OpenAI FC tool schema。

        v2.4.x：description = frontmatter description + skill.md 正文第一段（截止首个 H2），
        约 ~150-200 tokens，提供 pushy 触发场景 + examples + preconditions。

        Args:
            name: 技能名称。
            metadata: skill.md frontmatter dict。
            skill_md_path: skill.md 文件路径（用于提取正文第一段）。

        Returns:
            OpenAI tools 格式的 schema dict。
        """
        properties: dict[str, Any] = {}
        required: list[str] = []
        params_def = metadata.get("parameters") or {}

        for param_name, param_info in params_def.items():
            if not isinstance(param_info, dict):
                continue

            # 类型映射：规范化 skill.md 中的类型名到 JSON Schema 标准类型
            _TYPE_MAP = {"int": "integer", "str": "string", "bool": "boolean", "float": "number"}
            raw_type = param_info.get("type", "string")
            param_type = _TYPE_MAP.get(raw_type, raw_type)

            prop: dict[str, Any] = {"type": param_type}
            if "description" in param_info:
                prop["description"] = param_info["description"]
            if "default" in param_info:
                prop["default"] = param_info["default"]
            if "enum" in param_info:
                prop["enum"] = param_info["enum"]

            properties[param_name] = prop

            # 没有默认值的参数视为 required
            if "default" not in param_info:
                required.append(param_name)

        # v2.4.x: 用 skill.md 正文扩展 description（pushy + examples + preconditions）
        frontmatter_desc = metadata.get("description", "")
        if skill_md_path is not None:
            description = _extract_pushy_description(skill_md_path, frontmatter_desc)
        else:
            description = frontmatter_desc

        schema: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": f"skill-{name}",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        if required:
            schema["function"]["parameters"]["required"] = required

        return schema

    # ── 公开查询方法 ─────────────────────────────────────────────────────────

    def get(self, name: str) -> SkillEntry | None:
        """按名称获取技能条目。"""
        return self._skills.get(name)

    def list_skills(self) -> list[SkillEntry]:
        """返回所有已注册技能的列表。"""
        return list(self._skills.values())

    def get_fc_schemas(self) -> list[dict]:
        """返回所有技能的 FC tool schema 列表（排除 SKILLS_EXCLUDED_FROM_FC 中的技能）。

        被排除的技能（如 observe）由 core_tools 体系暴露给 LLM，
        不通过 skill: 前缀注册，避免 LLM 工具面重复。
        被排除的技能仍然在 _skills 中存储，可通过 get(name) 获取（供内部执行路由使用）。
        """
        return [
            entry.fc_schema
            for entry in self._skills.values()
            if entry.name not in SKILLS_EXCLUDED_FROM_FC
        ]

    def get_routing_summaries(self) -> list[dict[str, Any]]:
        """返回 L4 可注入 prompt 的结构化 skill routing 摘要。"""
        summaries: list[dict[str, Any]] = []
        for entry in self._skills.values():
            if entry.name in SKILLS_EXCLUDED_FROM_FC:
                continue
            summaries.append({
                "name": entry.name,
                "description": str(entry.metadata.get("description") or ""),
                "category": entry.metadata.get("category"),
                "routing": entry.routing,
            })
        return summaries


# ── 简易 YAML 解析器（无 PyYAML 时的 fallback）────────────────────────────────

def _simple_yaml_parse(text: str) -> dict:
    """解析简单的 YAML 文本（仅支持 skill.md 场景的子集）。

    支持：
    - 顶层 key: value（标量值）
    - 顶层 key: [list, items]（行内列表）
    - 缩进两空格的二级嵌套 key: value
    - 缩进四空格的三级嵌套 key: value（如 parameters.param_name.type）

    不支持多行字符串、四级以上嵌套等 YAML 特性。
    """
    result: dict[str, Any] = {}
    # 栈结构：[(indent_level, key, dict_ref), ...]
    # 用于追踪当前的嵌套层级
    stack: list[tuple[int, str, dict[str, Any]]] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if ":" not in stripped:
            continue

        indent = len(line) - len(line.lstrip())
        k, v = stripped.split(":", 1)
        k = k.strip()
        v = v.strip()

        # 弹出所有缩进 >= 当前行的栈帧（回到父级）
        while stack and stack[-1][0] >= indent:
            _indent, _key, _dict = stack.pop()
            # 将完成的嵌套 dict 写入其父级
            if stack:
                parent_dict = stack[-1][2]
                parent_dict[_key] = _dict
            else:
                result[_key] = _dict

        if v:
            # 有值：写入当前层级
            if stack:
                stack[-1][2][k] = _parse_yaml_value(v)
            else:
                result[k] = _parse_yaml_value(v)
        else:
            # 无值：开启新嵌套层
            stack.append((indent, k, {}))

    # 清空栈中剩余的嵌套
    while stack:
        _indent, _key, _dict = stack.pop()
        if stack:
            stack[-1][2][_key] = _dict
        else:
            result[_key] = _dict

    return result


def _parse_yaml_value(v: str) -> Any:
    """解析单个 YAML 值。"""
    # 去除引号
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]

    # 行内列表 [a, b, c]
    if v.startswith("[") and v.endswith("]"):
        items = v[1:-1].split(",")
        return [_parse_yaml_value(item.strip()) for item in items if item.strip()]

    # 布尔
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False

    # 数字
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        pass

    return v


# ── pushy description 提取（v2.4.x FC schema description 扩展）────────────────

# FC schema description 的最大字符数（约 200-300 tokens，中文约 1 字≈1 token）
_MAX_DESCRIPTION_CHARS = 800


def _extract_pushy_description(skill_md_path: Path, frontmatter_desc: str) -> str:
    """从 skill.md 提取完整的 pushy description（用于 FC schema）。

    格式：frontmatter description + 正文第一段（首个 H1 后的内容到首个 H2 之前）

    解析规则：
    - 跳过 YAML frontmatter（--- 包围）
    - 跳过文档开头的 H1 标题（# skill_name）
    - 提取直到第一个 ## 之前的所有正文文字
    - 用换行符连接 frontmatter desc 和正文（若正文开头重复 frontmatter desc 则去重）

    Args:
        skill_md_path: skill.md 文件路径
        frontmatter_desc: 已从 frontmatter 解析的 description 字段（fallback）

    Returns:
        合并后的 description 字符串，长度上限 _MAX_DESCRIPTION_CHARS。
        文件不存在或解析失败时返回 frontmatter_desc。
    """
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frontmatter_desc

    # 跳过 YAML frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:].lstrip("\n")

    # 提取正文第一段（H1 之后、H2 之前）
    lines = text.split("\n")
    body_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):  # 遇到 H2 停止
            break
        if stripped.startswith("# "):  # 跳过 H1 标题行
            continue
        body_lines.append(line)

    body = "\n".join(body_lines).strip()
    if not body:
        return frontmatter_desc

    # 避免重复：如果正文开头包含 frontmatter desc，直接用正文即可
    if frontmatter_desc and body.startswith(frontmatter_desc.rstrip("。.")):
        combined = body
    elif frontmatter_desc:
        combined = f"{frontmatter_desc}\n\n{body}"
    else:
        combined = body

    # 长度上限保护（避免 token 预算超支）
    if len(combined) > _MAX_DESCRIPTION_CHARS:
        combined = combined[:_MAX_DESCRIPTION_CHARS] + "..."

    return combined


# 需要 asyncio 用于签名校验中的 iscoroutinefunction
import asyncio
