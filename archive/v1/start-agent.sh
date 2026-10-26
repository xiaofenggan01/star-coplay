#!/usr/bin/env bash
# StarCoplay Agent 启动脚本（macOS）
# 用法:
#   ./start-agent.sh          — 连接游戏 Mod 运行
#   ./start-agent.sh --dry    — 干运行模式（无需游戏，用于开发调试）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[info]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
err()   { echo -e "${RED}[error]${NC} $*"; }

# ── 1. Python 环境检查 ──
info "检查 Python 环境..."

if [ -d ".venv" ]; then
    source .venv/bin/activate
    ok "已激活 .venv"
elif command -v python3 &>/dev/null; then
    warn "未找到 .venv，使用系统 python3"
    warn "建议运行: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
else
    err "未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

# 检查 Python 版本 >= 3.10
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    err "需要 Python 3.10+，当前版本: $PY_VERSION"
    exit 1
fi
ok "Python $PY_VERSION"

# ── 2. 依赖检查 ──
info "检查依赖..."
python3 -c "import dotenv, requests" 2>/dev/null || {
    warn "缺少依赖，正在安装..."
    pip install -r requirements.txt -q
    ok "依赖安装完成"
}

# ── 3. .env 检查 ──
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        warn "未找到 .env，已从 .env.example 复制模板"
        cp .env.example .env
        err "请编辑 .env 填写 API_KEY 等配置后重新运行"
        exit 1
    else
        err "未找到 .env 文件，请参考 .env.example 创建"
        exit 1
    fi
fi

# 检查关键配置
set -a
source .env
set +a
if [ -z "${API_KEY:-}" ]; then
    err ".env 中 API_KEY 未设置"
    exit 1
fi
ok ".env 已加载"

# ── 4. Bridge 目录检查 ──
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry|--dry-run) DRY_RUN=true ;;
    esac
done

if [ "$DRY_RUN" = true ]; then
    export STARCoplay_AGENT_DRY_RUN=1
    warn "干运行模式 — 不需要游戏 Mod"
else
    BRIDGE="${STARDEW_BRIDGE_DIR:-}"
    # 展开 ~ 为实际 HOME 路径
    BRIDGE="${BRIDGE/#\~/$HOME}"
    if [ -z "$BRIDGE" ]; then
        err ".env 中 STARDEW_BRIDGE_DIR 未设置"
        echo ""
        info "macOS 路径示例:"
        echo "  /Users/\$USER/Library/Application Support/Steam/steamapps/common/Stardew Valley/Contents/MacOS/Mods/StarCoPlay/bridge"
        echo ""
        info "或使用干运行模式: ./start-agent.sh --dry"
        exit 1
    fi
    if [ ! -d "$BRIDGE" ]; then
        warn "Bridge 目录不存在: $BRIDGE"
        warn "请确认游戏已安装 StardewAgentBridge Mod 并至少启动过一次"
        info "先以干运行模式启动? 使用: ./start-agent.sh --dry"
        exit 1
    fi
    ok "Bridge 目录: $BRIDGE"
fi

# ── 5. 启动 Agent ──
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  StarCoplay Agent 启动中...${NC}"
if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}  模式: 干运行 (DRY RUN)${NC}"
else
    echo -e "${CYAN}  模式: 连接游戏${NC}"
fi
echo -e "${GREEN}========================================${NC}"
echo ""

exec python3 -m agent.main
