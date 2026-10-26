from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields, is_dataclass
import json
import os
from pathlib import Path
import stat
import threading
import time
from typing import Any, Mapping


@dataclass(frozen=True)
class AuditEvent:
    event: str
    tool_call_id: str
    command_id: str
    runtime_ref: str
    binding_ref: str
    game_session_ref: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    log_id: str | None = None


class MemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


_SENSITIVE_KEYS = frozenset({
    "authorization", "credential", "password", "secret", "token",
    "access_token", "refresh_token", "api_key",
})
_MAX_RECORD_BYTES = 16 * 1024
_DEFAULT_MAX_BYTES = 20 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 7


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        raise ValueError("audit_detail_too_deep")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("audit_non_finite_number")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("audit_non_string_key")
            if key.lower() in _SENSITIVE_KEYS:
                raise ValueError("audit_sensitive_field")
            result[key] = _json_value(item, depth=depth + 1)
        return result
    if isinstance(value, (tuple, list)):
        return [_json_value(item, depth=depth + 1) for item in value]
    if hasattr(value, "value"):
        return _json_value(value.value, depth=depth + 1)
    raise ValueError("audit_unsupported_value")


class DurableJsonlAuditSink:
    """Small append-only persistence sink shared by Local and Hosted.

    It owns no MCP or game semantics.  The flat common envelope makes Skill
    and Capability events joinable without turning the audit log into another
    execution-state authority.
    """

    fail_closed = True

    def __init__(
        self, path: Path, *, clock=time.time,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        backup_count: int = _DEFAULT_BACKUP_COUNT,
    ) -> None:
        if not isinstance(path, Path) or not path.is_absolute() or not path.name:
            raise ValueError("audit_path_invalid")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ValueError("audit_max_bytes_invalid")
        if not isinstance(backup_count, int) or isinstance(backup_count, bool) or backup_count <= 0:
            raise ValueError("audit_backup_count_invalid")
        if path.parent.is_symlink():
            raise ValueError("audit_directory_symlink")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.resolve(strict=True)
        parent_stat = parent.stat()
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise ValueError("audit_directory_invalid")
        if hasattr(os, "geteuid") and parent_stat.st_uid != os.geteuid():
            raise PermissionError("audit_directory_owner_invalid")
        parent.chmod(0o700)
        if path.is_symlink():
            raise ValueError("audit_path_symlink")
        self.path = parent / path.name
        self._clock = clock
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()
        self._health_state = {
            "configured": True, "writable": None,
            "last_success_at": None, "last_error_code": None,
        }
        fd = self._open_current(create=True)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def health_state_snapshot(self) -> Mapping[str, Any]:
        """Return observations from actual appends; never probe the file."""
        with self._lock:
            return dict(self._health_state)

    @staticmethod
    def _secure_regular_file(fd: int) -> None:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("audit_file_not_regular")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise PermissionError("audit_file_owner_invalid")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)

    def _open_current(self, *, create: bool) -> int:
        flags = (
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if create:
            flags |= os.O_CREAT
        fd = os.open(self.path, flags, stat.S_IRUSR | stat.S_IWUSR)
        try:
            self._secure_regular_file(fd)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _assert_owned_regular_path(self, path: Path) -> os.stat_result:
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("audit_file_not_regular")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise PermissionError("audit_file_owner_invalid")
        mode = stat.S_IRUSR | stat.S_IWUSR
        if os.chmod in os.supports_follow_symlinks:
            os.chmod(path, mode, follow_symlinks=False)
        else:
            os.chmod(path, mode)
        return path.stat(follow_symlinks=False)

    def _rotate_if_needed(self, encoded_size: int) -> bool:
        current = self._assert_owned_regular_path(self.path)
        if current.st_size == 0 or current.st_size + encoded_size <= self._max_bytes:
            return False

        oldest = Path(f"{self.path}.{self._backup_count}")
        if oldest.exists() or oldest.is_symlink():
            self._assert_owned_regular_path(oldest)
            oldest.unlink()
        for number in range(self._backup_count - 1, 0, -1):
            source = Path(f"{self.path}.{number}")
            if not source.exists() and not source.is_symlink():
                continue
            self._assert_owned_regular_path(source)
            os.rename(source, Path(f"{self.path}.{number + 1}"))
        os.rename(self.path, Path(f"{self.path}.1"))
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            self._secure_regular_file(fd)
        finally:
            os.close(fd)
        return True

    def emit(self, event: Any) -> None:
        if not is_dataclass(event):
            raise ValueError("audit_event_invalid")
        source = {field.name: getattr(event, field.name) for field in fields(event)}
        event_name = source.pop("event", None)
        if not isinstance(event_name, str) or not event_name:
            raise ValueError("audit_event_invalid")
        detail = source.pop("detail", {})
        record = {
            "schema_version": "starcoplay.mcp-audit.v1",
            "recorded_at": float(self._clock()),
            "event": event_name,
            "request_id": source.pop("request_id", None),
            "tool_call_id": source.pop("tool_call_id", None),
            "skill_execution_id": source.pop("skill_execution_id", None),
            "command_id": source.pop("command_id", None),
            **source,
            "detail": detail,
        }
        encoded = (json.dumps(
            _json_value(record), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ) + "\n").encode("utf-8")
        if len(encoded) > _MAX_RECORD_BYTES:
            raise ValueError("audit_record_too_large")
        with self._lock:
            try:
                try:
                    self._rotate_if_needed(len(encoded))
                except Exception:
                    self._health_state["writable"] = False
                    self._health_state["last_error_code"] = "rotation_failed"
                    raise
                fd = None
                try:
                    fd = self._open_current(create=False)
                    start_size = os.fstat(fd).st_size
                    os.lseek(fd, 0, os.SEEK_END)
                    written = 0
                    try:
                        while written < len(encoded):
                            count = os.write(fd, encoded[written:])
                            if count <= 0:
                                raise OSError("audit_short_write")
                            written += count
                    except Exception:
                        os.ftruncate(fd, start_size)
                        os.fsync(fd)
                        raise
                    os.fsync(fd)
                finally:
                    if fd is not None:
                        os.close(fd)
            except Exception:
                if self._health_state["last_error_code"] != "rotation_failed":
                    self._health_state["writable"] = False
                    self._health_state["last_error_code"] = "write_failed"
                raise
            self._health_state["writable"] = True
            self._health_state["last_success_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock())
            )
            self._health_state["last_error_code"] = None
