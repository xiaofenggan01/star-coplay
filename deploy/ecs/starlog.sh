#!/usr/bin/env bash
set -euo pipefail

LOG_ROOT="${STARCOPLAY_LOG_DIR:-/var/log/starcoplay}"
PYTHON_BIN="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage:
  starlog.sh logid <log_id>
  starlog.sh runtime <runtime_id> [generation]
  starlog.sh binding <binding_id>
  starlog.sh command <command_id>
  starlog.sh errors
  starlog.sh metrics <metric_name>

Environment:
  STARCOPLAY_LOG_DIR defaults to /var/log/starcoplay
  PYTHON defaults to python3
EOF
}

cmd="${1:-}"
case "$cmd" in
  logid|binding|command|metrics)
    [[ $# -eq 2 ]] || { usage >&2; exit 2; }
    ;;
  runtime)
    [[ $# -eq 2 || $# -eq 3 ]] || { usage >&2; exit 2; }
    ;;
  errors)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    ;;
  -h|--help|help|"")
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

exec "$PYTHON_BIN" - "$LOG_ROOT" "$cmd" "${2:-}" "${3:-}" <<'PY'
import json
import re
import sys
from pathlib import Path

root, command, value, generation = sys.argv[1:5]
rotated_jsonl = re.compile(r"\.jsonl(?:\.\d+)?$")
sensitive_keys = {
    "authorization", "apikey", "api_key", "platform_token", "access_token",
    "refresh_token", "llm_relay_api_key", "cookie", "set-cookie",
}
bearer = re.compile(r"(?i)(Bearer )[A-Za-z0-9._~+/=-]+")


def redact(item):
    if isinstance(item, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in sensitive_keys else redact(child)
            for key, child in item.items()
        }
    if isinstance(item, list):
        return [redact(child) for child in item]
    if isinstance(item, str):
        return bearer.sub(r"\1[REDACTED]", item)
    return item


def matches(event):
    if command == "logid":
        return event.get("log_id") == value
    if command == "runtime":
        if event.get("runtime_id") != value:
            return False
        return not generation or (
            "generation" in event and str(event["generation"]) == generation
        )
    if command == "binding":
        return event.get("binding_id") == value
    if command == "command":
        return event.get("command_id") == value
    if command == "errors":
        return event.get("level") in {"ERROR", "CRITICAL"}
    if command == "metrics":
        return event.get("metric_name") == value or event.get("name") == value
    return False


base = Path(root)
paths = sorted(
    path for path in base.rglob("*")
    if not path.is_symlink() and path.is_file() and rotated_jsonl.search(path.name)
) if base.is_dir() else []

for path in paths:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(event, dict) and matches(event):
                    print(json.dumps(redact(event), ensure_ascii=False, separators=(",", ":")))
    except OSError:
        continue
PY
