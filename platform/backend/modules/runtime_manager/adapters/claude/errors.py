"""Claude-adapter-private exceptions.

These subclass ``RuntimeManagerError`` so callers depending only on the
public contract can catch generically, but the concrete types (and the
Claude-specific detail they carry, e.g. ``result.subtype``) stay private to
this package -- ``contracts.py`` is not touched.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from modules.runtime_manager.contracts import RuntimeManagerError


class ClaudeTurnFailedError(RuntimeManagerError):
    """Raised when a Claude CLI turn ends with ``is_error`` or a non-success
    ``result`` subtype. Never raised on ``system/api_retry`` alone."""

    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.detail = dict(detail or {})


class ClaudeTurnTimeoutError(RuntimeManagerError):
    """Raised when no terminal ``result`` frame arrives before the deadline."""


class ClaudeResultMissingError(RuntimeManagerError):
    """Raised when Claude ends its assistant response but omits ``result``."""

    def __init__(self, message: str, *, assistant_text: str) -> None:
        super().__init__(message)
        self.assistant_text = assistant_text


class ClaudeProcessExitedError(RuntimeManagerError):
    """Raised when the Claude CLI process exits before yielding a result for
    the in-flight turn (includes bootstrap-ready turn)."""

    def __init__(self, message: str, *, returncode: Optional[int]) -> None:
        super().__init__(message)
        self.returncode = returncode


class ClaudeTransportError(RuntimeManagerError):
    """Raised when the Claude stdio transport fails while the process may
    still be alive. The process must not be reused after this boundary is
    lost."""
