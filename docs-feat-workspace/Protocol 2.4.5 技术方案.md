

# Star-Coplay Agent-Mod Protobuf Protocol 2.4.5 技术方案

## 1. 目标范围

2.4.5 的协议层目标是补齐 Agent 与 Mod 之间缺失的最小可执行 contract，使双方对消息结构、命令生命周期、能力调用、错误结果、代码生成和 LLM FunctionSchema 生成形成统一约束。

本方案只定义协议层，不定义每个具体能力的业务实现，不展开 `MoveToParams`、`InteractParams`、`UseToolParams` 等具体字段。

本期协议目标：

* 使用 protobuf 作为唯一协议定义语言。
* 使用 WebSocket 作为传输通道。
* 每个 WebSocket binary frame 承载一个 protobuf `Envelope`。
* 不定义 gRPC service。
* 固定编译本期 capability。
* 生成 Python / C# 类型。
* 生成 LLM 可用 FunctionSchema。
* 明确 Transport Contract 与 Capability Contract 的边界。
* 定义最小命令生命周期。
* 定义统一错误与结果结构。
* 禁止不透明动态结构作为主协议路径。

---

## 2. 协议语言选择

本期使用 protobuf 作为协议语言。

### 2.1 使用 protobuf

原因：强类型、可生成、可校验。

`.proto` 文件是 Agent-Mod 协议的唯一权威来源。Agent 侧、Mod 侧、LLM FunctionSchema 生成器都必须基于同一份 `.proto` 产物生成，不允许各自维护独立 schema。

### 2.2 不使用 JSON 作为协议源

原因：类型约束弱。

WebSocket 不直接以 JSON 作为主协议格式。运行时消息使用 protobuf binary frame。JSON 可以用于调试视图、日志展开、LLM schema 导出，但不能作为协议权威来源。

### 2.3 不使用 YAML 作为协议源

原因：易漂移。

YAML 可以用于文档、配置或人工说明，但不能作为 Agent-Mod command contract 的权威定义。

### 2.4 `.proto` 中不定义 RPC service

原因：WebSocket 异步。

本项目通信模型不是 gRPC 的同步远程函数调用，而是 WebSocket 上的异步消息交换。命令可能被接受、运行、取消、超时、查询状态，并最终返回结果。因此 `.proto` 只定义 message，不定义：

```proto
service XxxService {
  rpc Xxx(...) returns (...);
}
```

### 2.5 禁止 `google.protobuf.Any` 作为主协议路径

原因：不利生成。

本期不允许在核心命令请求、命令结果、capability params、capability result 中使用 `google.protobuf.Any` 承载动态参数。

主要原因：

* 不利于生成 LLM FunctionSchema。
* 不利于静态类型检查。
* 不利于错误提前暴露。
* 不利于完全展开 JSON Schema。
* 容易绕过 capability contract。

### 2.6 谨慎使用动态结构

原因：降低约束。

本期不建议在核心路径使用：

```proto
google.protobuf.Struct
google.protobuf.Value
map<string, ...> 作为自由参数包
bytes 作为自定义 payload
```

如确需扩展，只能进入明确标记的实验性协议，不进入 2.4.5 core contract。

---

## 3. 代码生成产物

`.proto` 是唯一输入源，需要生成三类产物。

### 3.1 Python 类型

用途：Agent 侧。

从 `.proto` 生成 Python message 类型，用于：

* WebSocket 消息解码。
* 命令构造。
* 参数校验。
* 结果解析。
* 日志展开。
* LLM tool 调用封装。

### 3.2 C# 类型

用途：Mod 侧。

从 `.proto` 生成 C# message 类型，用于：

* WebSocket 消息解码。
* Mod runtime dispatch。
* 参数校验。
* 命令状态记录。
* 结果回传。
* 错误结构生成。

### 3.3 LLM FunctionSchema

用途：LLM 工具暴露。

需要从 `.proto` 生成适用于 LLM function/tool calling 的 JSON Schema。

要求：

