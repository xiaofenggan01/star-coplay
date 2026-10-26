from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


def _store_nonempty(store_dir: Path) -> bool:
    return store_dir.is_dir() and any(store_dir.iterdir())


def archive_memory_store_if_nonempty(store_dir: Path) -> None:
    if not _store_nonempty(store_dir):
        return
    history_root = store_dir.parent / "store_history"
    history_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    dest_parent = history_root / ts
    while dest_parent.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        dest_parent = history_root / ts
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest_store = dest_parent / "store"
    shutil.move(str(store_dir), str(dest_store))
    store_dir.mkdir(parents=True, exist_ok=True)
