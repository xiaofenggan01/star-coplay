# Turn 3（最后修复轮）：实现最小真实 MCP 闭环

这是最后一轮，只做最小实现，不做长篇调研。阅读现有 Claude Adapter 和 `real-claude-adapter-turn-2.md` 的验收要求。Turn 2 执行器卡住且零产物；你必须直接实现并测试。

允许改动仅限：

- `platform/backend/modules/runtime_manager/adapters/claude/**`
- `platform/backend/tests/runtime_manager/controlled_mcp_server.py`
- `platform/backend/tests/runtime_manager/test_claude_adapter_mcp_real.py`
- `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-2-real-mcp.md`

禁止公共 DTO 变更、生产/Gateway/Mod、真实平台凭据、commit/push/deploy、Driver 文件修改。

直接交付：

1. 最小 stdio MCP JSON-RPC server：支持 initialize、notifications/initialized、tools/list、tools/call。工具 `controlled_echo` 校验 WorkDir 0600 随机测试 credential并返回 fingerprint；`controlled_fail` 返回 `isError:true`；存在 revoke marker时 echo返回 `isError:true`。每次 call 写不含秘密的 JSONL audit。
2. Claude provider-private prepare/launch 可从 metadata 接收 server command/path，物化 0600 credential + MCP config，启动真正带 `--mcp-config` 和允许 MCP tool 的 `--tools`。不得把 MCP 字段加入公共 DTO顶层。
3. transport 保留 tool_use/tool_result相关 assistant/user frame，不把错误当成功。若 CLI turn最终 result仍success，以 server audit作为工具事实。
4. 默认skip的 opt-in真实测试：同一PID依次强制调用 echo成功、fail错误、创建 revoke marker后 echo拒绝；每阶段读取 audit证明真实 tools/call；fingerprint非secret；finally清理。
5. 运行默认 runtime tests与 `RUN_REAL_CLAUDE_TESTS=1` MCP测试。每turn <=60秒，总体<=180秒。若真实测试失败，保留失败证据，不得 mock绿色。

优先完成可执行代码和测试；报告可简短。完成后停止，不要继续重构。