* 每个 capability 生成一个 function schema。
* 只暴露 LLM 可见 capability。
* 只暴露 capability params，不暴露 transport envelope。
* schema 必须完全展开。
* 避免 `$ref`。
* enum 必须展开为字符串枚举。
* required 字段必须明确。
* description 来自 proto 注释或自定义 option。
* 不暴露 internal-only capability。
* 不暴露 `tp` 等兜底能力。

### 3.4 FunctionSchema 生成难点

重点：protobuf 与 JSON Schema 语义不同。

需要处理以下问题：

* proto3 没有自然 required 语义。
* 标量字段有默认值。
* `oneof` 需要转换为单个 tool 的 params。
* 嵌套 message 需要解引用。
* enum 名称需要适配 LLM 可读格式。
* 字段注释需要转为 description。
* internal 字段不能泄露给 LLM。
* transport 字段不能泄露给 LLM。

建议引入自定义 option 或独立生成规则，例如：

```proto
// required
// llm_description: "Target tile position."
```

或后续定义：

```proto
[(starcoplay.required) = true]
[(starcoplay.llm_description) = "..."]
[(starcoplay.llm_visible) = true]
```

本期可以先用注释约定，后续再改为 custom options。

---

## 4. 协议文件组织

建议拆分为以下 `.proto` 文件：

```text
protocol/common.proto
protocol/transport.proto
protocol/capability.proto
protocol/capability_params.proto
protocol/capability_results.proto
```

### 4.1 `common.proto`

定义共享基础类型：

* `Direction`
* `TilePosition`
* `Error`
* `ErrorCode`
* `Observation`
* 其他跨 capability 复用结构

### 4.2 `transport.proto`

定义 WebSocket 消息层：

* `Envelope`
* `Hello`
* `HelloAck`
* `ErrorResponse`
* transport-level message body

### 4.3 `capability.proto`

定义命令生命周期：

* `CommandRequest`
* `CommandAccepted`
* `CommandProgress`
* `CommandResult`
* `CommandCancel`
* `CommandStatusRequest`
* `CommandStatusResponse`
* `CommandStatus`

### 4.4 `capability_params.proto`

定义每个 capability 的 params message。

例如：

```proto
SayParams
EmoteParams
FaceParams
MoveToParams
GoToParams
InteractParams
UseToolParams
EquipParams
MenuClickParams
MenuCloseParams
```

本方案不展开具体字段。

### 4.5 `capability_results.proto`

定义每个 capability 的 result data message。

例如：

```proto
SayResult
EmoteResult
FaceResult
MoveToResult
GoToResult
InteractResult
UseToolResult
EquipResult
MenuResult
```

本方案不展开具体字段。

---

## 5. Transport Contract

Transport Contract 负责 WebSocket 消息层，不负责具体 capability 业务语义。

它回答：

* 每帧消息如何解码。
* 消息如何追踪。
* 请求如何关联响应。
* session 如何识别。
* 协议版本如何校验。
* 消息类型如何区分。

### 5.1 WebSocket frame 规则

每个 WebSocket binary frame 必须承载一个 protobuf `Envelope`。

```text
WebSocket binary frame
  -> Envelope
       -> oneof body
```

不允许一个 frame 承载多个 Envelope。

不允许一个 Envelope 同时包含多个 body。

### 5.2 Envelope

```proto
message Envelope {
  string protocol_version = 1;

  string message_id = 2;
  string request_id = 3;
  string session_id = 4;
  int64 timestamp_ms = 5;

  oneof body {
    Hello hello = 10;
    HelloAck hello_ack = 11;

    CommandRequest command_request = 20;
    CommandAccepted command_accepted = 21;
    CommandProgress command_progress = 22;
    CommandResult command_result = 23;

    CommandCancel command_cancel = 24;
    CommandStatusRequest command_status_request = 25;
    CommandStatusResponse command_status_response = 26;

    ErrorResponse error_response = 40;
  }
}
```

### 5.3 Envelope 字段含义

`protocol_version`

协议版本。必须等于 `"2.4.5"`。不匹配时不启用控制能力。

`message_id`

单帧消息 ID。每条 Envelope 唯一。用于日志、去重、链路追踪。

`request_id`

请求响应关联 ID。同一次请求链路复用同一个 `request_id`。

