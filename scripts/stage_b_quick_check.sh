#!/bin/bash
# Stage B 冒烟脚本：远程检查 Mac 端 Stage A Mod 加载状态 + dev_ws_server 状态。
# 用法：bash stage_b_quick_check.sh
set -uo pipefail

ts_exec() { /workspace/project-skills/tailscale-ssh/scripts/ts-connect.sh exec "$@"; }
log="~/.config/StardewValley/ErrorLogs/SMAPI-latest.txt"

echo "── SMAPI log size ──"
ts_exec "wc -l $log 2>/dev/null || echo MISSING"

echo "── Mod load + Simulator install lines (last 10) ──"
ts_exec "grep -E 'Star CoPlay|InputBridgeSimulator|inputSimulator' $log 2>/dev/null | tail -10 || echo NONE"

echo "── World ready / SaveLoaded (last 5) ──"
ts_exec "grep -E 'IsWorldReady|SaveLoaded|launching mod' $log 2>/dev/null | tail -5 || echo NONE"

echo "── dev_ws_server.py listening on :9527 ──"
ts_exec "lsof -nP -iTCP:9527 -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1 || echo NOT_LISTENING"

echo "── SDV process ──"
ts_exec "pgrep -f StardewValley | head -1 || echo NOT_RUNNING"
