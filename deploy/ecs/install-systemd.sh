#!/usr/bin/env bash
# install-systemd.sh — 在 ECS 上一次性安装 starcoplay-gateway systemd unit
#
# 使用场景：
#   1. 首次部署（从手动 nohup 切换到 systemd 管理）
#   2. service 文件内容有变更（如路径、环境变量、重启策略变化）
#
# 注意：本脚本幂等，可重复执行。
# 执行方式：
#   cd /root/workspace/starcoplay/star-coplay
#   sudo bash deploy/ecs/install-systemd.sh
#
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/workspace/starcoplay/star-coplay}"
SERVICE_SRC="$REPO_DIR/deploy/ecs/starcoplay-gateway.service"
SERVICE_DEST="/etc/systemd/system/starcoplay-gateway.service"
SERVICE_NAME="starcoplay-gateway"
LOG_DIR="${STARCOPLAY_LOG_DIR:-/var/log/starcoplay}"
PID_DIR="$REPO_DIR/.runtime/pids"
ENV_FILE="/etc/star-coplay.env"
LOGROTATE_SRC="$REPO_DIR/deploy/ecs/logrotate-starcoplay.conf"
LOGROTATE_DEST="/etc/logrotate.d/starcoplay"

log() { echo "[install-systemd] $*"; }

# 检查是否 root 或具有 sudo 能力
if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: 请以 root 身份或通过 sudo 运行此脚本" >&2
  exit 1
fi

# 检查 service 源文件是否存在
if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "ERROR: service 文件不存在: $SERVICE_SRC" >&2
  echo "       请确保已切换到最新 main 分支（git pull + git checkout main）" >&2
  exit 1
fi

# 检查 conda env 是否存在
CONDA_BIN="/root/miniconda3/envs/starcoplay-ws/bin/python"
if [[ ! -x "$CONDA_BIN" ]]; then
  echo "WARNING: conda python 不在预期路径 $CONDA_BIN" >&2
  echo "         service 文件中的 ExecStart 路径将无效，请手动修正后再执行" >&2
  exit 1
fi

# 检查 /etc/star-coplay.env 是否存在
if [[ ! -f "$ENV_FILE" ]]; then
  echo "WARNING: $ENV_FILE 不存在" >&2
  echo "         starcoplay-gateway.service 中的 EnvironmentFile 行将导致 service 启动失败" >&2
  echo "         请先创建 $ENV_FILE（参考 deploy/ecs/README.md 第 3 节），或手动注释掉 service 中的 EnvironmentFile 行" >&2
  read -r -p "是否继续安装（y/N）？" ans
  if [[ "${ans,,}" != "y" ]]; then
    echo "已取消" >&2
    exit 1
  fi
fi

# 确保日志和 PID 目录存在
mkdir -p \
  "$LOG_DIR/operations" \
  "$LOG_DIR/metrics" \
  "$LOG_DIR/audit" \
  "$LOG_DIR/sessions" \
  "$LOG_DIR/systemd" \
  "$LOG_DIR/incidents" \
  "$PID_DIR"
chmod 0750 "$LOG_DIR" "$LOG_DIR/operations" "$LOG_DIR/metrics" "$LOG_DIR/sessions" "$LOG_DIR/systemd" "$LOG_DIR/incidents"
chmod 0700 "$LOG_DIR/audit"
log "已确认目录存在: $LOG_DIR, $PID_DIR"

# 幂等性：若已有旧 service 文件，先备份
if [[ -f "$SERVICE_DEST" ]]; then
  BACKUP="${SERVICE_DEST}.bak.$(date +%Y%m%d%H%M%S)"
  cp "$SERVICE_DEST" "$BACKUP"
  log "已备份旧 service 文件: $BACKUP"
fi

# 复制 service 文件
cp "$SERVICE_SRC" "$SERVICE_DEST"
log "已安装: $SERVICE_DEST"

if [[ -f "$LOGROTATE_SRC" ]]; then
  cp "$LOGROTATE_SRC" "$LOGROTATE_DEST"
  chmod 0644 "$LOGROTATE_DEST"
  log "已安装 logrotate: $LOGROTATE_DEST"
else
  echo "WARNING: logrotate 配置不存在: $LOGROTATE_SRC" >&2
fi

# 重载 systemd daemon
systemctl daemon-reload
log "systemctl daemon-reload 完成"

# 设置开机自启（不立即 start，由人工或 CI cutover 流程控制）
systemctl enable "$SERVICE_NAME"
log "已启用开机自启: $SERVICE_NAME"

echo ""
echo "====== install-systemd.sh 完成 ======"
echo "service 文件已安装，开机自启已启用。"
echo ""
echo "下一步（cutover）："
echo "  1. 停旧进程：kill <nohup PID>"
echo "  2. 启动服务：systemctl start $SERVICE_NAME"
echo "  3. 检查状态：systemctl status $SERVICE_NAME --no-pager"
echo "  4. 查看日志：journalctl -u $SERVICE_NAME -n 50 --no-pager"
echo "  5. 按 log_id 查日志：bash deploy/ecs/starlog.sh logid <log_id>"
echo "  6. 完整流程见：deploy/ecs/cutover.md"
