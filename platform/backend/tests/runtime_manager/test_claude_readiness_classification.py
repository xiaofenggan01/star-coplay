from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from modules.runtime_manager.adapters.claude.adapter import (
    ClaudeAgentRuntimeAdapter,
    _ClaudeHandleState,
)
from modules.runtime_manager.adapters.claude.errors import ClaudeProcessExitedError
from modules.runtime_manager.adapters.claude.protocol import ClaudeLaunchConfig
from modules.runtime_manager.adapters.claude.transport import ClaudeTurnOutcome
from modules.runtime_manager.contracts import (
    ReadinessFailureKind,
    RuntimeHandle,
)


class _Transport:
    def __init__(self, outcome=None, error=None, *, log_lines=(), returncode=None):
        self.outcome = outcome
        self.error = error
        self.log_lines = list(log_lines)
        self.returncode = returncode

    async def run_turn(self, _content, *, deadline_seconds):
        if self.error is not None:
            raise self.error
        return self.outcome


def _handle(tmp_path: Path, transport: _Transport) -> RuntimeHandle:
    # The launch config is not consulted by wait_ready; constructing through
    # __new__ keeps this test independent of Claude CLI configuration parsing.
    config = ClaudeLaunchConfig.__new__(ClaudeLaunchConfig)
    state = _ClaudeHandleState(transport=transport, launch_config=config)
    return RuntimeHandle("runtime-1", 1, tmp_path, state)


@pytest.mark.asyncio
async def test_claude_402_bootstrap_rejection_is_safely_classified(tmp_path):
    frame = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 402,
        "terminal_reason": "api_error",
        "result": "sensitive provider response must not cross the boundary",
        "token": "secret-token",
    }
    outcome = ClaudeTurnOutcome("sensitive assistant text", frame, "provider-session")

    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _handle(tmp_path, _Transport(outcome=outcome)), deadline=1.0
    )

    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.PROVIDER_REJECTED
    assert result.detail == {"failure": "provider_rejected"}
    rendered = repr(result)
    assert "402" not in rendered
    assert "sensitive" not in rendered
    assert "secret-token" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "failure_kind"),
    [
        (asyncio.TimeoutError("sensitive timeout body"), ReadinessFailureKind.TIMEOUT),
        (
            ClaudeProcessExitedError("sensitive stderr", returncode=17),
            ReadinessFailureKind.PROCESS_EXITED,
        ),
    ],
)
async def test_claude_bootstrap_transport_failures_are_distinct_and_sanitized(
    tmp_path, error, failure_kind,
):
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _handle(tmp_path, _Transport(error=error)), deadline=1.0
    )

    assert result.ready is False
    assert result.failure_kind is failure_kind
    assert result.detail == {"failure": failure_kind.value}
    assert "sensitive" not in repr(result)


@pytest.mark.asyncio
async def test_claude_non_provider_bootstrap_failure_is_generic(tmp_path):
    outcome = ClaudeTurnOutcome(
        "", {"type": "result", "subtype": "error", "is_error": True}, None
    )
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _handle(tmp_path, _Transport(outcome=outcome)), deadline=1.0
    )
    assert result.failure_kind is ReadinessFailureKind.READINESS_FAILED
    assert result.detail == {"failure": "readiness_failed"}


@pytest.mark.asyncio
async def test_process_exit_logs_only_bounded_sanitized_stderr(tmp_path, caplog):
    transport = _Transport(
        error=ClaudeProcessExitedError("raw provider failure", returncode=2),
        returncode=2,
        log_lines=[
            {"stream": "stderr", "line": "safe diagnostic"},
            {"stream": "stdout", "protocol_error": True, "raw": "safe stdout diagnostic"},
        ],
    )
    with caplog.at_level("WARNING"):
        await ClaudeAgentRuntimeAdapter().wait_ready(
            _handle(tmp_path, transport), deadline=1.0
        )
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "runtime_manager.claude.readiness.process_exited"
    )
    assert record.returncode == 2
    assert record.diagnostic_tail == ["safe diagnostic", "safe stdout diagnostic"]
    assert "raw provider failure" not in caplog.text
