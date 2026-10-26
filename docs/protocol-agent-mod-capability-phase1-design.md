# 技术方案：Protocol 承接 Agent-Mod 执行能力的分层、适配与阶段一落地

---

## 0. 元信息

| 字段 | 内容 |
| --- | --- |
| 文档状态 | Draft |
| 作者 | @Codex |
| 创建日期 | 2026-05-18 |
| 最后更新 | 2026-05-18 |
| Reviewers | 待补充 |
| 关联链接 | `docs/模版.md`、`protocol/proto/*.proto`、`mod/StarCoPlay/V2/*`、`Makefile` |

---

## 1. TL;DR

本方案聚焦一件事：让 `protocol/proto` 中定义的执行能力，真正成为 Agent 与 Mod 之间稳定、可验证、可分阶段推进的通信契约，而不是继续把运行时细节暴露给 Agent 或散落在各类专用调用里。  

阶段一不重写现有 `mod` 的 L1/L2 执行内核，优先引入一层 **protocol adapter**，把 `proto` 请求可靠映射到当前已经可用的 `V2` handler。这样可以用最小风险把 `say / emote / move_to / go_to / go_to_bed / face / interact / use_tool / equip / menu_click / menu_close` 纳入统一协议面。  

当前仓库里已经有 `proto` 定义、Python/C# 生成代码、以及可工作的 `mod` 侧执行 handler，但三者还没有进入同一条主链。主链目前仍然是 `V2Command JSON -> AdapterV2 -> Handler -> V2Result JSON`。因此阶段一的关键不是“再做一套新执行逻辑”，而是把协议层与现有运行时清晰解耦，并建立统一的本地模拟测试链路。  

统一测试方式不依赖真实 Agent，不走真实对话编排，而是通过 `SMAPI console + 本地命令回放 + test_input 底层验证` 三层组合完成，确保后续阶段二能在同一套能力契约上继续推进。  

---

## 2. 背景与问题

### 2.1 目标背景

本次需求的本质，不是单独补几个游戏动作，而是把“Agent 如何向 Mod 发执行命令”这件事抽象成稳定协议。目标是让协议承担起 Agent 与 Mod 侧通信的标准形式，并逐步把现有执行能力收敛到协议面上。

用户这次明确点名的能力包括：

- `say`
- `emote`
- `move_to`
- `go_to`
- `go_to_bed`
- `face`
- `interact`
- `use_tool`
- `equip`
- `tp`
- `menu_click`
- `menu_close`

这些能力横跨三层：

1. `proto` 契约层：定义请求/结果、错误码、结构化参数。
2. `mod` 执行层：当前 `V2` handler、`InputBridge`、`ActionExecutor`、`CompoundDispatcher` 等运行时实现。
3. 测试与验证层：如何不依赖真实 Agent，把能力在本地稳定回放、观察和验收。

### 2.2 当前现状

当前仓库已经具备协议和执行的两套资产，但它们还没有收敛成同一条运行链路：

- `protocol/proto/*.proto` 已经定义了 `CommandRequest / CommandResult / Envelope` 以及本次关注能力的参数和结果结构。
- `Makefile` 已经支持把 `proto` 生成到：
  - Python：`agent/generated/protocol`
  - C#：`mod/StarCoPlay/Generated/Protocol`
- `mod` 中已经存在相当完整的 `V2` 执行链：
  - `AdapterV2`
  - `CommandProcessor`
  - `MoveToHandlerV2`
  - `GoToHandler`
  - `GoBedHandler`
  - `FaceHandlerV2`
  - `InteractHandlerV2`
  - `UseToolHandlerV2`
  - `EquipHandlerV2`
  - `MenuHandlersV2`
  - `SayHandler`
  - `EmoteHandler`
  - `TpHandler`

但主链目前仍然是：

```text
Agent / Gateway / FileBridge
  -> V2Command JSON
  -> CommandProcessor.DeserializeParams
  -> AdapterV2.Dispatch
  -> 当前各类 handler
  -> V2Result JSON
```

而不是：

```text
Envelope(CommandRequest proto)
  -> proto-native decoder
  -> capability adapter
  -> 统一执行内核
  -> proto-native CommandResult
```

### 2.3 当前真正的问题

如果不先把边界写清楚，后续阶段一、阶段二会反复返工，核心问题主要有五类。

#### 问题 A：协议参数与当前运行时参数并不一致

`proto` 里的定义更结构化，但 `mod` 当前 handler 仍然吃的是扁平 `Dictionary<string, object>` 参数。例如：

- `SayParams` 用的是 `content`，但 `SayHandler` 当前读取的是 `message`
- `EmoteParams` 用的是 `emote_type enum`，但 `EmoteHandler` 当前读取的是字符串 `type`
- `InteractParams` / `UseToolParams` 用的是 `target_tile`，但运行时读取的是 `x`,`y`
- `GoToParams` 是 `map_id + x + y + arrival_mode`，但当前 `GoToHandler` 吃的是语义化 `target`
- `GoToBedParams` 在协议中叫 `go_to_bed`，但运行时 capability 名是 `go_bed`
- `TpParams` 使用 `TilePosition + reason`，但 `TpHandler` 当前读取的是 `location/x/y`

这意味着阶段一不能假装“proto 已经直接可跑”，必须显式引入适配层。

#### 问题 B：运行时能力命名与协议命名不完全一致

最典型的是：

- 协议：`go_to_bed`
- 当前 `mod` 注册名：`go_bed`

如果不在方案里明确这个命名映射，后续阶段推进时，Agent 侧、协议层、Mod 侧会各说各话。

#### 问题 C：有些能力已经完成，但并不等于“纯协议化”

例如：

- `equip` 当前是“按键模拟 + direct API fallback”混合实现，不是纯输入模拟
- `go_bed` 当前语义上接近“warp + 睡觉”，但不是显式经由 `tp` capability 调度
- `use_tool` 设计目标是“仅手持工具类物品时使用”，但当前代码只检查 `CurrentTool != null`
- `interact` 不做距离校验，默认调用前已经通过 `move_to` 到邻接位置

如果阶段一文档把这些能力描述得比代码更“纯”，后续测试和联调就会持续出现“文档正确但代码不是这样”的冲突。

#### 问题 D：当前仓库已经有 proto 生成物，但运行主链仍未切过去

`mod/StarCoPlay/Generated/Protocol` 和 `agent/generated/protocol` 已经生成成功，但当前主链：

- WS 仍然主要传 `type=command / command_result / adapter_info` 的 JSON 帧
- `CommandProcessor` 仍然是 JSON 反序列化后调用 `AdapterV2.Dispatch`
- Agent 侧仍然主要依赖 `adapter_info` 与 `results.jsonl` / WS JSON result

这说明阶段一不应该直接去重构核心驱动逻辑，而是应该把 proto 视为 **契约源**，把当前 `V2` 视为 **执行内核**。

#### 问题 E：测试入口分散，没有统一实机模拟验收方式

当前已经存在三类可复用入口：

1. `SMAPI console` 调试命令：`mod/StarCoPlay/TestCommands.cs`
2. `test_input` 临时 capability：`ModEntry.HandleTestInputCommand`
3. `AnimationAwaiter` / `DebugReporter` / `agent_report` 这类执行完成与结果收集设施

但它们还没有被收敛成一套“阶段一统一测试方法”。

---

## 3. 目标与非目标

### Goals

- 建立一份对整个项目一致成立的系统方案，明确 `proto -> adapter -> V2 runtime -> result -> test harness` 全链路责任边界。
- 明确本次基础执行能力的协议契约、运行时映射、命名映射、完成状态与已知偏差。
- 阶段一采用“加适配层、不重写现有 handler 内核”的方式，把协议接到当前稳定执行链上。
- 建立统一的本地模拟测试方式，不依赖真实 Agent，不以真实对话驱动作为能力验收入口。
- 给出可执行的阶段一和阶段二推进顺序，使后续实现时能够按模块拆分、逐步替换，而不是一次性重写。
- 达成“阶段一协议切片完备”：本文列出的所有基础执行能力，都必须能从协议请求或协议等价 fixture 进入 adapter，再驱动当前 `V2` runtime，并产出可映射、可审计的结果。

### Non-Goals

- 不在本方案中重写 `mod` 当前 L1/L2 执行核心逻辑。
- 不在阶段一把全部 WS/文件桥接传输切换成 protobuf binary envelope。
- 不在阶段一直接废弃 `AdapterV2`、`V2Command`、`V2Result`。
- 不在本方案中引入依赖真实 Agent 对话编排的测试方式。
- 不把 `tp` 暴露给 LLM 作为常规能力。
- 不要求阶段一达到“全系统 proto-native 完备”：传输、Agent 工具注册、所有历史 query 能力、所有 result 细字段都不在本阶段一次性收敛。