`session_id`

控制会话 ID。用于区分当前控制会话和旧 session。

`timestamp_ms`

发送方时间戳。用于日志排序，不作为可信时间源。

`body`

实际消息体。使用 `oneof` 保证单消息类型。

---

## 6. Handshake

Handshake 负责协议版本门禁和 runtime capability 宣告。

### 6.1 Hello

Agent 发给 Mod。

```proto
message Hello {
  string agent_id = 1;
  string required_protocol_version = 2;
}
```

字段含义：

`agent_id`

Agent 实例 ID。用于诊断。

`required_protocol_version`

Agent 要求的协议版本。本期必须为 `"2.4.5"`。

### 6.2 HelloAck

Mod 发给 Agent。

```proto
message HelloAck {
  bool accepted = 1;
  string protocol_version = 2;
  string mod_runtime_id = 3;
  string session_id = 4;

  repeated string enabled_capabilities = 5;

  Error error = 10;
}
```

字段含义：

`accepted`

是否接受控制协议。

`protocol_version`

Mod 当前协议版本。

`mod_runtime_id`

Mod runtime 实例 ID。

`session_id`

本次控制会话 ID。

`enabled_capabilities`

当前 runtime 实际启用的 capability 名称集合。

`error`

拒绝时返回错误。

### 6.3 Handshake 规则

Agent 只有在以下条件满足时才启用控制能力：

```text
HelloAck.accepted == true
HelloAck.protocol_version == Agent.required_protocol_version
```

否则 Agent 进入：

```text
chat-only / read-only diagnostic mode
```

不做旧版本 fallback。

不做向前兼容。

---

## 7. Transport Contract 与 Capability Contract 的关系

Transport Contract 负责消息信封和链路语义。

Capability Contract 负责命令语义和能力参数。

关系如下：

```text
Envelope
  -> CommandRequest
       -> oneof params
            -> MoveToParams / InteractParams / ...
```

Transport 层不定义：

* `move_to` 的坐标字段。
* `interact` 的 target 字段。
* `use_tool` 的 charge 字段。
* `menu_click` 的 UI 参数。

Capability 层不定义：

* `message_id`
* `request_id`
* `session_id`
* `protocol_version`
* WebSocket frame 规则

`CommandRequest` 是连接两层的边界对象。

---

## 8. Capability Contract

Capability Contract 负责命令调用、生命周期、结果、错误和 capability-specific params/result。

本期 capability 固定编译进 proto，不支持 runtime 动态定义新参数结构。

Runtime manifest 只声明哪些已编译 capability 当前启用，不声明新 schema。

---

## 9. 命令生命周期

本期使用最小生命周期。

```text
requested
  -> accepted / rejected
  -> running
  -> completed / failed / timeout / cancelled
```

### 9.1 生命周期规则

Agent 发出：

```text
CommandRequest
```

Mod 收到后先校验。

校验失败：

```text
CommandResult(status = REJECTED)
```

校验成功：

```text
CommandAccepted
```

长耗时命令可选发送：

```text
CommandProgress(status = RUNNING)
```

最终必须发送：

```text
CommandResult
```

每条被 accepted 的命令必须且只能有一个终态结果。

### 9.2 CommandStatus

```proto
enum CommandStatus {
  COMMAND_STATUS_UNSPECIFIED = 0;

  COMMAND_STATUS_ACCEPTED = 1;
  COMMAND_STATUS_RUNNING = 2;

  COMMAND_STATUS_COMPLETED = 10;
  COMMAND_STATUS_REJECTED = 11;
  COMMAND_STATUS_FAILED = 12;
  COMMAND_STATUS_TIMEOUT = 13;
  COMMAND_STATUS_CANCELLED = 14;
}
```

### 9.3 状态语义

`ACCEPTED`

Mod 已接管命令。非终态。

`RUNNING`

命令执行中。非终态。

`COMPLETED`

执行成功。终态。

`REJECTED`

接收前拒绝。终态。

`FAILED`

执行失败。终态。

`TIMEOUT`

执行超时。终态。

`CANCELLED`

命令已取消。终态。

### 9.4 终态规则

