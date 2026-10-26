# Agent-Mod 执行链路问题定位

日期：2026-05-16  
范围：Agent 指令无法被 Mod 完整执行、无法提供有效反馈的系统性定位。本文只做问题归因和证据整理，不展开修复设计。

## 摘要

当前问题不能归因于单个 capability 或单个 Mod bug。更准确的判断是：系统缺少一套可执行、可校验、可演进的“能力契约 + 命令生命周期 + 端到端观测”协议层。同时，线上还存在运行时所有权、Mod 执行态、输入/菜单事务、部署版本与日志观测等多条高风险链路。

优先级最高的 P0 问题集中在：

- Agent 调用的能力/API 与运行时 Mod 暴露能力不一致。
- 连接 owner、命令 target、AI 控制实例三者语义混用。
- 旧 session/旧 agent 没有立即停止，可能并发发命令。
- cancel/timeout 协议没有闭环。
- 菜单/对话/交互类动作缺少事务完成语义。

## P0 问题

### 1. 能力表与 Agent 调用不一致

错误类别：能力契约断裂。

佐证：线上 `mod/bridge/adapter_info.json` 的 `adapter_version=2.3.3` 只暴露到 `query_container_items`，没有 `go_to`、`find_targets`、`interact`、菜单能力或 `menu_interact`；但日志仍出现这些 `cap=`。`AdapterV2.Dispatch` 对未注册能力返回 `UNKNOWN_CAPABILITY`，见 `mod/StarCoPlay/V2/AdapterV2.cs`。

表现：Agent 认为已发出动作，Mod 立即拒绝或 Agent 等到超时；用户看到“正在做”但游戏无动作。

频率/严重性：高频，高严重。线上 `bridge.log` 抽样显示 `UNKNOWN_CAPABILITY` 多次出现。

优先级：P0。

### 2. L3 技能调用不存在的 SkillContext API

错误类别：Agent 内部能力执行 API 断裂。

佐证：`agent/v2_skills/builtin/go_bed/script.py` 调用 `ctx.send_command`，但 `agent/v2_skills/context.py` 没有该方法。线上 2026-05-16 09:44/09:46 多次出现 `AttributeError: 'SkillContext' object has no attribute 'send_command'`。`ship_items` 也有同类调用。

表现：`skill:go_bed` 成功排队，但 0 秒内失败，Mod 根本没收到 `go_bed`。

频率/严重性：对应技能每次触发必现，高严重。

优先级：P0。

### 3. 控制目标与连接 owner 混用

错误类别：运行时所有权/目标绑定断裂。

佐证：Mod hello 上报的是当前连接实例的 player id；执行 gate 使用本地 `_config.TargetPlayerId == Game1.player.UniqueMultiplayerID`，并要求 `_bridgeActive && EnableAIControl && _isAIControlledInstance` 才 `Poll()`。线上 2026-05-16 10:13-10:15 多个 query 60 秒超时。

表现：WS 已连接、Gateway 已发命令，但实际连接实例可能不是 AI 控制实例，Mod 不执行或无结果。

频率/严重性：多人/双开/重连时高风险，高严重。

优先级：P0。

### 4. target mismatch 静默丢弃

错误类别：失败反馈缺失。

佐证：`CommandProcessor` 遇到 `target_player_id` 与 `_bridgePlayerId` 不一致时只 Trace 日志并 `continue`，不返回 `command_result`。

表现：Agent 只能等超时，无法知道是目标不匹配。

频率/严重性：目标错配时必现，高严重。

优先级：P0。

### 5. new-kicks-old 没有立即停止旧 session/agent

错误类别：Session 生命周期竞争。

佐证：Gateway 新连接只把 displaced instance 转为 DRAINING，旧 websocket 和旧 `session_runner` 不会立即关闭；真正停止发生在 drain destroy。线上 `new-kicks-old` 计数较高，并观察到短时间多个 `session_runner` 同时发命令。

表现：同一用户可能有多个 Agent 影子实例同时决策、发命令，导致成功/失败/timeout 交错。

频率/严重性：线上不是偶发，高严重。

优先级：P0。

### 6. cancel 协议没有被 V2 正确执行

错误类别：命令生命周期协议缺失。

佐证：Agent cancel 发送 `capability=""` 且 `cancel=true`；Mod `WsBridge` 设置 `Action="__cancel__"`，但 V2 构造仍优先使用空 `Capability`，随后进入 Adapter 分发。线上大量 `cap= action_id=...` 与 timeout 后 cancel 相吻合。

表现：超时或用户取消不一定取消 Mod 正在执行的动作，反而可能产生 unknown/busy。

频率/严重性：每次 timeout/cancel 都可能触发，高严重。

优先级：P0。

### 7. 菜单/对话事务没有完成语义

错误类别：动作成功语义过早。

佐证：`InteractHandlerV2` 明确不等待对话框结果；InputBridge 完成只表示按键队列完成，不表示菜单/对话/睡觉事务完成。线上已有 `MENU_TIMEOUT`、`INVALID_PARAMS`、睡觉链路失败。

表现：`interact` 返回 OK 或进入下一步，但对话框仍开着、确认框未选择、菜单未完成，后续动作被阻塞。

频率/严重性：睡觉、商店、NPC 对话都受影响，高严重。

优先级：P0。

## P1 问题

### 8. 主线程单命令锁过粗，读操作可能被拖住

错误类别：执行并发模型不一致。

