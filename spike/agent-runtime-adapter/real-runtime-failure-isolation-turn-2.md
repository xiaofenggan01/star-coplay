# 最后一轮：真实 Claude 故障、双实例隔离与 reconcile opt-in tests

只做 provider-private 测试、必要最小实现和 evidence。先读现有 Claude Adapter、ProcessSupervisor、reconcile tests、真实 tests。允许修改：

- `platform/backend/modules/runtime_manager/adapters/claude/**`
- `platform/backend/tests/runtime_manager/test_claude_adapter_real.py`
- 可新增 `platform/backend/tests/runtime_manager/test_claude_failure_isolation_real.py`
- `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-3-failure-isolation.md`

禁止公共 contract/DTO 变更、生产/Gateway/Mod、真实平台凭据、commit/push/deploy、Driver 文件。

新增默认 skip、仅 `RUN_REAL_CLAUDE_TESTS=1` 运行的真实测试，必须覆盖：

1. **Stop escalation**：实际 Claude 的 EOF stop 若正常退出，只声称 EOF 路径。另用受控 provider-private 外层 wrapper/进程组故障注入：忽略 EOF但响应 TERM，证明 TERM 有界退出；忽略 EOF+TERM，证明 KILL兜底。不得声称 Claude本体忽略TERM，除非真实发生。验证整个process group无残留。
2. **双真实 Runtime隔离**：同时启动 A/B真实Claude进程，显式不同HOME、WorkDir、MCP config、credential、audit/log、session。A/B各完成至少一turn；stop A后B继续一turn；用A credential对B context发起真实MCP call并由B server audit证明 `runtime-mismatch`/拒绝。PID/session/路径/fingerprint均不同，无secret写日志。
3. **真实 process reconcile**：启动真实Claude后丢弃第一个 Manager/Harness/Adapter对象，仅保留owner/generation record。新实例检查持久owner/nonce/PID。当前stdio无法跨Manager重附时，必须返回/执行 terminate-or-restart安全裁决并确认旧进程终止，不能返回伪ADOPT。若需对 provider-private reconcile做最小修复，可以，但不改公共契约。
4. 每个测试 finally清理；测试后无Claude、controlled MCP、wrapper子进程；WorkDir/日志/evidence无 credential全文。

运行：

```bash
cd platform/backend
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest tests/runtime_manager/test_claude_adapter_real.py tests/runtime_manager/test_claude_failure_isolation_real.py -q -s
python3 -m pytest tests/runtime_manager -q
python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
```

测试必须有总时限，双实例控制成本。真实认证/额度失败不能xfail成通过；如失败记录并停止。Evidence明确区分：真实Claude事实、受控wrapper故障注入事实、尚未支持的能力。完成后停止。
