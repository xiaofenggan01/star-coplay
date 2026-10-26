"""test_registry — SkillRegistry 单元测试。

测试技能加载、frontmatter 解析、FC schema 生成和错误处理。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from v2_skills.registry import SkillRegistry, SkillEntry, _simple_yaml_parse, _extract_pushy_description
from v2_skills.errors import SkillLoadError


# ── 技能文件模板 ─────────────────────────────────────────────────────────────

VALID_SKILL_MD = """\
---
name: test_skill
description: "测试技能描述"
category: test
version: "1.0.0"
parameters:
  count:
    type: integer
    description: "数量参数"
    default: 0
  name:
    type: string
    description: "名称参数"
tags: [test]
---

# test_skill

这是一个测试技能。
"""

VALID_SCRIPT_PY = """\
async def test_skill(ctx, count: int = 0, name: str = ""):
    return {"success": True, "status": "ok", "data": {"count": count, "name": name}}
"""


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _create_skill_dir(base_dir: Path, skill_name: str, skill_md: str, script_py: str) -> Path:
    """在 base_dir/builtin/skill_name/ 下创建技能文件。"""
    skill_dir = base_dir / "builtin" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "skill.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "script.py").write_text(script_py, encoding="utf-8")
    return skill_dir


# ── 正常加载测试 ─────────────────────────────────────────────────────────────

class TestRegistryLoad:
    """测试正常技能加载。"""

    def test_load_valid_skill(self, tmp_path):
        """成功加载有效技能。"""
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, VALID_SCRIPT_PY)

        registry = SkillRegistry(tmp_path)

        assert len(registry.list_skills()) == 1
        entry = registry.get("test_skill")
        assert entry is not None
        assert entry.name == "test_skill"
        assert entry.metadata["description"] == "测试技能描述"
        assert callable(entry.func)

    def test_load_skill_routing_metadata(self, tmp_path):
        skill_md = VALID_SKILL_MD.replace(
            "tags: [test]",
            """routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: current_map
  ambiguity_policy: ask_if_scope_missing
tags: [test]""",
        )
        _create_skill_dir(tmp_path, "test_skill", skill_md, VALID_SCRIPT_PY)

        registry = SkillRegistry(tmp_path)

        entry = registry.get("test_skill")
        assert entry is not None
        assert entry.routing["owns_discovery"] is True
        assert entry.routing["handles_precheck"] is True
        assert entry.routing["verifies_result"] is True
        assert entry.routing["execution_scope"] == "current_map"
        assert entry.routing["ambiguity_policy"] == "ask_if_scope_missing"

    def test_routing_summaries_expose_only_fc_visible_skills(self, tmp_path):
        routed_md = VALID_SKILL_MD.replace(
            "tags: [test]",
            """routing:
  owns_discovery: true
  handles_precheck: true
  verifies_result: true
  execution_scope: current_map
tags: [test]""",
        )
        _create_skill_dir(tmp_path, "test_skill", routed_md, VALID_SCRIPT_PY)

        observe_md = routed_md.replace("test_skill", "observe").replace(
            """parameters:
  count:
    type: integer
    description: "数量参数"
    default: 0
  name:
    type: string
    description: "名称参数"
""",
            """parameters:
  detail:
    type: string
    description: "detail"
    default: ""
