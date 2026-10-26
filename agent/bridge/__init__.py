"""
bridge — WebSocket 桥通信模块（V2 协议）。

生产路径通过 WSBridge 与 Mod 通信（WebSocket 经 Gateway 路由）。
FileBridge（文件桥）已于 R1 清理中删除，仅保留 WSBridge 主路径。

公共 API：
    WSBridge       — WebSocket 桥（生产主路径）
    BridgeBackend  — 桥抽象 Protocol（供类型注解使用）
    BridgeResult   — V2 命令执行结果数据类
"""

from bridge.bridge_backend import BridgeBackend
from bridge.bridge_result import BridgeResult
from bridge.ws_bridge import WSBridge

__all__ = ["WSBridge", "BridgeBackend", "BridgeResult"]
