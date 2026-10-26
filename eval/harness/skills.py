"""Skill loading shared by the runner and the goal policies."""

from __future__ import annotations

import importlib


def load_skill(name: str):
    module = importlib.import_module(f"v2_skills.builtin.{name}.script")
    func = getattr(module, name, None)
    if func is None:
        raise AttributeError(f"skill module {module.__name__} has no callable {name!r}")
    return func