---

## 4. 方案概览

**核心一句话**：以 `proto` 为能力契约源，以当前 `V2` handler 为执行内核，在中间增加一层 protocol adapter，把结构化协议请求映射到现有稳定执行链，并通过统一的本地模拟测试入口完成阶段一验收。

```text
proto contract (source of truth)
  -> generated protocol classes (Python / C#)
  -> protocol adapter
  -> runtime capability mapping
  -> existing V2 execution kernel
       - AdapterV2
       - L1 handlers
       - CompoundDispatcher / L2 handlers
       - InputBridge / ActionExecutor / AnimationAwaiter
  -> normalized result mapping
  -> local replay / SMAPI console / debug report
```

### 4.1 系统分层图

```text
┌──────────────────────────────────────────────────────────────┐
│ Layer 0: Transport                                           │
│  file bridge / ws json frames / future proto envelope        │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│ Layer 1: Protocol Contract                                   │
│  protocol/proto/common.proto                                 │
│  protocol/proto/capability_params.proto                      │
│  protocol/proto/capability_results.proto                     │
│  protocol/proto/capability.proto                             │
│  protocol/proto/transport.proto                              │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│ Layer 2: Protocol Adapter                                    │
│  proto request/result <-> current runtime param/result shape │
│  naming mapping / enum mapping / field normalization         │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│ Layer 3: Runtime Capability Router                           │
│  CommandProcessor -> AdapterV2 -> handlers                   │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│ Layer 4: Execution Kernel                                    │
│  L1: InputBridge, InputCombo, AnimationAwaiter, handlers     │
│  L2: CompoundDispatcher, GoToHandler, GoBedHandler           │
│  Core: ActionExecutor, StateReader, MenuNavigator            │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│ Layer 5: Verification                                         │
│  SMAPI console / test_input / local replay / debug report    │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 阶段总策略

- 阶段一：
  - 不改能力语义，不重写 handler
  - 明确契约
  - 增加 protocol adapter
  - 建立统一模拟测试
  - 让 `proto` 可以稳定驱动当前执行链
  - 必须把 replay/fixture 纳入验收，避免只验证旧的 `V2Command` 链路

- 阶段二：
  - 逐步把传输、结果、能力注册从 `adapter_info + V2Command` 迁移到更 proto-native 的形式
  - 收敛命名差异、字段差异和混合实现
  - 清理阶段一临时测试入口

### 4.3 完备性边界

阶段一需要的完备性不是“整个系统全部 proto-native”，而是“本文所列能力的协议切片闭环完备”。

必须完成：

1. `say / emote / face / move_to / go_to / go_to_bed / interact / use_tool / equip / menu_click / menu_close / tp` 都有唯一的协议到运行时映射。
2. 每个能力至少有一个成功 fixture 和一个失败 fixture，fixture 必须经过 `protocol request -> mapper -> V2Command -> runtime -> result mapper`。
3. 不支持或阶段一暂不实现的协议字段必须显式返回 `NOT_SUPPORTED` 或 `INVALID_PARAMS`，不能静默忽略。
4. `V2Result` 到协议结果的映射必须有统一规则；无法稳定填充的字段必须在 result 或测试记录中标明为阶段二缺口。
5. `TestCommands` 和 `test_input` 只能作为 runtime/输入层验证，不能替代 protocol 完备性验证。

阶段一不要求完成：

1. WebSocket / file bridge 全量切到 protobuf envelope。
2. 所有 handler 直接接收 generated proto class。
3. 所有 `CommandResult.oneof data` 字段 100% 填充。
4. `equip.item_category` 的完整选择策略。
5. `go_to_bed` 内部必须显式调用 `tp` capability。
6. `tp` 对 LLM 常规开放。

---

## 5. 方案详解

### 5.1 模块 A：协议层作为唯一能力契约源

**子问题**：协议应该在项目中承担什么角色，哪些文件是事实来源？

#### 5.1.1 事实来源

本次能力的协议事实来源是：

- [common.proto](/D:/PROJECT/star-coplay/protocol/proto/common.proto)
- [capability_params.proto](/D:/PROJECT/star-coplay/protocol/proto/capability_params.proto)
- [capability_results.proto](/D:/PROJECT/star-coplay/protocol/proto/capability_results.proto)
- [capability.proto](/D:/PROJECT/star-coplay/protocol/proto/capability.proto)
- [transport.proto](/D:/PROJECT/star-coplay/protocol/proto/transport.proto)

`Makefile` 已定义生成方式：

- Python 输出到 `agent/generated/protocol`
- C# 输出到 `mod/StarCoPlay/Generated/Protocol`

这意味着：

- `proto` 负责定义跨语言共享的输入、输出、错误码和消息形态
- 生成代码负责提供强类型载体
- 运行时可以暂时不直接用 protobuf binary，但不能再让运行时私自漂移出另一套契约

#### 5.1.2 本次能力在 proto 中的定义范围

协议层已经覆盖：

- 社交/表达：
  - `SayParams` / `SayResult`
  - `EmoteParams` / `EmoteResult`
  - `FaceParams` / `FaceResult`
- 移动/导航：
  - `MoveToParams` / `MoveToResult`
  - `GoToParams` / `GoToResult`
  - `GoToBedParams` / `GoToBedResult`
  - `InteractParams` / `InteractResult`
  - `UseToolParams` / `UseToolResult`
  - `EquipParams` / `EquipResult`
- 菜单：
  - `OpenMenuParams`
  - `QueryMenuParams`
  - `MenuClickParams`
  - `MenuCloseParams`
  - `MenuResult` / `MenuSnapshot`
- 内部：
  - `TpParams` / `TpResult`

#### 5.1.3 本层结论

后续阶段一、阶段二都必须以 `proto` 为能力契约源。  
即使当前执行链仍走 JSON，也应保证：

1. 协议的字段语义是主语义。
2. JSON 只是当前 transport/runtime bridge 的承载形式。
3. 任何运行时字段名差异，都必须在 adapter 层显式转换，而不是扩散到 Agent 或 handler 里。

---

### 5.2 模块 B：当前运行时主链与协议主链的关系

**子问题**：现有系统实际是怎么跑的，协议当前处于什么位置？

#### 5.2.1 当前主链

当前 `mod` 侧实际执行链路是：

```text
file commands.jsonl / ws type=command json
  -> AgentCommand
  -> CommandProcessor
  -> V2Command
  -> AdapterV2.Dispatch
  -> 对应 handler
  -> V2Result
  -> results.jsonl / ws type=command_result json
```

关键代码位置：

- [CommandProcessor.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/Bridge/CommandProcessor.cs)
- [AdapterV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/AdapterV2.cs)
- [WsBridge.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/Bridge/WsBridge.cs)

#### 5.2.2 proto 当前所在位置

当前 `proto` 代码已经生成，但没有进入主执行链的关键节点：

- 生成物存在：
  - [mod/StarCoPlay/Generated/Protocol](/D:/PROJECT/star-coplay/mod/StarCoPlay/Generated/Protocol)
  - [agent/generated/protocol](/D:/PROJECT/star-coplay/agent/generated/protocol)
- 但 `CommandProcessor` 并没有直接读取 `CommandRequest`
- `WsBridge` 也没有直接收发 `Envelope`
- Agent 侧慢循环仍主要依赖 `adapter_info` 和桥接结果解析

#### 5.2.3 为什么阶段一不能直接切协议主链

原因不是“做不到”，而是“不该在这个阶段这么做”：

- 当前 `V2` runtime 已经有可工作的 L1/L2 handler
- 协议字段与运行时字段还有明显差异
- 传输仍然以 JSON frame / JSONL 为主
- 如果阶段一同时做：
  - transport 切换
  - capability 契约切换
  - handler 参数切换
  - result 切换
  - 测试入口重做
  
  那就不是“协议落地”，而是“重写半个系统”

#### 5.2.4 本层结论

阶段一必须采用：

```text
proto contract
  -> protocol adapter
  -> current V2 runtime
