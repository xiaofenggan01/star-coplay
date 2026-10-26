# Agent 侧 Protocol 2.4.5 接入方案

## 背景

### 项目简介

Star CoPlay 是一个星露谷物语 AI Agent 项目。架构分三层：

- **Agent**（Python）：LLM 驱动的决策层，调用 OpenAI FC 格式的工具
- **Gateway**（Node.js）：中间层，管理 Agent 子进程，转发消息
- **Mod**（C# SMAPI Mod）：游戏内执行层，接收命令并操作游戏

通信链路：
```
Agent (Python) ←stdin/stdout NDJSON→ Gateway (Node.js) ←WebSocket→ Mod (C#)
```

### 问题根因

第三轮内测暴露：Agent 和 Mod 各自独立维护能力定义（参数结构、工具名、返回格式），导致 Agent 认为能做的事 Mod 不认识。根因是缺少双方共同遵守的强类型协议层。

### Protocol 2.4.5 解决方案

引入 protobuf 作为唯一手写事实源：

```
.proto 文件（唯一权威）
  → 生成 Python 类型（Agent 用）
  → 生成 C# 类型（Mod 用）
  → 手写 LLM FunctionSchema（对齐 proto 参数结构）
```

Proto 源文件位于 `protocol/proto/`，共 5 个文件：
- `transport.proto` — 传输信封（Envelope、Hello/HelloAck、CommandRequest/Result）
- `capability.proto` — 命令请求/结果的 oneof 分发
- `capability_params.proto` — 各能力的输入参数
- `capability_results.proto` — 各能力的执行结果
- `common.proto` — 共享类型（Direction、Error、LocationSnapshot 等）

Python 生成物已在 `agent/generated/protocol/`。

## 本次改动范围

本分支（`fix/after_third_beta+agent`）只改 Agent 侧，不动 Gateway 和 Mod。

### 改

| 模块 | 改动 |
|------|------|
| `agent/bridge/ws_bridge.py` | protobuf struct 构造 + Envelope 包装 + 握手状态机 |
| `agent/bridge/bridge_result.py` | 新增 `from_proto` 方法 |
| `agent/session_runner.py` | 握手调用集成 |
| `agent/slow_loop/core_tools.py` | 重写工具定义，参数对齐 proto |
| `agent/slow_loop/deliberative_loop.py` | 砍意图解析，params 透传 |

### 不动

| 模块 | 原因 |
|------|------|
| Gateway | 只做 NDJSON 透传，格式仍是 JSON |
| `search_wiki` / `read_wiki` / `remember` | 纯 Agent 侧工具，不走 Mod |
| `observe` | 后续单独处理 |
| L3 Skill 体系 | 本期不动 |

### 新增

| 模块 | 说明 |
|------|------|
| 握手状态机 | Hello → HelloAck → READY / REJECTED |
| `agent/tests/test_wsbridge_protocol.py` | 握手 + 命令收发单元测试 |

## 关键设计决策

| 决策 | 结论 | 原因 |
|------|------|------|
| 传输格式 | protobuf struct → `MessageToDict()` → JSON dict → `_emit` | Gateway 不改，只做透传；Agent 侧获得类型安全 |
| `_emit` 接口 | 仍接收 dict | 改动范围最小，session_runner 不动 |
| 握手方式 | 阻塞等待 10s | 简单可靠，进程级生命周期 |
| agent_id 来源 | `--agent-instance-id` 启动参数（已有） | 无需新增配置 |
| state/event 帧 | 暂不包 Envelope，走旧路径 | 后续迁移 |
| LLM Schema | 手写新版，不做自动生成器 | proto 注释面向开发者 ≠ LLM 友好 description |
| 意图解析层 | 砍掉（`go_to` 不再接受 `"Lewis"`） | 参数统一，LLM 自行查询坐标后决策 |
| effective_capabilities | 先写死，注释 TODO | 握手宣告机制后续启用 |
| 结果呈现 | 直接返回 proto result dict 给 LLM | 信息完整，LLM 自行提取有用部分 |

## 文档索引

| 文档 | 内容 |
|------|------|
| [wsbridge-protocol-design.md](./wsbridge-protocol-design.md) | WSBridge 改造详细设计（握手、Envelope、接口变化） |
| [core-tools-rewrite.md](./core-tools-rewrite.md) | core_tools.py 重写方案（新工具面 + 参数结构 + 透传改造） |

## 实现顺序

1. WSBridge 改造（握手 + Envelope + protobuf 收发）
2. session_runner 集成（握手调用 + 新帧类型路由）
3. core_tools.py 重写（参数对齐 proto，LLM 友好 description）
4. _process_trigger 透传改造（砍意图解析）
5. 单元测试
