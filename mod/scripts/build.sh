#!/bin/bash
# build.sh — 检测依赖 → 编译 Mod → 部署到游戏 Mods 目录
# 用法: ./scripts/build.sh [--package]
# 平台: macOS / Linux

set -euo pipefail

# ──────── 配置区 ────────

MOD_NAME="StarCoPlay"

# 自动检测平台和游戏路径
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS Steam 默认路径
    GAME_MODS_DIR="$HOME/Library/Application Support/Steam/steamapps/common/Stardew Valley/Contents/MacOS/Mods"
elif [[ "$OSTYPE" == "linux"* ]]; then
    # Linux Steam 默认路径
    GAME_MODS_DIR="$HOME/.steam/steam/steamapps/common/Stardew Valley/Mods"
fi

# 允许环境变量覆盖
GAME_MODS_DIR="${GAME_MODS_DIR}"

# ──────── 依赖检测 ────────

ERRORS=0

echo "=== StarCoPlay V2 Build ==="
echo ""
echo "Checking dependencies..."

# 1. dotnet SDK
if command -v dotnet &>/dev/null; then
    DOTNET_VER=$(dotnet --version 2>/dev/null || echo "unknown")
    echo "  [OK] dotnet SDK: $DOTNET_VER"
else
    echo "  [FAIL] dotnet SDK not installed"
    echo "     macOS: brew install dotnet-sdk"
    echo "     Linux: https://dotnet.microsoft.com/download/dotnet/6.0"
    ERRORS=$((ERRORS + 1))
fi

# 2. Game directory
GAME_DIR=$(dirname "$GAME_MODS_DIR")
if [ -d "$GAME_DIR" ]; then
    echo "  [OK] Game directory: $GAME_DIR"
else
    echo "  [FAIL] Game directory not found: $GAME_DIR"
    echo "     Verify Stardew Valley is installed via Steam/GOG"
    echo "     Set GAME_MODS_DIR env var if path differs"
    ERRORS=$((ERRORS + 1))
fi

# 3. SMAPI (Mods directory)
if [ -d "$GAME_MODS_DIR" ]; then
    echo "  [OK] SMAPI Mods directory exists"
else
    echo "  [FAIL] SMAPI not installed (Mods directory not found)"
    echo "     Install SMAPI: https://smapi.io/"
    ERRORS=$((ERRORS + 1))
fi

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "[FAIL] $ERRORS missing dependencies. Install them first."
    exit 1
fi

echo ""

# ──────── 编译 & 部署 ────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/../StarCoPlay"
CONTRACT_HARNESS="$SCRIPT_DIR/../ControlFraming.ContractHarness/ControlFraming.ContractHarness.csproj"
CONTRACT_FIXTURE="$SCRIPT_DIR/../../protocol/schemas/hosted-route-contract.json"
TARGET_DIR="$GAME_MODS_DIR/$MOD_NAME"

echo "Running control framing contract harness..."
dotnet run --project "$CONTRACT_HARNESS" -c Release -- "$CONTRACT_FIXTURE"

echo "Building $MOD_NAME..."
cd "$PROJECT_DIR"
dotnet build -c Release -p:GamePath="$GAME_DIR"

echo "Deploying to $TARGET_DIR..."
mkdir -p "$TARGET_DIR"

# Copy build artifacts
cp -f bin/Release/net6.0/*.dll "$TARGET_DIR/" 2>/dev/null || true
cp -f bin/Release/net6.0/*.pdb "$TARGET_DIR/" 2>/dev/null || true
cp -f manifest.json "$TARGET_DIR/"
# i18n 翻译文件（SMAPI Translation API 必需）
cp -rf i18n "$TARGET_DIR/"

echo ""
echo "[OK] Build & Deploy complete!"
echo "   Mod location: $TARGET_DIR"
echo "   Launch game to load the mod"

# ──────── 打包分发（可选） ────────

if [[ "${1:-}" == "--package" ]]; then
    PACKAGE_DIR="$(mktemp -d)/StarCoPlay"
    ZIP_PATH="$HOME/Desktop/StarCoPlay.zip"

    echo ""
    echo "Creating distribution package..."
    mkdir -p "$PACKAGE_DIR"
    cp -f "$TARGET_DIR"/*.dll "$PACKAGE_DIR/" 2>/dev/null || true
    cp -f "$TARGET_DIR"/manifest.json "$PACKAGE_DIR/"
    cp -rf "$TARGET_DIR"/i18n "$PACKAGE_DIR/" 2>/dev/null || true
    cd "$(dirname "$PACKAGE_DIR")"
    rm -f "$ZIP_PATH"
    zip -r "$ZIP_PATH" StarCoPlay/

    rm -rf "$(dirname "$PACKAGE_DIR")"

    echo "[OK] Package created: $ZIP_PATH"
fi
