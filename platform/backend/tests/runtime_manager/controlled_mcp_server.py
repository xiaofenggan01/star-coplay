"""Minimal, test-only MCP stdio JSON-RPC server used to prove a real,
non-mocked Claude CLI <-> MCP tool-call loop (turn 2 of the real-Claude
adapter spike).

Standard-library only, run as a standalone subprocess (spawned by the real
``claude`` CLI via ``--mcp-config``), so it deliberately does NOT import
anything from ``modules.runtime_manager`` -- it must work even if the adapter
package is not importable from wherever ``claude`` resolves its cwd/PYTHONPATH.

Wire protocol: JSON-RPC 2.0 over stdio, one UTF-8 JSON message per line. MCP
stdio explicitly uses newline-delimited messages; LSP ``Content-Length``
framing is not accepted here.

Tools exposed:

- ``controlled_echo``: reads a test credential file (path from
  ``CONTROLLED_MCP_CREDENTIAL_PATH``), verifies its POSIX mode is 0600,
  computes a sha256 fingerprint (first 16 hex chars) of its contents, and
  returns the fingerprint -- never the raw secret. If a revoke marker file
  exists at call time (path from ``CONTROLLED_MCP_REVOKE_MARKER_PATH``), the
  call fails with ``isError: true`` instead.
- ``controlled_fail``: always returns ``isError: true``.

Every ``tools/call`` appends one JSON line to the audit log at
``CONTROLLED_MCP_AUDIT_LOG_PATH``: timestamp, tool name, isError, fingerprint
(never the raw credential).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
from typing import Any, Dict, Optional


def _log(msg: str) -> None:
    print(f"[controlled_mcp_server] {msg}", file=sys.stderr, flush=True)


def _read_message(stream: Any) -> Optional[Dict[str, Any]]:
    """Read one newline-delimited MCP JSON-RPC message; return ``None`` on EOF."""
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line.strip():
            return json.loads(line.decode("utf-8"))


def _write_message(stream: Any, message: Dict[str, Any]) -> None:
    stream.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    stream.flush()


def _append_audit(path: str, *, tool: str, is_error: bool, fingerprint: Optional[str], note: str = "") -> None:
    entry = {
        "timestamp": time.time(),
        "tool": tool,
        "is_error": is_error,
        "fingerprint": fingerprint,
        "note": note,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _handle_controlled_echo(args: Dict[str, Any]) -> Dict[str, Any]:
    message = str(args.get("message", ""))
    audit_path = os.environ["CONTROLLED_MCP_AUDIT_LOG_PATH"]
    revoke_marker_path = os.environ.get("CONTROLLED_MCP_REVOKE_MARKER_PATH", "")
    credential_path = os.environ["CONTROLLED_MCP_CREDENTIAL_PATH"]
    expected_runtime_id = os.environ["CONTROLLED_MCP_RUNTIME_ID"]

    if args.get("runtime_id") != expected_runtime_id:
        _append_audit(audit_path, tool="controlled_echo", is_error=True, fingerprint=None, note="runtime-mismatch")
        return {
            "content": [{"type": "text", "text": "runtime binding mismatch; call rejected"}],
            "isError": True,
        }

    if revoke_marker_path and os.path.exists(revoke_marker_path):
        _append_audit(audit_path, tool="controlled_echo", is_error=True, fingerprint=None, note="revoked")
        return {
            "content": [{"type": "text", "text": "credential has been revoked; call rejected"}],
            "isError": True,
        }

    try:
        mode = stat.S_IMODE(os.stat(credential_path).st_mode)
    except OSError as exc:
        _append_audit(audit_path, tool="controlled_echo", is_error=True, fingerprint=None, note=f"stat-failed:{exc}")
        return {"content": [{"type": "text", "text": f"credential file unreadable: {exc}"}], "isError": True}

    if mode != 0o600:
        _append_audit(
            audit_path, tool="controlled_echo", is_error=True, fingerprint=None, note=f"bad-mode:{oct(mode)}"
        )
        return {
            "content": [{"type": "text", "text": f"credential file has unsafe mode {oct(mode)}, refusing"}],
            "isError": True,
        }

    with open(credential_path, "rb") as fh:
        secret_bytes = fh.read()
    fingerprint = hashlib.sha256(secret_bytes).hexdigest()[:16]

    _append_audit(audit_path, tool="controlled_echo", is_error=False, fingerprint=fingerprint, note="ok")
    return {
        "content": [{"type": "text", "text": f"echo:{message} fingerprint:{fingerprint}"}],
        "isError": False,
    }


def _handle_controlled_fail(args: Dict[str, Any]) -> Dict[str, Any]:
    audit_path = os.environ["CONTROLLED_MCP_AUDIT_LOG_PATH"]
    _append_audit(audit_path, tool="controlled_fail", is_error=True, fingerprint=None, note="deliberate-failure")
    return {
        "content": [{"type": "text", "text": "controlled_fail: deliberate tool error"}],
        "isError": True,
    }


_TOOLS = [
    {
        "name": "controlled_echo",
        "description": "Echo a message and return a non-secret fingerprint of the test credential.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "runtime_id": {"type": "string"},
            },
            "required": ["message", "runtime_id"],
        },
    },
    {
        "name": "controlled_fail",
        "description": "Always fails, for testing honest error propagation.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
]


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    audit_path = os.environ["CONTROLLED_MCP_AUDIT_LOG_PATH"]

    while True:
        message = _read_message(stdin)
        if message is None:
            _log("EOF on stdin, exiting")
            return

        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            _append_audit(audit_path, tool="__protocol__", is_error=False, fingerprint=None, note="protocol-init")
            _write_message(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "serverInfo": {"name": "controlled-mcp-server", "version": "0.1.0"},
                        "capabilities": {"tools": {}},
                    },
                },
            )
        elif method == "notifications/initialized":
            _log("received notifications/initialized")
            # notification: no response
        elif method == "tools/list":
            _append_audit(audit_path, tool="__protocol__", is_error=False, fingerprint=None, note="protocol-tools-list")
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "controlled_echo":
                result = _handle_controlled_echo(args)
            elif name == "controlled_fail":
                result = _handle_controlled_fail(args)
            else:
                result = {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": result})
        elif method == "ping":
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif msg_id is not None:
            # Unknown request: respond with a method-not-found error so the
            # client never hangs waiting for a reply.
            _write_message(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                },
            )
        else:
            _log(f"ignoring unknown notification: {method}")


if __name__ == "__main__":
    main()
