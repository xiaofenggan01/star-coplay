from __future__ import annotations

import json
from typing import Any


_MOJIBAKE_MARKERS = (
    "鎴", "浣", "鐜", "杈", "鐮", "粰", "璧", "鍙", "浜", "鍛", "涔", "涓", "锛", "紝", "傛", "�",
)


def repair_mojibake_text(text: str) -> str:
    if not text or not any(m in text for m in _MOJIBAKE_MARKERS):
        return text
    try:
        source = text.replace("\ufffd", "")
        repaired = source.encode("gb18030", errors="ignore").decode("utf-8", errors="ignore")
    except UnicodeError:
        return text
    if not repaired:
        return text
    original_markers = sum(text.count(m) for m in _MOJIBAKE_MARKERS)
    repaired_markers = sum(repaired.count(m) for m in _MOJIBAKE_MARKERS)
    cjk_count = sum(1 for c in repaired if "\u4e00" <= c <= "\u9fff")
    if cjk_count >= 2 and repaired_markers < original_markers:
        return repaired
    return text


def sanitize_for_utf8_json(obj: Any) -> Any:
    if isinstance(obj, str):
        safe = "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in obj)
        return repair_mojibake_text(safe)
    if isinstance(obj, dict):
        return {sanitize_for_utf8_json(k): sanitize_for_utf8_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_utf8_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_for_utf8_json(v) for v in obj)
    return obj


def utf8_safe_json_line(obj: Any) -> str:
    clean = sanitize_for_utf8_json(obj)
    line = json.dumps(clean, ensure_ascii=False) + "\n"
    return line.encode("utf-8", errors="replace").decode("utf-8")
