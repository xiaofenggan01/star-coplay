# Agent Runtime Adapter Spike 完成审计

> 审计日期：2026-07-13
> 结论：**Spike 已证明核心 Claude Runtime 可托管路径，R1–R12 在本次非生产验证边界内全部满足。**
> 边界：未接入生产流量、Gateway、Mod、数据库，也未执行部署或其他生产副作用。

## 1. 本次独立复验

从 `platform/backend` 执行：

```text
python3 -m pytest tests/runtime_manager -q
# 45 passed, 12 skipped；R6 修复后 fresh suite 连续三次全绿

python3 -m pytest \
  tests/test_agent_subprocess_handle.py \
  tests/test_agent_worker.py \
  tests/test_gateway_runtime_telemetry.py -q
# 8 passed in 0.03s

RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_claude_adapter_real.py \
  tests/runtime_manager/test_claude_failure_isolation_real.py \
  tests/runtime_manager/test_manager_process_crash_real.py -q -s
# 11 passed in 90.63s

python3 -m compileall -q modules/runtime_manager tests/runtime_manager
git diff --check
# exit 0
```

`compileall`、diff 检查、公共边界扫描和测试进程残留检查均通过。当前 Python 环境未安装 `ruff` / `mypy`，因此本轮未执行这两项；真实测试使用当前已认证 Claude CLI 和 `claude-sonnet-5`，没有读取、复制或输出认证材料。上述结果是本次审计重新运行所得，不依赖旧报告的绿灯。

## 2. R1–R12 审计

证据等级：E1=本机自动化；E2=真实 Claude Adapter；E3=受控真实 MCP；E4=真实 Gateway/Mod、服务管理器或宿主故障环境。

