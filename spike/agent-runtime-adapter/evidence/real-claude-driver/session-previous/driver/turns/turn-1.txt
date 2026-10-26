# Turn 1：AgentRuntimeAdapter Harness 第一阶段

你是执行 Agent。严格阅读并遵守仓库根 `DRIVER.md`，以及本 worktree 内的正式实现规格：

`spike/agent-runtime-adapter/implementation-spec.md`

## 本轮范围

只实现第一阶段：

1. `platform/backend/modules/runtime_manager/contracts.py`
2. `platform/backend/modules/runtime_manager/clock.py`
3. `platform/backend/modules/runtime_manager/process_supervisor.py`
4. `platform/backend/modules/runtime_manager/fake/` 下的 Fake NDJSON 真子进程与 Fake Adapter
5. `platform/backend/tests/runtime_manager/` 下的基础测试：
   - 明确 ready 握手与 ready timeout；PID 存活不等于 ready
   - 同一 PID/runtime/generation 连续三轮输入与内存 marker 保持
   - 正常应用级 stop
   - 忽略应用 stop 后 TERM
   - 忽略 TERM 后 KILL
   - stdout/stderr drain 与进程退出收敛

遵循规格中的 provider-neutral 接口、WorkDir 隔离和 `runtime_id + generation` 身份。允许为了测试性对规格中的具体类型做小幅实现性调整，但必须在最终报告解释，不能加入 Claude、Gateway、Mod 或 `agent_instance_id` 专有字段。

## 严格禁止

- 不修改 `platform/backend/modules/runtime/` 及其他 legacy 文件。
- 不实现 TTL Harness、reconcile、生产 MCP、Gateway、数据库、Claude Adapter；这些属于后续轮次。
- 不读取真实凭据，不接生产流量。
- 不 commit、push、deploy。
- 不修改 `DRIVER.md`、`DRIVER_STATE.json`、`.driver/`、本任务文件或正式规格。
- 不通过放宽断言、跳过测试或只 mock `process.alive` 来制造通过；关键路径必须运行真实 Fake 子进程。

## 自检

从 `platform/backend` 运行：

```bash
python3 -m pytest tests/runtime_manager -q
python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
```

如 ruff/mypy 已安装，再运行：

```bash
python3 -m ruff check modules/runtime_manager tests/runtime_manager
python3 -m mypy modules/runtime_manager
```

最终报告：修改文件、关键设计、精确测试结果、未完成项与风险。完成后停止，不继续下一阶段。
