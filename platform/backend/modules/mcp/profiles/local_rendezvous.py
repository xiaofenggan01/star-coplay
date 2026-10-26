"""Owner-only rendezvous used by an already-running local Mod."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Mapping

from modules.mcp.core.errors import McpError


def default_rendezvous_path() -> Path:
    return Path.home() / ".starcoplay" / "local-direct" / "rendezvous.json"


class LocalRendezvous:
    def __init__(self, path: Path | None = None):
        self.path = path or default_rendezvous_path()
        self._published = False
        self._published_identity: tuple[int, int] | None = None

    def publish(self, material: Mapping[str, object]) -> Path:
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        if self.path.exists():
            raise McpError("conflict", "another local rendezvous is already published")
        fd, temporary = tempfile.mkstemp(prefix=".rendezvous-", dir=directory)
        temp_path = Path(temporary)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            os.write(fd, json.dumps(dict(material), separators=(",", ":"), sort_keys=True).encode())
            os.fsync(fd)
            os.close(fd)
            fd = -1
            # Hard-link creation is atomic and refuses to replace an owner.
            os.link(temp_path, self.path)
            self._published = True
            published = self.path.stat()
            self._published_identity = (published.st_dev, published.st_ino)
            return self.path
        finally:
            if fd >= 0:
                os.close(fd)
            temp_path.unlink(missing_ok=True)

    def cleanup(self) -> None:
        try:
            current = self.path.stat()
        except FileNotFoundError:
            current_identity = None
        else:
            current_identity = (current.st_dev, current.st_ino)
        # Once ours is claimed, a later publisher may own the stable path.
        # Never unlink an inode which this instance did not publish.
        if self._published_identity is not None and current_identity == self._published_identity:
            self.path.unlink(missing_ok=True)
        self._published = False
        self._published_identity = None

    def claimed(self) -> bool:
        return self._published and not self.path.exists()

    def __repr__(self) -> str:
        return f"LocalRendezvous(path={self.path!r}, material=<redacted>)"
