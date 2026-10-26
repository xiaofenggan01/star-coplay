# 真实 MCP Gate — 通过 ✅

日期：2026-07-11
执行模型：`claude-sonnet-5`

## 结论

**Turn 1 真实 MCP Gate 全部 5 条验收条件通过。**

真实 Claude CLI (`claude-sonnet-5`) 通过 `--mcp-config` + `--strict-mcp-config` 与 `controlled_mcp_server.py` stdio JSON-RPC server 完整握手，在同一 PID 下完成了 initialize → tools/list → controlled_echo(success) → controlled_fail(isError:true) → revoke → controlled_echo(isError:true) 全链路。

## 验收条件逐条证据

### 1. 同一真实 Claude PID 完成全部阶段

| 阶段 | PID 断言 |
|------|---------|
| ready (bootstrap) | PID 获取，非 None |
| controlled_echo success | `assert pid unchanged` ✅ |
| controlled_fail | `assert pid unchanged` ✅ |
| revoke → echo rejected | `assert pid unchanged` ✅ |

### 2. Server audit 序列且无秘密全文

Audit 条目按顺序：

```
protocol-init → protocol-tools-list → ok → deliberate-failure → revoked
```

- `protocol-init`: `{"tool": "__protocol__", "is_error": false, "fingerprint": null, "note": "protocol-init"}`
- `protocol-tools-list`: `{"tool": "__protocol__", "is_error": false, "fingerprint": null, "note": "protocol-tools-list"}`
- `ok`: `{"tool": "controlled_echo", "is_error": false, "fingerprint": "<sha256[:16]>", "note": "ok"}`
- `deliberate-failure`: `{"tool": "controlled_fail", "is_error": true, "fingerprint": null, "note": "deliberate-failure"}`
- `revoked`: `{"tool": "controlled_echo", "is_error": true, "fingerprint": null, "note": "revoked"}`

**无秘密全文**：audit 全文不包含 credential 原文（`assert secret not in audit_text` ✅）。
每条 audit entry 仅含 5 个字段：`timestamp, tool, is_error, fingerprint, note`。

### 3. `controlled_fail` 与 revoke 的 MCP tool result 是 `isError:true`

| 工具调用 | audit `is_error` | 模型输出 |
|---------|-----------------|---------|
| controlled_fail | `true` ✅ | 模型如实报告 tool error |
| revoke 后 controlled_echo | `true` ✅ | 模型输出含 `"revoked"` ✅ |

CLI 的 result frame `subtype=success`（因为 turn 本身成功，Agent 正确处理了 tool error），但测试通过 **audit 日志** 而非 CLI result frame 验证 `isError:true`，不会误报工具成功。

### 4. initialize / tools/list 有真实 protocol 证据

Server audit 在 `initialize` 时写入 `protocol-init`，在 `tools/list` 时写入 `protocol-tools-list`。这些事件：
- 来自 server `main()` 的 JSON-RPC dispatch，不是模型自然语言输出
- 不含 credential/secret
- 与 tool call 共用同一 audit schema

### 5. finally 无 Claude/MCP 子进程残留

`adapter.stop()` 返回 `stopped=True`。测试在 finally 中用 `os.kill(pid, 0)` 确认进程已退出（`ProcessLookupError` ✅）。

## 测试命令与结果

```bash
# Wire 测试（无需 Claude CLI）
cd platform/backend
python3 -m pytest tests/runtime_manager/test_controlled_mcp_wire.py -q
# 2 passed in 0.12s

# 真实 Claude 测试（需 RUN_REAL_CLAUDE_TESTS=1 + 已认证 claude CLI）
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest tests/runtime_manager/test_claude_adapter_real.py -q -s
# 4 passed in 27.03s

# 完整回归
python3 -m pytest tests/runtime_manager/ -q
# 27 passed, 4 skipped in 4.59s
```

## 本次修改清单

| 文件 | 改动 |
|------|------|
| `controlled_mcp_server.py` | `main()` 增加 `protocol-init` / `protocol-tools-list` audit 事件 |
| `test_controlled_mcp_wire.py` | audit 序列断言更新：增加前两个 protocol 事件 |
| `test_claude_adapter_real.py` | 修复 `import json`；增强 `test_real_claude_calls_controlled_mcp_and_observes_revocation`：PID 逐段断言、controlled_fail 调用、完整 audit 序列验证、秘密防泄漏、进程残留检查 |

## Fingerprint 机制

- `adapter.prepare()` 生成 32 字节 `secrets.token_urlsafe(32)` credential → 写入 `workdir/mcp-credential` (0o600)
- MCP config 引用 credential 路径（环境变量），不内联 credential 原文
- Server 读取 credential 计算 `sha256[:16]` fingerprint → 写入 audit，永不输出原文
- `mcp_config_path` 文件本身不包含 credential 原文（`assert credential.read_text() not in config_raw` ✅）