`CommandResult` 只能使用终态：

```text
COMPLETED
REJECTED
FAILED
TIMEOUT
CANCELLED
```

`CommandProgress` 通常只使用：

```text
RUNNING
```

`CommandAccepted` 不需要携带 `ACCEPTED` status，消息类型本身已经表达。

---

## 10. CommandRequest

`CommandRequest` 表示 Agent 请求 Mod 执行一条 capability command。

```proto
message CommandRequest {
  string command_id = 1;
  int64 timeout_ms = 2;
  CommandSource source = 3;

  string capability = 4;

  oneof params {
    SayParams say = 10;
    EmoteParams emote = 11;
    FaceParams face = 12;

    MoveToParams move_to = 20;
    GoToParams go_to = 21;
    InteractParams interact = 22;
    UseToolParams use_tool = 23;
    EquipParams equip = 24;

    MenuClickParams menu_click = 30;
    MenuCloseParams menu_close = 31;
  }
}
```

### 10.1 字段含义

`command_id`

命令生命周期 ID。session 内唯一。

`timeout_ms`

命令执行超时。由 Mod 执行层使用。

`source`

命令来源。用于日志、安全和诊断。

`capability`

能力名称。用于日志和 manifest 对齐。

`params`

具体 capability 参数。必须且只能设置一个。

### 10.2 capability 字段规则

虽然 `oneof params` 可以推导 capability，本期仍保留 `capability` 字段。

原因：便于日志。

约束：

```text
capability 必须与 oneof params 分支一致。
```

例如：

```text
capability = "move_to"
params = move_to
```

不一致时返回：

```text
INVALID_PARAMS
```

### 10.3 CommandSource

```proto
enum CommandSource {
  COMMAND_SOURCE_UNSPECIFIED = 0;
  COMMAND_SOURCE_LLM = 1;
  COMMAND_SOURCE_AGENT_RUNTIME = 2;
  COMMAND_SOURCE_USER = 3;
  COMMAND_SOURCE_DIAGNOSTIC = 4;
}
```

### 10.4 CommandSource 语义

`LLM`

来自 LLM tool call。

`AGENT_RUNTIME`

来自 Agent 内部运行时。

`USER`

来自用户直接触发。

`DIAGNOSTIC`

来自诊断或开发工具。

### 10.5 CommandRequest 校验规则

必须校验：

* `command_id` 非空。
* `timeout_ms > 0`。
* `source != UNSPECIFIED`。
* `capability` 非空。
* `params` 设置且只设置一个。
* `capability` 与 `params` 分支一致。
* capability 存在于 enabled capabilities。
* params 满足 capability schema。

校验失败返回：

```text
CommandResult(status = REJECTED, error = INVALID_PARAMS / CAPABILITY_MISSING)
```

---

## 11. CommandAccepted

`CommandAccepted` 表示 Mod 已通过接收前校验并接管命令。

```proto
message CommandAccepted {
  string command_id = 1;
  int64 accepted_at_ms = 2;
  int64 timeout_ms = 3;
  bool cancellable = 4;
}
```

字段含义：

`command_id`

对应命令 ID。

`accepted_at_ms`

Mod 接管时间。

`timeout_ms`

最终采用的超时时间。

`cancellable`

当前命令是否支持取消。

### 11.1 Accepted 规则

收到 `CommandAccepted` 不表示成功。

它只表示：

```text
Mod 已接管命令，并承诺返回终态。
```

---

## 12. CommandProgress

`CommandProgress` 表示命令执行中的中间状态。

```proto
message CommandProgress {
  string command_id = 1;
  CommandStatus status = 2;
  Observation observation = 3;
  string message = 4;
}
```

字段含义：

`command_id`

对应命令 ID。

`status`

通常为 `RUNNING`。

`observation`

当前观察信息。

`message`

人类可读进度说明。

### 12.1 Progress 规则

`CommandProgress` 是可选消息。

短命令可以不发送。

长命令建议发送。

---

## 13. CommandResult

`CommandResult` 表示命令终态结果。