```

而不是直接把当前 runtime 改写成 proto-native engine。

---

### 5.3 模块 C：能力分层与责任边界

**子问题**：这批能力在系统里分别属于哪一层，职责如何拆？

#### 5.3.1 L0 / L1 / L2 划分

- L0：输入模拟与底层注入能力
  - `InputBridge`
  - `InputCombo`
  - `MenuNavigator`
  - `test_input` 临时调试入口

- L1：同地图、单意图、原子能力
  - `say`
  - `emote`
  - `face`
  - `move_to`
  - `interact`
  - `use_tool`
  - `equip`
  - `menu_click`
  - `menu_close`
  - `open_menu`
  - `query_menu`
  - `tp`（内部）

- L2：编排型复合能力
  - `go_to`
  - `go_to_bed`

#### 5.3.2 设计原则

1. `move_to` 只承担同地图原子移动，不承接跨地图语义。
2. `go_to` 承接跨地图编排、路由和动态目标追踪。
3. `go_to_bed` 是单独的睡觉流程，不与通用导航混成一体。
4. `tp` 是系统内部兜底与恢复能力，不是 Agent 常规工具。
5. `menu_*` 单独作为菜单域基础原语，不混入普通交互语义。

#### 5.3.3 模块依赖图

```text
say/emote
  -> ActionExecutor

face/interact/use_tool/equip/menu_*
  -> InputCombo / MenuNavigator / AnimationAwaiter
  -> optional ActionExecutor fallback

move_to
  -> ActionExecutor.NavigateTo
  -> InputBridge / InputCombo
  -> AnimationAwaiter

go_to
  -> IMoveToHandler
  -> ActionExecutor (door interact only)
  -> route planner / resolver / compound dispatcher

go_to_bed
  -> ActionExecutor.WarpTo
  -> bed detection / dialog confirmation / NewDay fallback
```

---

### 5.4 模块 D：能力逐项映射与系统级链路检查

**子问题**：每个能力在协议、运行时、边界和测试上分别是什么状态？

### 5.4.1 `say`

- 协议：
  - `SayParams.content`
  - `SayResult.content_length`
- 当前运行时：
  - [SayHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/SayHandler.cs)
  - 读取参数名：`message`
  - 依赖：`Game1.chatBox != null`
  - 执行：`ActionExecutor.SendChat`
- 边界：
  - 当前不是严格按 `content` 字段
  - 非聊天场景或聊天框不可用会失败
- 阶段一适配策略：
  - `content -> message`
  - result 由 adapter 填补 `content_length`
- 测试重点：
  - 聊天框可用时成功发出
  - 聊天框不可用时返回结构化失败

### 5.4.2 `emote`

- 协议：
  - `EmoteParams.emote_type` 枚举
  - `EmoteResult.emote_type`
- 当前运行时：
  - [EmoteHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/EmoteHandler.cs)
  - 读取参数名：`type`
  - 执行：`ActionExecutor.DoEmote`
- 边界：
  - 当前吃字符串，不吃 proto enum
  - 失败时返回可用列表
- 阶段一适配策略：
  - `EmoteType enum <-> string type`
- 测试重点：
  - 各枚举到字符串映射正确
  - 非法枚举映射为清晰错误

### 5.4.3 `face`

- 协议：
  - `FaceParams.direction`
- 当前运行时：
  - [FaceHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/FaceHandlerV2.cs)
  - 支持 int 或 string 方向
  - 通过 `InputCombo.Face()` 入队
  - 通过 `AnimationAwaiter` 等待完成
- 边界：
  - 已是稳定 L1V2 能力
  - 主要差异在于协议用 enum，运行时吃 int/string
- 阶段一适配策略：
  - `Direction enum -> int/string`
- 测试重点：
  - 四方向映射正确
  - repeated face 幂等

### 5.4.4 `move_to`

- 协议：
  - `MoveToParams`
  - 模式：`EXACT / EDGE_WARP / ADJACENT`
- 当前运行时：
  - [MoveToHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/MoveToHandlerV2.cs)
  - L1 同地图原子移动
  - 三种模式：
    - A: `x,y [+ face_direction]`
    - B: `x,y,direction`
    - C: `x,y,adjacent_side`
  - 模式优先级：`adjacent_side > direction > exact`
- 核心链路：
  - `Start()` 解析参数
  - `NavigateTo` 或 hold direction
  - `Tick()` 驱动状态机
  - `AnimationAwaiter` / `InputBridge` 协同确认完成
  - `BuildCompletionResult()`
- 边界：
  - 只做同地图
  - 不追踪动态实体
  - 被外部中断时会尝试识别为 `PATH_BLOCKED`
- 阶段一适配策略：
  - `MoveToMode` 映射到当前三模式参数
  - `Direction enum` 映射到当前 `direction/face_direction/adjacent_side`
  - result 由 adapter 从当前 `V2Result.data` 补成 `MoveToResult`
- 测试重点：
  - exact 模式
  - edge warp 模式
  - adjacent 模式
  - 超时
  - 外部中断

### 5.4.5 `go_to`

- 协议：
  - `GoToParams.map_id + x + y + arrival_mode`
  - 更偏“坐标化跨地图导航”
- 当前运行时：
  - [GoToHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/Compound/GoToHandler.cs)
  - 当前吃的是语义化 `target + optional face_direction`
  - 状态机：
    - `Resolving`
    - `Routing`
    - `Segment`
    - `Tracking`
    - `Done/Failed`
- 核心链路：
  - 名称/目标解析
  - 路由规划
  - 按段派发 `move_to`
  - door interact / edge warp / dynamic tracking
- 关键事实：
  - 当前实现比 proto 中的 `map_id+x+y` 更强，支持 NPC、动物、建筑、TargetRef、坐标语法、玩家等语义目标
  - 但协议定义更窄
- 阶段一适配策略：
  - 阶段一不削弱当前 handler
  - protocol adapter 先支持“坐标化 go_to 请求 -> 语义 target”
    - `map_id + x + y -> "Map:x,y"`
    - `arrival_mode` 决定是否追加 `face_direction` 或邻接逻辑
  - 不在阶段一强行重写 `GoToHandler` 参数模型
- 测试重点：
  - 单纯跨地图坐标到达
  - 中间 edge warp
  - 终点 facing
  - route fail

### 5.4.6 `go_to_bed`

- 协议：
  - 名称：`go_to_bed`
  - 语义：`tp + 移动 + 交互 + 睡觉 + 次日推进`
- 当前运行时：
  - 实际注册名：`go_bed`
  - [GoBedHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/Compound/GoBedHandler.cs)
  - 本质是：
    - warp home
    - 找床
    - 定位到床
    - 触发交互
    - 多轮确认对话
    - 必要时 `Game1.NewDay`
- 关键事实：
  - 当前实现没有显式调 `tp` capability
  - 实际是直接调用 `ActionExecutor.WarpTo`
  - 运行时名字不是 `go_to_bed`
- 阶段一适配策略：
  - 协议层维持 `go_to_bed`
  - 运行时映射到 `go_bed`
  - result 先尽量填充已有字段，未覆盖字段明确标注为阶段二补齐
- 测试重点：
  - 不在家时回家并睡
  - 已在家时直接睡
  - 无床/异常状态失败
  - 次日推进判定

### 5.4.7 `interact`

- 协议：
  - `InteractParams.target_tile`
- 当前运行时：
  - [InteractHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/InteractHandlerV2.cs)
  - 读取：`x,y`
  - 执行：`InputCombo.Interact()` -> `X`
- 边界：
  - 不做距离校验
  - 假设调用前已经处于邻接位置
- 阶段一适配策略：
  - `target_tile -> x,y`
- 测试重点：
  - 邻接状态成功
  - 非邻接状态失败行为可观测

### 5.4.8 `use_tool`

- 协议：
  - `UseToolParams.target_tile`
  - `charge_level`
- 当前运行时：
  - [UseToolHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/UseToolHandlerV2.cs)
  - 读取：`x,y,charge_level`
  - 执行：`InputCombo.UseTool()` / `UseToolCharged()`
- 键位事实：
  - 语义上对应 useToolButton
  - 当前键盘实现映射为 `C`
- 边界：
  - 当前只检查 `CurrentTool != null`
  - 并未严格校验“是否工具类物品”
- 阶段一适配策略：
  - `target_tile -> x,y`
  - 保持 `charge_level`
  - 文档明确写当前校验边界
- 测试重点：
  - 普通使用
  - charged 使用
  - 未持工具失败

### 5.4.9 `equip`

- 协议：
  - `slot > item_name > item_category`
- 当前运行时：
  - [EquipHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/EquipHandlerV2.cs)
  - 现实实现：
    - `slot`：直接改 `CurrentToolIndex`
    - `item_name` 命中前 12 槽：`InputCombo.EquipSlot()`
    - 否则 fallback `ActionExecutor.EquipItemByName()`
- 关键事实：
  - 当前不是纯输入模拟
  - 当前也没有按协议完整实现 `item_category`
- 阶段一适配策略：
  - 先支持 `slot` 与 `item_name`
  - `item_category` 作为阶段二补齐项
  - result 用 `method=direct_api/keypress` 明确落点
- 测试重点：
  - slot 命中
  - item_name 命中工具栏
  - item_name 命中背包深层 fallback

### 5.4.10 `tp`

- 协议：
  - `TpParams.target + face_direction + reason`
  - 内部能力，不对 LLM 暴露
- 当前运行时：
  - [TpHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/TpHandler.cs)
  - 读取：`location,x,y`
  - 执行：`ActionExecutor.WarpTo`
  - 无 `x,y` 时可用 `MapData.GetSafeWarpTarget`
- 阶段一适配策略：
  - 仅保留内部调用路径
  - `TilePosition -> location/x/y`
  - `reason` 进入审计字段
- 测试重点：
  - 指定坐标传送
  - safe warp target
  - 不暴露给 Agent 常规工具

### 5.4.11 `menu_click`

- 协议：
  - `component_id`
  - `source`
- 当前运行时：
  - [MenuHandlersV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/MenuHandlersV2.cs)
  - 需要菜单已打开
  - 委托 `MenuNavigator.Click`
- 边界：
  - 点击失败返回不可达
- 阶段一适配策略：
  - 结构基本一致
- 测试重点：
  - page source
  - tab source
  - 无菜单失败

### 5.4.12 `menu_close`

- 协议：
  - 无参
  - 幂等
- 当前运行时：
  - [MenuHandlersV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/MenuHandlersV2.cs)
  - 无菜单时 success + `already_closed=true`
- 阶段一适配策略：
  - 与协议基本一致
- 测试重点：
  - 菜单存在时关闭
  - 菜单不存在时幂等成功

---

### 5.5 模块 E：当前命名映射与字段映射

**子问题**：协议与运行时之间具体有哪些必须显式处理的映射？

### 5.5.1 capability 名称映射

| proto capability | 当前 runtime capability | 说明 |
| --- | --- | --- |
| `say` | `say` | 一致 |
| `emote` | `emote` | 一致 |
| `face` | `face` | 一致 |
| `move_to` | `move_to` | 一致 |
| `go_to` | `go_to` | 一致，但参数模型不同 |
| `go_to_bed` | `go_bed` | 不一致，必须映射 |
| `interact` | `interact` | 一致 |
| `use_tool` | `use_tool` | 一致 |
| `equip` | `equip` | 一致 |
| `menu_click` | `menu_click` | 一致 |
| `menu_close` | `menu_close` | 一致 |
| `tp` | `tp` | 一致，但内部能力 |

### 5.5.2 参数字段映射

| proto params | runtime params | 说明 |
| --- | --- | --- |
| `SayParams.content` | `message` | 当前 handler 读 `message` |
| `EmoteParams.emote_type` | `type` | enum 需要转字符串 |
| `InteractParams.target_tile` | `x,y` | 结构体拆平 |
| `UseToolParams.target_tile` | `x,y` | 结构体拆平 |
| `TpParams.target.map_id/x/y` | `location/x/y` | 名称不同 |
| `GoToParams.map_id/x/y` | `target="Map:x,y"` | 当前 handler 走语义 target |
| `GoToBedParams` | `go_bed` 无需复杂参数 | 仅 timeout 对齐 |

### 5.5.3 结果字段映射

阶段一对结果采取“adapter 补齐”策略：

- 运行时已有结构，优先重用
- 运行时结果不足的字段，在 adapter 层通过上下文补填
- 仍然无法稳定填出的字段，文档明确标为阶段二

阶段一必须先统一公共结果语义：

| V2 result | proto result | 规则 |
| --- | --- | --- |
| `V2Result.Success=true` 且 `Status=ok` | `success=true, status=COMPLETED` | 正常完成 |
| `V2Result.Success=true` 且 `Status=partial` | `success=true, status=COMPLETED` 或阶段二扩展 | 阶段一保留 `observation` 和 `data.partial_reason`，不新增 proto 状态 |
| `V2Result.Error.Code=TIMEOUT/NAVIGATION_TIMEOUT/WARP_TIMEOUT` | `success=false, status=TIMEOUT` | 超时类错误优先映射到 `TIMEOUT` |
| `V2Result.Error.Code=CANCELLED` | `success=false, status=CANCELLED` | 取消类错误优先映射到 `CANCELLED` |
| 其他 `V2Result.Success=false` | `success=false, status=FAILED` | 按 error code 映射 |
| `V2Result.Observation` | `CommandResult.observation` | 必须保留给 Agent 理解 |
| `V2Result.Data` | `CommandResult.data` | 能结构化映射则填 oneof；不能映射则记录 phase1 gap |

错误码映射必须集中在 `ProtoResultMapper`，不能散落到 handler 中。阶段一允许 `CommandResult.data` 字段不完整，但不允许公共字段缺失：`command_id / success / status / capability / observation / error` 必须稳定。

能力特定结果的阶段一最低要求：

| capability | 阶段一最低 result 要求 | 允许阶段二补齐 |
| --- | --- | --- |
| `say` | `content_length` | 聊天实际可见性细节 |
| `emote` | `emote_type` | 动画完成帧级信息 |
| `face` | `final_direction` | 输入队列细节 |
| `move_to` | 目标坐标、最终坐标、完成/失败原因 | 完整路径、每段耗时 |
| `go_to` | 目标地图/坐标、最终位置、路由失败原因 | 逐段 route trace、tracking trace |
| `go_to_bed` | `home_location`、是否触发睡觉、停止原因 | 完整 `tp/move/interact/NewDay` 分段结果 |
| `interact` | 目标 tile、是否触发输入 | 具体对象效果分类 |
| `use_tool` | 目标 tile、工具名、charge_level | 工具类别严格校验、地块变化详情 |
| `equip` | slot/item/method、是否找到 | `item_category` 完整策略 |
| `menu_click` | component/source、是否点击成功 | 菜单转移前后完整快照 |
| `menu_close` | `already_closed` 或关闭成功 | 菜单关闭动画细节 |
| `tp` | from/target/final、reason | 审计链路与调用方追踪 |

---

### 5.6 模块 F：阶段一 protocol adapter 设计

**子问题**：阶段一到底新增什么，不新增什么？

#### 5.6.1 新增内容

阶段一建议新增一个独立适配层，职责是：

1. 接收 proto request 或 proto 等价结构
2. 做 capability 名称映射
3. 做参数字段映射
4. 构建当前 `V2Command`
5. 调用现有 `CommandProcessor / AdapterV2 / handler`
6. 收到 `V2Result` 后映射为 proto result

建议拆成两个方向：

- `ProtoToRuntimeCapabilityAdapter`
- `RuntimeToProtoResultAdapter`

阶段一实现时建议拆成以下文件：

```text
mod/StarCoPlay/Protocol/
  CapabilityNameMap.cs
  ProtoCommandMapper.cs
  ProtoResultMapper.cs
  ProtocolReplayCommand.cs
  ProtocolFixtureLoader.cs
  ProtocolMappingDiagnostics.cs
