"""Claude CLI stream-json wire protocol: frame shapes, command line, and
config resolution. Kept separate from ``transport.py`` so the "what does a
frame mean" logic can be unit-reasoned about independently of process
plumbing.

Protocol facts encoded here come from
``spike/agent-runtime-adapter/evidence/real-claude-driver/turn-1-cli-protocol.md``
turn 1:

- Input frame: ``{"type":"user","message":{"role":"user","content":str}}``.
- Every turn emits ``system`` with ``subtype=init`` first (per-turn, not a
  global one-shot ready signal) carrying a stable Claude session id.
- ``system`` with ``subtype=api_retry`` is a transient retry marker: it must
  never be treated as turn success or as refreshing readiness.
- A turn converges on ``type=result``; ``subtype=success`` (and
  ``is_error=False``) is the only success shape. Anything else (timeout,
  ``is_error=True``, non-success subtype, EOF/exit before a result) is a
  structured failure.
- ``assistant`` frames may appear zero or more times before ``result`` and
  carry the visible turn text.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_PERMISSION_MODE = "default"
DEFAULT_TOOLS = ""
HOSTED_MCP_ALLOWED_TOOL = "mcp__starcoplay-hosted__*"

# The bootstrap turn is submitted internally by the adapter right after spawn
# to prove a real protocol handshake (system/init -> result) before business
# input is accepted. It must never surface as submit_input()'s turn 1.
BOOTSTRAP_PROMPT = (
    "Reply with the single word OK. Do not use any tool. This message is an "
    "internal readiness check and is not part of the conversation."
)


@dataclass(frozen=True)
class ClaudeLaunchConfig:
    """Resolved, provider-private launch configuration for one runtime process."""

    binary: str
    model: str
    permission_mode: str
    tools: str
    ready_deadline_seconds: float
    turn_deadline_seconds: float
    mcp_config_path: Optional[str] = None
    settings_path: Optional[str] = None
    append_system_prompt_file: Optional[str] = None
    allowed_tools: tuple[str, ...] = ()
    cli_path: Optional[str] = None

    def command(self) -> List[str]:
        command = [self.binary]
        if self.cli_path:
            command.append(self.cli_path)
        command.extend([
            "-p",
            "--model",
            self.model,
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--strict-mcp-config",
            "--tools",
            self.tools,
            "--permission-mode",
            self.permission_mode,
        ])
        if self.mcp_config_path:
            command[command.index("--strict-mcp-config"):command.index("--strict-mcp-config")] = [
                "--mcp-config", self.mcp_config_path
            ]
        if self.settings_path:
            command.extend(["--settings", self.settings_path])
        if self.append_system_prompt_file:
            command.extend(["--append-system-prompt-file", self.append_system_prompt_file])
        if self.allowed_tools:
            command.extend(["--allowedTools", *self.allowed_tools])
        return command


def _resolve_binary(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    # Fall back to the path proven to work in the turn-1 spike evidence when
    # PATH resolution (e.g. inside a stripped subprocess env) doesn't find it.
    fallback = os.path.expanduser("~/.local/bin/claude")
    return fallback


def resolve_launch_config(metadata: Mapping[str, Any]) -> ClaudeLaunchConfig:
    """Build a ``ClaudeLaunchConfig`` from ``RuntimeSpec.provider_private_metadata``.

    Every key is optional; callers get the turn-1-evidence defaults otherwise.
    """
    return ClaudeLaunchConfig(
        binary=_resolve_binary(metadata.get("claude_binary")),
        model=str(metadata.get("model", DEFAULT_MODEL)),
        permission_mode=str(metadata.get("permission_mode", DEFAULT_PERMISSION_MODE)),
        tools=str(metadata.get("tools", DEFAULT_TOOLS)),
        ready_deadline_seconds=float(metadata.get("ready_deadline_seconds", 30.0)),
        turn_deadline_seconds=float(metadata.get("turn_deadline_seconds", 60.0)),
        mcp_config_path=str(metadata["mcp_config_path"]) if metadata.get("mcp_config_path") else None,
        settings_path=str(metadata["claude_settings_path"])
        if metadata.get("claude_settings_path")
        else None,
        append_system_prompt_file=str(metadata["append_system_prompt_file"])
        if metadata.get("append_system_prompt_file") else None,
        allowed_tools=tuple(
            str(tool) for tool in metadata.get("claude_allowed_tools", ())
        ),
        cli_path=str(metadata["claude_cli_path"])
        if metadata.get("claude_cli_path") else None,
    )


def build_user_frame(content: str) -> Dict[str, Any]:
    return {"type": "user", "message": {"role": "user", "content": content}}


def is_system_init(frame: Mapping[str, Any]) -> bool:
    return frame.get("type") == "system" and frame.get("subtype") == "init"


def is_system_api_retry(frame: Mapping[str, Any]) -> bool:
    return frame.get("type") == "system" and frame.get("subtype") == "api_retry"


def is_assistant(frame: Mapping[str, Any]) -> bool:
    return frame.get("type") == "assistant"


def is_result(frame: Mapping[str, Any]) -> bool:
    return frame.get("type") == "result"


def result_is_success(frame: Mapping[str, Any]) -> bool:
    return frame.get("subtype") == "success" and not frame.get("is_error", False)


def extract_assistant_text(frame: Mapping[str, Any]) -> str:
    """Best-effort text extraction; never raises on unexpected shapes."""
    message = frame.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


# ── Bootstrap init sanitization ──

_HST_PFX = "mcp__starcoplay-hosted__"


def _hosted_tool_names(tools: Any) -> List[str]:
    """Return sorted names of tools whose name starts with ``_HST_PFX``."""
    if not isinstance(tools, list):
        return []
    names: List[str] = []
    for t in tools:
        name: Optional[str] = None
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            name = str(t["name"])
        elif isinstance(t, str):
            name = t
        if name is not None and name.startswith(_HST_PFX):
            names.append(name)
    return sorted(names)


def extract_startup_summary(init_frame: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract a narrow, sanitized startup summary from a bootstrap ``system/init`` frame.

    Only captures MCP server name/status and hosted-MCP tool
    count/names (from the **top-level** ``tools`` list, filtered by
    ``mcp__starcoplay-hosted__`` prefix).  Never retains credentials, env
    vars, raw config, arbitrary init fields, prompts, or secrets.

    Returns a dict safe for inclusion in ``ReadyResult.detail``.
    """
    summary: Dict[str, Any] = {}

    mcp_servers = init_frame.get("mcp_servers")
    if isinstance(mcp_servers, list):
        servers: List[Dict[str, Any]] = []
        for srv in mcp_servers:
            if not isinstance(srv, dict):
                continue
            entry: Dict[str, Any] = {}
            name = srv.get("name")
            if isinstance(name, str):
                entry["name"] = name
            status = srv.get("status")
            if isinstance(status, str):
                entry["status"] = status
            servers.append(entry)
        summary["mcp_servers"] = servers

    hosted_names = _hosted_tool_names(init_frame.get("tools"))
    summary["hosted_tool_count"] = len(hosted_names)
    summary["hosted_tool_names"] = hosted_names

    return summary


