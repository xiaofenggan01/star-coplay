# 受控 MCP wire 与 Claude Adapter 集成验证

日期：2026-07-11

## 结论

受控、本地、无生产凭据的 MCP stdio server 已按 newline-delimited JSON-RPC 实现；此前未验证的 LSP `Content-Length` 假设已删除。自动 wire-level 测试已证明 initialize、tools/list、成功调用、标准 tool failure、credential revoke、跨 Runtime binding 拒绝及脱敏 JSONL 审计。Claude Adapter 已在 provider-private metadata 内物化 0600 credential 与 MCP config，启动参数真实包含 `--mcp-config <path> --strict-mcp-config`，公共 `AgentRuntimeAdapter` Contract 未变化。

这份证据**不声称真实 Claude 模型 MCP gate 已通过**。真实模型测试保留为 `RUN_REAL_CLAUDE_TESTS=1` opt-in；当前已有额度 403 证据，因此本轮未再次消耗模型额度。默认测试明确 skip，而不是用 mock 冒充真实 Claude 成功。

## 协议依据

本机 `claude --help`（当前安装版本）确认：

- `--mcp-config <configs...>`：加载指定 MCP JSON 配置。
- `--strict-mcp-config`：忽略其他 MCP 配置，仅使用显式配置。

受控测试客户端与 server 使用 MCP stdio 的逐行 JSON-RPC framing；测试同时断言 server 返回首字节为 `{`，避免退回 `Content-Length` framing。

## 自动验证

```text
python3 -m pytest platform/backend/tests/runtime_manager -q
27 passed, 4 skipped in 7.47s
```

4 个 skip 均为需要真实 Claude CLI、认证、网络和模型额度的 opt-in 测试。默认 suite 不调用模型。

```text
python3 -m pytest \
  platform/backend/tests/test_agent_subprocess_handle.py \
  platform/backend/tests/test_agent_worker.py \
  platform/backend/tests/test_gateway_runtime_telemetry.py -q
8 passed in 0.63s
```

```text
git diff --check
# exit 0
```

进程扫描未发现 `controlled_mcp_server` 或 `claude ... stream-json` 孤儿进程。

## 安全边界

- credential 为随机测试值，只写入隔离 WorkDir 的 0600 文件。
- MCP config 只记录 credential 文件路径，不嵌入 secret。
- audit 只记录 timestamp、tool、is_error、fingerprint 和固定 note。
- 成功响应只返回 SHA-256 前 16 位 fingerprint。
- 跨 Runtime 请求以服务端环境中的 Runtime binding 为准，客户端 claim 不匹配即拒绝。
- revoke marker 在每次调用时检查，存活 server 会立即拒绝后续调用。
- 未连接 Gateway、Mod、数据库、生产 MCP 或生产流量；未读取真实平台凭据；未 commit、push 或 deploy。

## 未完成 Gate

需要额度/认证恢复后执行：

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  platform/backend/tests/runtime_manager/test_claude_adapter_real.py \
  -k controlled_mcp -q
```

只有该 opt-in 测试真实通过后，才能把“当前 Claude CLI 已完成 MCP initialize/list/call 与 revoke”从未验证改为已验证。认证、额度或 ready 失败会显式 `xfail`，不会伪绿。