```

职责边界：

| 文件 | 职责 | 禁止事项 |
| --- | --- | --- |
| `CapabilityNameMap.cs` | 协议名与 runtime 名互转，例如 `go_to_bed -> go_bed` | 不读取游戏状态 |
| `ProtoCommandMapper.cs` | `CommandRequest` 或等价 fixture 转 `V2Command` | 不调用 handler，不写 result |
| `ProtoResultMapper.cs` | `V2Result` 转协议 result 或协议等价 JSON | 不修正 runtime 行为 |
| `ProtocolReplayCommand.cs` | 注册并执行 `protocol_replay*` 调试命令 | 不绕过 mapper 直接构造旧命令 |
| `ProtocolFixtureLoader.cs` | 加载内置/外部 fixture，校验 schema | 不做能力语义转换 |
| `ProtocolMappingDiagnostics.cs` | 输出映射前后参数、错误、phase1 gap | 不参与业务决策 |

`ProtoCommandMapper` 的最小输入可以是 generated `CommandRequest`，也可以是与 `CommandRequest` 字段同构的 JSON fixture。阶段一为了降低实机调试成本，可以优先支持 JSON fixture，但字段名和 oneof 结构必须贴近 proto，不能退化成当前 `V2Command` fixture。

推荐 fixture 形态：

```json
{
  "command_id": "fixture-say-ok",
  "timeout_ms": 5000,
  "source": "COMMAND_SOURCE_TEST",
  "capability": "say",
  "params": {
    "say": {
      "content": "hello"
    }
  }
}
```

映射输出必须可观测：

```json
{
  "command_id": "fixture-say-ok",
  "proto_capability": "say",
  "runtime_capability": "say",
  "runtime_params": {
    "message": "hello"
  },
  "phase1_gaps": []
}
```

#### 5.6.2 阶段一必做映射

| capability | 协议输入 | runtime 输出 | 不支持/特殊处理 |
| --- | --- | --- | --- |
| `say` | `say.content` | `message` | 空内容返回 `INVALID_PARAMS` |
| `emote` | `emote.emote_type` | `type` 字符串 | `UNSPECIFIED` 返回 `INVALID_PARAMS` |
| `face` | `face.direction` | `direction` int 或 string | `UNSPECIFIED` 返回 `INVALID_PARAMS` |
| `move_to` | `move_to.mode/x/y/direction/face_direction/adjacent_side` | 三模式参数 | `UNSPECIFIED mode` 返回 `INVALID_PARAMS` |
| `go_to` | `go_to.map_id/x/y/arrival_mode/stand_side/face_direction` | `target="Map:x,y"` + 可用方向字段 | `ARRIVAL_ADJACENT` 若不能完整落地，必须记录 gap 或返回 `NOT_SUPPORTED` |
| `go_to_bed` | `go_to_bed.timeout_frames` | capability=`go_bed` | timeout 当前仅尽量对齐，不保证 handler 消费 |
| `interact` | `interact.target_tile` | `x,y` | 缺失 target 返回 `INVALID_PARAMS` |
| `use_tool` | `use_tool.target_tile/charge_level` | `x,y,charge_level` | charge 越界返回 `INVALID_PARAMS` |
| `equip` | `equip.slot/item_name/item_category` | `slot/item_name` | `item_category` 阶段一未实现则返回 `NOT_SUPPORTED` |
| `menu_click` | `menu_click.component_id/source` | `component_id/source` | `source=UNSPECIFIED` 可默认 page 或返回 `INVALID_PARAMS`，实现前必须固定 |
| `menu_close` | 无 | 无 | 无菜单成功保持幂等 |
| `tp` | `tp.target/face_direction/reason` | `location,x,y` + audit 字段 | `reason=UNSPECIFIED` 返回 `INVALID_PARAMS` |

#### 5.6.3 阶段一不得静默降级的字段

以下字段如果协议传入而当前 runtime 不能完整消费，必须显式处理：

- `go_to.arrival_mode=ARRIVAL_ADJACENT` 与 `stand_side`
- `equip.item_category`
- `tp.reason`
- `tp.face_direction`
- `go_to_bed.timeout_frames`

处理方式只有三种：

1. 完整映射到 runtime。
2. 返回 `NOT_SUPPORTED`，并在 observation 中说明阶段一未支持。
3. 允许执行，但在 diagnostics/result 中记录 `phase1_gaps`，用于实机验收时确认风险。

#### 5.6.4 接收边界与校验分层

Mod 侧不能假设 Agent 永远发来合法命令。即使 Agent 侧后续会做 schema / mapper 校验，Mod 的 protocol adapter 仍然必须作为接收边界重新校验一次；否则真实 transport 切换后，非法 `CommandRequest` 可能直接进入 runtime handler。

阶段一的校验目标不是把所有游戏业务规则都提前拦掉，而是保证协议结构、字段组合和不支持能力不会穿透到执行内核。

| 层级 | 阶段一是否必须 | 校验内容 | 失败处理 |
| --- | --- | --- | --- |
| transport / replay 入口 | 必须 | request 可解析；协议版本匹配；只包含一个 command body | 返回 protocol error 或 replay 失败，不进入 adapter |
| `ProtoCommandMapper` 基础校验 | 必须 | `command_id` 非空；`timeout_ms > 0`；`source != UNSPECIFIED`；必须设置且只设置一个 params oneof | 返回 `CommandResult(REJECTED)`，错误码 `INVALID_PARAMS` |
| capability/oneof 一致性 | 必须 | `capability` 必须与 params 分支一致，例如 `capability=say` 只能配 `SayParams` | 返回 `INVALID_PARAMS` |
| capability 支持性 | 必须 | capability 必须在阶段一映射表内；internal-only capability 只能由授权路径触发 | 未知能力返回 `CAPABILITY_MISSING`；阶段一不支持字段返回 `NOT_SUPPORTED` 或 `INVALID_PARAMS` |
| 参数轻量语义 | 必须 | enum 不为 `UNSPECIFIED`；`use_tool.charge_level` 在 0-4；`menu_click.source` 合法；`tp.reason` 必填 | 返回 `INVALID_PARAMS` |
| runtime 业务校验 | 不由 adapter 替代 | tile 是否可达、物品是否存在、菜单组件是否存在、当前状态是否允许执行 | 由对应 handler 返回 `REJECTED / FAILED / TIMEOUT` 与 observation/error |

阶段一 adapter 必须拒绝或显式标注以下输入：

- 缺失 `CommandRequest.params`。
- `capability` 与 oneof 分支不一致。
- `source=COMMAND_SOURCE_UNSPECIFIED`。
- `timeout_ms <= 0`。
- `Direction / EmoteType / MoveToMode / ArrivalMode / MenuComponentSource / TeleportReason` 为 `UNSPECIFIED` 且业务语义不允许。
- `use_tool.charge_level < 0` 或 `> 4`。
- `equip.item_category` 在阶段一未实现时被传入。
- `go_to.ARRIVAL_ADJACENT + stand_side` 在阶段一不能完整消费时未记录 gap 或未返回不支持。
- `tp.reason` 缺失，或 `tp` 来自非内部/诊断路径。

阶段一 adapter 不做以下业务校验：

- 不检查 `move_to/go_to` 的最终 tile 是否可达，这属于导航 runtime。
- 不检查 `interact/use_tool` 目标 tile 是否真的有可交互对象。
- 不检查背包是否存在 `equip.item_name` 指定物品。
- 不检查当前菜单里是否存在 `menu_click.component_id`。
- 不判断当前工具是否适合目标 tile。

这些校验留给当前已经存在的 runtime handler。adapter 只负责把明显非法的协议输入拦在执行层外，并保证失败结果结构统一、可审计。

#### 5.6.5 不新增内容

阶段一不应新增：

- 新的 `move_to` 执行核心
- 新的 `go_to` 路由内核
- 新的 `go_to_bed` 睡觉流程
- 直接替代 `AdapterV2` 的大一统驱动
- 全量传输协议切换

#### 5.6.6 适配层位置建议

建议放在：

```text
mod/StarCoPlay/Protocol/
  ProtoCommandMapper.cs
  ProtoResultMapper.cs
  CapabilityNameMap.cs
