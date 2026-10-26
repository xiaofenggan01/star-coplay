"""
v2_skills — L3 SkillRuntime 框架。

提供 Python async 技能的加载、注册和执行能力，
通过 IPC（FileBridge）编排 Mod 侧的 L1 能力。
"""

from v2_skills.errors import BridgeError, SkillCancelled, SkillLoadError
from v2_skills.models import GameState
from v2_skills.context import SkillContext
from v2_skills.runner import SkillRunner
from v2_skills.registry import SkillEntry, SkillRegistry

__all__ = [
    "BridgeError",
    "SkillCancelled",
    "SkillLoadError",
    "GameState",
    "SkillContext",
    "SkillRunner",
    "SkillEntry",
    "SkillRegistry",
]