```proto
message CommandResult {
  string command_id = 1;

  bool success = 2;
  CommandStatus status = 3;

  Observation observation = 4;
  Error error = 5;

  string capability = 6;

  oneof data {
    SayResult say = 20;
    EmoteResult emote = 21;
    FaceResult face = 22;

    MoveToResult move_to = 30;
    GoToResult go_to = 31;
    InteractResult interact = 32;
    UseToolResult use_tool = 33;
    EquipResult equip = 34;

    MenuResult menu = 40;
  }
}
```

### 13.1 字段含义

`command_id`

对应命令 ID。

`success`

是否成功。

`status`

终态状态。

`observation`

执行后的观察信息。

`error`

失败错误。

`capability`

能力名称。用于日志和结果关联。

`data`

capability-specific 结果数据。

### 13.2 success 与 status 规则

成功：

```text
success = true
status = COMPLETED
error 为空
```

失败：

```text
success = false
status = REJECTED / FAILED / TIMEOUT / CANCELLED
error 通常非空
```

### 13.3 observation 规则

失败也应尽量返回 `observation`。

原因：便于恢复。

### 13.4 data 规则

`data` 只放 capability-specific result。

不放 transport 信息。

不放自然语言计划。

不使用 `Any`。

---

## 14. CommandCancel

`CommandCancel` 表示 Agent 请求取消某条未终结命令。

```proto
message CommandCancel {
  string command_id = 1;
  string reason = 2;
}
```

字段含义：

`command_id`

要取消的命令 ID。

`reason`

取消原因。用于日志。

### 14.1 Cancel 规则

取消请求不是终态。

Mod 收到后，应最终返回：

```text
CommandResult(status = CANCELLED)
```

如果命令已经终结，返回错误。

本期可用：

```text
EXECUTION_FAILED
```

并在 `detail` 中说明状态不合法。

---

## 15. CommandStatusRequest / CommandStatusResponse

用于查询命令状态。

```proto
message CommandStatusRequest {
  string command_id = 1;
}
```

```proto
message CommandStatusResponse {
  string command_id = 1;
  CommandStatus status = 2;
  bool terminal = 3;

  Observation observation = 4;
  Error error = 5;
}
```

### 15.1 Status 查询规则

`CommandStatusRequest` 不改变命令状态。

用途：

* 等待超时后查询。
* 重连后恢复。
* 日志诊断。
* 结果消息疑似丢失。

---

## 16. ErrorResponse

`ErrorResponse` 用于 transport-level 错误。

```proto
message ErrorResponse {
  Error error = 1;
}
```

使用场景：

* Envelope 无法处理。
* 协议版本不匹配。
* session 非法。
* message 格式非法。
* 非 command-specific 错误。

命令执行失败优先使用 `CommandResult.error`。

---

## 17. Error

统一错误结构。

```proto
message Error {
  ErrorCode code = 1;
  string message = 2;
  string detail = 3;
}
```

字段含义：

`code`

机器可读错误码。

`message`

简短人类可读说明。

`detail`

详细诊断信息。

### 17.1 ErrorCode

```proto
enum ErrorCode {
  ERROR_CODE_UNSPECIFIED = 0;

  CAPABILITY_MISSING = 1;
  INVALID_PARAMS = 2;
  BUSY = 3;
  TIMEOUT = 4;
  CANCELLED = 5;
  EXECUTION_FAILED = 6;
}
```

### 17.2 ErrorCode 语义

`CAPABILITY_MISSING`

能力不存在或未启用。

`INVALID_PARAMS`

参数缺失、非法或组合冲突。

`BUSY`

当前 runtime 无法接收新命令。

`TIMEOUT`

命令执行超时。

`CANCELLED`

命令被取消。

`EXECUTION_FAILED`

执行中失败或异常。

### 17.3 暂不扩展错误码

本期暂不加入大量细分错误码。

原因：保持最小。

能力内部细分原因放入：

```text
completion_reason
error.detail
capability-specific result
```

---

## 18. Capability 固定编译策略

本期 capability 固定编译进 `.proto`。

即：

```proto
oneof params {
  MoveToParams move_to = ...;
  InteractParams interact = ...;
}
```

不允许 runtime 发送未知 capability schema。

### 18.1 Runtime manifest 的作用

