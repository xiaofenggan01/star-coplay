# 技术方案：Agent 侧 Protocol 2.4.5 协议对接

---

## 0. 元信息

| 字段 | 内容 |
| --- | --- |
| 文档状态 | Draft |
| 作者 | @Codex |
| 创建日期 | 2026-05-18 |
| 最后更新 | 2026-05-18 |
| Reviewers | 待补充 |
| 关联链接 | `protocol/proto/*.proto`、`agent/generated/protocol/*`、`docs-feat-workspace/Protocol 2.4.5 技术方案.md`、`docs-feat-workspace/core_tool.md` |

---

## 1. TL;DR

本文只描述 Agent 侧 Protocol 2.4.5 协议构造层 Phase 1，不描述 Agent 以外的执行实现，也不把本文视为完整端到端上线方案。
目标是在 Agent 内建立 LLM FunctionSchema、tool args 映射、`CommandRequest` 构造、`Envelope` 封装和单元测试闭环。
LLM 只能看到 capability params schema，看不到 transport 字段；`tp` 不进入 LLM schema。
本期先验证 Agent 能稳定输出合法 Protocol 2.4.5 指令，再决定是否接入真实 binary transport。
评审结论：本文可作为 Agent 侧协议构造 PoC 进入实现；在替换现有 `core_tools.py` 或接入真实 transport 前，必须关闭旧工具迁移、runtime capability gate、结果/错误语义等决策。

---

## 2. 背景与问题

Agent 当前执行链仍以旧工具面为主：

```text
LLM tool_call
  -> slow_loop.core_tools schema
  -> ActionDispatcher.submit(action, params)
  -> bridge.write_command(action, params)
```

这条链路可以继续支撑开发，但它没有证明 Agent 能输出 Protocol 2.4.5 的 `CommandRequest`，也没有证明 LLM 可见 schema 与 proto params 一致。

Agent 侧当前还有一个前置问题：`agent/generated/protocol/*_pb2.py` 由 `protoc 6.31.1` 生成，而本机 Python protobuf runtime 是 `5.29.3`。直接 import 会触发 major version mismatch。Agent 协议模块和测试落地前，必须先解决 gencode/runtime 版本一致性。

---

## 3. 目标与非目标

### Goals（必达）

- Agent 侧能把 LLM tool call 转换为合法 `CommandRequest`。
- Agent 侧能把 `CommandRequest` 封装为 `Envelope`，并完成 protobuf 序列化/反序列化测试。
- Agent 侧输出简洁有效的 LLM FunctionSchema：无 `$ref`，无 transport 字段，required 明确。
- Agent 侧 schema 只暴露本期允许 LLM 调用的 capability；`tp` 仅允许内部/诊断构造，不暴露给 LLM。
- Agent 侧 schema 必须支持静态 exposure policy、runtime enabled capabilities 与本地 mapper 支持集求交，避免 LLM 调用 Mod 未启用能力。
- 明确现有 `slow_loop.core_tools` 与新 protocol capability 的兼容/迁移边界，不把 Agent-local tool 强行映射为 protocol command。
- Agent 单测覆盖正常构造、非法参数、enum、required、capability/oneof 一致性。

### Non-Goals（明确不做）

- 不描述 Agent 以外模块的内部实现。
- 不新增或修改 Agent 以外的 capability。
- 不把 LLM 暴露为可直接构造 `Envelope` / `CommandRequest` 的协议编辑器。
- 不在本文中定义指令发送后的接收、返回或执行错误策略。
- 不在本期重写 `DeliberativeLoop`、`ActionDispatcher` 或 L3 skill 体系。
- 不在本期把 `observe`、`search_wiki`、`read_wiki`、`remember` 等 Agent-local tool 直接包装成 Protocol 2.4.5 command；除非后续协议显式定义对应 capability。

---

## 4. 方案概述

核心一句话：Agent 新增独立协议构造层，把 LLM 可见工具参数转换为 proto params，再由 Agent runtime 填充命令与信封字段。

