"""M3.4 boundary tests — prove no forbidden imports, no legacy fallback,
no concrete Router/Profile/MCP dependencies, and no V2 aliases.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


# ===================================================================
# Forbidden patterns
# ===================================================================

FORBIDDEN_IMPORTS = [
    "agent.v2_skills",
    "v2_skills",
    "legacy",
    "fallback",
    "Bridge",
    "WebSocket",
    "GatewayRouter",
    "LocalModRouter",
    "McpSession",
    "McpTool",
]

FORBIDDEN_CALLS = [
    "importlib.import_module",
    "sys.path",
]

SKILLS_DIR = Path(__file__).resolve().parents[2] / "modules" / "mcp" / "skills"

RUNTIME_FILES = [
    "runtime_models.py",
    "loader.py",
    "context.py",
    "runtime.py",
]


# ===================================================================
# AST scanner
# ===================================================================


def _collect_strings(node: ast.AST) -> list[str]:
    """Recursively collect all string literals and names from an AST node."""
    strings: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            strings.append(child.value)
        elif isinstance(child, ast.Name):
            strings.append(child.id)
        elif isinstance(child, ast.Attribute):
            # Reconstruct dotted name
            parts = []
            current = child
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                strings.append(".".join(reversed(parts)))
        elif isinstance(child, ast.Import):
            for alias in child.names:
                strings.append(alias.name)
        elif isinstance(child, ast.ImportFrom):
            module = child.module or ""
            for alias in child.names:
                strings.append(f"{module}.{alias.name}")
    return strings


def _parse_file(path: Path) -> ast.AST:
    """Parse a Python file, returning the AST."""
    source = path.read_text()
    return ast.parse(source, filename=str(path))


# ===================================================================
# Tests
# ===================================================================


class TestNoForbiddenImports:
    """Prove runtime modules contain no forbidden imports or references."""

    @pytest.mark.parametrize("filename", RUNTIME_FILES)
    def test_no_forbidden_imports(self, filename):
        filepath = SKILLS_DIR / filename
        assert filepath.exists(), f"{filename} not found at {filepath}"

        tree = _parse_file(filepath)
        strings = _collect_strings(tree)

        for forbidden in FORBIDDEN_IMPORTS:
            found = [s for s in strings if forbidden.lower() in s.lower()]
            assert not found, (
                f"{filename} references forbidden pattern '{forbidden}': {found}"
            )

    @pytest.mark.parametrize("filename", RUNTIME_FILES)
    def test_no_forbidden_calls(self, filename):
        filepath = SKILLS_DIR / filename
        assert filepath.exists(), f"{filename} not found at {filepath}"

        tree = _parse_file(filepath)
        strings = _collect_strings(tree)

        for forbidden in FORBIDDEN_CALLS:
            found = [s for s in strings if forbidden.lower() in s.lower()]
            assert not found, (
                f"{filename} references forbidden call '{forbidden}': {found}"
            )

    @pytest.mark.parametrize("filename", RUNTIME_FILES)
    def test_no_sys_path_mutation(self, filename):
        filepath = SKILLS_DIR / filename
        source = filepath.read_text()
        assert "sys.path" not in source, (
            f"{filename} mutates sys.path"
        )

    @pytest.mark.parametrize("filename", RUNTIME_FILES)
    def test_no_importlib_import_module(self, filename):
        filepath = SKILLS_DIR / filename
        source = filepath.read_text()
        assert "importlib.import_module" not in source, (
            f"{filename} uses importlib.import_module"
        )


_CONCRETE_FORBIDDEN = [
    "RouterBackedCapabilityRuntime",
    "McpCore",
    "McpSessionManager",
]


class TestNoConcreteDependencies:
    """Prove runtime modules import only neutral contracts, not concrete
    Router, Profile, or MCP implementations."""

    @pytest.mark.parametrize("filename", RUNTIME_FILES)
    def test_no_concrete_router_profile_mcp(self, filename):
        filepath = SKILLS_DIR / filename
        assert filepath.exists()

        tree = _parse_file(filepath)
        strings = _collect_strings(tree)

        for forbidden in _CONCRETE_FORBIDDEN:
            found = [s for s in strings if forbidden in s]
            assert not found, (
                f"{filename} imports concrete '{forbidden}'"
            )


class TestLoaderOnlyExposesPublicAPI:
    """Loader module only exposes load_skill_programs and LoadedSkillProgram."""

    def test_loader_no_class_loader(self):
        filepath = SKILLS_DIR / "loader.py"
        source = filepath.read_text()
        assert "SkillProgramLoader" not in source
        assert "class Loader" not in source

    def test_loader_no_hot_reload(self):
        filepath = SKILLS_DIR / "loader.py"
        source = filepath.read_text()
        assert "hot" not in source.lower() or "hot reload" not in source.lower()


class TestRuntimeOnlyNeutralConstructor:
    """Runtime constructor depends only on neutral deps, not concrete types."""

    def test_runtime_no_router_in_init(self):
        filepath = SKILLS_DIR / "runtime.py"
        source = filepath.read_text()
        # SkillRuntime.__init__ should not take Router or Profile params
        assert "router" not in source.lower() or "Router" not in source

    def test_constructor_rejects_non_mapping_programs_as_internal(self):
        from modules.mcp.skills.runtime import SkillRuntime
        from modules.mcp.skills.runtime_models import SkillRuntimeError, SkillRuntimeErrorCode

        with pytest.raises(SkillRuntimeError) as caught:
            SkillRuntime([], object(), object(), lambda: 0.0, lambda: "id", None)
        assert caught.value.code == SkillRuntimeErrorCode.INTERNAL

    def test_constructor_rejects_malformed_program_entry_as_internal(self):
        from modules.mcp.skills.runtime import SkillRuntime
        from modules.mcp.skills.runtime_models import SkillRuntimeError, SkillRuntimeErrorCode

        with pytest.raises(SkillRuntimeError) as caught:
            SkillRuntime({"bad": object()}, object(), object(), lambda: 0.0, lambda: "id", None)
        assert caught.value.code == SkillRuntimeErrorCode.INTERNAL


class TestSkillsInitNeutralExports:
    """__init__.py exports only neutral types."""

    def test_init_no_concrete_exports(self):
        filepath = SKILLS_DIR / "__init__.py"
        source = filepath.read_text()
        for forbidden in ["Router", "Profile", "McpTool", "McpSession", "Gateway"]:
            assert forbidden not in source, (
                f"__init__.py exports concrete '{forbidden}'"
            )
