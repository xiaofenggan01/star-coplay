# Turn 1：实现 provider-private 真实 Claude Adapter 与 opt-in contract tests

你是执行者。本轮在现有 provider-neutral Harness 上实现真实 Claude Code Adapter。先完整阅读：

- `spike/agent-runtime-adapter/implementation-spec.md`
- `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-1-cli-protocol.md`
- `platform/backend/modules/runtime_manager/contracts.py`
- `platform/backend/modules/runtime_manager/process_supervisor.py`
- `platform/backend/modules/runtime_manager/fake/adapter.py`
- `platform/backend/tests/runtime_manager/`

允许修改：

- `platform/backend/modules/runtime_manager/adapters/claude/**`
- `platform/backend/modules/runtime_manager/adapters/__init__.py`
- `platform/backend/tests/runtime_manager/test_claude_adapter_real.py`
- `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-1-adapter-implementation.md`

如 `adapters/` 尚不存在可创建。禁止修改公共 DTO/Protocol 来迎合 Claude；若现有 contract 确实无法实现，停止并报告，不得泄漏 `claude_session_id`、Claude CLI flags 或 MCP 私有字段到 `contracts.py`。禁止修改 `DRIVER.md`、`DRIVER_STATE.json`、`.driver/`；禁止 commit/push/deploy；不接生产、Gateway、Mod，不读取/打印凭据。

## 已验证协议

真实 CLI 已证明下列命令形态的单一 PID 可以从持续 stdin 承接 3 条 user frame，每条产生独立 `result/success`，EOF 后 rc=0：

```text
claude -p --model claude-sonnet-5 \
  --input-format stream-json --output-format stream-json --verbose \
  --no-session-persistence --strict-mcp-config --tools "" \
  --permission-mode bypassPermissions
```

输入 frame：`{"type":"user","message":{"role":"user","content":"..."}}`。每个 turn 都出现 `system/init`，session id 稳定；PID 存活不能作为 ready；`result` 是 turn 完成边界；`system/api_retry` 不能算成功或活动。

## 实现要求

1. provider-private 目录中实现公共 `AgentRuntimeAdapter` 所需方法，复用/组合现有 `ProcessSupervisor` 的进程、stdio、TERM/KILL 思想；不要复制一套公共状态机。
2. `prepare` 创建隔离 WorkDir 与 owner identity；Claude 私有运行配置只能在 prepared/handle 私有 metadata 或 adapter 私有类型中。
3. 启动命令使用显式可配置 binary/model，默认模型 `claude-sonnet-5`；使用 stream-json stdin/stdout。环境必须继承当前代理以便本机验证，但不能记录环境秘密。
4. ready 必须来自真实协议握手/受控 bootstrap turn，不得以 PID 存活代替。明确处理每-turn `system/init`，避免污染业务 response queue。
5. `submit_input` 串行，收集当前 turn 的 assistant 文本直到匹配 `result`，超时/`is_error`/进程退出必须结构化失败，不得伪造成功。
6. `checkpoint` 可明确返回 unsupported/None，但不能伪造持久化。
7. stop 幂等并有界：优先关闭 stdin/应用级退出，必要时 TERM/KILL；不得留下子进程。
8. 公共源码不得出现 Claude 专有字段；Claude 专有 session id 仅在 adapter 私有类型/日志中。

## opt-in 真实测试

新增默认 skip 的真实测试，仅当 `RUN_REAL_CLAUDE_TESTS=1` 时运行；模型仍显式 `claude-sonnet-5`。至少证明：

- wait_ready 不是 PID 检查；
- 同一 PID 连续 3 个 `submit_input`；
- 第一轮要求记住随机 marker 且禁止落盘，第三轮不在 prompt 重复 marker并召回；
- 在 WorkDir/源码/测试/协议证据中扫描不到 marker；
- stop/EOF 后进程退出；重复 stop 幂等；
- 测试 finally 始终清理进程。

本轮不实现 MCP server/config/call；MCP 属于下一轮。先运行所有 Fake runtime tests，若环境允许再用 opt-in 开关运行真实测试。真实调用每轮 timeout <=60 秒，总体 <=180 秒。若 CLI/认证/网络失败，保留结构化证据并停止，不得 mock 成通过。

最终报告修改文件、接口映射、精确测试结果、PID/三轮/marker/stop 证据、仍未知项，然后停止。