| ID | 裁决 | 已证明 | 缺口 / E4 风险 | 权威证据 |
|---|---|---|---|---|
| R1 非交互启动 | **完成（E1/E2）** | 非 TTY Claude 启动成功；同一锁内以原子 shared startup task 决定 owner/waiter，并发相同 spec 只执行一次 prepare/start、复用同一 entry/PID和异常；启动中的冲突 spec / generation 结构化拒绝 | 当前幂等锁为单 Manager 进程内语义；跨 Manager 分布式锁属于生产控制面 E4 | `harness.py::start_runtime`; `test_start_idempotency.py`; `test_claude_adapter_real.py` |
| R2 Ready 非 PID | **完成（E1/E2）** | Harness 拥有并有界调用 `wait_ready`，成功前不发布 READY；Fake timeout 明确；真实 Claude spawn 后 `ready=False`，必须完成受控 bootstrap `system/init -> result/success` 才 ready，测试 worker 对 Fake/Claude 使用各自有界 deadline | E4 中真实生产 MCP/Gateway readiness 尚未组合；当前 ready 只证明 Runtime/受控 MCP路径 | `harness.py`; `manager_crash_worker.py`; `test_adapter_contract.py::test_ready_timeout_is_explicit_not_pid_alive`; `test_claude_adapter_real.py::test_real_claude_ready_is_bootstrap_turn_not_pid_alive` |
| R3 多轮持续运行 | **完成（E1/E2）** | 同一 runtime/generation/PID 连续 3 轮；内存 marker 未落盘且可召回；真实双 Runtime 中 B 在停止 A 后继续第二轮 | E4 长时间运行、网络抖动和资源压力未覆盖 | `test_adapter_contract.py::test_three_turns_same_pid_runtime_generation_with_memory_marker`; `test_claude_adapter_real.py::test_real_claude_three_turns_same_pid_and_marker_recall_without_disk_leak`; Turn 1/4 evidence |
| R4 MCP initialize/list/call | **完成（E1/E2/E3，受控 MCP）** | newline JSON-RPC wire；initialize bootstrap 使用客户端总 deadline，服务端提前退出时保留 stderr 证据；真实 Claude 同 PID完成 initialize、tools/list、success、`isError:true` failure、revoke 后拒绝；模型如实报告 tool error | 未连接生产 StarCoPlay MCP、Gateway/Mod；不能外推生产多租户 MCP 已完成 | `controlled_mcp_server.py`; `test_controlled_mcp_wire.py`; `test_claude_adapter_real.py::test_real_claude_calls_controlled_mcp_and_observes_revocation`; Turn 2 evidence |
| R5 MCP credential 撤销与隔离 | **完成受控 E1/E3；生产 claims 未验证** | 0600 随机 credential；config 不内联 secret；存活 server 每次调用检查 revoke；跨 runtime context 拒绝；audit 仅 fingerprint | 当前不是 Runtime Manager 签名 claims，不含真实 `binding_version/generation/aud/exp/jti` 校验；生产撤销传播属后续 MCP 工作 | `adapter.py::prepare`; `controlled_mcp_server.py`; wire/dual-runtime tests; Turn 2/4 evidence |
| R6 600 秒 idle TTL 等价测试 | **完成（E1）** | 默认 600 秒；ManualClock 9.9 秒不回收、10 秒回收；输入被 Harness 接受时在实例锁内刷新活动，成功完成再刷新而失败/timeout不刷新完成活动；真实阻塞 turn 证明 t=9 接受、t=10 不 drain、t=19 才 DRAINING；STOPPING 前新输入以 drain epoch 原子取消旧 drain，失效 waiter 不得停止新 turn，STOPPING 后仍拒绝输入；MCP result刷新，log/重复错误不刷新 | E4 的真实 600 秒 wall-clock、真实长 turn/cancel、Manager 重启后 TTL 恢复未覆盖 | `test_idle_ttl.py`; `harness.py`; `evidence/phase-2-verification.md` |
| R7 stop/TERM/KILL | **完成（E1/E2）** | Fake 与真实 Claude/真实命令 wrapper覆盖 EOF app stop、TERM、KILL；重复 stop 幂等；真实测试 5/5 通过且无子进程残留 | E4 systemd/container cgroup、宿主强杀、真实磁盘/端口资源清理由后续验证 | `test_process_supervisor.py`; `test_claude_failure_isolation_real.py`; Turn 4 evidence |
| R8 Manager crash/reconcile | **完成（E1/E2）** | 文件持久 RuntimeRepository；sentinel 以同目录临时文件、flush/fsync、原子 replace 发布，reader 容忍短暂不完整内容并报告提前退出 stderr；独立 Manager OS 进程在 STARTING、READY、RUNNING、DRAINING 分别被 SIGKILL，第二 Manager仅凭 record + owner identity/PID 收敛到 STOPPED；真实 Claude READY/RUNNING同机制通过且从不伪 ADOPT | E4 的 systemd/container/宿主重启仍属生产控制面验证，不在本 Spike 非生产边界 | `repository.py`; `manager_crash_worker.py`; `test_manager_process_crash.py`; `test_manager_process_crash_real.py`; `evidence/r8-manager-process-crash-reconcile.md` |
| R9 双 Runtime 隔离 | **完成（E1/E2/E3）** | A/B PID、Claude session、WorkDir、settings、credential fingerprint、audit、marker均不同；A runtime id 调 B context 被拒；停止 A 后 B继续 | 使用共享宿主认证入口，因此不是 OS/容器级安全隔离；恶意代码读取同用户其他目录的防护属于 E4 sandbox 设计 | `test_isolation.py`; `test_claude_failure_isolation_real.py::test_dual_runtime_isolation_distinct_credentials_and_cross_mcp_rejection`; Turn 4 evidence |
| R10 provider-neutral Contract | **完成（Fake/Claude E1/E2）** | 同一参数化核心 Contract 对 Fake 与真实 Claude 验证完整生命周期；Protocol-only trap 证明 Harness 只调用八个公共方法，Harness 不再依赖 Fake 私有 `mcp_call`，成功外部活动经 provider-neutral `report_effective_activity` 上报；公共 DTO 无 provider 字段泄漏 | 旧 Python Agent Adapter 尚未实现，明确列为迁移非完成项；不影响本 Spike 以 Fake+Claude 冻结 provider-neutral contract | `test_core_contract_suite.py`; `harness.py`; `contracts.py`; `evidence/r1-r10-r12-verification.md` |
| R11 stdout/stderr、退出码、有效活动 | **完成 E1/E2 核心行为** | 独立 drain stdout/stderr；bad JSON计 protocol error；退出码和 escalation记录；日志不刷新 TTL；真实 Claude result/失败/exit可观测 | 没有生产日志管线、长期背压/轮转和跨节点 trace E4；证据主要为 pytest断言与摘要，不是每次运行持久 raw log bundle | `process_supervisor.py`; `transport.py`; `test_process_supervisor.py`; Turn 1/4 evidence |
| R12 WorkDir 可重建与清理 | **完成（E1/E2）** | stop 清理 credential、MCP config/revoke marker、scratch 与 Persona 快照，保留 owner/log evidence；重复 stop 幂等；同 RuntimeSpec 可重建；PersonaArtifact revision/hash 以 0444 快照物化且不改写源 | 生产 retention 周期、磁盘配额与日志归档仍属 E4 运维策略 | `workspace.py`; Fake/Claude `stop`; `test_workspace_lifecycle.py`; 真实双 Runtime cleanup 断言 |

