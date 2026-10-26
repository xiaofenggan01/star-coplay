# Turn 3：Harness、TTL、MCP 模拟、Reconcile 与双实例隔离

继续 Driver session `44c47323-72a5-456f-bb4a-dd13936cef12`。先重新阅读 worktree 内正式规格：

`spike/agent-runtime-adapter/implementation-spec.md`

本轮只允许修改：

- `platform/backend/modules/runtime_manager/`
- `platform/backend/tests/runtime_manager/`
- `spike/agent-runtime-adapter/evidence/`（如需写不含秘密的测试证据）

## 必须实现并测试

### 1. Harness / Registry / ManualClock

- 新增 provider-neutral Harness 与 in-memory registry；生命周期状态与锁按正式规格实现。
- 测试用 `ManualClock` 必须可确定性 `advance()` 唤醒 waiter，单测不得真实等待 600 秒。
- `RuntimeSpec.idle_ttl_seconds` 默认值必须保持 `600`。
- 注入 TTL=10 秒：推进到 9.9 秒不得回收；推进到 10 秒进入回收流程。
- 有效活动刷新 TTL：用户输入、正常可见输出、成功 MCP call/result、正常 turn 完成。
- 纯 stdout/log、heartbeat、重复错误/故障循环不得刷新 TTL。
- turn inflight 时到期必须先进入 `DRAINING`，不得直接 KILL；有界等待/取消后才 stop。

### 2. MCP Fake 交互与撤销

- Fake Runtime/Adapter 支持 `initialize → tools/list → tool call → result` 的最小模拟协议和可审计 trace。
- 每个 runtime 使用独立 `mcp.json` 与 credential fingerprint，禁止在日志/结果输出 credential 全文。
- revoke 后，即使进程仍活，后续 MCP call 必须被拒绝，并有结构化错误。
- credential 的权限与 workdir 隔离继续保持。

### 3. Reconcile

- 决策必须校验 `runtime_id + generation + runtime-owner nonce`，单凭 PID 不得认领。
- 相同可信 identity 的活 Runtime 可 adopt/continue；desired STOPPED 的可信孤儿必须终止。
- nonce 或 generation 不匹配必须拒绝认领/隔离，不得删除任意未知目录。
- 旧 generation 的 exit/event 不得改变当前新 generation record。
- 用真实 Fake 子进程覆盖至少一个“丢弃首个 Harness 对象，由第二个 Harness/repository reconcile 后收敛”的路径，不能只 mock alive。

### 4. 双 Runtime 隔离

- A/B 同时运行时，workdir、cwd/env、credential/fingerprint、logs、checkpoint/marker 全部隔离。
- A credential 不能用于 B context。
- stop A 不影响 B，B 能继续完成下一 turn。
- 同 runtime 的新 generation 不得复用旧 generation credential/session。

## 工程约束

- 不改 `platform/backend/modules/runtime/` 和任何 legacy 测试/代码。
- 不接真实 Gateway、Mod、生产 MCP、数据库或真实凭据。
- 不实现 Claude Adapter，不加入 Claude/Gateway 专有字段。
- 不修改 `DRIVER.md`、`DRIVER_STATE.json`、`.driver/`、正式规格或本任务文件。
- 不 commit、push、deploy。
- 测试关键路径必须用真实 Fake NDJSON 子进程；不得用布尔 mock 替代进程证据。
- 保持改动聚焦；发现现有第一阶段缺陷可以最小修复并解释。

## 自检

```bash
cd platform/backend
python3 -m pytest tests/runtime_manager -q
python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
```

若命令受权限限制，明确报告，Codex 会独立运行。最终列出修改文件、状态/竞态裁决、测试结果、未完成项与风险，然后停止；不要进入真实 Claude Adapter 或生产集成。
