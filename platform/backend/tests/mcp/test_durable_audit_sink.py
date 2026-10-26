from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from modules.mcp.core.audit import AuditEvent, DurableJsonlAuditSink
from modules.mcp.skills.runtime_models import SkillAuditEvent


def _skill_event(detail=None):
    return SkillAuditEvent(
        "skill.capability.result", "request-1", "tool-1", "skill-exec-1",
        "stardew.proto.say", "1.0.0", "sha256:manifest", "sha256:implementation",
        "command-1", "runtime-1", "binding-1", 2, "game-1",
        "sha256:capabilities", detail or {"state": "SUCCEEDED"},
    )


def _all_rotated_records(path: Path):
    backups = [candidate for candidate in path.parent.glob(f"{path.name}.*")
               if candidate.name.rsplit(".", 1)[-1].isdigit()]
    paths = [path, *sorted(
        backups, key=lambda candidate: int(candidate.name.rsplit(".", 1)[1]),
    )]
    return [json.loads(line) for candidate in paths
            for line in candidate.read_text().splitlines() if line]


def test_jsonl_sink_persists_joinable_skill_and_capability_events_across_reopen(tmp_path):
    path = (tmp_path / "audit" / "mcp.jsonl").resolve()
    first = DurableJsonlAuditSink(path, clock=lambda: 100.0)
    first.emit(_skill_event())
    DurableJsonlAuditSink(path, clock=lambda: 101.0).emit(AuditEvent(
        "command.result", "tool-1", "command-1", "runtime-1", "binding-1",
        "game-1", {"state": "SUCCEEDED", "error_code": None},
    ))

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert [record["recorded_at"] for record in records] == [100.0, 101.0]
    skill, command = records
    assert (
        skill["request_id"], skill["tool_call_id"],
        skill["skill_execution_id"], skill["command_id"],
    ) == ("request-1", "tool-1", "skill-exec-1", "command-1")
    assert command["request_id"] is None
    assert command["tool_call_id"] == "tool-1"
    assert command["command_id"] == "command-1"
    assert {record["schema_version"] for record in records} == {
        "starcoplay.mcp-audit.v1"
    }


def test_jsonl_sink_persists_server_log_id_for_cross_log_join(tmp_path):
    path = (tmp_path / "audit.jsonl").resolve()
    DurableJsonlAuditSink(path).emit(AuditEvent(
        "command.result", "tool-1", "command-1", "runtime-1", "binding-1",
        "game-1", {"state": "SUCCEEDED"}, log_id="log_" + "a" * 32,
    ))
    assert json.loads(path.read_text())["log_id"] == "log_" + "a" * 32


@pytest.mark.parametrize("detail", [
    {"credential": "must-not-land"},
    {"nested": {"access_token": "must-not-land"}},
    {"bad": float("nan")},
])
def test_jsonl_sink_fails_closed_before_persisting_sensitive_or_invalid_detail(tmp_path, detail):
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path)
    with pytest.raises(ValueError):
        sink.emit(_skill_event(detail))
    assert path.read_text() == ""


def test_jsonl_sink_rejects_symlink_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("")
    link = tmp_path / "audit.jsonl"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="audit_path_symlink"):
        DurableJsonlAuditSink(link.resolve(strict=False).parent / link.name)


def test_single_sink_appends_complete_json_lines_from_multiple_threads(tmp_path):
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path)

    def write(index):
        sink.emit(replace(
            _skill_event(), request_id=f"request-{index}",
            tool_call_id=f"tool-{index}", command_id=f"command-{index}",
        ))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(100)))
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 100
    assert {record["request_id"] for record in records} == {
        f"request-{index}" for index in range(100)
    }


@pytest.mark.parametrize("name,value", [("max_bytes", 0), ("backup_count", 0)])
def test_rotation_settings_must_be_positive(tmp_path, name, value):
    with pytest.raises(ValueError, match=f"audit_{name}_invalid"):
        DurableJsonlAuditSink(
            (tmp_path / "audit.jsonl").resolve(), **{name: value},
        )


def test_exact_threshold_does_not_rotate_but_next_record_does(tmp_path):
    probe = DurableJsonlAuditSink((tmp_path / "probe.jsonl").resolve(), clock=lambda: 1.0)
    probe.emit(_skill_event())
    record_size = probe.path.stat().st_size
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(
        path, clock=lambda: 1.0, max_bytes=record_size * 2, backup_count=2,
    )
    sink.emit(_skill_event())
    sink.emit(_skill_event())
    assert path.stat().st_size == record_size * 2
    assert not Path(f"{path}.1").exists()
    sink.emit(_skill_event())
    assert path.stat().st_size == record_size
    assert Path(f"{path}.1").stat().st_size == record_size * 2


