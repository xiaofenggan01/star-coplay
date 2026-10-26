# Star Co-Play ⭐

> AI Co-Player for Stardew Valley — 一个活在星露谷世界里的 AI 搭子

进化酒馆 Agent 黑客松 | 2026.03.28-29 | Track 2 垂直进化型 Agent

## 项目简介

Star Co-Play 是一个星露谷物语 AI Agent，以 Player 2 的身份进入游戏世界，能够感知环境、自主决策、与玩家协作，并在互动中持续进化。

**核心差异化**：不是"知道"，是"在场"。AI 不是对话框里的助手，而是和你一起在星露谷世界里生活的搭子。

## 项目结构

```
star-coplay/
├── landing/          # Landing 页面（用户引流 + 产品展示）
├── mod/              # Stardew Valley Mod（C# / SMAPI）
│   └──               # 游戏感知层 + 行动执行层（Adapter）
├── agent/            # Agent 服务端（决策层 + 记忆层）
│   └──               # LLM 调用 + Tool Use 编排 + 记忆管理
├── assets/           # 设计素材、品牌物料、Pitch deck
└── docs/             # 项目文档
```

## 架构

```
LLM (Claude)        → 语义级决策（"去浇水" / "和玩家说话"）
    ↓ Tool Use
Agent 服务端         → 原子操作翻译 + 记忆管理
    ↓ JSON
Mod 客户端 (SMAPI)   → 游戏内执行（寻路、交互、对话）
```

## 团队

| 角色 | 成员 |
|------|------|
| Team Lead + 游戏工程 + Pitch | 小海 |
| AI 决策层 + 记忆层 | 康康 |
| 产品 + Demo 导演 + 前端 | 晓风 |
| 品牌策划 + 视觉 + 运营 | 图图 |

## 开发指南

### 环境要求

- **Mod**：.NET 8.0 SDK + Stardew Valley + SMAPI 4.x
- **Agent**：Python 3.11+ / Node.js 20+（待定）
- **Landing**：待定

### 快速开始

```bash
git clone https://github.com/luy-0/star-coplay.git
cd star-coplay
# 各模块的启动方式见各自目录下的 README
```

## License

MIT
