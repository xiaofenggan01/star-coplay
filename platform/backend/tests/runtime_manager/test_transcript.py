"""Tests for transcript reading, async input acceptance, and output API.

Covers:
- Native Claude JSONL parsing with parentUuid/message.model keys
- sessionId-based file location and ownership validation
- HTTP 202 receipt on input acceptance
- Background Turn execution
- Allowlist filtering (thinking, tool_use, tool_result excluded)
- Byte-offset cursor pagination
- Receipt cursor and since+limit queries
- Fail-closed on unknown frames, unsafe content, wrong session
- Path isolation (no cross-runtime/generation leaks)
- Full InputReceipt identity validation in read_output
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modules.runtime_manager.contracts import (
    InputReceipt,
    OutputPage,
    OutputQuery,
    PreparedRuntime,
    ProviderStatus,
    ReadyResult,
    RuntimeHandle,
    RuntimeSpec,
    StopResult,
    TranscriptEntry,
    TurnResult,
)
from modules.runtime_manager.transcript import TranscriptReader


# ── Helpers ──


def _write_native_transcript(projects_dir: Path, session_id: str, lines: list[dict]) -> Path:
    """Write Claude-native JSONL under *projects_dir* matching *session_id*."""
    projects_dir.mkdir(parents=True, exist_ok=True)
    file_path = projects_dir / f"{session_id}.jsonl"
    content = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"
    file_path.write_text(content, encoding="utf-8")
    return file_path


# ── TranscriptReader unit tests ──


class TestTranscriptReader:
    """Unit tests for the native Claude JSONL allowlist-based parser."""

    def test_empty_dir_returns_error(self, tmp_path: Path):
        projects = tmp_path / "projects"
        projects.mkdir()
        reader = TranscriptReader(projects, "no-such-session")
        with pytest.raises(FileNotFoundError):
            reader.query(OutputQuery())

    def test_no_matching_session_file(self, tmp_path: Path):
        projects = tmp_path / "projects"
        _write_native_transcript(projects, "other-session", [
            {"type": "user", "sessionId": "other-session",
             "message": {"role": "user", "content": "Hi"}},
        ])
        reader = TranscriptReader(projects, "my-session")
        with pytest.raises(FileNotFoundError):
            reader.query(OutputQuery())

    def test_user_and_assistant_text_surfaced(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-001"
        user_uuid = "u-001"
        assistant_uuid = "a-001"
        ts = "2026-07-18T10:00:00+00:00"
        _write_native_transcript(projects, session_id, [
            {"type": "system", "subtype": "init", "session_id": session_id},
            {
                "type": "user",
                "sessionId": session_id,
                "uuid": user_uuid,
                "timestamp": ts,
                "message": {"role": "user", "content": "Hello, what is the weather?"},
            },
            {
                "type": "assistant",
                "sessionId": session_id,
                "uuid": assistant_uuid,
                "parentUuid": user_uuid,
                "timestamp": "2026-07-18T10:00:05+00:00",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [
                        {"type": "text", "text": "The weather is sunny today."},
                    ],
                },
            },
            {"type": "result", "subtype": "success", "sessionId": session_id},
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 2
        assert page.entries[0].role == "user"
        assert page.entries[0].content == "Hello, what is the weather?"
        assert page.entries[0].uuid == user_uuid
        assert page.entries[0].parent_uuid is None
        assert page.entries[0].timestamp is not None
        # model is None for user messages (no message.model)
        assert page.entries[0].model is None
        assert page.entries[1].role == "assistant"
        assert page.entries[1].content == "The weather is sunny today."
        assert page.entries[1].uuid == assistant_uuid
        assert page.entries[1].parent_uuid == user_uuid
        # model from message.model (nested), not top-level
        assert page.entries[1].model == "claude-sonnet-5"

    def test_parent_uuid_camel_case_key_used(self, tmp_path: Path):
        """Prove that only parentUuid (camelCase) is extracted, not parent_uuid."""
        projects = tmp_path / "projects"
        session_id = "s-camel"
        _write_native_transcript(projects, session_id, [
            {
                "type": "assistant",
                "sessionId": session_id,
                "uuid": "a-1",
                "parent_uuid": "snake-case-should-be-ignored",
                "parentUuid": "camel-case-wins",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-8",
                    "content": [{"type": "text", "text": "Ok"}],
                },
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        # parentUuid (camelCase) is the native Claude key
        assert page.entries[0].parent_uuid == "camel-case-wins"
        assert page.entries[0].model == "claude-opus-4-8"

    def test_top_level_model_ignored(self, tmp_path: Path):
        """Prove that top-level 'model' is NOT used; only message.model counts."""
        projects = tmp_path / "projects"
        session_id = "s-model"
        _write_native_transcript(projects, session_id, [
            {
                "type": "assistant",
                "sessionId": session_id,
                "uuid": "a-1",
                "model": "wrong-top-level-model",
                "message": {
                    "role": "assistant",
                    "model": "claude-haiku-4-5",
                    "content": [{"type": "text", "text": "Ok"}],
                },
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].model == "claude-haiku-4-5"

    def test_wrong_session_id_records_skipped(self, tmp_path: Path):
        """Records with a different sessionId are silently skipped (fail-closed)."""
        projects = tmp_path / "projects"
        session_id = "my-session"
        _write_native_transcript(projects, session_id, [
            {
                "type": "user",
                "sessionId": "other-session",
                "message": {"role": "user", "content": "Should be skipped"},
            },
            {
                "type": "user",
                "sessionId": session_id,
                "message": {"role": "user", "content": "My content"},
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "My content"

    def test_missing_session_id_record_is_skipped(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "my-session"
        _write_native_transcript(projects, session_id, [
            {"type": "assistant", "message": {"role": "assistant", "content": "unsafe"}},
            {"type": "assistant", "sessionId": session_id,
             "message": {"role": "assistant", "content": "safe"}},
        ])
        page = TranscriptReader(projects, session_id).query(OutputQuery())
        assert [entry.content for entry in page.entries] == ["safe"]

    def test_cursor_beyond_native_eof_fails_closed(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "my-session"
        path = _write_native_transcript(projects, session_id, [
            {"type": "user", "sessionId": session_id,
             "message": {"role": "user", "content": "hello"}},
        ])
        with pytest.raises(ValueError, match="exceeds transcript EOF"):
            TranscriptReader(projects, session_id).query(
                OutputQuery(receipt=str(path.stat().st_size + 1))
            )

    def test_thinking_blocks_excluded(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {
                "type": "assistant",
                "sessionId": session_id,
                "uuid": "a-1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Let me think about this..."},
                        {"type": "text", "text": "The answer is 42."},
                        {"type": "thinking", "thinking": "Actually, that's correct."},
                    ],
                },
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "The answer is 42."

    def test_tool_use_excluded_tool_result_user_skipped(self, tmp_path: Path):
        """Tool-use blocks are stripped from assistant messages.
        User messages whose content is only tool_result blocks
        produce empty visible text and are skipped entirely."""
        projects = tmp_path / "projects"
        session_id = "s-tools"
        _write_native_transcript(projects, session_id, [
            {
                "type": "assistant",
                "sessionId": session_id,
                "uuid": "a-1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {"type": "tool_use", "id": "t1", "name": "read", "input": {}},
                        {"type": "text", "text": "Done checking."},
                    ],
                },
            },
            {
                "type": "user",
                "sessionId": session_id,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "secret data"},
                    ],
                },
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        # Assistant: "Let me check." + "Done checking." = 1 visible entry
        # User: tool_result blocks are not "text" → empty string → skipped
        assert len(page.entries) == 1
        assert page.entries[0].content == "Let me check.Done checking."

    def test_system_init_and_result_frames_excluded(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {"type": "system", "subtype": "init", "session_id": session_id, "sessionId": session_id},
            {"type": "result", "subtype": "success", "is_error": False, "sessionId": session_id},
            {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "Hi"}},
            {"type": "system", "sessionId": session_id, "subtype": "api_retry"},
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].role == "user"

    def test_unknown_frame_types_ignored_fail_closed(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {"type": "unknown_new_type", "sessionId": session_id, "secret": "leaked"},
            {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "Safe"}},
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "Safe"

    def test_unparseable_lines_skipped(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        file_path = _write_native_transcript(projects, session_id, [])
        # Append unparseable + valid lines
        with open(str(file_path), "a", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps({
                "type": "user", "sessionId": session_id,
                "message": {"role": "user", "content": "OK"},
            }) + "\n")
            f.write("{broken\n")
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "OK"

    def test_byte_offset_cursor_pagination(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-paged"
        lines = []
        for i in range(25):
            lines.append({
                "type": "user",
                "sessionId": session_id,
                "uuid": f"u-{i}",
                "message": {"role": "user", "content": f"Message {i}"},
            })
        _write_native_transcript(projects, session_id, lines)
        reader = TranscriptReader(projects, session_id)

        # First page
        page1 = reader.query(OutputQuery(limit=10))
        assert len(page1.entries) == 10
        assert page1.entries[0].content == "Message 0"
        assert page1.has_more is True
        assert page1.next_cursor is not None

        # Second page using byte-offset cursor
        page2 = reader.query(OutputQuery(receipt=page1.next_cursor, limit=10))
        assert len(page2.entries) == 10
        assert page2.entries[0].content == "Message 10"
        assert page2.has_more is True

        # Last page
        page3 = reader.query(OutputQuery(receipt=page2.next_cursor, limit=10))
        assert len(page3.entries) == 5
        assert page3.has_more is False
        assert page3.next_cursor is None

    def test_since_timestamp_filter(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-since"
        _write_native_transcript(projects, session_id, [
            {
                "type": "user", "sessionId": session_id, "uuid": "u-1",
                "timestamp": "2026-07-18T10:00:00+00:00",
                "message": {"role": "user", "content": "Early"},
            },
            {
                "type": "user", "sessionId": session_id, "uuid": "u-2",
                "timestamp": "2026-07-18T10:05:00+00:00",
                "message": {"role": "user", "content": "On time"},
            },
            {
                "type": "user", "sessionId": session_id, "uuid": "u-3",
                "timestamp": "2026-07-18T10:10:00+00:00",
                "message": {"role": "user", "content": "Late"},
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery(since="2026-07-18T10:05:00+00:00", limit=50))
        assert len(page.entries) == 2
        assert page.entries[0].content == "On time"
        assert page.entries[1].content == "Late"

    def test_invalid_receipt_raises(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "Hi"}},
        ])
        reader = TranscriptReader(projects, session_id)
        with pytest.raises(ValueError):
            reader.query(OutputQuery(receipt="not-a-number"))

    def test_negative_cursor_raises(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "Hi"}},
        ])
        reader = TranscriptReader(projects, session_id)
        with pytest.raises(ValueError):
            reader.query(OutputQuery(receipt="-1"))

    def test_file_not_found_raises(self, tmp_path: Path):
        reader = TranscriptReader(tmp_path / "nonexistent", "no-session")
        with pytest.raises(FileNotFoundError):
            reader.query(OutputQuery())

    def test_limit_bounds(self):
        with pytest.raises(ValueError):
            OutputQuery(limit=0)
        with pytest.raises(ValueError):
            OutputQuery(limit=201)
        q = OutputQuery(limit=1)
        assert q.limit == 1
        q = OutputQuery(limit=200)
        assert q.limit == 200

    def test_empty_user_message_skipped(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": ""}},
            {"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "Real"}},
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "Real"

    def test_assistant_with_only_tool_use_skipped(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {
                "type": "assistant",
                "sessionId": session_id,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "read", "input": {}},
                    ],
                },
            },
            {
                "type": "assistant",
                "sessionId": session_id,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Visible"},
                    ],
                },
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "Visible"

    def test_nested_text_blocks_concatenated(self, tmp_path: Path):
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {
                "type": "assistant",
                "sessionId": session_id,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Part 1. "},
                        {"type": "text", "text": "Part 2."},
                        {"type": "text", "text": " Part 3."},
                    ],
                },
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        assert page.entries[0].content == "Part 1. Part 2. Part 3."

    def test_metadata_safe_fields_only(self, tmp_path: Path):
        """Prove unsafe fields (api_key, token_count, env, mcp_servers)
        never leak through _safe_metadata."""
        projects = tmp_path / "projects"
        session_id = "s-1"
        _write_native_transcript(projects, session_id, [
            {
                "type": "user",
                "sessionId": session_id,
                "uuid": "u-safe",
                "parentUuid": "p-safe",
                "timestamp": "2026-07-18T10:00:00+00:00",
                "api_key": "sk-secret",
                "token_count": 500,
                "env": {"HOME": "/root"},
                "mcp_servers": [{"name": "secret", "status": "connected"}],
                "message": {"role": "user", "content": "Safe content", "model": "should-not-leak"},
            },
        ])
        reader = TranscriptReader(projects, session_id)
        page = reader.query(OutputQuery())
        assert len(page.entries) == 1
        entry = page.entries[0]
        assert entry.uuid == "u-safe"
        assert entry.parent_uuid == "p-safe"
        assert entry.timestamp is not None
        # model is None for user (message.model is not a user-visible concept,
        # but if present we don't expose it for user messages)
        serialized = {
            "role": entry.role,
            "content": entry.content,
            "uuid": entry.uuid,
            "parent_uuid": entry.parent_uuid,
            "timestamp": entry.timestamp,
            "model": entry.model,
        }
        assert set(serialized.keys()) == {
            "role", "content", "uuid", "parent_uuid", "timestamp", "model",
        }

    def test_cursor_exceeds_max_bound(self):
        with pytest.raises(ValueError, match="cursor exceeds"):
            TranscriptReader._parse_cursor(str(129 * 1024 * 1024))


# ── InputReceipt contract tests ──


def test_input_receipt_fields():
    """InputReceipt must contain the required fields per spec.md.
    No file paths, no database table references."""
    receipt = InputReceipt(
        runtime_id="rt-1",
        generation=1,
        provider="claude",
        session_id="sess-abc",
        accepted_at="2026-07-18T10:00:00+00:00",
        transcript_cursor="0",
        operation_id="op-1",
    )
    d = {
        "runtime_id": receipt.runtime_id,
        "generation": receipt.generation,
        "provider": receipt.provider,
        "session_id": receipt.session_id,
        "accepted_at": receipt.accepted_at,
        "transcript_cursor": receipt.transcript_cursor,
        "operation_id": receipt.operation_id,
    }
    assert "file" not in str(d).lower() or "file" not in d
    assert len(d) == 7
    # Cursor is a non-negative decimal string (byte offset)
    assert int(receipt.transcript_cursor) >= 0


def test_output_page_structure():
    page = OutputPage(
        entries=[
            TranscriptEntry(role="user", content="Hello", uuid="u1"),
            TranscriptEntry(role="assistant", content="Hi there", uuid="a1", parent_uuid="u1"),
        ],
        runtime_id="rt-1",
        generation=1,
        has_more=False,
        next_cursor=None,
    )
    assert len(page.entries) == 2
    assert page.entries[0].role == "user"
    assert page.entries[1].parent_uuid == "u1"


# ── HTTP API integration tests ──


ALL_TENANTS = {"service_id": "test-control-plane", "all_tenants": True}
TENANT = {"service_id": "test-tenant-service", "user_id": "user-1", "agentee_id": "agentee-1"}


class FakeCoordinatorForOutput:
    """Minimal coordinator stub for output endpoint testing."""

    def __init__(self):
        self.calls = []
        self.record = None
        self.max_input_bytes = 65536
        self.rejections = []
        self.output_page = None

    async def get(self, runtime_id):
        return self.record

    async def get_generation(self, runtime_id, generation):
        return self.record

    async def submit_input_async(self, runtime_id, generation, value):
        self.calls.append(("input_async", runtime_id, generation, value))
        return InputReceipt(
            runtime_id=runtime_id,
            generation=generation,
            provider="fake",
            session_id="fake-session",
            accepted_at="2026-07-18T10:00:00+00:00",
            transcript_cursor="0",
            operation_id=value.request_id,
        )

    async def read_output(self, runtime_id, generation, query):
        self.calls.append(("read_output", runtime_id, generation, query))
        return OutputPage(
            entries=[
                TranscriptEntry(role="user", content="Hello", uuid="u1"),
            ],
            runtime_id=runtime_id,
            generation=generation,
            has_more=False,
        )

    def record_input_rejection(self, runtime_id, generation, limit_kind):
        self.rejections.append((runtime_id, generation, limit_kind))

    async def status_for(self, record):
        from modules.runtime_manager.runtime_status_view import assemble_runtime_status
        return await assemble_runtime_status(record)


@pytest.mark.asyncio
async def test_input_returns_202_with_receipt():
    from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec
    from modules.runtime_manager.admission import RuntimeAdmissionPolicy

    coordinator = FakeCoordinatorForOutput()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(
        app, coordinator, lambda _request: TENANT,
        admission_policy=RuntimeAdmissionPolicy(allowed_user_ids=frozenset(("user-1",))),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post(
            "/internal/runtime-manager/v1/runtimes/runtime-1/input",
            json={"operation_id": "op-202", "generation": 1, "content": "test"},
        )
        assert resp.status == 202, f"Expected 202, got {resp.status}: {await resp.text()}"
        body = await resp.json()
        assert body["runtime_id"] == "runtime-1"
        assert body["generation"] == 1
        assert body["provider"] == "fake"
        assert body["session_id"] == "fake-session"
        assert body["operation_id"] == "op-202"
        assert "transcript_cursor" in body
        assert "accepted_at" in body
        # Check the receipt can be serialised back as JSON for the output query
        receipt_json = json.dumps(body)
        parsed = json.loads(receipt_json)
        assert parsed["runtime_id"] == "runtime-1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_output_endpoint_returns_filtered_page():
    from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec

    coordinator = FakeCoordinatorForOutput()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda _request: TENANT)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # Pass receipt as full JSON InputReceipt
        receipt = InputReceipt(
            runtime_id="runtime-1", generation=1, provider="fake",
            session_id="fake-session", accepted_at="2026-07-18T10:00:00+00:00",
            transcript_cursor="0", operation_id="op-1",
        )
        receipt_json = json.dumps({
            "runtime_id": receipt.runtime_id,
            "generation": receipt.generation,
            "provider": receipt.provider,
            "session_id": receipt.session_id,
            "accepted_at": receipt.accepted_at,
            "transcript_cursor": receipt.transcript_cursor,
            "operation_id": receipt.operation_id,
        })
        resp = await client.get(
            "/internal/runtime-manager/v1/runtimes/runtime-1/output",
            params={"receipt": receipt_json, "limit": "50"},
        )
        assert resp.status == 200, f"Expected 200, got {resp.status}: {await resp.text()}"
        body = await resp.json()
        assert body["runtime_id"] == "runtime-1"
        assert body["generation"] == 1
        assert len(body["entries"]) == 1
        assert body["entries"][0]["role"] == "user"
        assert body["entries"][0]["content"] == "Hello"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_output_endpoint_requires_owner_verification():
    from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec

    coordinator = FakeCoordinatorForOutput()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    other_tenant = {"service_id": "test", "user_id": "user-2", "agentee_id": "agentee-2"}
    setup_runtime_manager_routes(app, coordinator, lambda _request: other_tenant)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get(
            "/internal/runtime-manager/v1/runtimes/runtime-1/output",
            params={"limit": "50"},
        )
        assert resp.status == 404  # cross-owner hidden
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_output_invalid_limit_rejected():
    from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec

    coordinator = FakeCoordinatorForOutput()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda _request: TENANT)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get(
            "/internal/runtime-manager/v1/runtimes/runtime-1/output",
            params={"limit": "0"},
        )
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_output_malformed_receipt_rejected():
    from modules.http_api.runtime_manager_routes import setup_runtime_manager_routes
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec

    coordinator = FakeCoordinatorForOutput()
    coordinator.record = RuntimeProductRecord(RuntimeProductSpec(
        "runtime-1", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    ))
    app = web.Application()
    setup_runtime_manager_routes(app, coordinator, lambda _request: TENANT)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get(
            "/internal/runtime-manager/v1/runtimes/runtime-1/output",
            params={"receipt": "not-json", "limit": "50"},
        )
        assert resp.status == 400  # malformed receipt
    finally:
        await client.close()


# ── submit_input_async idempotency + exact-task regression ──


@pytest.mark.asyncio
async def test_submit_input_async_rejects_duplicate_with_different_content(tmp_path: Path):
    """Same operation_id with different content must raise Conflict."""
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec, RuntimeState
    from modules.runtime_manager.contracts import (
        RuntimeInput, PreparedRuntime, RuntimeHandle,
        TurnResult, ProviderStatus, ReadyResult, StopResult,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    from tests.runtime_manager.test_product_composition import (
        MemoryRepository, PersonaReader, Authority,
    )

    repo = MemoryRepository()
    spec = RuntimeProductSpec(
        "rt-idem2", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    )
    record = RuntimeProductRecord(spec, actual_state=RuntimeState.READY_UNBOUND, ready=True)
    repo.records[(spec.runtime_id, spec.generation)] = record
    repo.activation[(spec.user_id, spec.agentee_id, spec.activation_key)] = record

    class MiniAdapter:
        def __init__(self): self.turns = []
        async def prepare(self, s):
            wd = workspace / s.runtime_id / f"generation-{s.generation}"
            wd.mkdir(parents=True, exist_ok=True)
            (wd / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
            (wd / "runtime-owner.json").write_text(
                f'{{"runtime_id":"{s.runtime_id}","generation":{s.generation},"nonce":"n"}}')
            return PreparedRuntime(s, wd)
        async def start(self, p): return RuntimeHandle(p.spec.runtime_id, p.spec.generation, p.workdir, "t")
        async def wait_ready(self, h, d): return ReadyResult(ready=True, runtime_id=h.runtime_id, generation=h.generation)
        async def submit_input(self, h, v):
            self.turns.append(v); await asyncio.sleep(0.02)
            return TurnResult(v.request_id, h.runtime_id, h.generation, len(self.turns), "ok")
        async def inspect(self, h):
            return ProviderStatus(pid=1, returncode=None, ready=True, healthy=True, last_frame_monotonic=0.0)
        async def checkpoint(self, h, d): return None
        async def stop(self, h, r, g): return StopResult(stopped=True, exit_code=0, escalation="eof", reason=r)
        async def reconcile(self, rec): pass
        def receipt_context(self, h): return {"provider":"fake","session_id":"s","transcript_cursor":"0"}
        def claude_projects_dir(self, h): return h.workdir / ".claude" / "projects"

    adapter = MiniAdapter()
    coordinator = RuntimeManagerCoordinator(repo, PersonaReader(), Authority(), {"fake": adapter}, workspace)
    rt_spec = RuntimeSpec(runtime_id="rt-idem2", generation=1, user_id="user-1",
                          agentee_id="agentee-1", provider="fake", workspace_root=workspace)
    prepared = await adapter.prepare(rt_spec)
    key = ("rt-idem2", 1)
    coordinator._handles[key] = await adapter.start(prepared)
    coordinator._inflight[key] = 0

    # First submission succeeds
    receipt1 = await coordinator.submit_input_async(
        "rt-idem2", 1, RuntimeInput(request_id="op-1", content="hello"),
    )
    assert receipt1.operation_id == "op-1"

    # Same operation_id with different content → Conflict
    with pytest.raises(Exception):
        await coordinator.submit_input_async(
            "rt-idem2", 1, RuntimeInput(request_id="op-1", content="different"),
        )

    await asyncio.sleep(0.1)
    coordinator._handles.pop(key, None)
    coordinator._inflight.pop(key, None)


@pytest.mark.asyncio
async def test_submit_input_async_idempotent_returns_receipt_not_duplicate_task(tmp_path: Path):
    """Same operation_id with same content must return a receipt without
    spawning a duplicate background task."""
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec, RuntimeState
    from modules.runtime_manager.contracts import (
        RuntimeInput, PreparedRuntime, RuntimeHandle,
        TurnResult, ProviderStatus, ReadyResult, StopResult,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    from tests.runtime_manager.test_product_composition import (
        MemoryRepository, PersonaReader, Authority,
    )

    repo = MemoryRepository()
    spec = RuntimeProductSpec(
        "rt-idem3", 1, "activate-1", "user-1", "agentee-1", "fake",
        "adapter/default", "persona/ref",
    )
    record = RuntimeProductRecord(spec, actual_state=RuntimeState.READY_UNBOUND, ready=True)
    repo.records[(spec.runtime_id, spec.generation)] = record
    repo.activation[(spec.user_id, spec.agentee_id, spec.activation_key)] = record

    class MiniAdapter:
        def __init__(self): self.turns = []
        async def prepare(self, s):
            wd = workspace / s.runtime_id / f"generation-{s.generation}"
            wd.mkdir(parents=True, exist_ok=True)
            (wd / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
            (wd / "runtime-owner.json").write_text(
                f'{{"runtime_id":"{s.runtime_id}","generation":{s.generation},"nonce":"n"}}')
            return PreparedRuntime(s, wd)
        async def start(self, p): return RuntimeHandle(p.spec.runtime_id, p.spec.generation, p.workdir, "t")
        async def wait_ready(self, h, d): return ReadyResult(ready=True, runtime_id=h.runtime_id, generation=h.generation)
        async def submit_input(self, h, v):
            self.turns.append(v);
            return TurnResult(v.request_id, h.runtime_id, h.generation, len(self.turns), "ok")
        async def inspect(self, h):
            return ProviderStatus(pid=1, returncode=None, ready=True, healthy=True, last_frame_monotonic=0.0)
        async def checkpoint(self, h, d): return None
        async def stop(self, h, r, g): return StopResult(stopped=True, exit_code=0, escalation="eof", reason=r)
        async def reconcile(self, rec): pass
        def receipt_context(self, h): return {"provider":"fake","session_id":"s","transcript_cursor":"0"}
        def claude_projects_dir(self, h): return h.workdir / ".claude" / "projects"

    adapter = MiniAdapter()
    coordinator = RuntimeManagerCoordinator(repo, PersonaReader(), Authority(), {"fake": adapter}, workspace)
    rt_spec = RuntimeSpec(runtime_id="rt-idem3", generation=1, user_id="user-1",
                          agentee_id="agentee-1", provider="fake", workspace_root=workspace)
    prepared = await adapter.prepare(rt_spec)
    key = ("rt-idem3", 1)
    coordinator._handles[key] = await adapter.start(prepared)
    coordinator._inflight[key] = 0

    receipt1 = await coordinator.submit_input_async(
        "rt-idem3", 1, RuntimeInput(request_id="op-idem", content="hello"),
    )
    assert receipt1.operation_id == "op-idem"

    # Wait for the background task to complete
    await asyncio.sleep(0.1)

    # Same operation_id with same content → idempotent receipt (no new turn)
    turn_count_before = len(adapter.turns)
    receipt2 = await coordinator.submit_input_async(
        "rt-idem3", 1, RuntimeInput(request_id="op-idem", content="hello"),
    )
    assert receipt2.operation_id == "op-idem"
    # Must not have spawned a second turn
    assert len(adapter.turns) == turn_count_before

    coordinator._handles.pop(key, None)
    coordinator._inflight.pop(key, None)


# ── Full receipt identity validation regression ──


@pytest.mark.asyncio
async def test_read_output_rejects_wrong_runtime_in_receipt(tmp_path: Path):
    """Receipt with runtime_id different from URL must fail closed."""
    from modules.runtime_manager.coordinator import RuntimeManagerCoordinator
    from modules.runtime_manager.domain import RuntimeProductRecord, RuntimeProductSpec, RuntimeState

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    projects = workspace / "rt-1" / "generation-1" / ".claude" / "projects"
    projects.mkdir(parents=True)
    (projects / "sess.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": "sess", "message": {"role": "user", "content": "Hi"}}) + "\n")

    from tests.runtime_manager.test_product_composition import (
        MemoryRepository, PersonaReader, Authority,
    )
    repo = MemoryRepository()
    spec = RuntimeProductSpec("rt-1", 1, "activate-1", "user-1", "agentee-1", "fake",
                              "adapter/default", "persona/ref")
    record = RuntimeProductRecord(spec, actual_state=RuntimeState.READY_UNBOUND, ready=True)
    repo.records[(spec.runtime_id, spec.generation)] = record
    repo.activation[(spec.user_id, spec.agentee_id, spec.activation_key)] = record

    class MiniAdapter:
        async def prepare(self, s):
            wd = workspace / s.runtime_id / f"generation-{s.generation}"
            wd.mkdir(parents=True, exist_ok=True)
            (wd / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
            return PreparedRuntime(s, wd)
        async def start(self, p): return RuntimeHandle(p.spec.runtime_id, p.spec.generation, p.workdir, "t")
        async def wait_ready(self, h, d): return ReadyResult(ready=True, runtime_id=h.runtime_id, generation=h.generation)
        async def submit_input(self, h, v): return TurnResult("r", "rt", 1, 1, "")
        async def inspect(self, h): return ProviderStatus(pid=1, returncode=None, ready=True, healthy=True, last_frame_monotonic=0.0)
        async def checkpoint(self, h, d): return None
        async def stop(self, h, r, g): return StopResult(stopped=True, exit_code=0, escalation="eof", reason=r)
        async def reconcile(self, rec): pass
        def receipt_context(self, h): return {"provider": "fake", "session_id": "sess", "transcript_cursor": "0"}
        def claude_projects_dir(self, h): return h.workdir / ".claude" / "projects"

    adapter = MiniAdapter()
    coordinator = RuntimeManagerCoordinator(repo, PersonaReader(), Authority(), {"fake": adapter}, workspace)
    rt_spec = RuntimeSpec(runtime_id="rt-1", generation=1, user_id="user-1",
                          agentee_id="agentee-1", provider="fake", workspace_root=workspace)
    prepared = await adapter.prepare(rt_spec)
    key = ("rt-1", 1)
    coordinator._handles[key] = await adapter.start(prepared)
    coordinator._inflight[key] = 0

    # Build a receipt with wrong runtime_id
    wrong_receipt = json.dumps({
        "runtime_id": "rt-other",  # wrong!
        "generation": 1,
        "provider": "fake",
        "session_id": "sess",
        "accepted_at": "2026-07-18T10:00:00+00:00",
        "transcript_cursor": "0",
        "operation_id": "op-1",
    })
    from modules.runtime_manager.contracts import OutputQuery
    with pytest.raises(RuntimeError, match="receipt_runtime_mismatch"):
        await coordinator.read_output("rt-1", 1, OutputQuery(receipt=wrong_receipt, limit=50))

    coordinator._handles.pop(key, None)
    coordinator._inflight.pop(key, None)
