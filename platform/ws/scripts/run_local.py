"""Compatibility shim — delegates to platform/backend/app/main.py.

This file exists so that scripts, CI, or systemd units referencing the old
path `platform/ws/scripts/run_local.py` continue to work after the ws/ → backend/
migration. New code should use: `cd platform/backend && python -m app`
"""
from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

warnings.warn(
    "ws/scripts/run_local.py is deprecated. "
    "Use: cd platform/backend && python -m app",
    DeprecationWarning,
    stacklevel=1,
)

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

sys.exit(
    subprocess.call(
        [sys.executable, "-m", "app"],
        cwd=str(BACKEND_DIR),
    )
)