`HelloAck.enabled_capabilities` 只声明：

```text
当前运行时启用了哪些已编译 capability。
```

它不声明：

* 新 capability 参数。
* 新 result 类型。
* 动态 JSON Schema。
* 动态 handler contract。

### 18.2 Agent 有效能力集合

Agent 应计算：

```text
effective_capabilities =
  compiled_capabilities
  ∩ mod_enabled_capabilities
  ∩ exposure_policy
```

LLM 只能看到 `effective_capabilities` 中允许暴露的能力。

---

## 19. Capability 参数设计原则

每个 capability 至少需要：

```text
Params message
Result message
错误语义
completion_reason 视需要
文档注释
LLM schema 生成信息
```

本方案不展开每个具体 capability 的字段。

### 19.1 Params 原则

Params 只表达直接执行语义。

要求：

* 强类型。
* 结构化。
* 最小充分。
* 不使用自然语言 target。
* 不表达模糊意图。
* 不包含 transport 字段。
* 不包含 command lifecycle 字段。
* 不包含 LLM 计划说明。
* 不包含 Mod 可自行观测的冗余状态。
* 不包含底层算法策略。
* 不偷偷变成复合任务。

例如：

```text
move_to params 只描述移动目标。
interact params 只描述交互目标。
use_tool params 只描述工具输入目标。
```

### 19.2 Result 原则

Result 表达 Mod 执行后可稳定报告的结构化事实。

要求：

* 强类型。
* 可观测。
* 可诊断。
* 不使用 Any。
* 不承诺无法稳定观测的业务效果。
* 不替代 observation。
* 不包含自然语言长解释。

### 19.3 CompletionReason 原则

对于复杂命令，可以定义 capability-specific completion reason。

用途：

* 细化终止原因。
* 避免膨胀全局 ErrorCode。
* 帮助 Agent 恢复。

例如：

```text
MoveCompletionReason
ToolUseCompletionReason
InteractCompletionReason
```

### 19.4 required 字段规则

proto3 不直接依赖 required。

本期 required 通过以下方式之一表达：

* proto 注释约定。
* 生成器规则。
* 后续 custom option。

所有 required 字段生成 LLM FunctionSchema 时必须进入 `required` 数组。

### 19.5 enum 规则

所有 enum 必须包含：

```proto
XXX_UNSPECIFIED = 0;
```

业务上不允许 `UNSPECIFIED` 的字段，收到后返回：

```text
INVALID_PARAMS
```

### 19.6 默认值规则

默认值必须写入 contract 注释。

例如：

```text
charge_level 默认 0。
```

禁止 silent clamp。

例如：

```text
charge_level > 4 应返回 INVALID_PARAMS。
```

不应静默改成 4。

### 19.7 目标引用规则

目标必须结构化。

优先使用：

```text
TilePosition
Direction
EntityRef
ObjectRef
LocationRef
MenuRef
```

不要使用：

```text
string target = "go to the shop"
```

自然语言解析发生在 Agent 侧，不发生在 Mod capability params 中。

### 19.8 能力边界规则

每个 capability 必须注明：

* 做什么。
* 不做什么。
* 是否移动角色。
* 是否打开菜单。
* 是否消耗体力。
* 是否非幂等。
* 是否可取消。
* 默认 timeout。
* 失败时是否返回 observation。
* result 是否稳定可观测。

这些属于 capability contract 文档或 proto 注释。

---

## 20. LLM FunctionSchema 生成规则

LLM 不直接看到 protobuf Envelope。

LLM 只看到 capability function schema。

### 20.1 一个 capability 对应一个 function

例如：

```text
move_to -> MoveToParams
interact -> InteractParams
use_tool -> UseToolParams
```

### 20.2 FunctionSchema 输入

来源：

```text
capability params message
```

不包含：

* `command_id`
* `timeout_ms`
* `source`
* `capability`
* `session_id`
* `request_id`
* `message_id`
* `protocol_version`

这些由 Agent runtime 包装。

### 20.3 Schema 展开规则

必须：

