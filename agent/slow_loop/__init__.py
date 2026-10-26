"""
slow_loop -- 慢环：LLM 驱动的决策与规划层。

模块职责：
    - deliberative_loop: 慢环主体，消费 MessageBus 消息并协调规划
    - prompt_assembler:  组装 LLM Prompt（游戏状态 + 记忆 + 工具定义）

公共 API：
    DeliberativeLoop（延迟导入，避免循环依赖）
"""


def __getattr__(name: str):
    if name == "DeliberativeLoop":
        from .deliberative_loop import DeliberativeLoop
        return DeliberativeLoop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DeliberativeLoop"]
