"""Block schema drift across the complete M3/M4 published Skill surface."""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping

from modules.mcp.release_manifest import PROTO_CAPABILITIES
from modules.mcp.skills.contracts import published_skill_schemas
from modules.mcp.skills.models import SkillSource
from modules.mcp.skills.schema import parse_skill_package


REPO_ROOT = Path(__file__).resolve().parents[4]
SKILLS_ROOT = REPO_ROOT / "platform/backend/modules/mcp/skills"


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def test_all_38_published_packages_bind_to_authoritative_schemas():
    package_roots = sorted((SKILLS_ROOT / "builtin").glob("*/skill.yaml"))
    package_roots += sorted((SKILLS_ROOT / "controlled-m4").glob("*/skill.yaml"))
    assert len(package_roots) == 38

    for manifest_path in package_roots:
        package_root = manifest_path.parent
        source = (
            SkillSource.BUILTIN
            if package_root.parent.name == "builtin"
            else SkillSource.CONTROLLED
        )
        package = parse_skill_package(package_root, source, package_root.parent)
        assert package.manifest.input_schema["type"] == "object"
        assert package.manifest.output_schema["type"] == "object"
        if package.manifest.skill_id.startswith("stardew.proto."):
            expected = published_skill_schemas(package.manifest.skill_id)
            assert expected is not None
            assert _plain(package.manifest.input_schema) == expected[0]
            assert _plain(package.manifest.output_schema) == expected[1]


def test_atomic_proto_inputs_are_not_empty_placeholders_at_projection_time():
    for capability_id in PROTO_CAPABILITIES:
        package_root = SKILLS_ROOT / "builtin" / f"stardew.proto.{capability_id}"
        package = parse_skill_package(package_root, SkillSource.BUILTIN, package_root.parent)
        expected = published_skill_schemas(package.manifest.skill_id)
        assert expected is not None
        assert _plain(package.manifest.input_schema) == expected[0]
        assert _plain(package.manifest.output_schema) == expected[1]
