# 真实 Claude 故障、隔离与 Reconcile — 部分通过，Gate 未完成

日期：2026-07-11
Driver session：`42e18add-4c18-4dae-a3e1-cd2ec6e22415`
执行模型：`claude-sonnet-5`

## 总结

本轮新增真实 opt-in 测试与 provider-private reconcile 安全裁决，但完整 Gate **未通过**：

- 真实 Claude EOF stop：已有基础真实测试证明可正常退出。
- 受控 wrapper 的 KILL 兜底：通过。
- 受控 wrapper 的 TERM 路径：失败，测试构造了错误的 child command并等待 60 秒超时。
- 真实双 Runtime：未验证；隔离 HOME 导致两个 Runtime bootstrap 均 `is_error:true`，测试被 xfail。不能把 xfail当通过。
- 真实进程 reconcile：通过；新 Adapter不伪 ADOPT，按 `stdio-not-reattachable` 裁决 `TERMINATE_ORPHAN`，旧进程被终止。
- 无孤儿进程：验收后扫描通过。

## Claude 执行产物

- `platform/backend/tests/runtime_manager/test_claude_failure_isolation_real.py`
- `platform/backend/tests/runtime_manager/stop_escalation_wrapper.py`
- `platform/backend/modules/runtime_manager/adapters/claude/adapter.py` 的 reconcile 安全裁决

Claude executor运行真实测试超过约 3 分钟后继续长时间无最终输出；Subagent在约 10 分钟有界终止外层 Driver，并独立逐项验收。

## 独立验收

### TERM / KILL fault injection

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  ...::test_stop_escalation_term_when_process_ignores_eof \
  ...::test_stop_escalation_kill_when_process_ignores_eof_and_term -q -s

1 failed, 1 passed in 82.95s
```

失败原因不是“Claude忽略 TERM”：TERM 测试把 `claude_binary` 覆盖成 Python解释器后再包装，实际 child command不是有效 Claude CLI，bootstrap无 result并在 60 秒超时。因此：

- 可以声称受控 wrapper `ignore-eof-term` 的 process-group KILL兜底通过。
- 不能声称 TERM路径通过。
- 不能声称 Claude本体忽略过 EOF或TERM。

### 双真实 Runtime与 reconcile

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  ...::test_dual_runtime_isolation_distinct_credentials_and_cross_mcp_rejection \
  ...::test_real_claude_reconcile_terminates_orphan_never_adopts -q -s

1 passed, 1 xfailed in 4.81s
```

双实例 xfail精确原因：

```text
Claude unavailable: A={'is_error': True} B={'is_error': True}
```

测试给 A/B 设置全新空 HOME，导致认证/配置不可用。虽然 WorkDir/MCP credential会不同，但没有完成两个真实 Runtime各自的 turn、stop A后B继续、真实跨 credential拒绝，所以隔离 Gate未通过。

Reconcile 测试真实启动 Claude并写 owner PID/nonce；丢弃第一个 Adapter/handle后，新 Adapter基于 owner/generation判定 stdio不能重新附着，返回 `TERMINATE_ORPHAN` 而非 `ADOPT`，并确认旧 PID死亡。这个结论通过。

### 回归与残留

```text
python3 -m pytest tests/runtime_manager -q
27 passed, 9 skipped in 4.53s

python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
6 passed in 0.04s

git diff --check
exit 0
```

验收后 `ps` 未发现 Claude stream-json、stop wrapper、controlled MCP server或Fake Runtime残留。

## 仍需修复

Driver本轮是批准的最后一轮，Subagent不直接修改实现。后续最小修复应为：

1. TERM测试不要把真实 `claude_binary` 替换成 Python；wrapper command应是 `python wrapper.py ignore-eof <真实 claude command...>`。
2. 双实例隔离不能用空 HOME切断认证。需要构造“独立运行 HOME + 只读/受控认证入口”或使用不依赖 HOME 的受支持认证注入，同时不能复制/打印真实 token。
3. 删除双实例中的 `pytest.xfail` 成功退出路径；认证失败必须让 Gate失败或显式 blocked。
4. 修复后再次真实运行，并用 server audit证明 A credential在B context被拒绝、stop A后B继续。

结论：**Reconcile terminate/restart裁决和 KILL兜底有真实证据；TERM与双真实Runtime隔离尚未完成，不能标记整项完成。**
