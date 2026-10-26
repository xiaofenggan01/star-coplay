from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件，返回所有有效 JSON 行。跳过空行和不完整行。"""
    if not path.is_file():
        return []
    results: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        results.append(obj)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return results


def append_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    """追加写入 JSONL 文件，每个 item 一行，以 \\n 结尾确保完整行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    with path.open("a", encoding="utf-8") as f:
        f.write(lines)


def get_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