```text
LLM
  |
  | tool_call(name, arguments)
  v
Agent FunctionSchema
  |
  v
Agent ProtocolToolMapper
  - required 校验
  - enum 转换
  - 参数结构转换
  |
  v
Agent ProtocolCommandBuilder
  - command_id
  - timeout_ms
  - source
  - capability
  - params oneof
  |
  v
Agent ProtocolEnvelopeBuilder
  - protocol_version
  - message_id
  - request_id
  - session_id
  - timestamp_ms
  |
  v
protobuf bytes / test assertion
```

---

## 5. 方案详解

### 5.1 Python protobuf 前置修复

Agent 侧协议代码必须能 import generated protobuf classes。

可选方式：

- 升级 Agent Python runtime protobuf 到与当前 gencode 兼容的 6.x。
- 重新生成 Python protobuf，使 gencode 匹配当前 runtime 5.x。

二者必须选一个。未解决前，不进入 mapper/builder 实现。

### 5.2 Agent 协议模块

建议新增：

```text
agent/protocol/
  __init__.py
  constants.py
  capability_registry.py
  schemas.py
  tool_mapper.py
  command_builder.py
  envelope_builder.py
  result_parser.py
```

职责：

| 模块 | 职责 |
| --- | --- |
| `constants.py` | 协议版本、默认 timeout、LLM 可见 capability、internal-only capability |
| `capability_registry.py` | 定义 `CapabilitySpec` 中心表，绑定 tool name、capability、params class、oneof 分支、timeout、exposure policy、enum 映射 |
| `schemas.py` | 导出 Agent LLM FunctionSchema |
| `tool_mapper.py` | LLM args -> proto params message |
| `command_builder.py` | proto params -> `CommandRequest` |
| `envelope_builder.py` | `CommandRequest` -> `Envelope` |
| `result_parser.py` | 第二阶段使用：`Envelope(CommandResult)` -> Agent 可消费结果 |

`CapabilitySpec` 是本期减少漂移的核心约束。schema、mapper、builder 测试不应各自散落维护 capability 字符串、oneof 字段名、timeout 和 enum 映射；至少要从同一张中心表读取，或通过测试强制三者一致。

### 5.3 Agent LLM schema 边界

LLM schema 只描述 capability params，不包含：

- `protocol_version`
- `message_id`
- `request_id`
- `session_id`
- `timestamp_ms`
- `command_id`
- `timeout_ms`
- `source`
- `capability`

这些字段由 Agent runtime 填充。

本期 LLM 可见能力建议：

| tool | LLM 参数 | proto params |
| --- | --- | --- |
| `say` | `message`, optional `emote` | `SayParams.content`；可选拆出 `EmoteParams` |
| `emote` | `emote_type` | `EmoteParams.emote_type` |
| `face` | `direction` | `FaceParams.direction` |
| `move_to` | `mode`, `x`, `y`, optional direction fields | `MoveToParams` |
| `go_to` | `map_id`, `x`, `y`, `arrival_mode` | `GoToParams` |
| `go_to_bed` | optional `timeout_frames` | `GoToBedParams` |
| `interact` | `x`, `y` | `InteractParams.target_tile` |
| `use_tool` | `x`, `y`, optional `charge_level` | `UseToolParams.target_tile/charge_level` |
| `equip` | `slot` or `item_name` | `EquipParams.slot/item_name` |
| `query_inventory` | optional `include_details` | `QueryInventoryParams` |
| `query_location` | none | `QueryLocationParams` |
| `query_map_exits` | none | `QueryMapExitsParams` |
| `open_menu` | none | `OpenMenuParams` |
| `query_menu` | none | `QueryMenuParams` |
| `menu_click` | `component_id`, `component_source` | `MenuClickParams.source` |
| `menu_close` | none | `MenuCloseParams` |

Internal-only：

| tool | 策略 |
| --- | --- |
| `tp` | Agent runtime 可用于诊断/内部流程构造，不进入 LLM FunctionSchema |

#### 5.3.1 Runtime capability gate

LLM 最终可见 schema 不是单纯的静态列表，而是以下集合的交集：

```text
LLM visible tools
  = static exposure policy
  ∩ runtime enabled_capabilities
  ∩ local mapper supported capabilities
  - internal-only capabilities
```

规则：