def test_repeated_rotation_is_bounded_and_preserves_complete_unique_records(tmp_path):
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path, max_bytes=900, backup_count=20)
    for index in range(20):
        sink.emit(replace(
            _skill_event(), request_id=f"request-{index}",
            tool_call_id=f"tool-{index}", command_id=f"command-{index}",
        ))
    backups = [candidate for candidate in tmp_path.glob("audit.jsonl.*")
               if candidate.name.rsplit(".", 1)[-1].isdigit()]
    assert len(backups) <= 20
    assert not Path(f"{path}.21").exists()
    records = _all_rotated_records(path)
    assert len(records) == 20
    assert {record["request_id"] for record in records} == {
        f"request-{index}" for index in range(20)
    }
    assert all(candidate.stat().st_mode & 0o777 == 0o600
               for candidate in [path, *backups])


def test_rotation_deletes_only_beyond_configured_backup_bound(tmp_path):
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path, max_bytes=1, backup_count=2)
    for index in range(6):
        sink.emit(replace(_skill_event(), request_id=f"request-{index}"))
    assert Path(f"{path}.1").is_file()
    assert Path(f"{path}.2").is_file()
    assert not Path(f"{path}.3").exists()
    records = _all_rotated_records(path)
    assert len(records) == 3
    assert len({record["request_id"] for record in records}) == 3


def _prime_rotation(sink):
    sink.emit(_skill_event())


def test_rename_failure_is_rotation_failed(tmp_path, monkeypatch):
    import modules.mcp.core.audit as audit_module
    sink = DurableJsonlAuditSink((tmp_path / "audit.jsonl").resolve(), max_bytes=1)
    _prime_rotation(sink)
    def fail_rename(*_args):
        raise OSError("rename")
    monkeypatch.setattr(audit_module.os, "rename", fail_rename)
    with pytest.raises(OSError, match="rename"):
        sink.emit(_skill_event())
    assert sink.health_state_snapshot()["last_error_code"] == "rotation_failed"


def test_write_failure_updates_health_and_fails_closed(tmp_path, monkeypatch):
    import modules.mcp.core.audit as audit_module
    sink = DurableJsonlAuditSink((tmp_path / "audit.jsonl").resolve())
    def fail_write(*_args):
        raise OSError("write")
    monkeypatch.setattr(audit_module.os, "write", fail_write)
    with pytest.raises(OSError, match="write"):
        sink.emit(_skill_event())
    assert sink.health_state_snapshot()["last_error_code"] == "write_failed"


def test_short_writes_are_completed_as_one_valid_json_line(tmp_path, monkeypatch):
    import modules.mcp.core.audit as audit_module
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path)
    original_write = audit_module.os.write

    def short_write(fd, data):
        return original_write(fd, data[:7])

    monkeypatch.setattr(audit_module.os, "write", short_write)
    sink.emit(_skill_event())
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "skill.capability.result"


def test_partial_write_then_failure_rolls_back_incomplete_line(tmp_path, monkeypatch):
    import modules.mcp.core.audit as audit_module
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path)
    sink.emit(_skill_event())
    before = path.read_bytes()
    original_write = audit_module.os.write
    calls = 0

    def partial_then_fail(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(fd, data[:11])
        raise OSError("partial-write-failed")

    monkeypatch.setattr(audit_module.os, "write", partial_then_fail)
    with pytest.raises(OSError, match="partial-write-failed"):
        sink.emit(_skill_event())
    assert path.read_bytes() == before
    assert sink.health_state_snapshot()["last_error_code"] == "write_failed"


def test_write_failure_is_not_silently_accepted(tmp_path):
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path)
    path.unlink()
    path.mkdir()
    with pytest.raises(OSError):
        sink.emit(_skill_event())


def test_append_failure_preserves_prior_audit_and_reports_unwritable(tmp_path, monkeypatch):
    """Audit failure is a failed operation, never a best-effort diagnostic loss."""
    path = (tmp_path / "audit.jsonl").resolve()
    sink = DurableJsonlAuditSink(path)
    sink.emit(_skill_event())
    before = path.read_bytes()

    def fail_audit_write(_fd, _payload):
        raise OSError("audit disk unavailable")

    monkeypatch.setattr(os, "write", fail_audit_write)
    with pytest.raises(OSError, match="audit disk unavailable"):
        sink.emit(replace(
            _skill_event(), request_id="request-rejected",
            tool_call_id="tool-rejected", command_id="command-rejected",
        ))

    assert path.read_bytes() == before
    health = sink.health_state_snapshot()
    assert health["configured"] is True
    assert health["writable"] is False
    assert health["last_success_at"] is not None
    assert health["last_error_code"] == "write_failed"
