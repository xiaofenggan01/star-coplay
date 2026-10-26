"""Path bootstrap for importing the agent packages from the eval harness.

The agent packages use absolute imports rooted at ``agent/`` (e.g.
``from v2_skills.context import SkillContext``) plus a virtual ``agent``
parent package for the few modules that use relative imports.  This mirrors
``agent/tests/conftest.py`` so the evaluator can reuse production skill code
instead of re-implementing it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"

_SUB_PACKAGES = ["bridge", "bt_engine", "memory", "messages", "slow_loop", "report", "v2_skills"]

_bootstrapped = False


def bootstrap() -> None:
    """Make ``agent/`` importable as both flat modules and a virtual package."""
    global _bootstrapped
    if _bootstrapped:
        return

    for path in (str(AGENT_DIR), str(AGENT_DIR.parent)):
        if path not in sys.path:
            sys.path.insert(0, path)

    if "agent" not in sys.modules:
        pkg = types.ModuleType("agent")
        pkg.__path__ = [str(AGENT_DIR)]
        pkg.__package__ = "agent"
        sys.modules["agent"] = pkg

    for sub in _SUB_PACKAGES:
        fqn = f"agent.{sub}"
        if fqn in sys.modules:
            continue
        module = types.ModuleType(fqn)
        module.__path__ = [str(AGENT_DIR / sub)]
        module.__package__ = fqn
        sys.modules[fqn] = module

    _bootstrapped = True