- `tp` 永远从 LLM schema 中排除，即使 runtime manifest 或测试 fixture 中出现。
- 未接入真实 transport 的 Phase 1 测试可用 fake enabled capabilities 验证 gate 行为。
- 接入 transport 后，`HelloAck.enabled_capabilities` 或 adapter manifest 必须参与 schema 生成；Agent 不应向 LLM 暴露 Mod 当前未启用的 capability。
- 如果 runtime enabled capabilities 为空或缺失，默认使用最保守策略：不暴露 protocol command，或只暴露显式配置允许的 mock capability。

#### 5.3.2 与现有 `core_tools.py` 的兼容/迁移边界

当前 `slow_loop.core_tools.py` 暴露给 LLM 的是 9 个旧工具：`say`、`interact`、`use_tool`、`equip`、`go_to`、`observe`、`search_wiki`、`read_wiki`、`remember`。Protocol 2.4.5 的 capability 更底层、更结构化，二者不能直接按名称整体替换。

| 当前工具 | 本期策略 | 与 protocol 的关系 |
| --- | --- | --- |
| `say` | 可迁移 | `message` 映射到 `SayParams.content`；Phase 1 将 `emote` 拆成第二条 `EmoteParams` |
| `interact` | 条件迁移 | 协议要求 `target_tile`；若旧工具仍允许不传 `x/y`，必须先定义“面前 tile”解析规则，否则 schema 替换时改为 required |
| `use_tool` | 条件迁移 | 同 `interact`；Phase 1 `charge_level` 使用 0-4 |
| `equip` | 条件迁移 | Phase 1 只暴露 `slot`、`item_name`，schema 与 mapper 都拒绝 `item_category` |
| `go_to` | 不直接迁移 | 旧工具是 `target` 自然语言 UX；协议 `GoToParams` 是 `map_id/x/y/arrival_mode` 结构化参数，需要 parser/wrapper 或替换 UX |
| `observe` | 保留 Agent-local | 可与 `query_inventory/query_location/query_map_exits/query_menu` 部分重叠，但不是一一映射 |
| `search_wiki` / `read_wiki` | 保留 Agent-local | 知识查询不属于当前 Protocol 2.4.5 capability |
| `remember` | 保留 Agent-local | 记忆写入不属于当前 Protocol 2.4.5 capability |
| `move_to` / `face` / `emote` / menu/query capabilities | 新 protocol 能力 | 只有 runtime 启用且 Agent 决定暴露时才进入 LLM schema，不自动替换旧工具 |

迁移期间必须避免同时暴露语义重复但参数形态不同的工具，尤其是 `go_to(target)` 与 `go_to(map_id,x,y,arrival_mode)`。

#### 5.3.3 Phase 1 已采用实现决策

- `menu_click` 的 LLM 参数使用 `component_source`，mapper 再写入 proto 字段 `MenuClickParams.source`，避免 LLM schema 暴露名为 `source` 的 transport 字段。
- `say(message, emote)` 映射为明确的 command list：先 `SayParams`，再可选 `EmoteParams`，不伪装成单条 `CommandRequest`。
- protocol 版 `interact` / `use_tool` 要求显式 `x`、`y`；旧工具面“不传则面前 tile”的 UX 不在协议 mapper 内隐式处理。
- `use_tool.charge_level` 采用 Protocol 2.4.5 注释中的 `0-4`，不兼容旧工具面的 `0-5`。
- `equip.item_category` 本期不暴露给 LLM，也不被 mapper 接受；只接受 `slot` 或 `item_name` 且二选一。

### 5.4 Agent mapper 校验

Agent 必须把 LLM tool call 当作非可信输入。这里的校验只保证 Agent 不构造明显非法的 protocol command，不承担发送后的业务判断。

必做校验：

- tool name 在 Agent 允许列表中。
- required 参数必须在原始 args dict 层校验存在性，不能依赖 protobuf 默认值；例如 `x=0`、`y=0` 可能是合法坐标，缺失与默认值必须区分。
- enum 字符串合法，且所有 enum 映射集中维护：`direction`、`emote_type`、`move_to.mode`、`go_to.arrival_mode`、`menu_click.component_source` 等。
- `capability` 与 params oneof 分支一致。
- `timeout_ms > 0`。
- `source != COMMAND_SOURCE_UNSPECIFIED`。
- 条件必填字段按 mode 校验：例如 `move_to.mode=edge_warp` 时需要 `direction`，`go_to.arrival_mode=adjacent` 时需要明确是否允许省略 `stand_side`。
- `use_tool.charge_level` 范围必须以 `CapabilitySpec` 单一常量为准；Phase 1 采用 0-4。
- `equip` selector 必须互斥；Phase 1 只接受 `slot` 或 `item_name`，拒绝 `item_category`。
- `say(emote=...)` 拆成 `say` + `emote` 两条 command，mapper 返回明确 command list，而不是伪装成单条 `CommandRequest`。
- `tp` 不允许从 LLM schema 进入。