```

原因：

- 避免污染 `V2` 内核目录
- 明确这是“协议层适配”，不是“执行内核”
- 便于阶段二继续向 proto-native runtime 演进

---

### 5.7 模块 G：统一模拟测试方案

**子问题**：如何做到“实机内统一测试”，但不通过 Agent 真实测试？

本方案定义三层测试。

#### 5.7.1 层 1：L0 输入模拟验证

入口：

- `ModEntry.HandleTestInputCommand`
- capability：`test_input`

目标：

- 验证 `InputBridge / InputCombo / MenuNavigator`
- 验证按键注入是否稳定

适用动作：

- `face`
- `tap`
- `hold`
- `hold_for`
- `use_tool`
- `interact`
- `equip`
- `query_menu`
- `menu_click`
- `status`

定位：

- 这是阶段一底层调试工具
- 不是长期正式协议接口

#### 5.7.2 层 2：L1/L2 capability 实机模拟测试

入口：

- [TestCommands.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/TestCommands.cs)
- `PlatformToken=dev` 时注册的 SMAPI console 命令

现有命令包括：

- `agent_goto`
- `agent_warp`
- `agent_interact`
- `agent_equip`
- `agent_usetool`
- `agent_emote`
- `agent_gobed`
- `agent_status`
- `agent_report`

定位：

- 阶段一 runtime 行为验收入口
- 不依赖真实 Agent
- 直接在实机内观察画面、状态与日志
- 不能单独证明 protocol 完备性，因为它可以绕过 `CommandRequest -> mapper` 链路

#### 5.7.3 层 3：协议回放 / 命令回放

阶段一必须补一个统一 replay 入口。该入口是 protocol 完备性验收的主入口，不是可选增强。

最小要求：

1. 从固定 JSON fixture 加载协议等价 request。
2. 经过 `ProtoCommandMapper` 生成 `V2Command`。
3. 经过当前 `AdapterV2 / CommandProcessor` 路径执行。
4. 收到 `V2Result` 后经过 `ProtoResultMapper`。
5. 输出 mapping diagnostics，包含 proto capability、runtime capability、runtime params、result、error、phase1 gaps。

推荐形式：

```text
protocol_replay <fixture_name>
protocol_replay_file <path>
protocol_replay_dir <dir>
```

作用：

- 测试 protocol adapter
- 复用同一批 capability fixture
- 未来阶段二可直接切到 proto request 回放

不允许的回放方式：

- 直接把 fixture 写成当前 `V2Command`，跳过协议字段。
- 直接调用 `agent_goto / agent_usetool / agent_gobed` 后宣称 protocol 验证通过。
- 对不支持字段静默丢弃后继续执行。

推荐目录：

```text
mod/StarCoPlay/Protocol/Fixtures/
  say.ok.json
  say.invalid_empty.json
  emote.ok_happy.json
  emote.invalid_unspecified.json
  face.ok_up.json
  move_to.ok_exact.json
  move_to.ok_edge_warp.json
  move_to.ok_adjacent.json
  move_to.invalid_mode.json
  go_to.ok_coord.json
  go_to.unsupported_adjacent_gap.json
  go_to.invalid_route.json
  go_to_bed.ok_default.json
  interact.ok_adjacent.json
  interact.invalid_missing_target.json
  use_tool.ok_basic.json
  use_tool.invalid_no_tool.json
  equip.ok_slot.json
  equip.ok_item_name.json
  equip.unsupported_item_category.json
  menu_click.ok_page.json
  menu_click.invalid_no_menu.json
  menu_close.ok_open.json
  menu_close.ok_already_closed.json
  tp.ok_test.json
  tp.invalid_unspecified_reason.json
