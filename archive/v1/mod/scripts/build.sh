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
    # GOG: GAME_MODS_DIR="/Applications/Stardew Valley.app/Contents/MacOS/Mods"
elif [[ "$OSTYPE" == "linux"* ]]; then
    # Linux Steam 默认路径
    GAME_MODS_DIR="$HOME/.steam/steam/steamapps/common/Stardew Valley/Mods"
fi

# ──────── 依赖检测 ────────

ERRORS=0

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
    echo "     Edit GAME_MODS_DIR in this script if path differs"
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
TARGET_DIR="$GAME_MODS_DIR/$MOD_NAME"

echo "Building $MOD_NAME..."
cd "$PROJECT_DIR"
dotnet build -c Release -p:GamePath="$GAME_DIR"

echo "Deploying to $TARGET_DIR..."
mkdir -p "$TARGET_DIR"

# Copy build artifacts
cp -f bin/Release/net6.0/*.dll "$TARGET_DIR/" 2>/dev/null || true
cp -f bin/Release/net6.0/*.pdb "$TARGET_DIR/" 2>/dev/null || true
cp -f manifest.json "$TARGET_DIR/"

# Copy Bridge/tools.json (needed at runtime for Agent layer tool discovery)
mkdir -p "$TARGET_DIR/Bridge"
if [ -f "bin/Release/net6.0/Bridge/tools.json" ]; then
    cp -f "bin/Release/net6.0/Bridge/tools.json" "$TARGET_DIR/Bridge/"
    echo "  [OK] Bridge/tools.json copied"
fi

# Ensure test-reports directory exists
mkdir -p "$TARGET_DIR/test-reports"

echo ""
echo "[OK] Deployed!"
echo "   Mod location: $TARGET_DIR"
echo "   Launch game to load the mod"
echo ""
echo "Tips:"
echo "   - Press F7 to toggle HUD (hidden/compact/detailed)"
echo "   - Type /agent_help in game chat for commands"
echo "   - Type agent_status in SMAPI console"

# ──────── 打包分发（可选） ────────

if [[ "${1:-}" == "--package" ]]; then
    PACKAGE_DIR="$(mktemp -d)/StarCoPlay"
    ZIP_PATH="$HOME/Desktop/StarCoPlay.zip"

    echo ""
    echo "Creating distribution package..."
    mkdir -p "$PACKAGE_DIR"
    cp -f "$TARGET_DIR"/*.dll "$PACKAGE_DIR/" 2>/dev/null || true
    cp -f "$TARGET_DIR"/manifest.json "$PACKAGE_DIR/"
    mkdir -p "$PACKAGE_DIR/Bridge"
    cp -f "$TARGET_DIR/Bridge/tools.json" "$PACKAGE_DIR/Bridge/" 2>/dev/null || true

    cd "$(dirname "$PACKAGE_DIR")"
    rm -f "$ZIP_PATH"
    zip -r "$ZIP_PATH" StarCoPlay/

    rm -rf "$(dirname "$PACKAGE_DIR")"

    echo "[OK] Package created: $ZIP_PATH"
    echo ""
    echo "For teammates:"
    echo "   1. Extract StarCoPlay.zip"
    echo "   2. Copy StarCoPlay/ folder to game's Mods/ directory"
    echo "   3. Launch game via SMAPI"
    echo "   4. Press F7 for HUD, type /agent_help in chat"
fi