Agent 不做：

- 不检查 tile 是否可达。
- 不检查物品是否真实存在。
- 不检查目标对象是否可交互。
- 不检查菜单组件是否存在。
- 不判断工具是否适合目标 tile。

这些不属于 Agent 协议构造模块，本文不展开。

### 5.5 Command 构造规则

`CommandRequest` 字段来源：

| 字段 | 来源 |
| --- | --- |
| `command_id` | Agent runtime 生成 |
| `timeout_ms` | Agent 默认值或 tool policy |
| `source` | LLM tool call 使用 `COMMAND_SOURCE_LLM`；内部诊断使用其他 source |
| `capability` | Agent mapper 根据 tool name 写入 |
| `params oneof` | Agent mapper 根据 capability 设置且只设置一个分支 |

构造失败时不输出半成品 `CommandRequest`，而是返回本地可读错误，供 Agent 测试或后续 tool result 使用。

### 5.6 Envelope 构造规则

`Envelope` 字段来源：

| 字段 | 来源 |
| --- | --- |
| `protocol_version` | Agent 常量，例如 `2.4.5` |
| `message_id` | Agent runtime 生成 |
| `request_id` | 与一次 command lifecycle 关联 |
| `session_id` | protocol session 上下文 |
| `timestamp_ms` | Agent 发送时钟，仅诊断 |
| `body` | `command_request` |

Envelope builder 不理解具体游戏能力，只负责 transport 包装。

### 5.7 Schema 撰写原则

- 采用静态 schema 先落地，后续再考虑从 proto descriptor 生成。
- 静态 schema 不应散落手写，优先从 `CapabilitySpec` 中心表读取 tool name、required、enum、exposure policy 和 timeout policy。
- 每个 LLM 可见 capability 对应一个 function schema。
- enum 用 LLM 友好的小写字符串，mapper 转 proto enum。
- 不使用 `$ref`。
- 不暴露 internal-only capability。
- 不暴露本期 Agent mapper 不支持的字段。
- 与总协议方案“`.proto` 是唯一事实源”的长期目标保持一致：Phase 1 静态 schema 只是过渡实现，必须用测试监控 proto/schema/mapper 漂移。

---

## 6. 备选方案

| 方案 | 描述 | 优势 | 劣势 | 结论 |
| --- | --- | --- | --- | --- |
| 方案 A（采用） | Agent 独立新增 schema/mapper/builder/test | 模块边界清楚，不影响现有执行链 | 初期与旧工具面并存 | 采用 |
| 方案 B | 直接替换 `core_tools.py` 为 protocol schema | 切换快 | LLM 行为和执行链同时变化，风险高 | 不选 |
| 方案 C | 直接改 bridge 输出 binary protobuf | 更接近最终传输 | transport 和 tool mapper 风险混在一起 | 后续阶段 |
| 方案 D | 直接从 proto 自动生成 schema | 长期一致性最好 | required、enum、internal policy 短期成本高 | 后续阶段 |

---

## 7. 风险与权衡

| 风险 | 影响范围 | 触发条件 | 缓解措施 |
| --- | --- | --- | --- |
| Python protobuf 版本不匹配 | Agent protocol 模块无法 import | gencode/runtime major 不一致 | 先修依赖或重新生成 |
| 新 schema 改变 LLM 行为 | Agent 决策质量 | 过早替换现有 `core_tools.py` | 先单独测试，不直接替换生产工具面 |
| 参数映射漂移 | Agent 输出 command 不符合 proto | 手写 schema 和 mapper 不一致 | schema 测试与 mapper 测试同时覆盖 |
| internal capability 暴露 | LLM 调用不应调用的工具 | schema 过滤缺失 | `tp` schema blacklist 测试 |
| transport 过早切换 | 问题定位困难 | mapper、schema、transport 同时变化 | 分阶段推进 |
| 旧工具面迁移断裂 | LLM 行为退化或调用不存在能力 | 直接替换 `core_tools.py`，但 `go_to/observe/wiki/memory` 没有协议等价物 | 先保留迁移矩阵与兼容测试，逐项替换 |
| runtime capability 未参与 schema gate | Agent 暴露 Mod 未启用 capability | 仅用静态列表生成 LLM schema | schema 生成时与 `enabled_capabilities` 求交 |
| 静态 `CapabilitySpec` 与 proto 漂移 | schema/mapper 看似通过但协议字段变化 | proto 更新后未同步 Agent 表 | 增加 registry/proto 一致性测试 |

