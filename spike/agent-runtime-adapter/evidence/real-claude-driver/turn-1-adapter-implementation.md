# 真实 Claude Adapter — Turn 1 验收

日期：2026-07-11
Driver session：`668e8e7e-91e3-482d-b831-caafdf516dc9`
执行模型：`claude-sonnet-5`

## 结果

Claude执行者通过 Driver wrapper 新增 provider-private Adapter：

- `platform/backend/modules/runtime_manager/adapters/claude/adapter.py`
- `platform/backend/modules/runtime_manager/adapters/claude/transport.py`
- `platform/backend/modules/runtime_manager/adapters/claude/protocol.py`
- `platform/backend/modules/runtime_manager/adapters/claude/errors.py`
- `platform/backend/modules/runtime_manager/adapters/claude/__init__.py`
- `platform/backend/tests/runtime_manager/test_claude_adapter_real.py`

没有修改公共 `contracts.py` DTO/Protocol。Claude session id、CLI flags、model 和 stream-json schema 都留在 provider-private package。

## 独立验证

Codex/Subagent从 `platform/backend` 独立运行：

```text
python3 -m pytest tests/runtime_manager -q
25 passed, 3 skipped in 4.16s

RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest \
  tests/runtime_manager/test_claude_adapter_real.py -q -s
3 passed in 19.20s

python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
6 passed in 0.07s

python3 -m compileall -q \
  modules/runtime_manager/adapters/claude \
  tests/runtime_manager/test_claude_adapter_real.py
exit 0

git diff --check
exit 0
```

真实 opt-in 测试覆盖：

- spawn 后 PID 存活但 `ready=False`；bootstrap protocol turn 收敛后才 ready。
- 同一 PID 连续完成三轮业务输入。
- 第 1 轮随机 marker 不写文件，第 3 轮 prompt 不重复 marker并成功召回。
- stop 通过 EOF/TERM/KILL 梯级有界退出，重复 stop 幂等。
- 测试 finally 清理；验收后未发现 `claude ... stream-json` 残留进程。

公共源码泄漏扫描只命中 `contracts.py` 的负面边界 docstring：

```text
contracts.py:4 (claude_session_id) / (agent_instance_id) forbidden-boundary explanation
```

没有公共字段或可执行 import 泄漏 Claude 专有结构。

## 实现裁决

- Ready：受控 bootstrap user turn，要求真实 result success；不使用 PID。
- Turn：单锁串行，直到 result 边界；assistant 文本累计；api_retry 不算成功。
- Checkpoint：诚实返回 `None`，因为 `--no-session-persistence`。
- Stop：stdin EOF → TERM → KILL；幂等。
- Reconcile：使用 owner identity + PID，但当前 ADOPT 只表达决策，未实现跨 Manager 重新附着 stdio。

## 风险与下一 Gate

- Adapter 目前通过 `--tools ""` 禁用工具，尚不能声明 MCP 可用。
- `--strict-mcp-config` 已带上，但没有 `--mcp-config` 路径，也未验证 initialize/list/call。
- 真实 marker 测试的源码/evidence root 计算存在注释与路径偏差，但 WorkDir 扫描有效；Turn 3 若需要可补直接路径验证。
- Claude executor 在产物和真实测试落盘后再次长期无最终 Driver 输出，Subagent于约 7 分钟终止外层 executor；以上结果全部由 Subagent独立运行命令确认。

结论：**真实 Claude Adapter 基础链路通过；进入受控本地真实 MCP Gate。**
