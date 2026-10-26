# AgentRuntimeAdapter Harness 实现规格

> 本文件是本次 Spike 的正式实现输入；执行与验收只引用此 worktree 内路径。

## 1. 目标与边界

在 `star-coplay-hosted-agent-runtime` 内新增一个**独立、仅用于 Spike 的 provider-neutral Harness**，用可执行测试证明：非交互启动、明确 ready、多轮输入、MCP 调用模拟、idle TTL、分级停止、Manager 重启后的 reconcile、双实例隔离。首版以 Fake Adapter/Fake Runtime 为基准，不接 Gateway、Mod、生产 MCP、数据库或真实凭据，也不修改旧 Python Agent 链路。

权威约束来自文档：

- `stardew-agent-hosted-agent-runtime/workspace/hosted-agent-runtime/02-target-architecture.md`
- `.../04-contracts-and-identities.md`
- `.../05-runtime-adapter-spike.md`

旧实现仅作经验输入：

- `platform/backend/modules/runtime/subprocess_handle.py`：可复用 stdio、send lock、STOP→TERM→KILL 思想，但其身份、命令、环境与 session runner 均 provider-specific。
- `platform/backend/modules/runtime/agent_worker.py`：明确属于 legacy；其 Runtime 与 WS session 同生共死的设计不得进入新 Harness。
- `platform/backend/modules/runtime/instance_registry.py`：属于 Gateway `AgentInstance`，不得扩展成 `RuntimeInstance`。

## 2. 建议目录

```text
platform/backend/modules/runtime_manager/
├── __init__.py
├── contracts.py          # 公共数据类型、Protocol/ABC、错误类型
├── clock.py              # Clock protocol + SystemClock
├── process_supervisor.py # provider-neutral 子进程监督
├── harness.py            # in-memory Runtime Manager/TTL/reconcile 编排
└── fake/
    ├── __init__.py
    ├── adapter.py        # FakeAgentRuntimeAdapter
    └── runtime_process.py# NDJSON 测试子进程

platform/backend/tests/runtime_manager/
├── test_adapter_contract.py
├── test_process_supervisor.py
├── test_idle_ttl.py
├── test_reconcile.py
└── test_isolation.py
```

不要放入现有 `modules/runtime/`，避免让 Spike 与 legacy Gateway runtime 形成隐式耦合。若团队坚持同目录，至少使用 `modules/runtime/adapter_harness/` 子包，禁止 import `AgentInstance`、`Session`、`AgentSessionWorker`。

## 3. 公共契约

`contracts.py` 只使用标准库。建议不可变 dataclass + `str Enum`：

```python
class RuntimeActualState(str, Enum):
    STOPPED = "stopped"
    PROVISIONING = "provisioning"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    IDLE = "idle"
    DRAINING = "draining"
    STOPPING = "stopping"
    FAILED = "failed"

@dataclass(frozen=True)
class RuntimeSpec:
    runtime_id: str
    generation: int
    user_id: str
    agentee_id: str
    provider: str
    workspace_root: Path
    idle_ttl_seconds: float = 600.0
    env: Mapping[str, str] = field(default_factory=dict)
    provider_private_metadata: Mapping[str, JSONValue] = field(default_factory=dict)

@dataclass(frozen=True)
class PreparedRuntime:
    spec: RuntimeSpec
    workdir: Path
    provider_private_metadata: Mapping[str, JSONValue]

@dataclass(frozen=True)
class RuntimeInput:
    request_id: str
    content: str

@dataclass(frozen=True)
class ReadyResult: ...
@dataclass(frozen=True)
class TurnResult: ...
@dataclass(frozen=True)
class ProviderStatus: ...
@dataclass(frozen=True)
class StopResult: ...
@dataclass(frozen=True)
class ReconcileDecision: ...
```

公共字段严禁出现 `claude_session_id`、Claude CLI 参数或 Gateway `agent_instance_id`。`runtime_id + generation` 是进程归属主键。

Adapter 使用异步 `Protocol`，Harness 依赖此接口而非具体实现：

```python
class AgentRuntimeAdapter(Protocol):
    async def prepare(self, spec: RuntimeSpec) -> PreparedRuntime: ...
    async def start(self, prepared: PreparedRuntime) -> RuntimeHandle: ...
    async def wait_ready(self, handle: RuntimeHandle, deadline: float) -> ReadyResult: ...
    async def submit_input(self, handle: RuntimeHandle, value: RuntimeInput) -> TurnResult: ...
    async def inspect(self, handle: RuntimeHandle) -> ProviderStatus: ...
    async def checkpoint(self, handle: RuntimeHandle, deadline: float) -> CheckpointResult | None: ...
    async def stop(self, handle: RuntimeHandle, reason: str, grace_period: float) -> StopResult: ...
    async def reconcile(self, record: RuntimeRecord) -> ReconcileDecision: ...
```