关键权衡：Agent 侧先保证“能构造正确协议指令”，再处理真实传输。这样模块边界清楚，出问题时能判断是 schema、mapper、builder 还是 transport。

---

## 8. 失败模式与容错

| 组件 | 失败场景 | 影响 | 策略 |
| --- | --- | --- | --- |
| generated protobuf | import 失败 | 协议模块不可用 | 阻断实现，先修版本 |
| schema | required/enum 错误 | LLM 可能给出非法 args | schema 单测 |
| runtime capability gate | 暴露未启用 capability | Mod 返回 unknown capability 或命令超时 | 本地阻断并记录 disabled capability |
| mapper | 参数转换失败 | command 不应发送 | 返回本地 validation error |
| command builder | oneof 设置错误 | protobuf command 非法 | 单测断言 `WhichOneof("params")` |
| envelope builder | body 设置错误 | transport 输出非法 | 单测断言 `WhichOneof("body")` |

---

## 9. 测试计划 + DoD

### 测试计划

| 测试类型 | 覆盖范围 | 关键用例 |
| --- | --- | --- |
| import 测试 | generated protobuf | `agent.generated.protocol` 可 import |
| registry 测试 | `capability_registry.py` | 每个 exposed capability 有 params class、oneof 分支、timeout、enum 映射 |
| schema 测试 | `schemas.py` | 无 `$ref`、无 transport 字段、无 `tp` |
| runtime gate 测试 | schema 生成入口 | 静态允许列表与 fake `enabled_capabilities` 求交，未启用 capability 不暴露 |
| 迁移兼容测试 | 旧 `core_tools` 与 protocol schema | `go_to(target)`、`observe/wiki/remember` 不被误映射；重复工具不同时暴露 |
| mapper 测试 | `tool_mapper.py` | 每个 tool 正常参数、缺失参数、非法 enum、非法范围 |
| command 测试 | `command_builder.py` | `capability` 与 oneof 一致，source/timeout 正确 |
| envelope 测试 | `envelope_builder.py` | 序列化后反序列化，body 是 `command_request` |

### Definition of Done

- [ ] Agent protobuf generated classes 可 import。
- [ ] `CapabilitySpec` 覆盖本期所有 protocol-exposed capability，并与 schema/mapper/builder 测试共用同一事实表。
- [ ] Agent schema 不包含 transport 字段、不包含 `$ref`、不包含 `tp`。
- [ ] Agent schema 生成支持 runtime capability gate：未启用 capability 不暴露给 LLM。
- [ ] 现有 9 个 `core_tools` 的保留、迁移或替换路径已明确，且不会与新 protocol schema 重复暴露冲突。
- [ ] Agent mapper 覆盖所有本期 LLM 可见 tool。
- [ ] Agent mapper 拒绝非法 required、enum、范围和 capability/oneof 不一致输入。
- [ ] `say(emote=...)`、`use_tool.charge_level`、`equip.item_category` 等 Phase 1 决策已关闭并有测试。
- [ ] `CommandRequest` 构造测试覆盖每个 params oneof 分支。
- [ ] `Envelope` 序列化/反序列化测试通过。
- [ ] 文档只描述 Agent 侧 schema、mapper、builder、envelope 与测试。

---

## 10. 推进计划

