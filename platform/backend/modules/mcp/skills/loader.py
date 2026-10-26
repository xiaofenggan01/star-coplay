"""M3.4 Skill loader — eager, atomic, trusted same-process loading.

No network, no subprocess, no sandbox claim.  The only public API is
``load_skill_programs``.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import stat
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any

from modules.mcp.skills.models import (
    SkillPackage,
    SkillPackageError,
    SkillRef,
    _deep_freeze,
)
from modules.mcp.skills.runtime_models import (
    SkillRuntimeError,
    SkillRuntimeErrorCode,
)
from modules.mcp.skills.schema import parse_skill_package

# ---------------------------------------------------------------------------
# Maximum implementation file size (1 MiB)
# ---------------------------------------------------------------------------
_MAX_IMPL_BYTES: int = 1_048_576


# ===================================================================
# LoadedSkillProgram — immutable result of a single load
# ===================================================================


def _freeze_lsp(instance: object) -> None:
    """Apply ``_deep_freeze`` to every field, skipping callable fields."""
    for f in fields(instance):
        if f.name == "run":
            continue  # callable — not freezable
        value = getattr(instance, f.name)
        frozen_value = _deep_freeze(value)
        if frozen_value is not value:
            object.__setattr__(instance, f.name, frozen_value)


@dataclass(frozen=True)
class LoadedSkillProgram:
    """Immutable loaded program: package metadata, digests, module name, and
    the captured ``run`` coroutine function."""

    package: SkillPackage
    implementation_digest: str
    module_name: str
    run: Callable[[Any, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]

    def __post_init__(self) -> None:
        _freeze_lsp(self)


# ===================================================================
# Internal helpers
# ===================================================================


def _check_no_symlink(path: Path) -> None:
    """lstat *path* and every existing parent component; reject any symlink."""
    # Check the leaf
    try:
        st = os.lstat(path)
    except OSError:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    if stat.S_ISLNK(st.st_mode):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    # Check every parent
    for parent in path.parents:
        try:
            pst = os.lstat(parent)
        except OSError:
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
        if stat.S_ISLNK(pst.st_mode):
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)


def _safe_read_impl(impl_path: Path) -> bytes:
    """Securely read *impl_path* and return captured bytes.

    - lstat(path) and every parent — reject symlinks
    - open with O_RDONLY|O_CLOEXEC|O_NOFOLLOW (best-effort per platform)
    - fstat(fd) — must be regular file
    - lstat again, verify same (st_dev, st_ino)
    - 0 <= st_size <= 1_048_576
    - read from fd in a loop; fail if accumulated > 1_048_576
    - close fd on every path
    - compute sha256 of captured bytes
    """
    _check_no_symlink(impl_path)

    # lstat before open
    try:
        st_before = os.lstat(impl_path)
    except OSError:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    if stat.S_ISLNK(st_before.st_mode):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    if not stat.S_ISREG(st_before.st_mode):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    if not (0 <= st_before.st_size <= _MAX_IMPL_BYTES):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # Open — O_NOFOLLOW is platform-specific; best-effort
    open_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW

    try:
        fd = os.open(impl_path, open_flags)
    except OSError:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    try:
        # fstat the open fd
        try:
            st_fd = os.fstat(fd)
        except OSError:
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

        if not stat.S_ISREG(st_fd.st_mode):
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

        if not (0 <= st_fd.st_size <= _MAX_IMPL_BYTES):
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

        # Verify same inode as lstat
        if (st_before.st_dev, st_before.st_ino) != (st_fd.st_dev, st_fd.st_ino):
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

        # Read in a loop
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_IMPL_BYTES:
                raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
            chunks.append(chunk)

        return b"".join(chunks)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _compute_implementation_digest(data: bytes) -> str:
    """Return the Git-canonical digest of a Python implementation."""
    canonical = data.replace(b"\r\n", b"\n")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _make_module_name(
    skill_id: str,
    version: str,
    manifest_digest: str,
    implementation_digest: str,
) -> str:
    """Deterministic private module name::
        _starcoplay_skill_<sha256(skill_id + NUL + version + NUL +
                                 manifest_digest + NUL +
                                 implementation_digest).hexdigest()>
    """
    payload = f"{skill_id}\0{version}\0{manifest_digest}\0{implementation_digest}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"_starcoplay_skill_{digest}"


def _validate_entrypoint_callable(fn: Any) -> None:
    """Verify *fn* is an async function with exactly two positional-or-keyword
    parameters named ``ctx`` and ``arguments``, no defaults, no *args, no
    **kwargs, and no additional parameters."""
    if not inspect.iscoroutinefunction(fn):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    if inspect.isasyncgenfunction(fn):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    if inspect.isclass(fn):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    params = list(sig.parameters.values())

    # Must have exactly 2 parameters
    if len(params) != 2:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # Both must be POSITIONAL_OR_KEYWORD
    for p in params:
        if p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD:
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # Named exactly ctx and arguments
    if params[0].name != "ctx" or params[1].name != "arguments":
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # No defaults
    for p in params:
        if p.default != inspect.Parameter.empty:
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # No *args, **kwargs checked already by len==2 + kind check


# ===================================================================
# Single-package load
# ===================================================================


def _load_one(pkg: SkillPackage) -> LoadedSkillProgram:
    """Load a single *pkg* and return a ``LoadedSkillProgram``.

    Raises ``SkillRuntimeError(AUTHORIZATION_STALE)`` when reparse
    disagrees with the indexed package.  Raises
    ``SkillRuntimeError(IMPLEMENTATION_NOT_LOADED)`` on any read, compile,
    exec, entrypoint, or signature failure.
    """
    # 1. Reparse — require exact equality of all fields
    try:
        reparsed = parse_skill_package(
            pkg.package_root, pkg.source, pkg.package_root.parent,
        )
    except SkillPackageError:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    except Exception:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    if reparsed.manifest_digest != pkg.manifest_digest:
        raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
    if reparsed.manifest != pkg.manifest:
        raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
    if reparsed.package_root != pkg.package_root:
        raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
    if reparsed.implementation_path != pkg.implementation_path:
        raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
    if reparsed.source != pkg.source:
        raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)
    reparsed_ref = SkillRef(
        skill_id=reparsed.manifest.skill_id,
        version=reparsed.manifest.version,
    )
    indexed_ref = SkillRef(
        skill_id=pkg.manifest.skill_id,
        version=pkg.manifest.version,
    )
    if reparsed_ref != indexed_ref:
        raise SkillRuntimeError(SkillRuntimeErrorCode.AUTHORIZATION_STALE)

    # 2. Validate implementation_path constraints
    impl_path = pkg.implementation_path
    if impl_path != pkg.package_root / "implementation.py":
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    if impl_path.parent != pkg.package_root:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # resolve and check escape
    resolved_impl = impl_path.resolve()
    resolved_root = pkg.package_root.resolve()
    try:
        resolved_impl.relative_to(resolved_root.parent)
    except ValueError:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # 3. Secure read
    impl_bytes = _safe_read_impl(impl_path)

    # 4. Compute implementation digest
    implementation_digest = _compute_implementation_digest(impl_bytes)

    # 5. Module name
    module_name = _make_module_name(
        pkg.manifest.skill_id,
        pkg.manifest.version,
        pkg.manifest_digest,
        implementation_digest,
    )

    # 6. compile
    try:
        code = compile(impl_bytes, f"<{module_name}>", "exec")
    except SyntaxError:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
    except Exception:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # 7. exec in private namespace
    namespace: dict[str, Any] = {"__name__": module_name}
    try:
        exec(code, namespace)
    except Exception:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    # 8. Extract entrypoint: implementation:run → module "implementation", callable "run"
    entrypoint_callable = pkg.manifest.entrypoint_callable  # "run"
    if entrypoint_callable not in namespace:
        raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)

    run = namespace[entrypoint_callable]

    # 9. Validate signature
    _validate_entrypoint_callable(run)

    return LoadedSkillProgram(
        package=pkg,
        implementation_digest=implementation_digest,
        module_name=module_name,
        run=run,
    )


# ===================================================================
# Public API
# ===================================================================


def load_skill_programs(
    packages: Sequence[SkillPackage],
) -> Mapping[SkillRef, LoadedSkillProgram]:
    """Eagerly and atomically load every *package*.

    Returns an immutable ``MappingProxyType`` keyed by exact ``SkillRef``.
    Any failure rejects the entire batch — no partial mapping is ever
    returned.

    Raises:
        SkillRuntimeError(IMPLEMENTATION_NOT_LOADED): read/compile/exec/signature failure.
        SkillRuntimeError(AUTHORIZATION_STALE): reparse mismatch.
    """
    temp: dict[SkillRef, LoadedSkillProgram] = {}

    for pkg in packages:
        ref = SkillRef(
            skill_id=pkg.manifest.skill_id,
            version=pkg.manifest.version,
        )
        if ref in temp:
            raise SkillRuntimeError(SkillRuntimeErrorCode.IMPLEMENTATION_NOT_LOADED)
        temp[ref] = _load_one(pkg)

    return MappingProxyType(temp)
