# Agent↔Mod 协作协议

> 设计理念与架构决策，具体字段定义见 Adapter 层设计规格

---

## 核心思路

Agent（Python）和 Mod（C#）是两个独立进程，需要交换数据。
在 30h 黑客松约束下，选择**最简单可靠**的方案而非最优雅的方案。

## 通信机制：双向文件桥

**原则：各写各的文件，规避读写冲突。**

```
Mod（C#）                              Agent（Python）
   │                                       │
   ├── 只写 → game_state.json ──── 只读 ──┤
   ├── 只写 → events.jsonl ─────── 只读 ──┤
   ├── 只读 ← commands.jsonl ───── 只写 ──┤
   └── 只读 ← agent_status.json ── 只写 ──┘
```

### 为什么是四个文件

| 文件 | 写方 | 格式 | 设计理由 |
|------|------|------|---------|
| game_state.json | Mod | 覆写 | 状态快照，只关心"现在"，旧值无用 |
| events.jsonl | Mod | 追加 | 事件队列，每条都不能丢 |
| commands.jsonl | Agent | 追加 | 命令队列，每条都不能丢 |
| agent_status.json | Agent | 覆写 | Agent 当前状态，只关心"现在" |

### 为什么选文件桥而非 HTTP/WebSocket

- 跨语言最简单（C# 写文件、Python 读文件，各 5 行代码）
- 调试最方便（直接 cat 文件看内容）
- 文件轮询延迟（100-500ms）相比 LLM 决策延迟（1-3s）几乎无感
- HTTP 本质上也需要轮询，额外增加了网络栈复杂度

### 冲突规避

- 覆写文件：先写 `.tmp` 再 `rename`（原子替换）
- 追加文件：读方只处理完整行（以 `\n` 结尾），忽略写到一半的尾行
- 每个文件只有一个写方 → 不存在同时写的冲突

## 生命周期：Mod 是主人，Agent 是客人

```
游戏启动 → Mod 创建 bridge/ 目录 + 写入初始状态
         → 玩家手动启动 Agent（python agent.py）
         → Agent 发现 game_state.json → 开始工作

游戏退出 → Mod 写入 status: "shutdown"
         → Agent 自行退出

Agent 崩溃 → Mod 无感，游戏继续
           → 重启 Agent 即可，读最新状态继续
```

**为什么不让 Mod 启动 Agent**：C# 启 Python 子进程涉及路径、环境变量、错误处理，30h 不值得折腾。手动启动在 Demo 中完全可接受。

## 节奏同步：事件驱动 + 定时心跳

```
触发源 A：事件驱动（被动响应）
  events.jsonl 新事件 → Agent 判断是否反应 → 需要则调 LLM

触发源 B：定时心跳（主动行为）
  每 30s Agent 主动读状态 → 判断该不该主动做点什么
```

### 动作并发策略

- 同一时间只执行一个物理动作（移动/工具/交互）
- 聊天不算物理动作，可以边走边说
- Agent 等 LLM 返回时，已发出的移动命令继续执行

## 命令的请求-响应

Agent 通过 cmd_id 关联命令与结果：

```
Agent 写 commands.jsonl → {"cmd_id": "c001", "action": "move_to", ...}
Mod 执行后写 events.jsonl → {"type": "cmd_result", "cmd_id": "c001", "success": true, ...}
```

## 开发分工的接口契约

```
小海（Mod 侧）：
├── game_state.json 写入
├── events.jsonl 写入
├── commands.jsonl 读取与执行
├── bridge/ 目录管理
└── cmd_result 回写

康康（Agent 侧）：
├── game_state.json 读取
├── events.jsonl 读取与处理
├── commands.jsonl 写入
├── agent_status.json 写入
├── Agent Loop + LLM 调用
└── 记忆系统
```

## 务实策略

- **状态过期不特殊处理**：星露谷节奏慢，1-3s 内状态几乎不变；偶尔失败靠重试兜底
- **文件清理不做**：30h 内数据量不会涨到需要清理的程度
- **降级方案**：如文件桥出现性能问题（极不可能），可升级为 HTTP+SSE

---

## 关联文档

- `design-Adapter层扩展.md` — 事件类型与能力缺口分析
- `SMAPI技术摸底总结.md` — 技术基线与核心能力判定
- `协作测试方案.md` — 远程协作测试工作流
