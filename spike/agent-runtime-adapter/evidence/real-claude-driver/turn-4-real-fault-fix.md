# 真实 Claude TERM 与双 Runtime 隔离 Gate — 通过

日期：2026-07-11

## 修复范围

本轮只修复 turn 3 未完成的两个 provider-private Gate：

1. TERM 故障注入不再把 `claude_binary` 错替换为 Python。测试实际执行 `python stop_escalation_wrapper.py ignore-eof <真实 Claude command...>`，真实 bootstrap 收敛后，关闭 stdin 不退出，最终由 TERM 有界退出。
2. 双 Runtime 不再用空 `HOME` 切断认证。两个 Runtime 保留宿主已授权的系统认证入口，不读取、不复制、不打印 OAuth token；同时分别使用独立 WorkDir、MCP config/credential/audit、显式 `--settings` 文件，以及 `--no-session-persistence`，隔离可变配置和会话状态。

公共 `AgentRuntimeAdapter` contract 未修改。仅在 Claude provider-private launch config 增加可选 `claude_settings_path`，映射为 Claude CLI 的 `--settings`。

## 真实验收证据

### TERM 单项

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_claude_failure_isolation_real.py::test_stop_escalation_term_when_process_ignores_eof -q -s

1 passed in 12.54s
```

### 双 Runtime 单项

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_claude_failure_isolation_real.py::test_dual_runtime_isolation_distinct_credentials_and_cross_mcp_rejection -q -s

1 passed in 11.66s
```

测试证明：A/B 均完成真实 bootstrap 和首轮输入；PID、Claude session、WorkDir、settings、MCP credential fingerprint 均不同；停止 A 后 B 完成第二轮并恢复自己的 session-only marker；A runtime id 对 B MCP context 的调用被 B audit 记录为 `runtime-mismatch`；audit 不含完整 credential。

### 完整真实故障与隔离文件

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_claude_failure_isolation_real.py -q -s

5 passed in 51.67s
```

覆盖 EOF、TERM、KILL、双 Runtime 隔离、真实 orphan reconcile。相关 Gate 不再使用 xfail；认证失败或隔离失败会直接红。

### 默认与 legacy 回归

```text
python3 -m pytest tests/runtime_manager -q
27 passed, 9 skipped in 3.86s

python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
6 passed in 0.05s

git diff --check
exit 0
```

## 安全与残留

- 未读取、复制或输出用户 OAuth token。
- 测试只传递继承环境给真实 Claude 子进程；证据与 audit 只记录 credential fingerprint 或拒绝原因。
- 验收后进程扫描未发现 Claude stream-json、stop wrapper 或 controlled MCP server 孤儿。
- 未 commit、push、deploy；未接入 Gateway、Mod 或生产流量。

## 裁决

turn 3 的两个未完成 Gate 已有直接真实证据：TERM 有界升级通过，双真实 Claude Runtime 隔离与跨 MCP context 拒绝通过。
