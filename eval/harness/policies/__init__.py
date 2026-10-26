"""Goal policies: turn a structured goal into skill calls.

The policy is the seam where a future LLM/HTN planner plugs in.  Today it wraps
the deterministic ``GoalRouter`` so goal-mode scenarios exercise production
planning code end to end, against the same simulated world.
"""

from .routing import RoutingPolicy

__all__ = ["RoutingPolicy"]
