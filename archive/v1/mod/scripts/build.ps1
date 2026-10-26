# build.ps1 — Windows: 检测依赖 -> 编译 Mod -> 部署到游戏 Mods 目录
# 用法: .\scripts\build.ps1 [-Package] [-NoDeploy]
# MSBuild 默认会 EnableModDeploy，游戏运行时 StarCoPlay.dll 被占用会导致部署失败；
# 此处对 dotnet build 关闭自动部署，编译成功后由本脚本复制（失败时提示关闭游戏）。

param(
    [switch]$Package,
    [switch]$NoDeploy
)

$ErrorActionPreference = "Stop"

# ──────── 配置区 ────────

# Windows Steam 默认路径
$GAME_MODS_DIR = "D:\SteamLibrary\steamapps\common\Stardew Valley\Mods"
$MOD_NAME = "StarCoPlay"

# 如果使用 GOG:
# $GAME_MODS_DIR = "$env:ProgramFiles (x86)\GOG Galaxy\Games\Stardew Valley\Mods"

# ──────── 依赖检测 ────────

$Errors = 0

Write-Host "Checking dependencies..." -ForegroundColor Cyan

# 1. dotnet SDK
try {
    $dotnetVer = dotnet --version 2>$null
    Write-Host "  [OK] dotnet SDK: $dotnetVer" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] dotnet SDK not installed" -ForegroundColor Red
    Write-Host "     Download: https://dotnet.microsoft.com/download/dotnet/6.0"
    $Errors++
}

# 2. Game directory
$GAME_DIR = Split-Path $GAME_MODS_DIR -Parent
if (Test-Path $GAME_DIR) {
    Write-Host "  [OK] Game directory: $GAME_DIR" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] Game directory not found: $GAME_DIR" -ForegroundColor Red
    Write-Host "     Please verify Stardew Valley is installed via Steam/GOG"
    Write-Host "     Edit GAME_MODS_DIR in this script if path differs"
    $Errors++
}

# 3. SMAPI (Mods directory)
if (Test-Path $GAME_MODS_DIR) {
    Write-Host "  [OK] SMAPI Mods directory exists" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] SMAPI not installed (Mods directory not found)" -ForegroundColor Red
    Write-Host "     Install SMAPI first: https://smapi.io/"
    $Errors++
}

if ($Errors -gt 0) {
    Write-Host "`n[FAIL] $Errors missing dependencies. Please install them first." -ForegroundColor Red
    exit 1
}

Write-Host ""

# ──────── 编译 & 部署 ────────

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_DIR = Join-Path $SCRIPT_DIR "..\StarCoPlay"
$TARGET_DIR = Join-Path $GAME_MODS_DIR $MOD_NAME

Write-Host "Building $MOD_NAME..." -ForegroundColor Cyan
Push-Location $PROJECT_DIR
dotnet build -c Release -p:GamePath="$GAME_DIR" -p:EnableModDeploy=false
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "[FAIL] dotnet build failed. See MSBuild output above." -ForegroundColor Red
    Write-Host "       If game path is wrong, set GAME_MODS_DIR at the top of this script."
    exit $LASTEXITCODE
}
Pop-Location

$buildOutput = Join-Path $PROJECT_DIR "bin\Release\net6.0"

if ($NoDeploy) {
    Write-Host ""
    Write-Host "[OK] Build only (no deploy). Output: $buildOutput" -ForegroundColor Green
    exit 0
}

Write-Host "Deploying to $TARGET_DIR..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $TARGET_DIR | Out-Null

try {
    Copy-Item "$buildOutput\*.dll" $TARGET_DIR -Force -ErrorAction Stop
    Copy-Item "$buildOutput\*.pdb" $TARGET_DIR -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $PROJECT_DIR "manifest.json") $TARGET_DIR -Force -ErrorAction Stop
} catch {
    Write-Host ""
    Write-Host "[FAIL] Cannot copy mod files (DLL may be locked by the game)." -ForegroundColor Red
    Write-Host "       Close Stardew Valley / SMAPI, then run this script again." -ForegroundColor Yellow
    Write-Host "       Or use: .\build.ps1 -NoDeploy   (only compile to bin\Release\net6.0)" -ForegroundColor Yellow
    exit 1
}

# Copy Bridge/tools.json (needed at runtime for Agent layer tool discovery)
$bridgeSubDir = Join-Path $TARGET_DIR "Bridge"
New-Item -ItemType Directory -Force -Path $bridgeSubDir | Out-Null
$toolsJsonSrc = Join-Path $buildOutput "Bridge\tools.json"
if (Test-Path $toolsJsonSrc) {
    Copy-Item $toolsJsonSrc $bridgeSubDir -Force
    Write-Host "  [OK] Bridge/tools.json copied" -ForegroundColor Green
}

# Ensure test-reports directory exists
New-Item -ItemType Directory -Force -Path (Join-Path $TARGET_DIR "test-reports") | Out-Null

Write-Host ""
Write-Host "[OK] Deployed!" -ForegroundColor Green
Write-Host "   Mod location: $TARGET_DIR"
Write-Host "   Launch game to load the mod"
Write-Host ""
Write-Host "Tips:" -ForegroundColor Yellow
Write-Host "   - Press F7 to toggle HUD (hidden/compact/detailed)"
Write-Host "   - Type /agent_help in game chat for commands"
Write-Host "   - Type agent_status in SMAPI console"

# ──────── 打包分发（可选） ────────

if ($Package) {
    $PACKAGE_DIR = Join-Path $env:TEMP "StarCoPlay"
    $ZIP_PATH = Join-Path ([Environment]::GetFolderPath("Desktop")) "StarCoPlay.zip"

    Write-Host ""
    Write-Host "Creating distribution package..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $PACKAGE_DIR | Out-Null
    Copy-Item "$TARGET_DIR\*.dll" $PACKAGE_DIR -Force -ErrorAction SilentlyContinue
    Copy-Item "$TARGET_DIR\manifest.json" $PACKAGE_DIR -Force
    $pkgBridge = Join-Path $PACKAGE_DIR "Bridge"
    New-Item -ItemType Directory -Force -Path $pkgBridge | Out-Null
    Copy-Item "$TARGET_DIR\Bridge\tools.json" $pkgBridge -Force -ErrorAction SilentlyContinue

    if (Test-Path $ZIP_PATH) { Remove-Item $ZIP_PATH }
    Compress-Archive -Path $PACKAGE_DIR -DestinationPath $ZIP_PATH

    Remove-Item $PACKAGE_DIR -Recurse -Force

    Write-Host "[OK] Package created: $ZIP_PATH" -ForegroundColor Green
    Write-Host ""
    Write-Host "Instructions for teammates:" -ForegroundColor Yellow
    Write-Host "   1. Extract StarCoPlay.zip"
    Write-Host "   2. Copy StarCoPlay/ folder to game's Mods/ directory"
    Write-Host "      Windows Steam: C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley\Mods\"
    Write-Host "   3. Launch game via SMAPI"
    Write-Host "   4. Press F7 for HUD, type /agent_help in chat"
}
