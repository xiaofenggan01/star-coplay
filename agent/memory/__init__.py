"""
memory — Agent 工作记忆模块。

模块职责：
    - working_memory: 短期事件记忆 + 状态摘要（供慢环 Prompt 使用）

公共 API：
    WorkingMemory
"""

from memory.working_memory import WorkingMemory

__all__ = ["WorkingMemory"]