```

fixture 命名约定：

- `ok_*`：预期 runtime 成功或协议层成功。
- `invalid_*`：预期 mapper 或 runtime 返回 `INVALID_PARAMS`。
- `unsupported_*`：预期 mapper 返回 `NOT_SUPPORTED` 或记录明确 phase1 gap。
- `*.gap.json`：允许执行但必须在 diagnostics 中输出 `phase1_gaps`。

#### 5.7.4 结果采集

统一用以下设施采集：

- `AnimationAwaiter`
- `DebugReporter`
- `agent_report`
- SMAPI log

记录项至少包括：

- capability
- 输入参数
- 运行时映射参数
- 结果
- 错误码
- 观察项
- phase1 gaps
- fixture 名称
- mapper 版本
- runtime capability 名称

阶段一实机验收记录建议统一写入：

```text
mod/StarCoPlay/Protocol/Reports/
  protocol_replay_YYYYMMDD_HHMMSS.jsonl
```

每行至少包含：

```json
{
  "fixture": "say.ok.json",
  "command_id": "fixture-say-ok",
  "proto_capability": "say",
  "runtime_capability": "say",
  "runtime_params": {"message": "hello"},
  "success": true,
  "status": "COMPLETED",
  "error_code": null,
  "observation": "已发送消息: hello",
  "phase1_gaps": []
}
```

---

### 5.8 模块 H：构建与生成链路

**子问题**：如何保证协议修改后，Agent 与 Mod 两侧生成物一致？

#### 5.8.1 构建入口

[Makefile](/D:/PROJECT/star-coplay/Makefile) 已经定义：

- `make proto`
- `make proto-python`
- `make proto-csharp`
- `make proto-clean`

#### 5.8.2 阶段一要求

阶段一 DoD 必须包含：

1. 修改 `protocol/proto/*.proto` 后，两侧生成物同步更新
2. `agent/generated/protocol` 与 `mod/StarCoPlay/Generated/Protocol` 不出现漂移
3. 文档里声明 proto 版本与运行时适配版本

#### 5.8.3 当前风险

当前 proto 已生成，但运行时未消费。  
这会导致两种常见误判：

- 误以为“proto 已落地”
- 误以为“改 proto 不影响当前执行链”

因此阶段一必须把生成链和适配链一起纳入验收。

---

## 5.9 接口契约

| 接口 | 输入 | 输出 | 错误码 | 幂等性 |
| --- | --- | --- | --- | --- |
| `say` | `content` | `content_length` | `INVALID_PARAMS` / `EXECUTION_FAILED` | 否 |
| `emote` | `emote_type` | `emote_type` | `INVALID_PARAMS` | 否 |
| `face` | `direction` | `final_direction` | `INVALID_PARAMS` | 近似幂等 |
| `move_to` | `mode + coords` | `MoveToResult` | `PATH_BLOCKED` / `WARP_TIMEOUT` / `TIMEOUT` | 否 |
| `go_to` | `map_id + x + y + arrival` | `GoToResult` | `NO_ROUTE` / `PATH_BLOCKED` / `TRACKING_LOST` | 否 |
| `go_to_bed` | optional timeout | `GoToBedResult` | `EXECUTION_FAILED` / `TIMEOUT` | 否 |
| `interact` | `target_tile` | `TileEffectResult` | `INVALID_PARAMS` / context failure | 否 |
| `use_tool` | `target_tile + charge_level` | `TileEffectResult + tool_name` | `WRONG_TOOL` / `INVALID_PARAMS` | 否 |
| `equip` | `slot / item_name / item_category` | `EquipResult` | `ITEM_NOT_FOUND` / `INVALID_PARAMS` | 部分幂等 |
| `menu_click` | `component_id + source` | `MenuResult` | `INVALID_PARAMS` / `NOT_SUPPORTED` | 否 |
| `menu_close` | 无 | `MenuResult` | 无菜单时成功 | 是 |
| `tp` | `target + reason` | `TpResult` | `INVALID_PARAMS` / `EXECUTION_FAILED` | 否 |

---

## 5.10 状态机 / 数据流

### 5.10.1 `move_to`

```text
request
  -> mode parse
  -> start navigate / hold / adjacent
  -> tick state machine
  -> animation/input settle
  -> build result
```

### 5.10.2 `go_to`

```text
Resolving
  -> Routing
  -> Segment dispatch
  -> optional Tracking
  -> Done / Failed
```

### 5.10.3 `go_to_bed`

```text
home resolve
  -> warp home
  -> wait home loaded
  -> find bed
  -> trigger sleep interaction
  -> wait / confirm dialog
  -> fallback NewDay
  -> complete
```

---

## 6. 备选方案

| 方案 | 描述 | 优势 | 劣势 | 为什么没选 |
| --- | --- | --- | --- | --- |
| 方案 A：加 protocol adapter，复用当前 V2 runtime（采用） | 协议层与执行层解耦，阶段一只补映射与测试 | 风险最小，能快速形成统一契约 | 运行时仍有历史包袱 | 最符合当前项目状态 |
| 方案 B：直接把 CommandProcessor / WsBridge / Agent 全量切到 proto-native | 契约最纯，长期最整洁 | 需要同时重写传输、结果、测试和 handler 接口 | 范围过大，阶段一成本过高 | 不适合当前阶段 |
| 方案 C：继续以 `adapter_info + V2Command` 为事实协议，不引入 proto 适配 | 开发最快 | 协议漂移继续扩大，后续阶段二成本更高 | 与新需求目标相反 | 不满足解耦目标 |
| 方案 D：为每个 capability 单独写专用协议入口 | 可逐个上线 | 会快速形成多套桥接方式，无法系统收敛 | 不通用，不可维护 | 明确排除 |

---

## 7. 风险与权衡

| 风险 | 影响范围 | 触发条件 | 缓解措施 |
| --- | --- | --- | --- |
| 协议字段与运行时字段映射不完整 | Agent、Mod、测试三侧语义错位 | 新增 proto 字段后未同步 adapter | 所有 capability 都通过统一 adapter 维护映射表 |
| `go_to_bed` 与 `go_bed` 命名漂移 | 协议调用失败或结果对不上 | 直接按 proto 名路由 runtime | 统一 capability name map |
| `equip` 混合 direct API 与 keypress | 后续部署或更换输入路径时行为不一致 | 调用者误以为纯输入模拟 | 文档明确现状，阶段二再纯化 |
| `use_tool` 仅检查 `CurrentTool != null` | 非工具类边界判断不严格 | 手持非预期对象时调用 | 阶段二补工具类别校验 |
| `test_input` 被误当成正式接口 | 测试路径与正式能力收敛失败 | 阶段一后继续扩散使用 | 文档明确其临时性质，阶段二删除 |

**关键权衡**：

本方案最重要的取舍是：先保证系统边界正确，再追求实现形态纯粹。  

换句话说，阶段一优先实现：

- 协议是统一入口
- 运行时有统一映射
- 测试有统一方法

而不是优先实现：

- 所有能力立即改成 protobuf-native
- 所有 handler 一次性重写
- 所有历史 direct API 立刻清零

这是典型的短中期权衡。短期保留当前稳定执行内核，换来阶段一可控落地；中期再逐步把适配层向内收敛。

---

## 8. 失败模式与容错

| 组件 | 失败场景 | 影响 | 降级/容错策略 | 监控指标 |
| --- | --- | --- | --- | --- |
| protocol adapter | 字段映射失败 | 命令被拒绝或错误下发 | 返回 `INVALID_PARAMS`，不进入执行层 | 参数映射失败次数 |
| runtime handler | 执行中异常 | 单 capability 失败 | 结构化错误返回，不污染全局状态 | capability 失败率 |
| `go_to` routing | 找不到路由或 tracking 丢失 | 导航失败 | 返回 route/tracking 错误，不自动走硬编码 fallback | routing/tracking error |
| `go_bed` | dialog 未弹出或睡觉流程阻塞 | 次日推进失败 | 多级确认 + `Game1.NewDay` fallback | gobed fallback 次数 |
| `InputBridge` | 按键未被消化或动画未完成 | 早返回/假成功 | `AnimationAwaiter` 作为完成确认层 | animation timeout 数 |
| test harness | 回放入口与正式能力不一致 | 测试结论失真 | fixture 统一走 adapter/runtime 主链 | replay mismatch 数 |

---

## 9. 测试计划 + DoD

### 9.1 测试计划

| 测试类型 | 覆盖范围 | 关键用例 |
| --- | --- | --- |
| 单元测试 | protocol adapter 映射 | `content->message`、`target_tile->x,y`、`go_to_bed->go_bed`、enum 转换 |
| replay 集成测试 | protocol fixture -> adapter -> runtime capability -> result mapper | 每个基础 capability 至少 1 个成功用例 + 1 个失败用例 |
| 本地实机模拟 | protocol replay + SMAPI console 辅助 | `move_to` 三模式、`go_to` 跨图、`go_to_bed`、`menu_click/menu_close` |
| 底层输入验证 | `test_input` | `face / use_tool / interact / equip / menu_click / query_menu` |
| 构建一致性 | proto 生成 | `make proto` 后 Agent/Mod 两侧生成文件同步 |

测试优先级：

1. 先跑 mapper 单元测试，确保协议字段不会错误下发。
2. 再跑 protocol replay，确保协议切片能驱动 V2 runtime。
3. 最后跑 SMAPI console / `test_input`，定位输入注入或 handler 行为问题。

验收时不得把第 3 步替代第 1、2 步。

### 9.2 推荐的阶段一测试矩阵

#### `say`

- 前置：聊天框可用
- 输入：短文本
- 预期：游戏内消息可见，返回成功
- fixture：
  - `say.ok.json`
  - `say.invalid_empty.json`

#### `emote`

- 前置：角色可操作
- 输入：每种枚举至少一项
- 预期：正确气泡
- fixture：
  - `emote.ok_happy.json`
  - `emote.invalid_unspecified.json`

#### `face`

- 前置：静止
- 输入：四方向
- 预期：角色面向正确
- fixture：
  - `face.ok_up.json`
  - `face.ok_right.json`
  - `face.ok_down.json`
  - `face.ok_left.json`

#### `move_to`

- exact：
  - 输入：当前地图可达点
  - 预期：准确到点
- edge warp：
  - 输入：靠边界可达点 + 方向
  - 预期：成功跨图
- adjacent：
  - 输入：目标 tile + side
  - 预期：站在邻接位并朝向目标
- fixture：
  - `move_to.ok_exact.json`
  - `move_to.ok_edge_warp.json`
  - `move_to.ok_adjacent.json`
  - `move_to.invalid_mode.json`

#### `go_to`

- 输入：跨地图坐标
- 预期：按路由逐段到达
- fixture：
  - `go_to.ok_coord.json`
  - `go_to.invalid_route.json`
  - `go_to.unsupported_adjacent_gap.json`
- 验收重点：
  - `map_id+x+y` 必须映射成 runtime `target="Map:x,y"`。
  - `ARRIVAL_ADJACENT / stand_side` 如果阶段一不能完整表达，必须返回 `NOT_SUPPORTED` 或记录 `phase1_gaps`，不能装作已完整支持。

#### `go_to_bed`

- 输入：无参
- 预期：回家 -> 睡觉 -> 次日
- fixture：
  - `go_to_bed.ok_default.json`
  - `go_to_bed.ok_already_home.json`
- 验收重点：
  - 协议名必须是 `go_to_bed`。
  - runtime 名必须统一映射为 `go_bed`。
  - 当前内部直接 `WarpTo`，不得宣称已经经由 `tp` capability 编排。

#### `interact`

- 输入：邻接 tile
- 预期：触发对象交互
- fixture：
  - `interact.ok_adjacent.json`
  - `interact.invalid_missing_target.json`
- 验收重点：
  - `target_tile` 必须拆成 `x,y`。
  - 非邻接行为当前由游戏/输入结果体现，阶段一不新增距离校验。

#### `use_tool`

- 输入：目标 tile + charge_level
- 预期：工具正确使用
- fixture：
  - `use_tool.ok_basic.json`
  - `use_tool.ok_charged.json`
  - `use_tool.invalid_no_tool.json`
- 验收重点：
  - 当前只严格要求 `CurrentTool != null`。
  - 工具类别严格校验属于阶段二。

#### `equip`

- slot、item_name、fallback 各一例
- fixture：
  - `equip.ok_slot.json`
  - `equip.ok_item_name.json`
  - `equip.ok_fallback.json`
  - `equip.unsupported_item_category.json`
- 验收重点：
  - `slot` 和 `item_name` 阶段一必须可用。
  - `item_category` 如果不补实现，必须显式 `NOT_SUPPORTED`。

#### `menu_click` / `menu_close`

- 打开菜单后点击已知组件
- 关闭菜单
- 无菜单时关闭幂等
- fixture：
  - `menu_click.ok_page.json`
  - `menu_click.ok_tab.json`
  - `menu_click.invalid_no_menu.json`
  - `menu_close.ok_open.json`
  - `menu_close.ok_already_closed.json`

#### `tp`

- 输入：测试用途传送目标
- 预期：传送到指定地图坐标
- fixture：
  - `tp.ok_test.json`
  - `tp.invalid_unspecified_reason.json`
- 验收重点：
  - 仅作为内部/测试能力。
  - `reason` 必须存在并进入 diagnostics 或 result。

### 9.3 Definition of Done

- [ ] 新建正式方案文档，明确协议层、运行时层、测试层边界。
- [ ] 所有基础 capability 的协议名、运行时名、参数映射、结果映射都写清楚。
- [ ] `go_to_bed -> go_bed` 的命名映射在设计和实现中均有单一入口。
- [ ] 阶段一采用 protocol adapter，不修改现有 L1/L2 执行内核的核心语义。
- [ ] 建立统一本地 protocol replay 方式，不依赖真实 Agent。
- [ ] 所有 replay fixture 都必须走 `protocol request -> mapper -> V2Command -> runtime -> result mapper`，不得直接构造当前 `V2Command`。
- [ ] 每个基础 capability 至少有一个成功 fixture 和一个失败/不支持 fixture。
- [ ] `ProtoCommandMapper` 必须覆盖接收边界校验：基础字段、oneof、capability 一致性、enum、范围和 internal-only 能力策略。
- [ ] 阶段一未支持字段必须返回 `NOT_SUPPORTED` / `INVALID_PARAMS` 或记录 `phase1_gaps`，不得静默忽略。
- [ ] `ProtoResultMapper` 统一处理公共 result 字段：`command_id / success / status / capability / observation / error`。
- [ ] `make proto` 后 Python/C# 生成物同步更新并可编译。
- [ ] 至少覆盖 `move_to` 三模式、`go_to`、`go_to_bed`、`equip`、`menu_*` 的实机模拟验收。
- [ ] `test_input` 被标记为临时底层工具，不被当作正式协议能力。

---

## 10. 上线计划

### 阶段 1：协议适配与统一测试

范围：

- 增加 protocol adapter
- 打通基础 capability 的协议映射
- 增加 protocol replay/test harness
- 不改 handler 核心

验证内容：

- 本文列出的能力全部可通过 protocol replay 验证协议切片闭环
- SMAPI console / `test_input` 只作为 runtime 和输入层辅助验证
- 协议字段变化不会直接泄漏进 handler
- 不支持字段不会被静默丢弃

回滚条件：

- adapter 引入后导致现有 JSON V2 能力链路退化
- 本地模拟测试与原有手工行为明显不一致
- protocol replay 无法稳定给出 mapping diagnostics，导致测试结论不可审计

### 阶段 1 推荐实现顺序

1. 新增 `CapabilityNameMap`，只处理名称互转和是否内部能力。
2. 新增 `ProtoCommandMapper`，先实现不依赖游戏状态的字段映射单元测试。
3. 新增 `ProtoResultMapper`，先统一公共 result 字段与错误码映射。
4. 新增 `ProtocolFixtureLoader` 和最小 fixture 集。
5. 新增 `protocol_replay_file`，打通单个 fixture 从协议等价 request 到 runtime 的执行。
6. 新增 `protocol_replay <fixture_name>` 和内置 fixture 目录。
7. 用 replay 覆盖 `say / emote / face / interact / use_tool / equip / menu_close / tp` 这类单步能力。
8. 再覆盖 `move_to / go_to / go_to_bed / menu_click` 这类依赖状态机或菜单状态的能力。
9. 最后补实机报告输出和 DoD 记录。

### 阶段 1 切分建议

可以拆成三组互不阻塞的任务：

| 任务组 | 负责内容 | 主要输出 |
| --- | --- | --- |
| Adapter 核心 | name map、command mapper、result mapper、mapper 单测 | `mod/StarCoPlay/Protocol/*Mapper.cs` |
| Replay 入口 | fixture loader、console command、diagnostics/report | `protocol_replay*` 命令和报告 |
| 实机验收 | fixture 数据、SMAPI 验证记录、gap 清单 | `Protocol/Fixtures` 与 `Protocol/Reports` |

实现约束：

- Adapter 核心不得依赖 SMAPI console 命令。
- Replay 入口不得绕过 mapper。
- 实机验收发现 runtime 问题时，优先记录为 runtime gap，不在 mapper 中伪造成功。

### 阶段 2：协议主链收敛

范围：

- 逐步让 transport/result 更 proto-native
- 收敛 `go_bed` 命名
- 收敛 `equip`/`use_tool` 等混合边界
- 清理 `test_input`

验证内容：

- 协议 request/result 可直接成为主链输入输出

回滚条件：

- proto-native 主链引发兼容性回退

### 回滚方案

阶段一回滚很简单：

1. 关闭 protocol adapter 新入口
2. 保留当前 `V2Command -> AdapterV2 -> Handler` 主链
3. 保留现有 console 调试与 `test_input`

因为阶段一不动执行内核，回滚成本低。

---

## 11. 影响评估

| 维度 | 评估 |
| --- | --- |
| 业务影响 | Agent 与 Mod 执行命令边界变清晰，后续能力扩展和部署稳定性提升 |
| 用户范围 | 直接影响协议开发、Mod 开发、联调与测试流程 |
| 工程工时 | 阶段一预计 2-4 人日，取决于 adapter 与 replay 入口实现深度 |
| 长期维护成本 | 显著下降；后续新增能力不再需要在 Agent/Mod/文档三处各自发散 |
| 依赖团队 | 主要是 Mod/Agent 仓库内协同，无外部平台强依赖 |

---

## 12. 未解问题 Open Questions

| 问题 | 需要谁输入 | 决定截止日期 |
| --- | --- | --- |
| `go_to` 协议是否长期保持坐标化，还是向当前语义化 `target` 收敛 | 技术评审 | 阶段二前 |
| `equip.item_category` 是否在阶段一补最小可用实现 | 技术评审 | 阶段一开发前 |
| `go_to.ARRIVAL_ADJACENT + stand_side` 阶段一是实现、返回 `NOT_SUPPORTED`，还是允许 gap 执行 | 技术评审 | 阶段一开发前 |
| `menu_click.source=UNSPECIFIED` 是默认 `page` 还是返回 `INVALID_PARAMS` | 技术评审 | 阶段一开发前 |
| `go_to_bed.timeout_frames` 当前 handler 不消费时，是否仅记录 gap | 技术评审 | 阶段一开发前 |
| WS 传输何时从 JSON frame 切换到 proto envelope | 技术评审 | 阶段二规划时 |
| `tp.face_direction` 阶段一是否映射到实际朝向，还是仅审计记录 | 技术评审 | 阶段一开发前 |

---

## 13. Action Items

- @Codex 基于本方案实现 `proto -> runtime` 的 capability adapter，包括 `CapabilityNameMap`、`ProtoCommandMapper`、`ProtoResultMapper`。
- @Codex 为 `say / emote / face / move_to / go_to / go_to_bed / interact / use_tool / equip / menu_click / menu_close / tp` 建立 mapper 单测与 replay fixture。
- @Codex 增加阶段一统一的 `protocol_replay*` 本地调试入口，确保 replay 必须经过 mapper。
- @Codex 补齐阶段一 DoD 所需的实机 replay 报告记录方式。
- @Codex 明确 `go_to.ARRIVAL_ADJACENT`、`equip.item_category`、`go_to_bed.timeout_frames`、`tp.face_direction` 的阶段一处理策略。
- @Codex 在阶段一完成后，评估阶段二的 proto-native 收敛路径。

---

## 14. 参考资料

- [docs/模版.md](/D:/PROJECT/star-coplay/docs/模版.md)
- [Makefile](/D:/PROJECT/star-coplay/Makefile)
- [protocol/proto/common.proto](/D:/PROJECT/star-coplay/protocol/proto/common.proto)
- [protocol/proto/capability_params.proto](/D:/PROJECT/star-coplay/protocol/proto/capability_params.proto)
- [protocol/proto/capability_results.proto](/D:/PROJECT/star-coplay/protocol/proto/capability_results.proto)
- [protocol/proto/capability.proto](/D:/PROJECT/star-coplay/protocol/proto/capability.proto)
- [protocol/proto/transport.proto](/D:/PROJECT/star-coplay/protocol/proto/transport.proto)
- [mod/StarCoPlay/ModEntry.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/ModEntry.cs)
- [mod/StarCoPlay/Bridge/CommandProcessor.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/Bridge/CommandProcessor.cs)
- [mod/StarCoPlay/V2/AdapterV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/AdapterV2.cs)
- [mod/StarCoPlay/V2/L1V2/MoveToHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/MoveToHandlerV2.cs)
- [mod/StarCoPlay/V2/Compound/GoToHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/Compound/GoToHandler.cs)
- [mod/StarCoPlay/V2/Compound/GoBedHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/Compound/GoBedHandler.cs)
- [mod/StarCoPlay/V2/L1V2/FaceHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/FaceHandlerV2.cs)
- [mod/StarCoPlay/V2/L1V2/InteractHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/InteractHandlerV2.cs)
- [mod/StarCoPlay/V2/L1V2/UseToolHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/UseToolHandlerV2.cs)
- [mod/StarCoPlay/V2/L1V2/EquipHandlerV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/EquipHandlerV2.cs)
- [mod/StarCoPlay/V2/L1V2/MenuHandlersV2.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/L1V2/MenuHandlersV2.cs)
- [mod/StarCoPlay/V2/SayHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/SayHandler.cs)
- [mod/StarCoPlay/V2/EmoteHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/EmoteHandler.cs)
- [mod/StarCoPlay/V2/TpHandler.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/TpHandler.cs)
- [mod/StarCoPlay/V2/Input/InputCombo.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/Input/InputCombo.cs)
- [mod/StarCoPlay/V2/AnimationAwaiter.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/V2/AnimationAwaiter.cs)
- [mod/StarCoPlay/TestCommands.cs](/D:/PROJECT/star-coplay/mod/StarCoPlay/TestCommands.cs)

---

## 提交评审前自检清单

- [x] TL;DR 说明了方案目标、落地方式和阶段策略。
- [x] Goals / Non-Goals 明确。
- [x] 给出了系统分层图和链路图。
- [x] 重点能力逐项做了协议与运行时映射检查。
- [x] 明确指出了当前实现偏差，而不是把代码说得比实际更理想。
- [x] 给出了统一的本地 protocol replay 验收方法，并明确 SMAPI console / `test_input` 只作辅助验证。
- [x] 说明了阶段一与阶段二的职责边界。
- [x] 明确了阶段一协议切片完备与全系统 proto-native 完备的边界。
- [ ] 评审通过后再进入实现。

---

## 变更历史

| 日期 | 修改人 | 变更内容 |
| --- | --- | --- |
| 2026-05-18 | @Codex | 初版：完成 protocol-agent-mod 执行能力分层、映射与阶段一方案文档 |
| 2026-05-18 | @Codex | 评审后细化：补充阶段一协议切片完备边界、protocol replay 必选验收、adapter 文件职责、fixture 矩阵、DoD 与 Open Questions |
