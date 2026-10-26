# R8 独立 Manager 进程崩溃与 Reconcile 证据

日期：2026-07-11

## 结论

R8 已完成。验证使用独立 Manager OS 进程，不是删除 Python 对象：Manager 启动真实 Runtime child 并原子持久化 provider-neutral RuntimeRecord；测试对 Manager 发送 `SIGKILL`，确认 Runtime 在故障时刻仍存活；第二个全新 Manager 从 repository 与 owner 文件恢复事实，先撤销旧 credential，再依据 `runtime_id + generation + owner nonce + PID` 安全终止 orphan 或明确观察其已停止，最终写回 STOPPED。任何路径都不返回伪 ADOPT。

## Fake 四状态故障注入

```text
python3 -m pytest tests/runtime_manager/test_manager_process_crash.py -q -s
5 passed in 1.35s
```

覆盖 STARTING、READY、RUNNING、DRAINING 四个 crash point，以及旧 generation exit event 不污染新 generation。每个状态都真实强杀 Manager PID，并由另一个 Manager进程完成 reconcile；无需人工清 PID 或目录。

READY/RUNNING/DRAINING 的 Fake child 在 Manager stdio 管道消失后可能自行退出，因此第二 Manager允许 `MARK_STOPPED`；若仍存活则必须 `TERMINATE_ORPHAN`。两者都是可验证的安全收敛，`ADOPT` 一律失败。

## 真实 Claude 关键状态

```text
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_manager_process_crash_real.py -q -s
2 passed in 6.13s
```

真实 Claude覆盖 READY 与 RUNNING。使用已认证宿主入口但不读取或复制 token；Manager SIGKILL后，第二 Manager 撤销旧 MCP credential，执行 Claude provider 的 stdio-not-reattachable裁决，最终 STOPPED且无孤儿。

## 完整验收

```text
python3 -m pytest tests/runtime_manager -q
37 passed, 12 skipped in 5.49s

RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_claude_adapter_real.py \
  tests/runtime_manager/test_claude_failure_isolation_real.py \
  tests/runtime_manager/test_manager_process_crash_real.py -q -s
11 passed in 79.65s

python3 -m pytest tests/test_agent_subprocess_handle.py \
  tests/test_agent_worker.py tests/test_gateway_runtime_telemetry.py -q
8 passed in 0.03s
```

## 边界

FileRuntimeRepository是 Spike 的本地持久测试实现，不是生产数据库。未接生产流量、Gateway、Mod，未 commit、push、deploy。systemd/container 与宿主重启属于后续 E4 验证，不影响本次 Manager OS 进程崩溃目标。
