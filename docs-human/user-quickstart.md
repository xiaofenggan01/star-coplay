# 玩家最小上手 (3 步)

> 不需要任何额外的 Python / 终端 / 启动器。装 Mod、复制 Token、打开开关。

## 1. 装 Mod

1. 安装 [SMAPI](https://smapi.io/)。
2. 把 `StarCoPlay/` 文件夹解压到 `Stardew Valley/Mods/`。

## 2. 拿 Token

1. 打开 https://star-coplay.luosea.cn/register 注册账号（昵称即可）。
2. 复制页面上的 `平台 Token`。

## 3. 进游戏配置

1. 启动游戏（通过 SMAPI），加载存档。
2. 在主菜单 / 游戏内打开「Generic Mod Config Menu」→ `Star CoPlay`。
3. 把 Token 粘到「平台 Token」字段。
4. 打开「启用 AI 控制」。
5. 保存设置 — Mod 自动连云端 Gateway 并唤醒搭子。

## 看数据 / 监控

浏览器打开 https://star-coplay.luosea.cn/dashboard ，用同一个 Token 登录，可看：

- 当前会话状态（在线 / Agent 实例 ID / 在途命令）
- 实时游戏事件流 与 Agent 日志（SSE）
- 最近 7 / 30 日聚合（事件数、估算在线分钟、LLM 调用与 token）

## 联机：让 AI 控制特定玩家

1. 进入联机存档后在游戏聊天框输入 `/agent_players`，会列出所有在线玩家及其 `UniqueMultiplayerID`。
2. 把目标 ID 粘到 GMCM 的「目标玩家 ID」字段（留空 = 控制本机玩家）。
3. 保存即可。

## 常用聊天命令

- `/agent_status` 状态概览
- `/agent_control enable|disable` 启停 AI
- `/agent_players` 列出在线玩家 ID
- `/agent_help` 全部命令

## 故障排查

| 现象 | 处理 |
|------|------|
| GMCM 顶部连接状态显示「认证失败」 | Token 错或已过期，去 `/register` 重新生成 |
| 显示「未填 Token — 填入后自动连接」 | 在 GMCM 粘贴 Token 并保存 |
| 看板显示离线 | 确认游戏已加载存档且 GMCM「启用 AI 控制」打开 |
| WebSocket 地址需改私有部署 | 在 GMCM「高级设置 → WebSocket 地址」填入你自己的 `wss://...` |