def hosted_mcp_connected(init_frame: Mapping[str, Any]) -> bool:
    """Return True if the init frame proves ``starcoplay-hosted`` is connected/healthy."""
    mcp_servers = init_frame.get("mcp_servers")
    if not isinstance(mcp_servers, list):
        return False
    for srv in mcp_servers:
        if not isinstance(srv, dict):
            continue
        if srv.get("name") != "starcoplay-hosted":
            continue
        status = srv.get("status")
        if isinstance(status, str) and status in ("connected", "healthy"):
            return True
    return False


def hosted_mcp_tool_count(init_frame: Mapping[str, Any]) -> int:
    """Return the number of hosted-MCP tools in the **top-level** ``tools`` list.

    Only counts names starting with ``mcp__starcoplay-hosted__``.
    Returns 0 when ``tools`` is absent or the prefix has no matches.
    """
    return len(_hosted_tool_names(init_frame.get("tools")))


# ── Stderr classification & secret redaction ──

# Markers that suggest a stderr line is MCP-related.
_STDERR_MCP_MARKERS = (
    "mcp error",
    "mcp server",
    "mcp connect",
    "mcp initialize",
    "starcoplay-hosted",
    "mcp_config",
    "mcp-config",
    "mcp tool",
)

# Secret-bearing patterns.  After a credential keyword + separator, the
# value is matched greedily to end-of-line (fail-closed: no secret may
# survive after a keyword).  Lines without any keyword are unchanged so
# normal MCP error summaries stay useful.
_SECRET_REDACT_RE = re.compile(
    r"(?:"
    r"(?:authorization|auth|token|api[_-]?key|secret|credential|password|"
    r"x-api-key|access[_-]?token|refresh[_-]?token)\s*[=:]\s*"
    r"(?:bearer\s+)?"
    r"|"
    r"bearer\s+"
    r")\S+(?:\s+\S+)*",
    re.IGNORECASE,
)

_STDERR_MAX_LENGTH = 400
_MAX_STDERR_DIAGNOSTICS = 20


def redact_stderr_secrets(line: str) -> str:
    """Apply secret redaction to an arbitrary stderr line.

    Safe to call on every stderr line before storage.  Returns the
    (possibly unchanged) line with secret-bearing substrings replaced.
    """
    return _SECRET_REDACT_RE.sub("***REDACTED***", line)


def classify_stderr_diagnostic(line: str) -> Optional[Dict[str, Any]]:
    """Return a sanitised diagnostic dict if *line* appears MCP-related; otherwise ``None``.

    The returned dict is bounded-length and has secret-bearing substrings
    redacted.  It is safe to surface in ``ProviderStatus.detail``.
    """
    lower = line.lower()
    if not any(marker in lower for marker in _STDERR_MCP_MARKERS):
        return None

    sanitised = _SECRET_REDACT_RE.sub("***REDACTED***", line)
    if len(sanitised) > _STDERR_MAX_LENGTH:
        sanitised = sanitised[:_STDERR_MAX_LENGTH] + "..."

    return {"classification": "mcp_stderr", "summary": sanitised}
