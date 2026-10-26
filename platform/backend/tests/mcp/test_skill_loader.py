"""M3.4 Skill loader tests — stable digest, atomic failure, security gates,
post-load immutability, and signature validation.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from types import MappingProxyType

import pytest

from modules.mcp.skills.loader import (
    LoadedSkillProgram,
    _compute_implementation_digest,
    _validate_entrypoint_callable,
    load_skill_programs,
)
from modules.mcp.skills.models import SkillRef, SkillSource
from modules.mcp.skills.runtime_models import (
    SkillRuntimeError,
    SkillRuntimeErrorCode,
)
from modules.mcp.skills.schema import parse_skill_package

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skill_runtime"


def _parse(pkg_dir: str) -> tuple:
    """Parse a fixture package and return (pkg, ref)."""
    root = FIXTURES / pkg_dir
    pkg = parse_skill_package(root, SkillSource.BUILTIN, FIXTURES)
    ref = SkillRef(skill_id=pkg.manifest.skill_id, version=pkg.manifest.version)
    return pkg, ref


# ===================================================================
# Stable digest
# ===================================================================


class TestStableDigest:
    def test_digest_stable_across_loads(self):
        pkg, ref = _parse("test.minimal.v1")
        result1 = load_skill_programs([pkg])
        result2 = load_skill_programs([pkg])
        prog1 = result1[ref]
        prog2 = result2[ref]
        assert prog1.implementation_digest == prog2.implementation_digest
        assert prog1.implementation_digest.startswith("sha256:")

    def test_digest_changes_with_content(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            src = FIXTURES / "test.minimal.v1"
            dest = tdir / src.name
            shutil.copytree(str(src), str(dest))

            pkg1 = parse_skill_package(dest, SkillSource.BUILTIN, tdir)
            result1 = load_skill_programs([pkg1])
            ref = SkillRef(skill_id=pkg1.manifest.skill_id, version=pkg1.manifest.version)
            digest1 = result1[ref].implementation_digest

            # Modify implementation
            impl = dest / "implementation.py"
            impl.write_text("async def run(ctx, arguments):\n    return {\"changed\": True}\n")

            pkg2 = parse_skill_package(dest, SkillSource.BUILTIN, tdir)
            result2 = load_skill_programs([pkg2])
            digest2 = result2[ref].implementation_digest
            assert digest1 != digest2


# ===================================================================
# Eager atomic failure
# ===================================================================


class TestAtomicFailure:
    def test_single_failure_rejects_all(self):
        pkg_ok, _ = _parse("test.minimal.v1")
        pkg_bad, _ = _parse("test.syntax.error")
        with pytest.raises(SkillRuntimeError) as exc:
            load_skill_programs([pkg_ok, pkg_bad])
        assert exc.value.code == SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED

    def test_duplicate_skillref_fails_closed(self):
        pkg, _ = _parse("test.minimal.v1")
        with pytest.raises(SkillRuntimeError) as exc:
            load_skill_programs([pkg, pkg])
        assert exc.value.code == SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED

    def test_empty_packages_returns_empty_mapping(self):
        result = load_skill_programs([])
        assert isinstance(result, MappingProxyType)
        assert len(result) == 0


# ===================================================================
# Signature validation
# ===================================================================


class TestSignatureValidation:
    def test_syntax_error_rejected(self):
        pkg, _ = _parse("test.syntax.error")
        with pytest.raises(SkillRuntimeError) as exc:
            load_skill_programs([pkg])
        assert exc.value.code == SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED

    def test_sync_function_rejected(self):
        pkg, _ = _parse("test.sync.run")
        with pytest.raises(SkillRuntimeError) as exc:
            load_skill_programs([pkg])
        assert exc.value.code == SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED

    def test_wrong_params_rejected(self):
        pkg, _ = _parse("test.wrong.params")
        with pytest.raises(SkillRuntimeError) as exc:
            load_skill_programs([pkg])
        assert exc.value.code == SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED

    def test_async_generator_rejected(self):
        pkg, _ = _parse("test.async.gen")
        with pytest.raises(SkillRuntimeError) as exc:
            load_skill_programs([pkg])
        assert exc.value.code == SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED


# ===================================================================
# Entrypoint validation unit
# ===================================================================


class TestEntrypointValidation:
    def test_valid_coroutine_accepted(self):
        async def run(ctx, arguments):
            return {}

        _validate_entrypoint_callable(run)  # no exception

    def test_extra_param_rejected(self):
        async def run(ctx, arguments, extra):
            return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(run)

    def test_default_param_rejected(self):
        async def run(ctx, arguments=None):
            return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(run)

    def test_star_args_rejected(self):
        async def run(ctx, *arguments):
            return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(run)

    def test_kwargs_rejected(self):
        async def run(ctx, **arguments):
            return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(run)

    def test_wrong_names_rejected(self):
        async def run(a, b):
            return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(run)

    def test_class_rejected(self):
        class RunClass:
            async def run(self, ctx, arguments):
                return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(RunClass)

    def test_single_param_rejected(self):
        async def run(ctx):
            return {}

        with pytest.raises(SkillRuntimeError):
            _validate_entrypoint_callable(run)


# ===================================================================
# Post-load immutability
# ===================================================================


class TestPostLoadImmutability:
    def test_disk_change_invisible_after_load(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            src = FIXTURES / "test.minimal.v1"
            dest = tdir / src.name
            shutil.copytree(str(src), str(dest))

            pkg = parse_skill_package(dest, SkillSource.BUILTIN, tdir)
            result = load_skill_programs([pkg])
            ref = SkillRef(skill_id=pkg.manifest.skill_id, version=pkg.manifest.version)
            prog = result[ref]

            # Mutate disk
            impl = dest / "implementation.py"
            impl.write_text("async def run(ctx, arguments):\n    return {\"mutated\": True}\n")

            # The loaded program still uses captured bytes; run the captured callable
            output = asyncio.run(prog.run(None, {}))
            assert output == {}

    def test_reload_sees_new_content(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            src = FIXTURES / "test.minimal.v1"
            dest = tdir / src.name
            shutil.copytree(str(src), str(dest))

            pkg = parse_skill_package(dest, SkillSource.BUILTIN, tdir)
            result1 = load_skill_programs([pkg])
            ref = SkillRef(skill_id=pkg.manifest.skill_id, version=pkg.manifest.version)

            # Mutate disk
            impl = dest / "implementation.py"
            impl.write_text("async def run(ctx, arguments):\n    return {\"version\": 2}\n")

            # Reload sees new content
            result2 = load_skill_programs([pkg])  # reparse gets new impl bytes
            output = asyncio.run(result2[ref].run(None, {}))
            assert output == {"version": 2}


# ===================================================================
# Implementation digest
# ===================================================================


class TestImplementationDigest:
    def test_digest_format(self):
        digest = _compute_implementation_digest(b"hello")
        assert digest.startswith("sha256:")
        assert len(digest) == 71  # "sha256:" + 64 hex chars

    def test_digest_deterministic(self):
        d1 = _compute_implementation_digest(b"hello")
        d2 = _compute_implementation_digest(b"hello")
        assert d1 == d2

    def test_digest_is_stable_across_windows_checkout_newlines(self):
        assert _compute_implementation_digest(b"line one\r\nline two\r\n") == (
            _compute_implementation_digest(b"line one\nline two\n")
        )
