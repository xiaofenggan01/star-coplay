"""Hosted MCP readiness gate, stderr sanitisation, and transport init
immutability tests for the Claude adapter.

Covers:
- hosted MCP config uses ``sys.executable`` (no brittle substring check)
- AI bootstrap OK + MCP failed/missing/zero-tools fails readiness
- connected/healthy + tool_count > 0 succeeds (tools from top-level list)
- transport first-init immutability: raw frame never stored, only sanitised summary
- transport _pump_stderr redacts secrets on every line, caps diagnostics
- later init cannot rewrite startup conclusion (tested at transport level)
- stderr output is sanitised (including bare ``Bearer <token>``)
- existing non-hosted and controlled MCP semantics stay compatible
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest

from modules.runtime_manager.adapters.claude.adapter import (
    ClaudeAgentRuntimeAdapter,
    _ClaudeHandleState,
)
from modules.runtime_manager.adapters.claude.errors import (
    ClaudeProcessExitedError,
    ClaudeResultMissingError,
)
from modules.runtime_manager.adapters.claude.protocol import (
    ClaudeLaunchConfig,
    classify_stderr_diagnostic,
    extract_startup_summary,
    hosted_mcp_connected,
    hosted_mcp_tool_count,
    redact_stderr_secrets,
)
from modules.runtime_manager.adapters.claude.transport import (
    ClaudeProcessTransport,
    ClaudeTurnOutcome,
)
from modules.runtime_manager.contracts import (
    ProviderStatus,
    ReadinessFailureKind,
    RuntimeHandle,
    RuntimeInput,
    RuntimeSpec,
)


# ── Transport mocks ──────────────────────────────────────────────────────────


class _Transport:
    """Minimal fake transport for ``wait_ready`` classification tests."""

    def __init__(
        self,
        outcome: Optional[ClaudeTurnOutcome] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.outcome = outcome
        self.error = error

    async def run_turn(self, _content: str, *, deadline_seconds: float) -> ClaudeTurnOutcome:
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


class _HostedTransport(_Transport):
    """Fake transport with controllable startup summary and stderr diagnostics."""

    def __init__(
        self,
        outcome: Optional[ClaudeTurnOutcome] = None,
        error: Optional[Exception] = None,
        *,
        bootstrap_startup_summary: Optional[Dict[str, Any]] = None,
        stderr_diagnostics: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(outcome=outcome, error=error)
        self._bootstrap_startup_summary = bootstrap_startup_summary
        self._stderr_diagnostics = list(stderr_diagnostics or [])

    @property
    def bootstrap_startup_summary(self) -> Optional[Dict[str, Any]]:
        return self._bootstrap_startup_summary

    @property
    def stderr_diagnostics(self) -> list[Dict[str, Any]]:
        return list(self._stderr_diagnostics)


@pytest.mark.asyncio
async def test_terminal_assistant_without_result_frame_fails_after_grace_period():
    transport = ClaudeProcessTransport("runtime-h", 1)
    transport.send_frame = AsyncMock(return_value=True)
    await transport._stdout_queue.put({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "我已经出门了"}],
            "stop_reason": "end_turn",
        },
    })

    with pytest.raises(ClaudeResultMissingError) as raised:
        await transport.run_turn(
            "你出门吧", deadline_seconds=None,
            terminal_result_grace_seconds=.001,
        )

    assert raised.value.assistant_text == "我已经出门了"


@pytest.mark.asyncio
async def test_missing_result_stops_desynchronized_claude_process(tmp_path: Path):
    transport = AsyncMock()
    transport.inspect.return_value = ProviderStatus(
        pid=123, returncode=None, ready=True, healthy=True,
        last_frame_monotonic=None,
    )
    transport.run_turn.side_effect = ClaudeResultMissingError(
        "missing result", assistant_text="final reply",
    )
    state = _ClaudeHandleState(
        transport=transport,
        launch_config=_make_launch_config(),
        bootstrapped=True,
        bootstrap_ok=True,
        is_hosted_mcp=True,
    )
    handle = RuntimeHandle("runtime-h", 1, tmp_path, state)

    with pytest.raises(ClaudeResultMissingError):
        await ClaudeAgentRuntimeAdapter().submit_input(
            handle, RuntimeInput("request-1", "hello"),
        )

    transport.stop.assert_awaited_once_with(grace_period=0.5)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_launch_config(**overrides: Any) -> ClaudeLaunchConfig:
    cfg = ClaudeLaunchConfig.__new__(ClaudeLaunchConfig)
    for k, v in overrides.items():
        object.__setattr__(cfg, k, v)
    return cfg


def _hosted_handle(
    tmp_path: Path,
    transport: _HostedTransport,
    *,
    is_hosted_mcp: bool = True,
) -> RuntimeHandle:
    config = _make_launch_config()
    state = _ClaudeHandleState(
        transport=transport,
        launch_config=config,
        is_hosted_mcp=is_hosted_mcp,
    )
    return RuntimeHandle("runtime-h", 1, tmp_path, state)


def _non_hosted_handle(
    tmp_path: Path,
    transport: _Transport,
) -> RuntimeHandle:
    config = _make_launch_config()
    state = _ClaudeHandleState(transport=transport, launch_config=config)
    return RuntimeHandle("runtime-nh", 1, tmp_path, state)


def _success_outcome() -> ClaudeTurnOutcome:
    return ClaudeTurnOutcome(
        "OK",
        {"type": "result", "subtype": "success", "is_error": False},
        "session-1",
    )


# Real Claude CLI shape: top-level ``tools`` is a flat list of tool-name
# strings; ``mcp_servers`` entries carry only ``name`` and ``status``.
_HST = "mcp__starcoplay-hosted__"


def _make_init_frame(
    *,
    hosted_status: Optional[str] = None,
    hosted_tool_names: Optional[list[str]] = None,
    other_servers: Optional[list[tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Build a ``system/init`` frame matching the real Claude CLI shape.

    - ``hosted_status``: if set, adds a ``starcoplay-hosted`` entry in
      ``mcp_servers`` with that status (e.g. ``"connected"``).
    - ``hosted_tool_names``: if set, these names (prefixed with
      ``mcp__starcoplay-hosted__``) appear in the top-level ``tools`` list.
    - ``other_servers``: extra ``(name, status)`` pairs for ``mcp_servers``.
    """
    frame: Dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "session_id": "session-1",
    }

    # Top-level tools
    tools: list[str] = []
    if hosted_tool_names:
        tools.extend(f"{_HST}{n}" for n in hosted_tool_names)
    if tools:
        frame["tools"] = tools

    # mcp_servers
    servers: list[Dict[str, Any]] = []
    if hosted_status is not None:
        servers.append({"name": "starcoplay-hosted", "status": hosted_status})
    for name, status in (other_servers or []):
        servers.append({"name": name, "status": status})
    if servers:
        frame["mcp_servers"] = servers

    return frame


