"""Secure, reconstructable PersonaArtifact materialization."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
from uuid import uuid4


@dataclass(frozen=True)
class PersonaArtifactSnapshot:
    agentee_id: str
    persona_revision: str
    content_hash: str
    generated_at: str
    instruction_payload: str
    source_refs: Tuple[str, ...] = ()


class PersonaArtifactMaterializer:
    def __init__(self, allowed_root: Path) -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.allowed_root.mkdir(parents=True, exist_ok=True)

    def materialize(self, runtime_id: str, generation: int, artifact: PersonaArtifactSnapshot) -> Path:
        if not runtime_id or runtime_id in {".", ".."} or "/" in runtime_id or "\\" in runtime_id:
            raise ValueError("invalid runtime_id")
        if generation < 1:
            raise ValueError("invalid generation")
        expected_hash = artifact.content_hash.removeprefix("sha256:")
        actual_hash = hashlib.sha256(artifact.instruction_payload.encode("utf-8")).hexdigest()
        if expected_hash != actual_hash:
            raise ValueError("persona content_hash mismatch")

        runtime_dir = self.allowed_root / runtime_id
        workdir = runtime_dir / f"generation-{generation}"
        self._mkdir_without_symlinks(runtime_dir)
        self._mkdir_without_symlinks(workdir)
        resolved = workdir.resolve()
        if not resolved.is_relative_to(self.allowed_root):
            raise ValueError("workdir escapes allowed root")
        path = workdir / "persona-artifact.json"
        if path.is_symlink():
            raise ValueError("persona artifact target cannot be a symlink")
        payload = {
            "agentee_id": artifact.agentee_id,
            "persona_revision": artifact.persona_revision,
            "content_hash": artifact.content_hash,
            "generated_at": artifact.generated_at,
            "instruction_payload": artifact.instruction_payload,
            "source_refs": list(artifact.source_refs),
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        temp_name = f".persona-{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if os.name == "nt":
            temporary = workdir / temp_name
            try:
                fd = os.open(temporary, flags, 0o600)
                with os.fdopen(fd, "wb", closefd=True) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if path.exists():
                    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                try:
                    os.replace(temporary, path)
                finally:
                    if path.exists():
                        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            finally:
                temporary.unlink(missing_ok=True)
            return path
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(workdir, directory_flags)
        try:
            fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.chmod(
                path.name,
                stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)
        return path

    @staticmethod
    def _mkdir_without_symlinks(path: Path) -> None:
        if path.is_symlink():
            raise ValueError("workdir component cannot be a symlink")
        path.mkdir(parents=False, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise ValueError("invalid workdir component")
