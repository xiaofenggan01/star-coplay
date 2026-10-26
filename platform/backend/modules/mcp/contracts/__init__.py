"""Neutral contract exports.

Projection ports import capability models, while capability models import the
neutral contract models.  Keep projection exports lazy to avoid making the
package initializer part of that dependency cycle.
"""
from .models import *  # noqa: F401,F403

_PROJECTION_EXPORTS = {"ExposureFacts", "McpExposurePort", "McpSkillExecutionPort"}


def __getattr__(name: str):
    if name in _PROJECTION_EXPORTS:
        from . import projection
        return getattr(projection, name)
    raise AttributeError(name)


__all__ = [*globals().get("__all__", []), *_PROJECTION_EXPORTS]