`RuntimeHandle` 可包含 PID/内部 transport，但其 provider 私有内容不能进入 `RuntimeRecord` 顶层。所有 start/stop API 必须幂等；旧 generation 的事件必须被 Harness 丢弃。

## 4. Fake Runtime 与 Adapter

`fake/runtime_process.py` 是真实独立 Python 子进程，以 stdin/stdout NDJSON 通信，支持测试指令：

- 启动后输出 `{"type":"ready","runtime_id":...,"generation":...}`；ready 可配置延迟或永不发送。
- `input`：递增 turn counter，回传 runtime/generation/turn/content，并保存**仅内存** marker，证明多轮不重启。
- `mcp_call`：读取当前 WorkDir 内 `mcp.json`，回传 credential fingerprint 与 tool/result；不得输出秘密全文。
- `checkpoint`：写到本实例 WorkDir 并返回路径。
- `stop`：正常退出。
- 故障开关：忽略 stop、忽略 TERM、立即 crash、响应卡住、重复错误输出。

`FakeAgentRuntimeAdapter.prepare()` 必须创建：

```text
<workspace_root>/<runtime_id>/generation-<N>/
├── runtime-owner.json  # runtime_id/generation/provider/随机 nonce，不含秘密
├── mcp.json            # 测试 credential；权限 0600
├── scratch/
└── logs/
```

创建必须拒绝路径穿越、拒绝复用属于其他 runtime/generation 的目录。双实例必须使用独立 cwd、环境、stdio 与凭据。

## 5. ProcessSupervisor

职责限定为进程与 stdio，不负责 TTL/Binding/Persona：

1. `asyncio.create_subprocess_exec(..., cwd=workdir, env=minimal_env, start_new_session=True)`。
2. 并发 drain stdout/stderr，结构化事件写入实例内日志；stdout 日志本身不刷新 idle。
3. ready 必须来自协议帧并匹配 runtime/generation，不能以 PID 存活替代。
4. 单 turn 串行：用 lock 禁止并发写 stdin；后续可扩展 multiplex，不在 Spike 实现。
5. stop 顺序：应用级 `stop` → 等 grace → `terminate()` → 等 grace → `kill()`；清理 pump tasks，并检查 process group/子进程无残留。
6. `inspect()` 报告 pid、returncode、ready、protocol health、last frame time；不得把 stdout 活跃等同业务活跃。
7. 进程身份文件的 nonce、PID、generation 供 reconcile 校验，单凭 PID 不可认领。

从旧 `AgentSubprocessHandle` 复制思想即可，禁止继承或将其改造成新接口，避免影响旧链路。

## 6. Clock 与 idle TTL

定义：

```python
class Clock(Protocol):
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...
```

生产 `SystemClock` 用 `time.monotonic/asyncio.sleep`。测试 `ManualClock` 放在测试 helper 中，`advance(seconds)` 唤醒到期 waiter。不得 monkeypatch `time.time()`，不得让单测真实等待 600 秒。

Harness 的活动字段至少拆为 `last_user_activity`、`last_effective_runtime_activity`、`turn_inflight`。只有以下事件刷新有效活动：接受用户输入、正常可见输出、成功 MCP call/result、正常 turn 完成。纯日志、heartbeat、重复错误与故障循环不刷新。

TTL 流程：

1. 默认 `idle_ttl_seconds=600`；测试用 10 秒注入。
2. 到期且无 turn：`IDLE → DRAINING`，拒绝新输入，checkpoint（有界），stop。
3. 到期但 turn 在途：先 `DRAINING`，等待有界完成或取消，再 stop，不直接 KILL。
4. 新输入与 cleanup 竞态：在进入不可逆 `STOPPING` 前，可用同一实例锁取消 drain；进入 `STOPPING` 后返回明确 `RuntimeStopping`，由上层重启新 generation。
5. cleanup/submit/start/stop 均在每 runtime lock 下修改状态。

## 7. Harness Registry 与 reconcile

Spike 可用 in-memory record + WorkDir owner 文件模拟持久状态，但接口要允许未来 repository 替换。`RuntimeRecord` 包含 desired/actual state、runtime/generation、workdir、process identity、last effective activity、exit reason；不含 Gateway Binding 实现。

`reconcile_all()` 扫描 registry 与受控 workspace root：

