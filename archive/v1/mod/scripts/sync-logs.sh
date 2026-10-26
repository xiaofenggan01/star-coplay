#!/bin/bash
# sync-logs.sh — 将本地 Mod 测试日志推送到 VPS
# 用法: ./scripts/sync-logs.sh [watch]
#   无参数: 单次同步
#   watch:  持续监听，文件变动自动同步

set -euo pipefail

# ──────── 配置区（按实际情况修改） ────────

VPS_HOST="43.153.181.107"
VPS_USER="ubuntu"
VPS_PORT="22"
SSH_KEY="$HOME/.ssh/id_stardew_sync"
VPS_TARGET="$HOME/happyclaw/data/groups/flow-mmw81wqa-srb3/star-coplay/mod/test-reports/"

# Mod 测试报告本地路径（DebugReporter 写入位置）
# macOS Steam 默认路径:
LOCAL_REPORTS="$HOME/Library/Application Support/Steam/steamapps/common/Stardew Valley/Contents/MacOS/Mods/StarCoPlay/test-reports/"

# 如果使用 GOG 或自定义路径，取消注释并修改:
# LOCAL_REPORTS="/Applications/Stardew Valley.app/Contents/MacOS/Mods/StarCoPlay/test-reports/"

# ──────── 逻辑区 ────────

SSH_CMD="ssh -i \"$SSH_KEY\" -o IdentitiesOnly=yes -p $VPS_PORT"

sync_once() {
    if [ ! -d "$LOCAL_REPORTS" ]; then
        echo "⚠️  本地报告目录不存在: $LOCAL_REPORTS"
        echo "   请先运行游戏并执行 agent_report 生成至少一份报告"
        echo "   或检查 LOCAL_REPORTS 路径是否正确"
        exit 1
    fi

    echo "📤 同步测试报告到 VPS..."
    rsync -avz --progress \
        -e "$SSH_CMD" \
        "$LOCAL_REPORTS" \
        "${VPS_USER}@${VPS_HOST}:${VPS_TARGET}"

    echo "✅ 同步完成 $(date '+%H:%M:%S')"
}

watch_sync() {
    echo "👀 监听模式：文件变动自动同步（Ctrl+C 退出）"
    echo "   监听目录: $LOCAL_REPORTS"

    if ! command -v fswatch &> /dev/null; then
        echo "⚠️  需要安装 fswatch: brew install fswatch"
        exit 1
    fi

    # 先同步一次
    sync_once

    # 持续监听
    fswatch -o "$LOCAL_REPORTS" | while read -r _; do
        sleep 1  # 防抖：等待文件写入完成
        sync_once
    done
}

case "${1:-}" in
    watch)
        watch_sync
        ;;
    *)
        sync_once
        ;;
esac
