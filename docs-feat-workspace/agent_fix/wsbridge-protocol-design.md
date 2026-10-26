# WSBridge Protocol 2.4.5 改造设计

## 概述

将 Agent 侧 WSBridge 从裸 JSON 构造改为 protobuf struct 构造 + JSON 序列化，实现类型安全的协议通信。

## 一、握手状态机

### 状态定义

```
DISCONNECTED → HELLO_SENT → READY
                    ↓
                REJECTED (终态)
```

```python
class HandshakeState(Enum):
    DISCONNECTED = "disconnected"
    HELLO_SENT = "hello_sent"
    READY = "ready"
    REJECTED = "rejected"

@dataclass
class HandshakeResult:
    accepted: bool
    session_id: str | None = None
    protocol_version: str | None = None
    enabled_capabilities: frozenset[str] = frozenset()
    reject_reason: str | None = None
```

### 流程

1. `session_runner` 创建 Bridge 后调用 `bridge.initiate_handshake(agent_id=args.agent_instance_id)`
2. Bridge 构造 `Envelope { Hello { agent_id, required_protocol_version: "2.4.5" } }`
3. `MessageToDict()` → `_emit()` 发出
4. 状态变为 `HELLO_SENT`，阻塞等待 `_handshake_event`（threading.Event，超时 10s）
5. `handle_message` 收到 `hello_ack` 类型帧：
   - `accepted=true` → 存储 session_id + enabled_capabilities，状态 → `READY`，set event
   - `accepted=false` → 状态 → `REJECTED`，set event
6. 超时未收到 → raise `HandshakeTimeoutError`

### 约束

- `write_command` 在状态非 `READY` 时 raise `BridgeNotReadyError`
- 握手只执行一次，不支持重连重握手（进程级生命周期）

## 二、Envelope 包装

### 出向帧构造（Agent → Mod）

```python
def _wrap_envelope(self, body_field: str, body_msg) -> dict:
    """构造 Envelope 并序列化为 dict"""
    envelope = Envelope()
    envelope.protocol_version = "2.4.5" # 这里当然不能硬编码，示例
    envelope.message_id = self._next_message_id()
    envelope.session_id = self._session_id or ""
    envelope.timestamp_ms = int(time.time() * 1000)
    # 设置 body oneof
    getattr(envelope, body_field).CopyFrom(body_msg)
    return MessageToDict(envelope, preserving_proto_field_name=True)
```

### message_id 生成

```python
def _next_message_id(self) -> str:
    self._msg_seq += 1
    return f"msg_{self._msg_seq:06d}"
```

握手成功后带上 session_id 前缀：`msg_{session_id}_{seq:06d}`

### command_id 策略

复用现有 `action_id` 生成逻辑（`act_{seq:04d}_{uuid4[:4]}`），作为 `CommandRequest.command_id` 和 pending/result 关联键。

## 三、write_command 改造

### Before（当前）

```python
def write_command(self, action: str, params: dict, ...) -> str:
    frame = {
        "type": "command",
        "seq": self._cmd_seq,
        "action_id": action_id,
        "capability": action,
        "params": params,
    }
    self._sender(frame)
```

### After（新版）

```python
def write_command(self, capability: str, params: dict, timeout_ms: int = 30000, ...) -> str:
    if self._handshake_state != HandshakeState.READY:
        raise BridgeNotReadyError("Handshake not completed")

    # 构造 CommandRequest protobuf
    cmd_req = CommandRequest()
    cmd_req.command_id = action_id
    cmd_req.timeout_ms = timeout_ms
    cmd_req.source = CommandSource.COMMAND_SOURCE_LLM
    cmd_req.capability = capability
    # 设置 params oneof（通过 capability name 映射）
    self._set_capability_params(cmd_req, capability, params)

    # 包装 Envelope 并发出
    frame = self._wrap_envelope("command_request", cmd_req)
    self._sender(frame)
    return action_id
```

### _set_capability_params 映射

```python
CAPABILITY_PARAMS_MAP = {
    "say": ("say", SayParams),
    "emote": ("emote", EmoteParams),
    "face": ("face", FaceParams),
    "move_to": ("move_to", MoveToParams),
    "go_to": ("go_to", GoToParams),
    "go_to_bed": ("go_to_bed", GoToBedParams),
    "interact": ("interact", InteractParams),
    "use_tool": ("use_tool", UseToolParams),
    "equip": ("equip", EquipParams),
    "open_menu": ("open_menu", OpenMenuParams),
    "query_menu": ("query_menu", QueryMenuParams),
    "menu_click": ("menu_click", MenuClickParams),
    "menu_close": ("menu_close", MenuCloseParams),
    "query_inventory": ("query_inventory", QueryInventoryParams),
    "query_location": ("query_location", QueryLocationParams),
    "query_map_exits": ("query_map_exits", QueryMapExitsParams),
    # 这些 Query 函数后面还有可能出现修改，暂时先这么列着。
}

def _set_capability_params(self, cmd_req, capability: str, params: dict):
    field_name, ParamsClass = CAPABILITY_PARAMS_MAP[capability]
    params_msg = ParseDict(params, ParamsClass())
    getattr(cmd_req, field_name).CopyFrom(params_msg)
```

