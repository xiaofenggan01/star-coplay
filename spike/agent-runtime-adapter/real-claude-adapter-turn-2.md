# Turn 2：受控本地真实 MCP Server、配置与 Claude 工具调用验证

你是执行者。先阅读 Turn 1 Adapter、真实测试和两份 evidence。本轮只增加受控本地测试 MCP 能力，不连接生产、Gateway、Mod、数据库，不读取真实平台凭据。

允许修改：

- `platform/backend/modules/runtime_manager/adapters/claude/**`
- `platform/backend/tests/runtime_manager/controlled_mcp_server.py`
- `platform/backend/tests/runtime_manager/test_claude_adapter_mcp_real.py`
- `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-2-real-mcp.md`

禁止修改公共 `contracts.py`/DTO 来迎合 Claude/MCP；禁止修改 `DRIVER.md`、`DRIVER_STATE.json`、`.driver/`；禁止 commit/push/deploy；不得输出认证材料。

## 目标

启动一个完全本地、测试专用的 MCP stdio server，并由现有 `ClaudeAgentRuntimeAdapter` 在隔离 WorkDir 生成/加载 `--mcp-config`。真实 Claude 单进程必须完成以下闭环：

1. MCP initialize 成功。
2. tools/list 能看到测试工具。
3. 真实 Claude 调用 success tool 并收到结构化结果。
4. 真实 Claude 调用 failure tool，错误必须诚实传播，不能被 Adapter/模型表述成成功。
5. credential 文件被 revoke 后，**同一仍存活 Claude PID** 再次调用受保护工具必须被 server 拒绝；拒绝不能靠重启进程或删除工具实现。

## 受控 MCP Server

优先使用仓库已有 MCP Python 依赖；若没有，允许实现最小 MCP stdio JSON-RPC server，但必须遵守实际 MCP initialize/initialized、tools/list、tools/call wire schema，不能 fake Claude stdout。Server 只提供测试工具，例如：

- `controlled_echo`：读取 WorkDir 内测试 credential，校验 runtime/binding context，返回不含秘密的 fingerprint 和 echo。
- `controlled_fail`：返回标准 MCP tool error（`isError: true` 或 SDK 对应错误）。

测试 credential 是随机短期测试值，只写隔离 WorkDir，权限 0600；server 响应/日志只包含 hash fingerprint，不包含全文。revoke 可用由 Runtime Manager 测试端创建的 `credential.revoked` 文件，server 每次 call 实时检查。

## Adapter 要求

- 在 Claude provider-private `prepare` 物化 MCP config；配置路径与 server command 均来自 provider-private metadata/fixture，不进入公共 DTO 顶层。
- 启动命令真正带 `--mcp-config <path> --strict-mcp-config`，并允许该测试 MCP tool（不能继续 `--tools ""` 禁掉它）。
- Ready 必须区分 process/turn ready 与 MCP ready。通过 Claude stream-json 的 init metadata或一次确定性 probe 证明 MCP server 可用；如果 CLI没有可靠 init事件，则使用真实工具 probe并如实标注。
- tool use/result frame 必须被 transport 正确消费，不能当 unrecognized frame丢失；对业务 `submit_input` 仍以 result 为 turn边界。
- 不得在公共 `TurnResult` 中暴露 credential 或原始 Claude session结构。

## Opt-in 真实测试

默认 skip，仅 `RUN_REAL_CLAUDE_TESTS=1` 执行。测试必须：

- 记录同一 PID 在成功、失败、revoke 三阶段保持不变。
- 用强约束 prompt 要求调用指定工具，验证 server 侧审计日志确实收到 tools/call，不能只检查模型自然语言。
- 成功调用：审计为 success且只含 fingerprint。
- failure tool：审计为 error；最终 assistant/result不得被测试误判为工具成功。若 Claude turn本身仍 `result/success`（模型成功处理了工具错误），测试必须以 server audit + tool_result `is_error` 作为事实，不错误期待 CLI result失败。
- revoke：运行中创建 revoke marker，再调用 protected tool；server audit必须为 revoked，PID不变，工具不得返回原成功payload。
- finally 清理 Claude/server 子进程与测试 credential；不得残留。

每个 Claude turn timeout <=60s，总测试 <=180s。先跑默认全套，再运行 opt-in MCP 测试。失败时记录精确脱敏 frame/audit和退出码；不得 mock 绿色。最终写 Turn 2 evidence，列出 exact commands/results/PID/fingerprint/revoke；完成后停止。