- record + 匹配 owner nonce + 活进程：inspect 后 adopt/continue。
- record 为 STOPPED 但进程仍活：stop/kill，结论 `TERMINATE_ORPHAN`。
- 目录存在但无可信 record/nonce：**不得认领**；若能证明由 Harness 创建则清理，否则报告 `QUARANTINE_UNKNOWN`，不得删除任意用户目录。
- record generation=N，但收到 N-1 exit：忽略旧事件。
- 活记录但进程消失：标记 FAILED/STOPPED（按 exit reason），清理短期 secret。

真实 Manager crash 的进程跨 Python 测试进程收养较复杂；Spike 至少用独立 supervisor/harness 实例重建、相同 repository/owner file 验证。另加一个 integration test 启动 Fake Runtime、丢弃第一个 Harness 对象、由第二个 Harness reconcile 后停止，证明不是仅 mock。

## 8. 测试矩阵

### Adapter contract (`test_adapter_contract.py`)

- prepare 创建隔离、0600 credential、owner identity。
- start + ready 明确握手；ready timeout 结构化失败并清理。
- 3 个 turn 同 PID/runtime/generation，内存 marker 可召回。
- MCP 模拟 initialize/list/call trace；撤销 credential 后后续 call 拒绝（Fake Adapter 可通过 `credential.revoked` 文件或 injected validator 模拟）。
- stop 重复调用返回同一收敛结果。

### Supervisor (`test_process_supervisor.py`)

- 正常 stop 不 TERM。
- 忽略 app stop 后 TERM。
- 忽略 TERM 后 KILL。
- stdout/stderr drain 不死锁，坏 JSON 变协议错误而非 ready。
- crash 返回 exit code，所有 pump task 收敛。

### TTL (`test_idle_ttl.py`)

- 默认 spec 明确为 600 秒。
- ManualClock 9.9 秒不回收、10 秒回收。
- 正常 input/output/MCP result 刷新；日志/重复 error 不刷新。
- 在途 turn 到期进入 DRAINING，不直接 kill。
- cleanup 与新输入竞态满足上述边界。

### Reconcile (`test_reconcile.py`)

- Manager 重建后发现并停止可信活进程。
- STOPPED record 的孤儿被终止。
- PID/owner nonce 不匹配时拒绝认领。
- 旧 generation exit 不改变新 generation 状态。
- crash 后最终无进程、凭据失效、状态收敛。

### Isolation (`test_isolation.py`)

- A/B 同时运行，cwd/env/mcp credential/log/checkpoint/marker 不串。
- 停止 A 不影响 B。
- A credential 不能在 B context 使用。
- 同 runtime 新 generation 不复用旧 credential/session。

## 9. 实现顺序（交给执行 Agent）

1. 先写 contracts + Fake Runtime + adapter contract tests；不要先写 Claude Adapter。
2. 写 ProcessSupervisor 并通过 stop escalation/ready tests。
3. 写 Clock/Harness/TTL 与竞态测试。
4. 写 owner identity/reconcile 与真实子进程 integration test。
5. 写双实例隔离测试。
6. 仅在公共测试全绿后，以同一 contract suite 接 Claude Adapter；Claude 专属测试单列，不能反向修改公共契约来迎合 CLI。

## 10. 验收命令

从代码 worktree 执行：

```bash
cd /Users/river/Dev/star/star-coplay-hosted-agent-runtime/platform/backend
python3 -m pytest tests/runtime_manager -q
python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
python3 -m ruff check modules/runtime_manager tests/runtime_manager
python3 -m mypy modules/runtime_manager
```

若本机未安装开发工具，先记录缺失，不擅自改变锁文件或全局环境；可以使用仓库已有 venv。验收还需：

```bash
rg -n "claude_session_id|agent_instance_id|modules.gateway|AgentSessionWorker|AgentInstance" \
  platform/backend/modules/runtime_manager platform/backend/tests/runtime_manager
```

预期公共实现无上述耦合（测试描述 legacy 边界的文字可例外）。运行测试后检查没有 Fake Runtime 残留：

```bash
ps -axo pid,ppid,command | rg 'runtime_process.py' || true
```

## 11. 完成证据

执行 Agent 必须交付：测试输出、修改文件列表、Fake Runtime 的精确启动协议、至少一次 TERM/KILL 路径日志、双实例 PID/WorkDir/credential fingerprint、reconcile 决策记录，以及公共源码中无 Claude/Gateway 身份泄漏的 `rg` 结果。不得以 mock 的 `process.alive=True` 替代真实子进程验证；不得 commit、push、deploy。
