# 本地环境搭建指南

## 前置条件

| 软件 | 版本 | 获取方式 |
|------|------|---------|
| 星露谷物语 | 1.6.14+ | Steam / GOG |
| SMAPI | 4.5.2+ | [smapi.io](https://smapi.io/) |
| .NET 6 SDK | 6.0+ | [dotnet.microsoft.com](https://dotnet.microsoft.com/download/dotnet/6.0) |
| Git | - | 已有 |

### 游戏 Mods 目录参考

| 平台 | Steam 默认路径 |
|------|---------------|
| **Windows** | `C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley\Mods\` |
| **macOS** | `~/Library/Application Support/Steam/steamapps/common/Stardew Valley/Contents/MacOS/Mods/` |
| **Linux** | `~/.steam/steam/steamapps/common/Stardew Valley/Mods/` |

## 一、克隆仓库

```bash
git clone https://github.com/luy-0/star-coplay.git
cd star-coplay
```

## 二、编译 & 部署 Mod

**macOS / Linux**：
```bash
chmod +x mod/scripts/build.sh
./mod/scripts/build.sh
```

**Windows（PowerShell）**：
```powershell
.\mod\scripts\build.ps1
```

如果游戏路径不是 Steam 默认，编辑构建脚本中的 `GAME_MODS_DIR`。

## 三、运行测试

启动游戏（SMAPI 会自动加载 Mod），在 SMAPI 控制台中输入测试命令:

```
agent_status        — 查看状态
agent_goto 64 15    — 寻路测试
agent_warp Town     — 传送测试
agent_report        — 生成测试报告
```

## 四、同步日志到 VPS（可选）

```bash
# 单次同步
./mod/scripts/sync-logs.sh

# 持续监听模式（文件变动自动同步，macOS 需要 fswatch）
./mod/scripts/sync-logs.sh watch
```

## 五、日常开发循环

```
VPS/本地编写代码 → git push
    ↓
本地 git pull → 构建脚本 → 启动游戏
    ↓
SMAPI 控制台测试 → agent_report
    ↓
同步日志 → Agent 分析报告 → 修复 → 循环
```

## 六、打包分发给队友

**macOS / Linux**：
```bash
./mod/scripts/build.sh --package
```

**Windows**：
```powershell
.\mod\scripts\build.ps1 -Package
```

生成的 zip 包可直接发给队友，解压到 Mods 目录即可使用。