* 展开嵌套 message。
* 展开 enum。
* 移除 `$ref`。
* 移除 internal 字段。
* 使用字段注释生成 description。
* 将 required 字段显式列出。
* 将默认值写入 description 或 schema metadata。

### 20.4 oneof 转换规则

顶层 `CommandRequest.oneof params` 不直接暴露给 LLM。

生成器应将每个 oneof 分支拆成单独 function。

例如：

```text
CommandRequest.params.move_to -> function move_to
CommandRequest.params.say -> function say
```

### 20.5 exposure policy

不是所有编译进 proto 的 capability 都暴露给 LLM。

例如：

```text
tp 不暴露。
diagnostic-only capability 不暴露。
internal recovery capability 不暴露。
```

LLM 可见能力必须来自：

```text
effective_capabilities
```

---

## 21. 最小协议执行流程

### 21.1 建立连接

```text
Agent -> Mod: Hello
Mod -> Agent: HelloAck
```

### 21.2 启用控制能力

条件：

```text
accepted = true
protocol_version 匹配
```

Agent 生成：

```text
effective_capabilities
```

并生成 LLM FunctionSchema。

### 21.3 发送命令

```text
Agent -> Mod: CommandRequest
```

### 21.4 Mod 接收前校验

校验失败：

```text
Mod -> Agent: CommandResult(REJECTED)
```

校验成功：

```text
Mod -> Agent: CommandAccepted
```

### 21.5 执行中

可选：

```text
Mod -> Agent: CommandProgress(RUNNING)
```

### 21.6 执行完成

```text
Mod -> Agent: CommandResult(COMPLETED / FAILED / TIMEOUT / CANCELLED)
```

### 21.7 取消命令

```text
Agent -> Mod: CommandCancel
Mod -> Agent: CommandResult(CANCELLED)
```

### 21.8 查询状态

```text
Agent -> Mod: CommandStatusRequest
Mod -> Agent: CommandStatusResponse
```

---

## 22. 本期禁止事项

2.4.5 协议层禁止：

* 使用 JSON 作为权威协议源。
* 使用 YAML 作为权威协议源。
* 在 `.proto` 定义 RPC service。
* 使用 `Any` 承载核心 params/result。
* 使用动态 JSON params 包。
* 让 LLM 直接构造 Envelope。
* 让 LLM 看到 transport 字段。
* 让 Agent 调用 manifest 中不存在的 capability。
* 让 runtime 动态声明未知 schema。
* 暴露不稳定复合能力。
* 暴露 internal-only capability。
* 将自然语言意图塞进 params。
* 将实现算法细节塞进 params。
* 将 command lifecycle 字段塞进 capability params。

---

## 23. 本期交付物

2.4.5 协议层应交付：

```text
1. .proto 协议文件
2. Python generated classes
3. C# generated classes
4. LLM FunctionSchema generator
5. flattened JSON Schema 输出
6. capability exposure policy
7. protocol validation tests
8. sample WebSocket binary messages
9. sample decoded debug logs
10. protocol README
```

---

## 24. 验收标准

协议层验收标准：

```text
1. Agent 和 Mod 使用同一份 proto 生成类型。
2. WebSocket frame 可稳定 decode 为 Envelope。
3. protocol_version 不匹配时拒绝控制。
4. CommandRequest 参数非法时返回 REJECTED。
5. capability 不存在时返回 CAPABILITY_MISSING。
6. accepted 命令最终一定返回唯一 CommandResult。
7. timeout 命令返回 TIMEOUT。
8. cancel 命令最终返回 CANCELLED 或明确错误。
9. LLM FunctionSchema 不包含 transport 字段。
10. LLM FunctionSchema 完全展开，无 $ref。
11. LLM 只能看到 effective_capabilities。
12. 结果结构统一包含 success/status/observation/data/error。
```

---

## 25. 一句话总结

2.4.5 的协议层采用 **protobuf message over WebSocket**，不使用 RPC service，不使用动态 `Any`，通过 `Envelope` 定义 Transport Contract，通过 `CommandRequest / CommandResult / CommandCancel` 定义最小命令生命周期，通过强类型 capability params/result 支撑 Agent、Mod 与 LLM FunctionSchema 的统一生成。
