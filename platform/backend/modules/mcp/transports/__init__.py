"""Northbound MCP transports."""

from .stdio import StdioMcpTransport
from .remote import (
    FileCredentialSource, RemoteMcpTransport, RemoteTransportError,
    WebSocketRemoteConnector,
)
from .hosted_server import HostedMcpJsonRpcSession, HostedMcpWebSocketEndpoint


def __getattr__(name):
    # Keep ``python -m modules.mcp.transports.stdio_proxy`` from importing the
    # target module through its package before runpy executes it.
    if name == "StdioRemoteMcpProxy":
        from .stdio_proxy import StdioRemoteMcpProxy
        return StdioRemoteMcpProxy
    raise AttributeError(name)

__all__ = [
    "StdioMcpTransport", "FileCredentialSource", "RemoteMcpTransport",
    "RemoteTransportError", "WebSocketRemoteConnector", "StdioRemoteMcpProxy",
    "HostedMcpJsonRpcSession", "HostedMcpWebSocketEndpoint",
]
