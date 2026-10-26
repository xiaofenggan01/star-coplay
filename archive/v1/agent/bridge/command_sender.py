from __future__ import annotations

from pathlib import Path

from agent.bridge.file_bridge import append_jsonl, get_mtime


class CommandSender:
    """向 commands.jsonl 追加命令行。Mod 端通过 offset 增量读取。"""

    def __init__(self, bridge_dir: Path) -> None:
        self.path = bridge_dir / "commands.jsonl"

    def send(self, commands: list[dict]) -> float:
        """追加命令到 JSONL 文件，返回写入后的 mtime。"""
        append_jsonl(self.path, commands)
        return get_mtime(self.path)