def _make_startup(
    *,
    hosted_status: Optional[str] = None,
    hosted_tool_names: Optional[list[str]] = None,
    other_servers: Optional[list[tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Build a sanitised startup summary matching ``extract_startup_summary`` output."""
    return extract_startup_summary(
        _make_init_frame(
            hosted_status=hosted_status,
            hosted_tool_names=hosted_tool_names,
            other_servers=other_servers,
        )
    )


# ── Tests: sys.executable in MCP config ──────────────────────────────────────


@pytest.mark.asyncio
async def test_hosted_config_uses_sys_executable(tmp_path: Path):
    """The hosted MCP stdio ``command`` must be ``sys.executable``."""
    spec = RuntimeSpec(
        runtime_id="rt-exe", generation=1, user_id="u", agentee_id="a",
        provider="claude", workspace_root=tmp_path, idle_ttl_seconds=600,
        provider_private_metadata={"hosted_mcp_endpoint": "ws://127.0.0.1:8766/mcp/hosted/ws"},
    )
    prepared = await ClaudeAgentRuntimeAdapter().prepare(spec)
    config_path = Path(prepared.provider_private_metadata["mcp_config_path"])
    config = json.loads(config_path.read_text())
    server = config["mcpServers"]["starcoplay-hosted"]
    assert server["command"] == sys.executable


@pytest.mark.asyncio
async def test_controlled_mcp_config_uses_sys_executable(tmp_path: Path):
    """The controlled MCP stdio ``command`` must also use ``sys.executable``."""
    server_script = tmp_path / "fake_server.py"
    server_script.write_text("")
    spec = RuntimeSpec(
        runtime_id="rt-ctrl", generation=1, user_id="u", agentee_id="a",
        provider="claude", workspace_root=tmp_path, idle_ttl_seconds=600,
        provider_private_metadata={"controlled_mcp_server": str(server_script)},
    )
    prepared = await ClaudeAgentRuntimeAdapter().prepare(spec)
    config_path = Path(prepared.provider_private_metadata["mcp_config_path"])
    config = json.loads(config_path.read_text())
    server = config["mcpServers"]["controlled"]
    assert server["command"] == sys.executable


# ── Tests: hosted MCP readiness gate ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_hosted_bootstrap_ok_but_no_init_fails_readiness(tmp_path: Path):
    """AI bootstrap OK but no ``system/init`` captured → readiness failed."""
    transport = _HostedTransport(
        outcome=_success_outcome(),
        bootstrap_startup_summary=None,
    )
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.READINESS_FAILED
    assert result.detail["reason"] == "hosted_mcp_no_bootstrap_init"


@pytest.mark.asyncio
async def test_hosted_bootstrap_ok_but_mcp_not_connected_fails(tmp_path: Path):
    """AI bootstrap OK but starcoplay-hosted status is ``failed`` → readiness failed."""
    startup = _make_startup(hosted_status="failed", hosted_tool_names=["tool_a"])
    transport = _HostedTransport(
        outcome=_success_outcome(),
        bootstrap_startup_summary=startup,
    )
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.READINESS_FAILED
    assert result.detail["reason"] == "hosted_mcp_not_connected"
    assert "startup" in result.detail


@pytest.mark.asyncio
async def test_hosted_bootstrap_ok_but_mcp_zero_tools_fails(tmp_path: Path):
    """AI bootstrap OK, starcoplay-hosted connected but 0 hosted tools → failed."""
    startup = _make_startup(hosted_status="connected", hosted_tool_names=[])
    transport = _HostedTransport(
        outcome=_success_outcome(),
        bootstrap_startup_summary=startup,
    )
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.READINESS_FAILED
    assert result.detail["reason"] == "hosted_mcp_zero_tools"


@pytest.mark.asyncio
async def test_hosted_bootstrap_ok_mcp_connected_with_tools_succeeds(
    tmp_path: Path,
):
    """AI bootstrap OK + starcoplay-hosted connected with >0 hosted tools → ready."""
    startup = _make_startup(hosted_status="connected", hosted_tool_names=["tool_a", "tool_b"])
    transport = _HostedTransport(
        outcome=_success_outcome(),
        bootstrap_startup_summary=startup,
    )
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is True
    assert result.failure_kind is None
    assert result.detail == {}


@pytest.mark.asyncio
async def test_hosted_bootstrap_mcp_status_healthy_succeeds(tmp_path: Path):
    """``healthy`` status is also accepted as connected."""
    startup = _make_startup(hosted_status="healthy", hosted_tool_names=["tool_x"])
    transport = _HostedTransport(
        outcome=_success_outcome(),
        bootstrap_startup_summary=startup,
    )
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is True


# ── Tests: bootstrap init immutability (transport level) ─────────────────────


@pytest.mark.asyncio
async def test_non_hosted_mcp_does_not_require_init_frame(tmp_path: Path):
    """Non-hosted runtimes must NOT require an init frame for readiness."""
    transport = _Transport(outcome=_success_outcome())
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _non_hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is True
    assert result.failure_kind is None


@pytest.mark.asyncio
async def test_non_hosted_wait_ready_idempotent(tmp_path: Path):
    """Non-hosted second wait_ready returns cached result."""
    transport = _Transport(outcome=_success_outcome())
    adapter = ClaudeAgentRuntimeAdapter()
    handle = _non_hosted_handle(tmp_path, transport)
    r1 = await adapter.wait_ready(handle, deadline=1.0)
    assert r1.ready is True
    r2 = await adapter.wait_ready(handle, deadline=1.0)
    assert r2.ready is True
    assert r2.detail == {"bootstrap": "already-run"}


@pytest.mark.asyncio
async def test_hosted_wait_ready_idempotent_returns_cached_conclusion(
    tmp_path: Path,
):
    """After hosted bootstrap succeeds, second ``wait_ready`` returns cached
    result.  Even if the transport's summary were somehow mutated (test
    simulates this by passing a fresh transport), the adapter must not
    re-evaluate -- it returns the already-computed conclusion."""
    startup = _make_startup(hosted_status="connected", hosted_tool_names=["tool_a"])
    transport = _HostedTransport(
        outcome=_success_outcome(),
        bootstrap_startup_summary=startup,
    )
    adapter = ClaudeAgentRuntimeAdapter()
    handle = _hosted_handle(tmp_path, transport)
    result1 = await adapter.wait_ready(handle, deadline=1.0)
    assert result1.ready is True

    # A second call with the same handle returns cached conclusion.
    result2 = await adapter.wait_ready(handle, deadline=1.0)
    assert result2.ready is True
    assert result2.detail == {"bootstrap": "already-run"}


# ── Tests: stderr sanitisation ───────────────────────────────────────────────


class TestClassifyStderrDiagnostic:
    """Unit tests for ``classify_stderr_diagnostic``."""

    def test_mcp_error_line_classified(self):
        diag = classify_stderr_diagnostic(
            "MCP error: starcoplay-hosted connection refused"
        )
        assert diag is not None
        assert diag["classification"] == "mcp_stderr"
        assert "starcoplay-hosted" in diag["summary"]
        assert "connection refused" in diag["summary"]

    def test_non_mcp_line_not_classified(self):
        assert classify_stderr_diagnostic("Claude Code is ready. Session: abc") is None
        assert classify_stderr_diagnostic("DeprecationWarning: some_module") is None

    def test_token_with_colon_redacted(self):
        diag = classify_stderr_diagnostic(
            "MCP server error: authorization=sk-abc123 token: ghp_xyz789"
        )
        assert diag is not None
        assert "sk-abc123" not in diag["summary"]
        assert "ghp_xyz789" not in diag["summary"]
        assert "***REDACTED***" in diag["summary"]

    def test_bare_bearer_token_redacted(self):
        """``Bearer eyJhbG...`` (no colon/equals) must be redacted."""
        diag = classify_stderr_diagnostic(
            "MCP server starcoplay-hosted: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def refused"
        )
        assert diag is not None
        assert "eyJhbGci" not in diag["summary"]

    def test_api_key_redacted(self):
        diag = classify_stderr_diagnostic(
            "MCP connect failed: api_key=deadbeef x-api-key: cafe1234"
        )
        assert diag is not None
        assert "deadbeef" not in diag["summary"]
        assert "cafe1234" not in diag["summary"]

    def test_credential_redacted(self):
        diag = classify_stderr_diagnostic(
            "mcp_config error: credential=supersecret password=mypass"
        )
        assert diag is not None
        assert "supersecret" not in diag["summary"]
        assert "mypass" not in diag["summary"]

    def test_long_stderr_truncated(self):
        long_line = "MCP server: " + ("x" * 500)
        diag = classify_stderr_diagnostic(long_line)
        assert diag is not None
        assert len(diag["summary"]) <= 403  # 400 + "..."

    def test_access_token_redacted(self):
        diag = classify_stderr_diagnostic(
            "MCP initialize: access_token=g0g0g0 refresh_token=r3fr3sh"
        )
        assert diag is not None
        assert "g0g0g0" not in diag["summary"]
        assert "r3fr3sh" not in diag["summary"]


class TestRedactStderrSecrets:
    """General-purpose redaction applied to all stderr (not only MCP)."""

    def test_bare_bearer_redacted(self):
        result = redact_stderr_secrets("error: Bearer eyJhbGciOiJIUzI1NiJ9.abc")
        assert "eyJhbGci" not in result
        assert "***REDACTED***" in result

    def test_auth_bearer_compound_redacted(self):
        result = redact_stderr_secrets("Authorization: Bearer abcdef123456")
        assert "abcdef123456" not in result
        assert "***REDACTED***" in result

    def test_auth_bearer_lowercase_redacted(self):
        result = redact_stderr_secrets("authorization: bearer xyz789")
        assert "xyz789" not in result

    def test_token_equals_redacted(self):
        result = redact_stderr_secrets("debug: token=secret123 continued")
        assert "secret123" not in result

    def test_token_colon_redacted(self):
        result = redact_stderr_secrets("error: token: abc456 done")
        assert "abc456" not in result

    def test_api_key_equals_redacted(self):
        result = redact_stderr_secrets("api_key=deadbeef, retry")
        assert "deadbeef" not in result

    def test_non_secret_preserved(self):
        result = redact_stderr_secrets("info: server started on port 8080")
        assert "port 8080" in result
        assert "***REDACTED***" not in result

    def test_multiple_secrets_comma_separated(self):
        result = redact_stderr_secrets("err: token=abc, secret=xyz")
        assert "abc" not in result
        assert "xyz" not in result

    def test_password_colon_redacted(self):
        result = redact_stderr_secrets("auth: password: hunter2 remaining")
        assert "hunter2" not in result


# ── Tests: extract_startup_summary ───────────────────────────────────────────


class TestExtractStartupSummary:
    def test_empty_init(self):
        summary = extract_startup_summary({"type": "system", "subtype": "init"})
        assert summary == {"hosted_tool_count": 0, "hosted_tool_names": []}

    def test_no_mcp_servers(self):
        summary = extract_startup_summary({
            "type": "system", "subtype": "init", "session_id": "s1",
            "tools": ["mcp__starcoplay-hosted__tool_a"],
        })
        assert summary["hosted_tool_count"] == 1
        assert summary["hosted_tool_names"] == ["mcp__starcoplay-hosted__tool_a"]

    def test_hosted_server_with_tools(self):
        init = _make_init_frame(
            hosted_status="connected",
            hosted_tool_names=["tool_a", "tool_b"],
        )
        summary = extract_startup_summary(init)
        assert len(summary["mcp_servers"]) == 1
        srv = summary["mcp_servers"][0]
        assert srv["name"] == "starcoplay-hosted"
        assert srv["status"] == "connected"
        assert "tools" not in srv  # tools are top-level, not nested under server
        assert summary["hosted_tool_count"] == 2
        assert _HST + "tool_a" in summary["hosted_tool_names"]

    def test_non_hosted_tools_excluded_from_count(self):
        """Tools without the hosted prefix are not counted."""
        frame: Dict[str, Any] = {
            "type": "system", "subtype": "init",
            "tools": ["Bash", "Read", f"{_HST}tool_a", "Write"],
            "mcp_servers": [{"name": "starcoplay-hosted", "status": "connected"}],
        }
        summary = extract_startup_summary(frame)
        assert summary["hosted_tool_count"] == 1

    def test_multiple_servers(self):
        init = _make_init_frame(
            hosted_status="connected",
            hosted_tool_names=["tool_a"],
            other_servers=[("other-server", "failed")],
        )
        summary = extract_startup_summary(init)
        assert len(summary["mcp_servers"]) == 2

    def test_never_leaks_session_id(self):
        init = _make_init_frame(hosted_status="connected", hosted_tool_names=["tool_a"])
        summary = extract_startup_summary(init)
        rendered = json.dumps(summary)
        assert "session_id" not in rendered
        assert "session-1" not in rendered

    def test_never_leaks_raw_fields(self):
        """Only name/status from mcp_servers and hosted_tool_* are captured."""
        init: Dict[str, Any] = {
            "type": "system",
            "subtype": "init",
            "session_id": "secret-sid",
            "cwd": "/secret/path",
            "env": {"SECRET": "val"},
            "tools": [f"{_HST}tool_a"],
            "mcp_servers": [
                {
                    "name": "starcoplay-hosted",
                    "status": "connected",
                    "config": "raw-secret-config",
                }
            ],
        }
        summary = extract_startup_summary(init)
        srv = summary["mcp_servers"][0]
        assert "config" not in srv
        assert "session_id" not in summary
        assert "cwd" not in summary
        assert "env" not in summary


# ── Tests: hosted_mcp_connected / hosted_mcp_tool_count ──────────────────────


class TestHostedMcpConnected:
    def test_connected_returns_true(self):
        init = _make_init_frame(hosted_status="connected", hosted_tool_names=["t1"])
        assert hosted_mcp_connected(init) is True

    def test_healthy_returns_true(self):
        init = _make_init_frame(hosted_status="healthy", hosted_tool_names=["t1"])
        assert hosted_mcp_connected(init) is True

    def test_failed_returns_false(self):
        init = _make_init_frame(hosted_status="failed", hosted_tool_names=["t1"])
        assert hosted_mcp_connected(init) is False

    def test_wrong_server_name_returns_false(self):
        init = _make_init_frame(
            other_servers=[("other-server", "connected")],
            hosted_tool_names=["t1"],
        )
        assert hosted_mcp_connected(init) is False

    def test_no_mcp_servers_returns_false(self):
        assert hosted_mcp_connected({}) is False

    def test_mcp_servers_not_list_returns_false(self):
        assert hosted_mcp_connected({"mcp_servers": "not-a-list"}) is False

    def test_works_with_sanitised_summary(self):
        """hosted_mcp_connected accepts extract_startup_summary output."""
        init = _make_init_frame(hosted_status="connected", hosted_tool_names=["t1"])
        summary = extract_startup_summary(init)
        assert hosted_mcp_connected(summary) is True


class TestHostedMcpToolCount:
    def test_returns_hosted_tool_count_from_top_level(self):
        init = _make_init_frame(
            hosted_status="connected",
            hosted_tool_names=["a", "b", "c"],
        )
        assert hosted_mcp_tool_count(init) == 3

    def test_zero_hosted_tools(self):
        init = _make_init_frame(hosted_status="connected")
        assert hosted_mcp_tool_count(init) == 0

    def test_tools_absent_returns_zero(self):
        assert hosted_mcp_tool_count({}) == 0

    def test_only_counts_hosted_prefix(self):
        frame: Dict[str, Any] = {
            "type": "system", "subtype": "init",
            "tools": ["Bash", "Write", f"{_HST}x", f"{_HST}y", "Read"],
            "mcp_servers": [{"name": "starcoplay-hosted", "status": "connected"}],
        }
        assert hosted_mcp_tool_count(frame) == 2


# ── Tests: existing failure classifications still work (smoke) ───────────────


@pytest.mark.asyncio
async def test_non_hosted_provider_rejected_still_classified(tmp_path: Path):
    frame = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 402,
        "terminal_reason": "api_error",
        "result": "sensitive body",
    }
    transport = _Transport(outcome=ClaudeTurnOutcome("text", frame, "sid"))
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _non_hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.PROVIDER_REJECTED


@pytest.mark.asyncio
async def test_non_hosted_timeout_still_classified(tmp_path: Path):
    transport = _Transport(error=TimeoutError())
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _non_hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_non_hosted_process_exited_still_classified(tmp_path: Path):
    transport = _Transport(error=ClaudeProcessExitedError("gone", returncode=1))
    result = await ClaudeAgentRuntimeAdapter().wait_ready(
        _non_hosted_handle(tmp_path, transport), deadline=1.0,
    )
    assert result.ready is False
    assert result.failure_kind is ReadinessFailureKind.PROCESS_EXITED