| 阶段 | 范围 | 验证内容 |
| --- | --- | --- |
| 阶段 0 | 旧工具迁移决策 + capability registry | 迁移矩阵关闭，`CapabilitySpec` 中心表成型 |
| 阶段 1 | protobuf import 前置修复 | generated classes 可 import |
| 阶段 2 | schema + runtime gate + mapper + builder | 单元测试通过，未启用 capability 不暴露 |
| 阶段 3 | envelope 序列化闭环 | protobuf bytes 可反序列化 |
| 阶段 4 | 可选接入 transport adapter | mock transport 测试 |

回滚策略：不替换现有 `core_tools.py` 和 bridge 主链前，无运行时回滚成本。若后续接入 transport adapter，保留配置开关。

---

## 11. 影响评估

| 维度 | 评估 |
| --- | --- |
| 业务影响 | Agent 具备输出 Protocol 2.4.5 指令的能力 |
| 用户范围 | 当前开发分支内的 Agent 协议开发 |
| 工程工时 | 预计 2-3 人天完成 Agent schema/mapper/builder/test |
| 长期维护成本 | 初期增加一层 Agent mapping；长期减少工具面与 proto 漂移 |
| 依赖 | Python protobuf 版本一致性 |

---

## 12. 未解问题 Open Questions

| 问题 | 需要谁输入 | 决定截止日期 |
| --- | --- | --- |
| Python protobuf 是升级 runtime 还是重新生成 gencode | Agent 侧 owner | 实现前 |
| runtime enabled capabilities 的权威来源是 `HelloAck.enabled_capabilities`、adapter manifest，还是二者组合 | Agent/Mod owner | schema 生成入口实现前 |
| `go_to(target)` 是否保留为旧 UX tool，另做 parser 转结构化 params | Agent 侧 owner | schema 替换前 |
| `observe/search_wiki/read_wiki/remember` 是否继续作为 Agent-local tool 与 protocol tool 并存 | Agent 侧 owner | core_tools 替换前 |
| transport adapter 是否进入本期，还是只做到 bytes 输出测试 | Agent 侧 owner | 阶段 3 后 |

---

## 13. Action Items

- @AgentOwner 处理 Python protobuf gencode/runtime 版本一致性。
- @AgentOwner 关闭旧 `core_tools.py` 到 Protocol 2.4.5 capability 的迁移矩阵。
- @AgentOwner 新增 `agent/protocol/capability_registry.py`，定义 `CapabilitySpec` 中心表。
- @AgentOwner 新增 `agent/protocol/schemas.py`。
- @AgentOwner 新增 `agent/protocol/tool_mapper.py`。
- @AgentOwner 新增 `agent/protocol/command_builder.py`。
- @AgentOwner 新增 `agent/protocol/envelope_builder.py`。
- @AgentOwner 新增 runtime capability gate。
- @AgentOwner 新增 Agent 侧 protocol 单元测试，包括 registry、schema gate、mapper、builder、envelope 与旧工具迁移兼容测试。

---

## 14. 参考资料

- `protocol/proto/common.proto`
- `protocol/proto/transport.proto`
- `protocol/proto/capability.proto`
- `protocol/proto/capability_params.proto`
- `protocol/proto/capability_results.proto`
- `agent/generated/protocol/*`
- `agent/slow_loop/core_tools.py`
- `agent/slow_loop/action_dispatcher.py`
- `agent/bridge/ws_bridge.py`
- `docs-feat-workspace/Protocol 2.4.5 技术方案.md`
- `docs-feat-workspace/core_tool.md`

---

## 提交评审前自检清单

- [x] TL;DR 只讲 Agent 侧要做什么。
- [x] Goals / Non-Goals 明确排除 Agent 以外的执行实现。
- [x] 系统图只包含 Agent 内部模块和协议输出。
- [x] 第 5 节只描述 Agent schema、mapper、builder、envelope。
- [x] Alternatives 不少于 2 个。
- [x] Risks 不少于 3 条。
- [x] DoD 可勾选验证。
- [ ] 评审通过后再进入实现。

---

## 变更历史

| 日期 | 修改人 | 变更内容 |
| --- | --- | --- |
| 2026-05-18 | @Codex | 初版 |
| 2026-05-18 | @Codex | 收窄边界：移除 Agent 以外的执行实现与接收内容，仅保留 Agent 侧协议对接方案 |
| 2026-05-18 | @Codex | 根据评审补充 Phase 1 定位、旧工具迁移边界、runtime capability gate、CapabilitySpec、测试与 DoD |
