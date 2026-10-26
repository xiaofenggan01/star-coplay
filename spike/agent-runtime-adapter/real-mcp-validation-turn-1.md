# 真实 MCP 验收：Turn 1（最多两轮）

仅验收和最小修复当前实现。阅读：

- `platform/backend/tests/runtime_manager/test_claude_adapter_real.py`
- `platform/backend/tests/runtime_manager/test_controlled_mcp_wire.py`
- `platform/backend/tests/runtime_manager/controlled_mcp_server.py`
- Claude adapter package

允许修改以上文件及 `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-2-real-mcp.md`。禁止公共 DTO、生产/Gateway/Mod、真实平台凭据、commit/push/deploy、Driver 文件。

先运行：

```bash
cd platform/backend
python3 -m pytest tests/runtime_manager/test_controlled_mcp_wire.py -q
RUN_REAL_CLAUDE_TESTS=1 python3 -m pytest tests/runtime_manager/test_claude_adapter_real.py -q -s
```

真实 MCP 测试必须直接证明（若缺则最小补齐后重跑）：

1. 同一真实 Claude PID 完成 ready、echo success、controlled_fail、revoke 后 echo拒绝；每阶段 assert PID不变。
2. Server audit按顺序有 `ok`、`deliberate-failure`、`revoked`，且无秘密全文。
3. `controlled_fail` 与 revoke 的 MCP tool result 是 `isError:true`。若 CLI最终 result仍success，只表示 Agent成功处理错误，测试不得误报工具成功。
4. initialize/tools/list 有真实证据：优先从 server audit 增加不含秘密的 protocol events，测试 assert initialize和tools/list确实发生；不能仅用模型自然语言。
5. finally无Claude/MCP子进程残留。

不要 xfail额度/认证错误当通过；失败就保留精确脱敏错误。通过后更新正式 evidence，列出命令、结果、PID、audit事件、fingerprint、停止结果。另复跑基础真实 Adapter三项确认无回归。总等待有界，完成后停止。
