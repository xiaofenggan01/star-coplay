from __future__ import annotations

import hashlib
import os
from pathlib import Path


def user_memory_fingerprint(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def resolve_user_memory_root(
    app_dir: Path,
    mem_cfg: dict,
    user_id: str,
) -> Path:
    default_rel = mem_cfg.get("memory_root", "data/memory/default_save")
    default_path = Path(default_rel)
    if default_path.is_absolute():
        leaf = default_path.name
        branch = default_path.parent
    else:
        full = (app_dir / default_rel).resolve()
        leaf = full.name
        branch = full.parent

    h = user_memory_fingerprint(user_id)
    raw_depth = mem_cfg.get("user_shard_depth", os.environ.get("STARCOPLAY_USER_MEMORY_SHARD_DEPTH", 2))
    try:
        depth = int(raw_depth) if raw_depth is not None else 2
    except (TypeError, ValueError):
        depth = 2
    depth = max(0, min(2, depth))

    override = (mem_cfg.get("user_data_root") or os.environ.get("STARCOPLAY_USER_DATA_ROOT", "") or "").strip()
    if override:
        vault_base = Path(override).expanduser().resolve()
    else:
        vault_base = branch / "vault"

    p = vault_base
    if depth >= 1:
        p = p / h[0:2]
    if depth >= 2:
        p = p / h[2:4]
    p = p / f"u{h}" / leaf
    return p
