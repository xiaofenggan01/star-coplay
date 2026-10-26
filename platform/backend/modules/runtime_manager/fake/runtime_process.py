#!/usr/bin/env python3
"""Standalone NDJSON test subprocess for the AgentRuntimeAdapter spike.

Runs as a real, independent Python process (spawned via
``asyncio.create_subprocess_exec``). Communicates over stdin/stdout NDJSON.
Test-only fault injection is controlled entirely via CLI flags so behavior is
explicit and reviewable; nothing is provider- or Claude-specific here.

Protocol (one JSON object per line):

  in:  {"type": "input", "request_id": str, "content": str}
  out: {"type": "output", "request_id", "runtime_id", "generation", "turn", "content", "marker_count"}

  in:  {"type": "mcp_initialize", "request_id": str}
  out: {"type": "mcp_initialized", "request_id", "runtime_id", "generation"}

  in:  {"type": "mcp_tools_list", "request_id": str}
  out: {"type": "mcp_tools", "request_id", "tools": [str, ...]}

  in:  {"type": "mcp_call", "request_id": str, "tool": str, "args": {...}}
  out: {"type": "mcp_result", "request_id", "tool", "credential_fingerprint", "result"}
       or {"type": "mcp_error", "request_id", "reason"}

  args.credential (optional): if the caller supplies a credential value that
  does not match this runtime's own mcp.json credential, the call is rejected
  with mcp_error/credential_mismatch -- this is what proves runtime A's
  credential cannot be used to call into runtime B's context. Never echoes
  the supplied or stored credential text back, only fingerprints/reasons.

  in:  {"type": "query_isolation", "request_id": str}
  out: {"type": "isolation_info", "request_id", "cwd", "credential_fingerprint",
        "env_marker", "log_sentinel_path", "log_sentinel_value"}
       Test-only observability frame: returns only non-secret identity
       markers (cwd path, credential fingerprint, one whitelisted env var,
       a per-runtime log sentinel) -- never the full environment or credential.

  in:  {"type": "checkpoint", "request_id": str}
  out: {"type": "checkpoint_result", "request_id", "path"}

  in:  {"type": "stop", "reason": str}
  out: {"type": "stopped"}   (then process exits 0, unless --ignore-stop)

Startup: emits {"type": "ready", "runtime_id", "generation"} after
``--ready-delay`` seconds, unless ``--never-ready`` is set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any


def _emit(frame: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(frame, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _load_owner(cwd: Path) -> dict[str, Any]:
    owner_path = cwd / "runtime-owner.json"
    with owner_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _install_signal_handlers(ignore_term: bool) -> None:
    if ignore_term:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
    # SIGKILL cannot be caught or ignored; that is the point of the escalation ladder.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-delay", type=float, default=0.0)
    parser.add_argument("--never-ready", action="store_true")
    parser.add_argument("--ignore-stop", action="store_true")
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--crash-immediately", action="store_true")
    args = parser.parse_args()

    cwd = Path.cwd()
    owner = _load_owner(cwd)
    runtime_id = owner["runtime_id"]
    generation = owner["generation"]

    if args.crash_immediately:
        sys.stderr.write("fake runtime: crash-immediately flag set\n")
        sys.stderr.flush()
        return 17

    _install_signal_handlers(args.ignore_term)

    if not args.never_ready:
        if args.ready_delay > 0:
            time.sleep(args.ready_delay)
        _emit({"type": "ready", "runtime_id": runtime_id, "generation": generation})

    turn_counter = 0
    marker: list[str] = []  # in-memory only; proves multi-turn continuity without a restart
    scratch_dir = cwd / "scratch"
    scratch_dir.mkdir(exist_ok=True)
    logs_dir = cwd / "logs"
    logs_dir.mkdir(exist_ok=True)

    # Per-process log sentinel: unique to this runtime/generation's own logs
    # dir, proving A's log content is never visible/identical to B's.
    log_sentinel_value = f"{runtime_id}:{generation}:{owner.get('nonce', '')}"
    log_sentinel_path = logs_dir / "isolation-sentinel.log"
    with log_sentinel_path.open("w", encoding="utf-8") as fh:
        fh.write(log_sentinel_value + "\n")

    while True:
        line = sys.stdin.readline()
        if not line:
            return 0  # stdin closed: parent went away
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            _emit({"type": "protocol_error", "reason": "bad_json"})
            continue

        frame_type = frame.get("type")

        if frame_type == "input":
            turn_counter += 1
            marker.append(frame.get("content", ""))
            _emit(
                {
                    "type": "output",
                    "request_id": frame.get("request_id"),
                    "runtime_id": runtime_id,
                    "generation": generation,
                    "turn": turn_counter,
                    "content": frame.get("content", ""),
                    "marker_count": len(marker),
                }
            )
        elif frame_type == "mcp_initialize":
            _emit(
                {
                    "type": "mcp_initialized",
                    "request_id": frame.get("request_id"),
                    "runtime_id": runtime_id,
                    "generation": generation,
                }
            )
        elif frame_type == "mcp_tools_list":
            _emit({"type": "mcp_tools", "request_id": frame.get("request_id"), "tools": ["echo"]})
        elif frame_type == "mcp_call":
            mcp_path = cwd / "mcp.json"
            revoked_path = cwd / "credential.revoked"
            if revoked_path.exists() or not mcp_path.exists():
                _emit({"type": "mcp_error", "request_id": frame.get("request_id"), "reason": "credential_revoked"})
                continue
            with mcp_path.open("r", encoding="utf-8") as fh:
                mcp_config = json.load(fh)
            credential = str(mcp_config.get("credential", ""))
            args = frame.get("args") or {}
            supplied_credential = args.get("credential")
            if supplied_credential is not None and supplied_credential != credential:
                # Caller presented a credential that does not belong to this
                # runtime's own context (e.g. runtime A's credential used
                # against runtime B) -- reject without ever echoing either
                # credential value back.
                _emit({"type": "mcp_error", "request_id": frame.get("request_id"), "reason": "credential_mismatch"})
                continue
            fingerprint = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]
            _emit(
                {
                    "type": "mcp_result",
                    "request_id": frame.get("request_id"),
                    "tool": frame.get("tool"),
                    "credential_fingerprint": fingerprint,
                    "result": {"ok": True},
                }
            )
        elif frame_type == "query_isolation":
            mcp_path = cwd / "mcp.json"
            fingerprint = ""
            if mcp_path.exists():
                with mcp_path.open("r", encoding="utf-8") as fh:
                    credential = str(json.load(fh).get("credential", ""))
                fingerprint = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]
            _emit(
                {
                    "type": "isolation_info",
                    "request_id": frame.get("request_id"),
                    "cwd": str(cwd.resolve()),
                    "credential_fingerprint": fingerprint,
                    "env_marker": os.environ.get("RUNTIME_ISOLATION_MARKER", ""),
                    "log_sentinel_path": str(log_sentinel_path),
                    "log_sentinel_value": log_sentinel_value,
                }
            )
        elif frame_type == "checkpoint":
            checkpoint_path = scratch_dir / f"checkpoint-{turn_counter}.json"
            with checkpoint_path.open("w", encoding="utf-8") as fh:
                json.dump({"turn": turn_counter, "marker_count": len(marker)}, fh)
            _emit(
                {
                    "type": "checkpoint_result",
                    "request_id": frame.get("request_id"),
                    "path": str(checkpoint_path),
                }
            )
        elif frame_type == "stop":
            if args.ignore_stop:
                _emit({"type": "stop_ignored", "reason": frame.get("reason")})
                continue
            _emit({"type": "stopped"})
            return 0
        else:
            _emit({"type": "protocol_error", "reason": "unknown_type", "received": frame_type})


if __name__ == "__main__":
    sys.exit(main())
