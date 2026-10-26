#!/usr/bin/env bash
set -euo pipefail

proxy="${CLAUDE_PROXY_URL:-http://127.0.0.1:7890}"

if [[ "${CLAUDE_PROXY_READY:-}" != "1" ]]; then
  echo "refusing to run claude: CLAUDE_PROXY_READY=1 is required" >&2
  exit 64
fi

if [[ "$proxy" =~ ^http://([^:/]+):([0-9]+)$ ]]; then
  host="${BASH_REMATCH[1]}"
  port="${BASH_REMATCH[2]}"
  if command -v nc >/dev/null 2>&1; then
    nc -z "$host" "$port" >/dev/null 2>&1 || {
      echo "refusing to run claude: proxy $host:$port is not reachable" >&2
      exit 64
    }
  fi
fi

if [[ $# -lt 3 ]]; then
  echo "usage: delegate-claude.sh <session-id> <mode:session|resume> <task>" >&2
  exit 64
fi

session_id="$1"
mode="$2"
task="$3"

case "$mode" in
  session) session_flag=(--session-id "$session_id") ;;
  resume) session_flag=(--resume "$session_id") ;;
  *)
    echo "invalid mode: $mode; expected session or resume" >&2
    exit 64
    ;;
esac

export HTTP_PROXY="$proxy"
export HTTPS_PROXY="$proxy"
export ALL_PROXY="$proxy"
export http_proxy="$proxy"
export https_proxy="$proxy"
export all_proxy="$proxy"

exec claude -p \
  "${session_flag[@]}" \
  --output-format json \
  --permission-mode acceptEdits \
  --append-system-prompt "You are the Claude Code executor. Codex is the driver/evaluator. Follow DRIVER.md. Do not edit DRIVER.md, DRIVER_STATE.json, or .driver/. Do not commit or push. Report changed files, tests run, and blockers." \
  "$task"