佐证：`CommandProcessor` 有 `_pendingV2Command/_activeBatchAction/_activeCommand` 时会拒绝或阻塞新命令；动画等待期间只允许 `observe` 或 `query_` 前缀。线上出现多个 `query_inventory/find_targets/query_containers` 60 秒超时。

表现：Agent 观测卡住，后续基于旧状态决策。

优先级：P0/P1。

### 9. observe/query 失败被降级吞掉

错误类别：状态感知与错误恢复弱。

佐证：`SkillContext` query 超时后返回失败结构，但 observe collector 可能继续输出 `status=ok` 或 partial 内容。线上多次 query 超时后仍继续决策。

表现：Agent 以为 observe 完成，实际缺少关键数据。

优先级：P1。

### 10. command_result 与 game_state/event 缺少 reconcile

错误类别：结果归因缺失。

佐证：Agent 分别消费 `command_result`、`state_snapshot`、`player_warped` 等事件，但没有统一按 action_id/job 做状态归因。`EventRelay` 会把 `player_warped` 转为 `ReportEvent`，但 `_wait_for_result` 只看 `read_command_result`。

表现：`NAVIGATION_TIMEOUT` 时角色可能已经移动或跨图，但 Agent 仍按失败处理；反过来也可能误判成功。

优先级：P1。

### 11. Gateway inflight 计数方向错误

错误类别：会话状态观测错误。

佐证：Gateway outbound pump 对 `command` 加一，却只对 outbound 的 `command_result` 减一；实际 Mod `command_result` 是 inbound。`command_inflight_count` 可能只增不减。

表现：管理 UI、drain、状态页不可信，旧 session 销毁可能等到超时。

优先级：P1。

### 12. 技能失败被调度层标记为 done

错误类别：任务生命周期与业务成功混用。

佐证：`SkillRunner` 内部异常会返回 `success=false/status=failed`，但 `ActionDispatcher._run_skill_job` 只特殊处理 cancelled，其他结果会 `_finish_job(job, "done")`。

表现：用户看到 queued/done，但真实动作未执行或失败。

优先级：P1。

### 13. 菜单能力注册但缺 metadata

错误类别：能力发现链路不完整。

佐证：当前代码注册 `open_menu/query_menu/menu_click/menu_close` handler，但 `GenerateAdapterInfoJson` 对无 metadata 的能力会跳过。

表现：即使 handler 存在，Agent 也可能发现不到。

优先级：P1，若生产依赖菜单能力则升 P0。

### 14. 输入注入 tick 依赖 AI gate

错误类别：输入队列可被静默饿死。

佐证：`InputBridge.OnUpdateTicking()` 仅在 `_bridgeActive && EnableAIControl && _isAIControlledInstance` 下运行。

表现：handler 已入队，但实例状态变化后输入队列不推进，Agent 只看到 timeout。

优先级：P1。

### 15. 日志与部署控制面不统一

错误类别：运维观测链路风险。

佐证：Gateway 由 systemd 管理，Web 线上为独立 `next start` 进程；`autorun.sh` 也能启动 gateway/web。`gateway.err` 巨大，agent 子进程 stderr 混入 Gateway 日志，且日志/进程参数存在敏感信息暴露面。

表现：排查时难确认当前版本、当前进程、当前日志；安全风险较高。

优先级：P1，敏感信息暴露按安全事项单独升 P0。

## 次级但需要跟踪的问题

- `query_menu` 命名虽是查询，但不满足 `query_` passthrough 规则，动画等待期间可能被 busy 拒绝。
- AI 自己触发 ESC 可能被 InputBridge 当成用户紧急停止，清空后续输入队列。
- 菜单点击依赖 DPad 邻接图和 `component_id`，对 Stardew 菜单覆盖有限，容易出现 `NOT_SUPPORTED/INVALID_PARAMS`。
- LLM request/response 只保留摘要，缺少足够的端到端 action timeline。

## 排查域总结

### 契约协议

核心问题是没有单一事实来源描述 capability 名称、版本、参数 schema、返回 schema、超时、取消、互斥、错误码和 postcondition。`adapter_info` 有雏形，但没有成为 Agent 工具注册、技能执行和 prompt 生成的强约束。

### 运行时生命周期

连接实例、AI 控制实例、命令目标、旧 session 生命周期没有统一 owner 模型。这个域解释了大量“命令已发但没有结果”的现象。

### 游戏态执行

Mod 主线程和输入注入不是简单 RPC。按键完成、executor idle、菜单打开、对话确认、地图到达是不同层级的完成条件。当前多个 handler 把低层完成当作高层事务完成。

### 观测与恢复

当前有 action_id，但缺少统一 trace：LLM tool call -> skill/action -> Gateway send -> Mod receive -> dispatch -> result -> Agent consume -> state/event reconcile。没有这条链路，错误会被粗略归因成“Mod 不执行”。

## 建议的定位优先级

1. 固定运行时 adapter_info 为唯一能力真相，并阻止 Agent 调用未暴露能力。
2. 明确 owner/target/AI controlled 三者语义，target mismatch 必须返回结果。
3. 立即收束旧 session/旧 agent，避免并发命令源。
4. 定义 cancel/timeout 的 V2 协议和 result 语义。
5. 为菜单/对话/睡觉类动作建立 postcondition 和 next required action。
6. 建立端到端 action timeline 与日志脱敏/轮转。