""",
        )
        observe_py = 'async def observe(ctx, detail: str = ""):\n    return {"success": True}\n'
        _create_skill_dir(tmp_path, "observe", observe_md, observe_py)

        registry = SkillRegistry(tmp_path)

        summaries = registry.get_routing_summaries()
        assert [item["name"] for item in summaries] == ["test_skill"]
        assert summaries[0]["routing"]["owns_discovery"] is True
        assert summaries[0]["description"] == "测试技能描述"

    def test_load_empty_builtin(self, tmp_path):
        """builtin 目录为空时不报错。"""
        (tmp_path / "builtin").mkdir(parents=True)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_load_no_builtin(self, tmp_path):
        """builtin 目录不存在时不报错。"""
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_load_multiple_skills(self, tmp_path):
        """加载多个技能。"""
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, VALID_SCRIPT_PY)

        skill2_md = VALID_SKILL_MD.replace("test_skill", "another_skill")
        skill2_py = VALID_SCRIPT_PY.replace("test_skill", "another_skill")
        _create_skill_dir(tmp_path, "another_skill", skill2_md, skill2_py)

        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 2

    def test_skip_dir_without_files(self, tmp_path):
        """缺少 skill.md 或 script.py 的目录被跳过。"""
        skill_dir = tmp_path / "builtin" / "incomplete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text("---\nname: incomplete\n---", encoding="utf-8")
        # 缺少 script.py

        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0


# ── Frontmatter 解析测试 ─────────────────────────────────────────────────────

class TestFrontmatter:
    """测试 YAML frontmatter 解析。"""

    def test_parse_basic(self):
        """解析基本 key:value。"""
        result = _simple_yaml_parse('name: test\nversion: "1.0"')
        assert result["name"] == "test"
        assert result["version"] == "1.0"

    def test_parse_nested(self):
        """解析嵌套结构。"""
        yaml_text = "parameters:\n  count:\n    type: integer\n    default: 0"
        result = _simple_yaml_parse(yaml_text)
        assert "parameters" in result
        # 注意：简易解析器对深层嵌套支持有限，这里主要验证不崩溃
        # 实际使用中优先用 PyYAML

    def test_parse_list(self):
        """解析行内列表。"""
        result = _simple_yaml_parse("tags: [a, b, c]")
        assert result["tags"] == ["a", "b", "c"]

    def test_parse_boolean(self):
        """解析布尔值。"""
        result = _simple_yaml_parse("enabled: true\ndisabled: false")
        assert result["enabled"] is True
        assert result["disabled"] is False

    def test_parse_number(self):
        """解析数字。"""
        result = _simple_yaml_parse("count: 42\nratio: 3.14")
        assert result["count"] == 42
        assert result["ratio"] == 3.14

    def test_parse_quoted_string(self):
        """解析带引号的字符串。"""
        result = _simple_yaml_parse('desc: "hello world"')
        assert result["desc"] == "hello world"


# ── FC Schema 生成测试 ───────────────────────────────────────────────────────

class TestFCSchema:
    """测试 FC tool schema 生成。"""

    def test_schema_generation(self, tmp_path):
        """验证生成的 FC schema 结构。"""
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, VALID_SCRIPT_PY)
        registry = SkillRegistry(tmp_path)

        schemas = registry.get_fc_schemas()
        assert len(schemas) == 1

        schema = schemas[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "skill-test_skill"
        # v2.4.x: description = frontmatter description + 正文第一段（pushy）
        # 应该包含 frontmatter description
        assert "测试技能描述" in schema["function"]["description"]
        # 应该包含 skill.md 正文内容
        assert "这是一个测试技能" in schema["function"]["description"]

        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert "count" in params["properties"]
        assert params["properties"]["count"]["type"] == "integer"
        assert "name" in params["properties"]

    def test_schema_description_length_capped(self, tmp_path):
        """FC schema description 长度有上限保护。"""
        # 构造一个正文超长的 skill.md
        long_body = "很长的描述。" * 200  # ~1200 字符
        long_skill_md = f"""\
---
name: test_skill
description: "测试技能描述"
category: test
version: "1.0.0"
parameters:
  count:
    type: integer
    description: "数量参数"
    default: 0
  name:
    type: string
    description: "名称参数"
tags: [test]
---

# test_skill

{long_body}

## 详细说明

这里是不应该被包含的内容。
"""
        _create_skill_dir(tmp_path, "test_skill", long_skill_md, VALID_SCRIPT_PY)
        registry = SkillRegistry(tmp_path)

        schema = registry.get_fc_schemas()[0]
        desc = schema["function"]["description"]
        # 长度上限保护：不超过 _MAX_DESCRIPTION_CHARS + "..." 的长度
        assert len(desc) <= 803  # 800 + len("...")
        # H2 之后的内容不应该被包含
        assert "这里是不应该被包含的内容" not in desc

    def test_required_params(self, tmp_path):
        """没有 default 的参数标记为 required。"""
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, VALID_SCRIPT_PY)
        registry = SkillRegistry(tmp_path)

        schema = registry.get_fc_schemas()[0]
        params = schema["function"]["parameters"]

        # name 没有 default，应该在 required 中
        assert "required" in params
        assert "name" in params["required"]
        # count 有 default: 0，不应该在 required 中
        assert "count" not in params["required"]


# ── 错误处理测试 ─────────────────────────────────────────────────────────────

class TestRegistryErrors:
    """测试技能加载的错误处理。"""

    def test_missing_frontmatter(self, tmp_path):
        """skill.md 无 frontmatter 时跳过。"""
        no_frontmatter_md = "# No Frontmatter\nJust markdown."
        _create_skill_dir(tmp_path, "bad_skill", no_frontmatter_md, VALID_SCRIPT_PY)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_missing_function(self, tmp_path):
        """script.py 中找不到同名函数时跳过。"""
        wrong_func_py = 'async def wrong_name(ctx): return {"success": True}'
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, wrong_func_py)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_not_async_function(self, tmp_path):
        """函数不是 async 时跳过。"""
        sync_py = 'def test_skill(ctx, count=0, name=""): return {"success": True}'
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, sync_py)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_missing_ctx_param(self, tmp_path):
        """函数第一个参数不是 ctx 时跳过。"""
        no_ctx_py = 'async def test_skill(context, count=0, name=""): return {"success": True}'
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, no_ctx_py)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_missing_declared_param(self, tmp_path):
        """函数缺少 skill.md 中声明的参数时跳过。"""
        # skill.md 声明了 count 和 name，但脚本只有 count
        missing_param_py = 'async def test_skill(ctx, count: int = 0): return {"success": True}'
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, missing_param_py)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0

    def test_import_error_skipped(self, tmp_path):
        """script.py 导入失败时跳过。"""
        bad_py = "import nonexistent_module_xyz\nasync def test_skill(ctx): pass"
        _create_skill_dir(tmp_path, "test_skill", VALID_SKILL_MD, bad_py)
        registry = SkillRegistry(tmp_path)
        assert len(registry.list_skills()) == 0


# ── _extract_pushy_description 单元测试 ─────────────────────────────────────

class TestExtractPushyDescription:
    """测试 pushy description 提取函数。"""

    def test_extract_with_body(self, tmp_path):
        """提取正文第一段（frontmatter desc + H1 后 H2 前内容）。"""
        skill_md = """\
