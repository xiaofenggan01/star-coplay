"""Read-only transcript reader for generation-local Claude native JSONL.

Locates Claude's native session persistence file under
``<workdir>/.claude/projects/`` by matching the current transport
*session_id* against filenames.  Returns only allowlisted user/assistant
visible text with safe metadata.  Thinking blocks, tool-call requests,
tool results, attachments, environment variables, filesystem paths and
secrets are never exposed.

This module is the sole output parser; the HTTP output endpoint and any
future consumer MUST go through this reader rather than opening the
transcript file directly.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from modules.runtime_manager.contracts import OutputPage, OutputQuery, TranscriptEntry

log = logging.getLogger("runtime_manager.transcript")

# ── Allowlist: frame types whose visible text may be surfaced ──
_ALLOWED_FRAME_TYPES = frozenset({"user", "assistant"})

# ── Assistant content block types that are safe to surface ──
_ALLOWED_ASSISTANT_BLOCK_TYPES = frozenset({"text"})

# ── Max byte-offset cursor (defence-in-depth bound) ──
_MAX_CURSOR = 128 * 1024 * 1024  # 128 MiB


def _parse_iso_timestamp(value: object) -> Optional[str]:
    """Normalise an ISO 8601 timestamp from a frame field, or return None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def _extract_visible_text(message: Mapping[str, Any]) -> str:
    """Extract only allowlisted visible text from a Claude message.

    - User messages: content is a plain string → used directly.
    - Assistant messages: content is a list of blocks → only ``text``
      blocks are included; everything else (tool_use, thinking, etc.)
      is silently dropped.
    - Any unrecognised shape returns "" (fail-closed).
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type not in _ALLOWED_ASSISTANT_BLOCK_TYPES:
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _safe_metadata(frame: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """Extract only safe metadata fields from a Claude frame.

    Uses the observed Claude native keys: ``parentUuid`` (camelCase top-level)
    and ``message.model`` (nested inside message).  Never extracts model
    parameters, token counts, API keys, tool definitions, MCP server details,
    filesystem paths, or any field not explicitly listed here.
    """
    meta: Dict[str, Optional[str]] = {}
    # Claude native JSONL uses camelCase: parentUuid, uuid
    uuid_val = frame.get("uuid")
    meta["uuid"] = uuid_val if isinstance(uuid_val, str) else None
    parent_uuid_val = frame.get("parentUuid")
    meta["parent_uuid"] = parent_uuid_val if isinstance(parent_uuid_val, str) else None
    meta["timestamp"] = _parse_iso_timestamp(frame.get("timestamp"))
    # model lives inside message.model in Claude native JSONL
    msg = frame.get("message")
    if isinstance(msg, Mapping):
        model = msg.get("model")
        if isinstance(model, str):
            meta["model"] = model
        else:
            meta["model"] = None
    else:
        meta["model"] = None
    return meta


def _extract_message(frame: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """Safe extraction of the ``message`` sub-document from a frame."""
    msg = frame.get("message")
    if isinstance(msg, Mapping):
        return msg
    return None


def find_session_file(projects_dir: Path, session_id: str) -> Optional[Path]:
    """Walk *projects_dir* and return the first regular file whose name
    contains *session_id* as a substring.  Returns ``None`` when no match
    is found or the directory does not exist.
    """
    if not session_id or not projects_dir.is_dir():
        return None
    try:
        for dirpath, _dirnames, filenames in os.walk(str(projects_dir)):
            for name in filenames:
                if session_id in name:
                    candidate = Path(dirpath) / name
                    if candidate.is_file():
                        return candidate
    except OSError:
        return None
    return None


class TranscriptReader:
    """Reads Claude's generation-local native session JSONL and produces
    allowlist-filtered ``TranscriptEntry`` pages.

    The cursor is a non-negative byte offset into the native JSONL file.
    Each call to ``query`` seeks to the cursor position and reads forward
    until *query.limit* visible entries have been collected, or EOF.
    """

    def __init__(self, projects_dir: Path, session_id: str) -> None:
        self._projects_dir = projects_dir
        self._session_id = session_id

    def _resolve_path(self) -> Path:
        """Locate and validate the native JSONL file.  Raises ``FileNotFoundError``
        when no file matches the current session_id."""
        file_path = find_session_file(self._projects_dir, self._session_id)
        if file_path is None:
            raise FileNotFoundError(
                f"no native transcript file found for session {self._session_id}"
                f" under {self._projects_dir}"
            )
        return file_path

    def query(self, query: OutputQuery) -> OutputPage:
        """Read and filter the native transcript according to *query*.

        Returns an ``OutputPage`` with at most ``query.limit`` entries and
        a ``next_cursor`` for the next page (or ``None`` when exhausted).

        Raises ``FileNotFoundError`` when the transcript file does not
        exist, and ``ValueError`` for invalid/malformed cursor values.
        """
        file_path = self._resolve_path()
        try:
            file_size = file_path.stat().st_size
        except OSError as exc:
            raise OSError("native transcript cannot be inspected") from exc

        start_offset: int = 0
        use_since: bool = False
        since_dt: Optional[datetime] = None

        if query.receipt is not None:
            try:
                start_offset = self._parse_cursor(query.receipt)
            except (ValueError, IndexError) as exc:
                raise ValueError(f"invalid receipt cursor: {query.receipt}") from exc
            if start_offset > file_size:
                raise ValueError("receipt cursor exceeds transcript EOF")
        elif query.since is not None:
            use_since = True
            try:
                since_dt = datetime.fromisoformat(query.since)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid since timestamp: {query.since}") from exc
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)

        entries: List[TranscriptEntry] = []
        next_cursor: Optional[str] = None

        try:
            with open(str(file_path), "r", encoding="utf-8") as fh:
                if start_offset > 0:
                    fh.seek(start_offset)
                else:
                    start_offset = 0

                for raw in fh:
                    stripped = raw.strip()
                    if not stripped:
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    try:
                        frame = json.loads(stripped)
                    except (json.JSONDecodeError, ValueError):
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    if not isinstance(frame, Mapping):
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    # ── Validate session ownership ──
                    record_sid = frame.get("sessionId")
                    if not isinstance(record_sid, str) or record_sid != self._session_id:
                        # Missing or different ownership is never surfaced.
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    frame_type = frame.get("type")
                    if frame_type not in _ALLOWED_FRAME_TYPES:
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    msg = _extract_message(frame)
                    if msg is None:
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    visible_text = _extract_visible_text(msg)
                    if not visible_text.strip():
                        start_offset += len(raw.encode("utf-8"))
                        continue

                    meta = _safe_metadata(frame)

                    if use_since and since_dt is not None and meta["timestamp"] is not None:
                        try:
                            entry_dt = datetime.fromisoformat(meta["timestamp"])
                            if entry_dt < since_dt:
                                start_offset += len(raw.encode("utf-8"))
                                continue
                        except (ValueError, OverflowError, TypeError):
                            pass

                    entries.append(TranscriptEntry(
                        role=str(frame_type),
                        content=visible_text,
                        uuid=meta["uuid"],
                        parent_uuid=meta["parent_uuid"],
                        timestamp=meta["timestamp"],
                        model=meta["model"],
                    ))

                    start_offset += len(raw.encode("utf-8"))

                    if len(entries) >= query.limit:
                        next_cursor = str(start_offset)
                        break

                if len(entries) < query.limit:
                    next_cursor = None  # exhausted

        except OSError as exc:
            log.warning("transcript read failed: %s", exc)
            raise

        return OutputPage(
            entries=entries,
            runtime_id="",  # filled in by the coordinator
            generation=0,   # filled in by the coordinator
            has_more=next_cursor is not None,
            next_cursor=next_cursor,
        )

    @staticmethod
    def _parse_cursor(cursor: str) -> int:
        """Decode a cursor string into a non-negative byte offset.

        Raises ``ValueError`` when the cursor is negative, exceeds the
        safety bound, or is not a valid integer.
        """
        try:
            offset = int(cursor)
        except (ValueError, TypeError) as exc:
            raise ValueError("cursor must be an integer") from exc
        if offset < 0:
            raise ValueError("cursor must be non-negative")
        if offset > _MAX_CURSOR:
            raise ValueError("cursor exceeds maximum allowed offset")
        return offset
