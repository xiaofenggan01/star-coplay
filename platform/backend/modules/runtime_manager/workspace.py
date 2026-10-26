"""Provider-neutral WorkDir materialization and transient cleanup."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
from pathlib import Path

from modules.runtime_manager.contracts import PersonaArtifact


def materialize_persona(workdir: Path, artifact: PersonaArtifact | None) -> Path | None:
    if artifact is None:
        return None
    path = workdir / "persona-artifact.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        revision = payload.get("persona_revision", payload.get("revision"))
        if (
            payload.get("agentee_id") != artifact.agentee_id
            or revision != artifact.revision
            or payload.get("content_hash") != artifact.content_hash
            or payload.get("instruction_payload") != artifact.instruction_payload
        ):
            raise ValueError("existing persona artifact does not match authoritative snapshot")
        return path
    path.write_text(
        json.dumps(
            {
                "agentee_id": artifact.agentee_id,
                "revision": artifact.revision,
                "content_hash": artifact.content_hash,
                "instruction_payload": artifact.instruction_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return path


def materialize_system_prompt(workdir: Path, instructions: str) -> Path:
    """Materialize trusted, product-composed instructions for Claude."""
    path = workdir / "persona-instructions.md"
    if path.is_symlink():
        raise ValueError("persona prompt target cannot be a symlink")
    if path.exists():
        flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0))
        fd = os.open(path, flags)
        try:
            with os.fdopen(fd, "r", encoding="utf-8", closefd=True) as stream:
                if stream.read() != instructions:
                    raise ValueError("existing persona prompt does not match authoritative snapshot")
        finally:
            pass
        return path
    if os.name == "nt":
        temporary = workdir / f".persona-instructions-{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR,
            )
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as stream:
                stream.write(instructions)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        finally:
            temporary.unlink(missing_ok=True)
        return path
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(workdir, directory_flags)
    temporary = f".persona-instructions-{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                     stat.S_IRUSR, dir_fd=directory_fd)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as stream:
            stream.write(instructions)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.chmod(path.name, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
                 dir_fd=directory_fd, follow_symlinks=False)
        os.fsync(directory_fd)
    finally:
        try: os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError: pass
        os.close(directory_fd)
    return path


def materialize_persona_prompt(workdir: Path, artifact: PersonaArtifact | None) -> Path | None:
    """Materialize only trusted persona instructions for Claude."""
    if artifact is None:
        return None
    return materialize_system_prompt(workdir, artifact.instruction_payload)


def cleanup_transient_workdir(workdir: Path) -> None:
    """Remove reconstructable runtime state while retaining owner/log evidence."""
    for name in (
        "mcp.json",
        "mcp-credential",
        "mcp-config.json",
        "mcp-revoked",
        "credential.revoked",
        "persona-artifact.json",
        "persona-instructions.md",
    ):
        path = workdir / name
        try:
            if os.name == "nt" and path.exists():
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            path.unlink()
        except FileNotFoundError:
            pass
    shutil.rmtree(workdir / "scratch", ignore_errors=True)
