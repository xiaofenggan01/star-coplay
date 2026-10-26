# Star CoPlay Mod 快速安装指南

> 前提：已安装星露谷物语 + 已安装 SMAPI + 会用终端/命令行

## 一、安装 .NET SDK（编译用，一次性）

**macOS**：
```bash
brew install dotnet-sdk
```

**Windows**：
从 https://dotnet.microsoft.com/download/dotnet/6.0 下载安装器。

验证安装：
```bash
dotnet --version
```

## 二、获取代码 & 编译部署

```bash
git clone https://github.com/luy-0/star-coplay.git
cd star-coplay
```

**macOS / Linux**：
```bash
chmod +x mod/scripts/build.sh
./mod/scripts/build.sh
```

**Windows（PowerShell）**：
```powershell
.\mod\scripts\build.ps1
```

脚本会自动检测依赖，编译后部署到游戏 Mods 目录。

> **非 Steam 用户**：编辑构建脚本中的 `GAME_MODS_DIR` 变量。

## 三、启动游戏

正常通过 SMAPI 启动游戏，加载存档后即可使用。

## 四、使用方法

### 聊天框命令（推荐）

按 `T` 打开聊天框，输入 `/agent_help` 查看全部命令：

```
/agent_status        状态概览（位置/体力/金币）
/agent_goto 64 15    走到指定坐标
/agent_warp Farm     传送到农场
/agent_equip 斧头    装备物品
/agent_usetool 62 19 对坐标使用当前工具
/agent_npcs          查看当前地图 NPC
/agent_objects       查看附近物品
/agent_cancel        取消当前动作
```

### HUD 面板

按 **F7** 循环切换三种模式：
- **简约**：左上角小面板，显示状态/位置/体力
- **详细**：展开命令列表 + 最近操作日志
- **隐藏**：关闭面板

### SMAPI 控制台命令

在 SMAPI 控制台窗口直接输入（不需要 `/`）：
```
agent_status
agent_report
```

## 五、更新版本

```bash
cd star-coplay
git pull
```
然后重新执行对应平台的构建脚本。

## 遇到问题？

| 现象 | 解决 |
|------|------|
| `dotnet: command not found` | 安装 .NET SDK（见第一步） |
| `找不到 Mods 目录` | 确认 SMAPI 已安装，或编辑构建脚本中的路径 |
| Mod 未加载 | 看 SMAPI 控制台有无红色报错，截图发群 |
| Windows 执行策略限制 | PowerShell 中运行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
