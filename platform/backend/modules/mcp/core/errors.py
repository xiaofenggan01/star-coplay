"""Stable, redacted MCP errors."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDescriptor:
    code: str
    retryable: bool


ERRORS = {
    "invalid_arguments": ErrorDescriptor("invalid_arguments", False),
    "capability_denied": ErrorDescriptor("capability_denied", False),
    "context_expired": ErrorDescriptor("context_expired", False),
    "binding_unavailable": ErrorDescriptor("binding_unavailable", True),
    "route_unavailable": ErrorDescriptor("route_unavailable", True),
    # A transport timeout leaves a mutating outcome uncertain. Callers must use
    # status convergence, never blind replay, so the generic classification is
    # deliberately non-retryable.
    "command_timeout": ErrorDescriptor("command_timeout", False),
    "command_cancelled": ErrorDescriptor("command_cancelled", False),
    "player_interrupted": ErrorDescriptor("player_interrupted", False),
    "conflict": ErrorDescriptor("conflict", False),
    "upstream_protocol_error": ErrorDescriptor("upstream_protocol_error", False),
    "internal_error": ErrorDescriptor("internal_error", True),
}


class McpError(Exception):
    def __init__(self, code: str, message: str):
        descriptor = ERRORS[code]
        super().__init__(message)
        self.code = descriptor.code
        self.retryable = descriptor.retryable
        self.safe_message = message

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "retryable": self.retryable, "message": self.safe_message}
