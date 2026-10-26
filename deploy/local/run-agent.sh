#!/usr/bin/env bash
# 本地 Agent 启动脚本 — session_runner.py 包装
#
# session_runner.py 通过 stdin/stdout 收发 NDJSON 帧，本脚本只做环境准备。
#
# 用法：
#   echo '{"type":"hello","adapter_info":{"capabilities":[]}}' \
#     | ./deploy/local/run-agent.sh --agent-instance-id dev-001
#
# 选项（在其他参数之前）：
#   --install   强制重新安装 Python 依赖到 .venv
#
# 传给 session_runner.py 的参数透传：
#   --agent-instance-id <id>    必填
#   --platform-token <token>    可选
#   --user-id <uid>             可选
#   --platform-url <url>        可选
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
FORCE_INSTALL=0
[[ "${1:-}" == "--install" ]] && { FORCE_INSTALL=1; shift; }

if [[ ! -f "$VENV_DIR/bin/python" ]]; then
  echo "[local] creating venv at $VENV_DIR" >&2
  python3 -m venv "$VENV_DIR"
  FORCE_INSTALL=1
fi

PY="$VENV_DIR/bin/python"

if [[ "$FORCE_INSTALL" == "1" ]]; then
  echo "[local] installing dependencies..." >&2
  "$PY" -m pip install -q -r "$REPO_DIR/platform/backend/requirements.txt"
  "$PY" -m pip install -q -r "$REPO_DIR/agent/requirements.txt"
fi

exec "$PY" "$REPO_DIR/agent/session_runner.py" "$@"