## 四、handle_message 改造

### 新帧路由

```python
def handle_message(self, msg: dict) -> None:
    # 新协议帧：顶层有 body 相关字段（由 MessageToDict 展开 oneof）
    if "command_result" in msg or "hello_ack" in msg or "error_response" in msg:
        self._handle_protocol_frame(msg)
        return

    # 旧协议帧：按 type 字段分发（state_snapshot, game_event 等）
    msg_type = msg.get("type", "")
    # ... 现有逻辑不变
```

### hello_ack 处理

```python
def _handle_hello_ack(self, ack_data: dict):
    if ack_data.get("accepted", False):
        self._session_id = ack_data["session_id"]
        self._enabled_capabilities = frozenset(
            ack_data.get("enabled_capabilities", [])
        )
        self._handshake_state = HandshakeState.READY
    else:
        self._reject_reason = ack_data.get("reject_reason", "unknown")
        self._handshake_state = HandshakeState.REJECTED
    self._handshake_event.set()
```

### command_result 处理

```python
def _handle_command_result(self, result_data: dict):
    # ParseDict 反序列化为 protobuf struct（用于类型校验和结构化访问）
    proto_result = ParseDict(result_data, CommandResult())
    # 转为 BridgeResult（保持下游接口不变）
    bridge_result = BridgeResult.from_proto(proto_result)
    action_id = proto_result.command_id
    self._results_cache[action_id] = bridge_result
    self._pending_commands.pop(action_id, None)
```

## 五、BridgeResult 扩展

新增 `from_proto` 类方法，从 protobuf `CommandResult` 构造：

```python
@classmethod
def from_proto(cls, proto: CommandResult) -> "BridgeResult":
    return cls(
        action_id=proto.command_id,
        success=proto.success,
        status=CommandStatus.Name(proto.status).lower(),  # COMPLETED → "completed"
        data=MessageToDict(proto, preserving_proto_field_name=True).get("data", {}),
        observation=proto.observation.text if proto.HasField("observation") else "",
        error_code=ErrorCode.Name(proto.error.code) if proto.HasField("error") else None,
        error_message=proto.error.message if proto.HasField("error") else None,
    )
```

## 六、effective_capabilities 计算

```python
@property
def effective_capabilities(self) -> frozenset[str]:
    """proto 编译能力 ∩ HelloAck 宣告能力"""
    compiled = frozenset(CAPABILITY_PARAMS_MAP.keys())
    return compiled & self._enabled_capabilities
```

上层（DeliberativeLoop）用这个集合决定 LLM 可见的工具面。

## 七、cancel_command 改造

```python
def cancel_command(self, action_id: str) -> None:
    cancel = CommandCancel()
    cancel.command_id = action_id
    cancel.reason = "agent_timeout"
    frame = self._wrap_envelope("command_cancel", cancel)
    self._sender(frame)
    self._pending_commands.pop(action_id, None)
```

## 八、session_runner 集成

```python
def main() -> int:
    args = _parse_args()
    bridge = WSBridge(sender=_emit, target_player_id=tpid)

    # 新增：握手
    try:
        handshake = bridge.initiate_handshake(agent_id=args.agent_instance_id)
    except HandshakeTimeoutError:
        log.error("Handshake timeout, entering chat-only mode")
        # TODO: chat-only fallback
        return 1

    if not handshake.accepted:
        log.error(f"Handshake rejected: {handshake.reject_reason}")
        return 1

    log.info(f"Handshake OK, session={handshake.session_id}, "
             f"capabilities={handshake.enabled_capabilities}")

    # 后续正常流程...
```

## 九、新旧帧判断规则

| 条件 | 判定 | 处理路径 |
|------|------|---------|
| 顶层含 `command_result` / `hello_ack` / `error_response` / `command_request` 字段 | 新协议（Envelope oneof 展开） | `_handle_protocol_frame` |
| 顶层含 `type` 字段 | 旧协议 | 现有 `handle_message` 逻辑 |

注：`MessageToDict` 对 oneof 字段会展开为顶层 key，所以 Envelope 的 `body` oneof 会变成 `"command_result": {...}` 这样的顶层字段。

## 十、文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `agent/bridge/ws_bridge.py` | 重构 | 握手 + Envelope + protobuf 收发 |
| `agent/bridge/bridge_result.py` | 扩展 | 新增 `from_proto` |
| `agent/session_runner.py` | 小改 | 握手调用 |
| `agent/slow_loop/core_tools.py` | 重写 | 参数对齐 proto（后续步骤） |
| `agent/slow_loop/deliberative_loop.py` | 小改 | 用 effective_capabilities 过滤工具面（后续步骤） |
| `agent/tests/test_wsbridge_protocol.py` | 新增 | 握手 + 命令收发测试 |