## 3. 已冻结的 provider-neutral 结论

公共契约保持：

```text
prepare(RuntimeSpec) -> PreparedRuntime
start(PreparedRuntime) -> RuntimeHandle
wait_ready(RuntimeHandle, deadline) -> ReadyResult
submit_input(RuntimeHandle, RuntimeInput) -> TurnResult
inspect(RuntimeHandle) -> ProviderStatus
checkpoint(RuntimeHandle, deadline) -> CheckpointResult?
stop(RuntimeHandle, reason, grace_period) -> StopResult
reconcile(RuntimeRecord) -> ReconcileDecision
```

冻结边界：

- 稳定进程身份是 `(runtime_id, generation)`，不是 PID、Claude session 或旧 `agent_instance_id`。
- Ready 必须由 provider 协议握手/受控 bootstrap 证明，PID 存活不够。
- provider session、CLI flags、stream-json schema、MCP config shape 只属于 Adapter 私有 metadata。
- `checkpoint()` 允许诚实返回 `None`；当前 Claude 使用 `--no-session-persistence`，不得虚构恢复能力。
- Claude stdio Runtime 在 Manager 丢失 handle 后不可 ADOPT；安全裁决是终止 orphan 并启动新 generation。
- 600 秒 idle TTL 属 Manager/Harness，不属于 provider Adapter；纯日志和失败循环不能续期。
- MCP 工具错误与 Agent turn成功是不同维度，必须保留 `isError`，不可把“Agent正确处理错误”写成工具成功。

这些边界已经由 Fake 与真实 Claude 运行同一参数化核心 Contract Suite 证明。本次限定范围的 Spike contract 正式冻结并裁决为 **Go**；这不等于生产兼容性完成，legacy Adapter、生产 Gateway/Mod/MCP 和 E4 运维环境仍需后续迭代验证。

## 4. 非阻断迁移工作

旧 Python Agent 若未来作为正式 Adapter 保留，必须实现并跑同一核心 Contract Suite；它不是本次 Fake + Claude Spike冻结契约的完成条件。

## 5. 后续真实世界风险（E4）

- Gateway/Mod断线、resume与 RuntimeBinding状态没有实测。
- 生产多租户 MCP签名 claims、撤销传播、审计、限流和 Gateway Router没有实测。
- systemd/container/宿主重启、网络分区、进程资源预算和磁盘压力没有实测。
- 两个 Runtime共享同一 OS 用户与宿主认证入口，不构成恶意租户隔离。
- 真实 10 分钟 wall-clock与长 turn/cancel竞态、生产日志背压和 trace串联没有实测。

因此本审计裁决为：**本次限定范围的 Agent Runtime Adapter Spike 完成并 Go；生产集成与 E4 风险进入后续迭代。**
