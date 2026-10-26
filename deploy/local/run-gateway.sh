#!/usr/bin/env bash
# 本地开发启动脚本 — gateway (platform/ws)
# 用途：Mac/Linux 本地开发，直接用 Python venv，不走 conda
# 用法：./deploy/local/run-gateway.sh [--install]
#
# 选项：
#   --install   强制重新安装依赖到 .venv
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
FORCE_INSTALL=0
[[ "${1:-}" == "--install" ]] && FORCE_INSTALL=1

# 创建 venv（若不存在）
if [[ ! -f "$VENV_DIR/bin/python" ]]; then
  echo "[local] creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  FORCE_INSTALL=1
fi

PY="$VENV_DIR/bin/python"

# 安装依赖
if [[ "$FORCE_INSTALL" == "1" ]]; then
  echo "[local] installing dependencies..."
  "$PY" -m pip install -q -r "$REPO_DIR/platform/backend/requirements.txt"
  "$PY" -m pip install -q -r "$REPO_DIR/agent/requirements.txt"
fi

echo "[local] starting gateway (REPO=$REPO_DIR)"
exec "$PY" "$REPO_DIR/platform/ws/scripts/run_local.py"
