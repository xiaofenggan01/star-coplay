from __future__ import annotations

import stat
from types import SimpleNamespace

from modules.runtime_manager.coordinator import _credential_file_is_ready


class _Path:
    def __init__(self, *, mode: int, size: int) -> None:
        self._stat = SimpleNamespace(st_mode=mode, st_size=size)

    def stat(self):
        return self._stat


def test_windows_accepts_nonempty_regular_credential_file() -> None:
    path = _Path(mode=stat.S_IFREG | 0o666, size=16)

    assert _credential_file_is_ready(path, platform_name="nt") is True


def test_posix_requires_private_credential_mode() -> None:
    public = _Path(mode=stat.S_IFREG | 0o644, size=16)
    private = _Path(mode=stat.S_IFREG | 0o600, size=16)

    assert _credential_file_is_ready(public, platform_name="posix") is False
    assert _credential_file_is_ready(private, platform_name="posix") is True


def test_credential_file_must_be_regular_and_nonempty() -> None:
    directory = _Path(mode=stat.S_IFDIR | 0o700, size=16)
    empty = _Path(mode=stat.S_IFREG | 0o600, size=0)

    assert _credential_file_is_ready(directory, platform_name="nt") is False
    assert _credential_file_is_ready(empty, platform_name="nt") is False
