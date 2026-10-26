"""
bt_engine — 消息总线与游戏状态缓存模块。

原 BT 快环体系（ReactiveLoop / GspLoader / nodes / Blackboard）已于 FC 模式
迁移后全部弃用并删除（R1 清理，2026-05-08）。

当前仍在用的组件：
    - message_bus:      快环/慢环线程安全消息总线（被 main.py + EventRelay 使用）
    - game_state_cache: 游戏状态缓存（被 game_monitor.py 使用）

公共 API：
    MessageBus
"""

from bt_engine.message_bus import MessageBus

__all__ = [
    "MessageBus",
]
