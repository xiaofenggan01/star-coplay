#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
proxy="${CLAUDE_PROXY_URL:-http://127.0.0.1:7890}"

usage() {
  cat >&2 <<'EOF'
usage:
  claude-driver init --cwd <workspace> [--objective <text>]
  claude-driver run --cwd <workspace> --task <text-or-file>
EOF
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip("\n"))[1:-1])'
}

make_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr 'A-Z' 'a-z'
  else
    python3 -c 'import uuid; print(uuid.uuid4())'
  fi
}

require_cwd() {
  [[ -n "${cwd:-}" ]] || { usage; exit 64; }
  mkdir -p "$cwd"
  cd "$cwd"
}

cmd="${1:-}"
shift || true

cwd=""
objective="Codex drives Claude Code execution with independent verification."
task=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd) cwd="$2"; shift 2 ;;
    --objective) objective="$2"; shift 2 ;;
    --task) task="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 64 ;;
  esac
done

case "$cmd" in
  init)
    require_cwd
    mkdir -p .driver/logs .driver/turns
    session_id="$(make_uuid)"
    cp "$skill_dir/templates/delegate-claude.sh" .driver/delegate-claude.sh
    chmod +x .driver/delegate-claude.sh
    sed \
      -e "s|__OBJECTIVE__|$objective|g" \
      "$skill_dir/templates/DRIVER.md" > DRIVER.md
    objective_json="$(printf '%s' "$objective" | json_escape)"
    sed \
      -e "s|__OBJECTIVE__|$objective_json|g" \
      -e "s|__SESSION_ID__|$session_id|g" \
      -e "s|__PROXY__|$proxy|g" \
      "$skill_dir/templates/DRIVER_STATE.json" > DRIVER_STATE.json
    printf 'initialized driver workspace\ncwd=%s\nsession_id=%s\nproxy=%s\n' "$cwd" "$session_id" "$proxy"
    ;;
  run)
    require_cwd
    [[ -n "$task" ]] || { usage; exit 64; }
    [[ -f DRIVER_STATE.json ]] || { echo "missing DRIVER_STATE.json; run init first" >&2; exit 64; }
    [[ -x .driver/delegate-claude.sh ]] || { echo "missing executable .driver/delegate-claude.sh; run init first" >&2; exit 64; }
    if [[ -f "$task" ]]; then
      task_text="$(cat "$task")"
    else
      task_text="$task"
    fi
    session_id="$(python3 -c 'import json; print(json.load(open("DRIVER_STATE.json"))["session_id"])')"
    turn_count="$(python3 -c 'import json; print(len(json.load(open("DRIVER_STATE.json")).get("turns", [])))')"
    next_turn="$((turn_count + 1))"
    mode="resume"
    if [[ "$turn_count" == "0" ]]; then
      mode="session"
    fi
    printf '%s\n' "$task_text" > ".driver/turns/turn-${next_turn}.txt"
    CLAUDE_PROXY_READY=1 CLAUDE_PROXY_URL="$proxy" \
      .driver/delegate-claude.sh "$session_id" "$mode" "$task_text" \
      | tee ".driver/logs/turn-${next_turn}-claude.json"
    ;;
  *)
    usage
    exit 64
    ;;
esac