---
name: demo
description: "简短描述"
---

# demo

这是正文第一段，包含触发场景说明。

## 详细行为

这部分不应被包含。
"""
        path = tmp_path / "skill.md"
        path.write_text(skill_md, encoding="utf-8")

        result = _extract_pushy_description(path, "简短描述")
        assert "简短描述" in result
        assert "这是正文第一段" in result
        assert "这部分不应被包含" not in result

    def test_fallback_on_missing_file(self, tmp_path):
        """文件不存在时返回 frontmatter_desc。"""
        result = _extract_pushy_description(tmp_path / "nonexistent.md", "fallback desc")
        assert result == "fallback desc"

    def test_no_duplicate_when_body_starts_with_desc(self, tmp_path):
        """正文开头就是 frontmatter desc 时，不重复。"""
        skill_md = """\
---
name: demo
description: "浇灌作物"
---

# demo

浇灌作物。当你看到未浇水的作物时使用。

## 行为
"""
        path = tmp_path / "skill.md"
        path.write_text(skill_md, encoding="utf-8")

        result = _extract_pushy_description(path, "浇灌作物")
        # "浇灌作物" 不应出现两次
        assert result.count("浇灌作物") == 1

    def test_length_cap(self, tmp_path):
        """超长正文被截断到 _MAX_DESCRIPTION_CHARS。"""
        long_body = "长正文内容。" * 200  # ~1200 字符
        skill_md = f"---\nname: demo\ndescription: '简短'\n---\n\n# demo\n\n{long_body}\n\n## H2\n"
        path = tmp_path / "skill.md"
        path.write_text(skill_md, encoding="utf-8")

        result = _extract_pushy_description(path, "简短")
        assert len(result) <= 803  # 800 + len("...")
        assert result.endswith("...")

    def test_empty_body_returns_frontmatter_desc(self, tmp_path):
        """正文为空时返回 frontmatter_desc。"""
        skill_md = "---\nname: demo\ndescription: '只有 frontmatter'\n---\n\n# demo\n\n## 立即是 H2\n"
        path = tmp_path / "skill.md"
        path.write_text(skill_md, encoding="utf-8")

        result = _extract_pushy_description(path, "只有 frontmatter")
        assert result == "只有 frontmatter"


# ── 集成：加载真实 pet_animals 技能 ──────────────────────────────────────────

class TestPetAnimalsLoad:
    """验证真实的 pet_animals 技能可以被正确加载。"""

    def test_load_pet_animals(self):
        """从实际的 v2_skills 目录加载 pet_animals。"""
        skills_dir = Path(__file__).parent.parent.parent / "v2_skills"
        if not (skills_dir / "builtin" / "pet_animals" / "skill.md").exists():
            pytest.skip("pet_animals 技能文件不存在")

        registry = SkillRegistry(skills_dir)

        entry = registry.get("pet_animals")
        assert entry is not None
        assert entry.name == "pet_animals"
        assert "抚摸" in entry.metadata.get("description", "")
        assert entry.fc_schema["function"]["name"] == "skill-pet_animals"

        # v2.4.x: FC schema description 应包含 frontmatter desc + 正文 pushy 内容
        fc_desc = entry.fc_schema["function"]["description"]
        assert "抚摸" in fc_desc  # frontmatter desc 内容
        # description 应该比纯 frontmatter 更长（包含了正文）
        assert len(fc_desc) > len(entry.metadata.get("description", ""))

        # 验证 FC schema 参数
        params = entry.fc_schema["function"]["parameters"]
        assert "max_count" in params["properties"]
        assert params["properties"]["max_count"]["type"] == "integer"


class TestDailySkillRoutingMetadata:
    def test_daily_skills_have_l4_routing_metadata(self):
        skills_dir = Path(__file__).parent.parent.parent / "v2_skills"
        registry = SkillRegistry(skills_dir)

        for skill_name in [
            "water_crops",
            "harvest_crops",
            "pet_animals",
            "chop_trees",
            "go_bed",
        ]:
            entry = registry.get(skill_name)
            assert entry is not None, skill_name
            routing = entry.routing
            assert routing["owns_discovery"] is True, skill_name
            assert routing["handles_precheck"] is True, skill_name
            assert routing["verifies_result"] is True, skill_name
            assert routing["execution_scope"], skill_name
