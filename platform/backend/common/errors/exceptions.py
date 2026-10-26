from __future__ import annotations


class BackendError(Exception):
    """Base exception for all backend errors."""

    def __init__(self, message: str = "", *, code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.code = code
