# StarCoPlay

星露谷物语 AI 同行者——让 AI 角色和你一起生活在同一个农场世界里。

## 系统概览

四个子系统协同工作：

```
  玩家
   │ 游戏内聊天 / 触发事件
   ▼
┌─────────────────┐
│   mod/          │  C# SMAPI Mod
│  StarCoPlay     │  读取游戏状态、执行动作、WebSocket 连接
└────────┬────────┘
         │ WebSocket (game frames)
         ▼
┌─────────────────┐
│platform/backend/│  Python 后端服务
│  Gateway        │  认证、会话管理、spawn Agent 子进程
└────┬───────┬────┘
     │       │ HTTP
     │       ▼
     │  ┌─────────────────┐
     │  │  platform/web/  │  Next.js 用户界面
     │  │  Web App        │  注册、Dashboard、会话状态
     │  └─────────────────┘
     │ stdin/stdout NDJSON
     ▼
┌─────────────────┐
│   agent/        │  Python 决策代理
│  DeliberativeLoop│  LLM Function Calling 驱动的行为决策
└─────────────────┘
```

## 目录结构

```
star-coplay/
├── agent/                  # 决策代理（Python）
│   ├── main.py             # build_agent() 工厂函数
│   ├── session_runner.py   # 子进程入口，stdin/stdout NDJSON
│   ├── slow_loop/          # DeliberativeLoop：LLM 驱动决策环
│   ├── bridge/             # WSBridge：与 Gateway 的消息协议
│   ├── memory/             # 工作记忆 + MemorySystem
│   ├── knowledge/          # 游戏知识库（Wiki）
│   ├── v2_skills/          # L3 Skill 注册与执行
│   └── config/default.toml # 默认配置
│
├── platform/
│   ├── backend/            # Python 后端服务（WebSocket 网关 + HTTP API + LLM Router）
│   │   ├── app/            # 启动入口、bootstrap、lifecycle
│   │   ├── modules/        # 业务模块（gateway/http_api/llm/runtime/health）
│   │   ├── common/         # 共享基础设施（auth/db/settings/errors）
│   │   └── requirements.txt
│   │
│   ├── ws/                 # 兼容 shim（已迁移到 backend/，保留入口供旧引用）
│   │   └── scripts/run_local.py  # DeprecationWarning → 转调 backend/app/main.py
│   │
│   ├── web/                # 用户界面（Next.js 16 / App Router）
│   │   └── src/app/        # 页面：marketing、dashboard、guide、me
│   │
│   └── supabase/           # Supabase schema、Edge Functions、migrations
│
├── mod/
│   └── StarCoPlay/         # C# SMAPI Mod
│       ├── Bridge/         # WebSocket 客户端，向 Gateway 发帧
│       ├── Actions/        # 能力实现（move_to、say、emote …）
│       └── ModEntry.cs     # SMAPI 入口
│
├── dsh-plugin/             # DSH 原生插件（任务板 + 游戏内 HUD）
│   ├── src/                # route（本地 WS 路由）/ game（安装启动）/ tools / prompt
│   ├── lib/                # 构建产物（DSH 从仓库安装，需提交）
│   └── cordis.patch.yml    # DSH profile 补丁
│
├── deploy/
│   ├── ecs/                # 阿里云 ECS 生产部署
│   │   ├── autorun.sh      # 一键部署脚本（conda env）
│   │   ├── starcoplay-gateway.service  # systemd 服务单元
│   │   └── install-systemd.sh
│   └── local/              # 本地开发脚本
│       ├── run-gateway.sh  # 启动 platform/backend
│       ├── run-agent.sh    # 启动 agent（stdin/stdout 模式）
│       └── test-agent.py   # Agent 集成测试（4 项）
│
├── docs/                   # AI 可读设计文档（供 Agent 上下文加载）
├── docs-human/             # 人类可读文档（用户手册、架构笔记）
├── archive/                # 归档（v1 旧实现、legacy assets）
└── scripts/                # 一次性工具脚本
```

## 本地开发

### 依赖

- Python 3.11+（agent + platform/backend）
- Node.js 18+（platform/web）
- .NET 6+（mod，可选，仅修改 C# 时需要）

### 启动 Gateway

```bash
# 首次运行，创建 venv 并安装依赖
VENV_DIR=.venv ./deploy/local/run-gateway.sh --install

# 之后
./deploy/local/run-gateway.sh
```

Gateway 默认监听：WS `0.0.0.0:8765/ws`，HTTP `0.0.0.0:8766`。

### 启动 Web

```bash
cd platform/web
cp .env.example .env.local   # 填入 Supabase 配置
npm install
npm run dev
```

Web 默认地址：`http://localhost:3000`。

### 测试 Agent

```bash
# 运行集成测试（T1~T4）
VENV_DIR=.venv python3 deploy/local/test-agent.py

# 交互式启动（stdin 手动输入 NDJSON）
echo '{"type":"hello","adapter_info":{"capabilities":[]}}' \
  | VENV_DIR=.venv ./deploy/local/run-agent.sh --agent-instance-id dev-001
```

### 运行单元测试

```bash
# Agent 测试（593 个）
cd agent && python -m pytest

# Gateway/Backend 测试
cd platform/backend && python -m pytest

# Mod 编译检查
cd mod && dotnet build
```

## 评估

确定性的玩法语义评估集（离线、不依赖游戏与模型）：

```bash
make eval        # 跑全部场景
make eval-test   # 评估集自测
```

场景 schema、断言语法与当前基线见 [`eval/README.md`](eval/README.md)。push / PR 会自动跑（[`.github/workflows/eval.yml`](.github/workflows/eval.yml)）。

## LLM 配置

Agent 通过 `agent/.env` 配置 LLM（复制 `.env.example` 填写）：

```bash
cp agent/.env.example agent/.env
# 编辑 agent/.env，填入 LLM_MODEL 和对应的 API Key
```

无 LLM 配置时，Agent 以 fallback 模式运行（回复"让我想想..."），不影响架构验证。

## 生产部署

ECS 部署详见 `deploy/ecs/README.md`。入口：`autorun.sh`（conda env `starcoplay-ws`）+ systemd 服务管理 Gateway 进程。

