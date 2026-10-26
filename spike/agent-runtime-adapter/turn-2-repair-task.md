# Turn 2：修复 Ready 帧队列污染

继续上一轮。Codex 已独立运行：

```text
python3 -m pytest tests/runtime_manager -q
2 failed, 10 passed
```

两个失败具有同一根因：`ProcessSupervisor._pump_stdout()` 在识别并设置 ready 后，仍把 ready 帧放入通用 `_stdout_queue`，导致下一次 `submit_turn()` 或 `recv_frame()` 消费到旧 ready 帧。

失败用例：

- `test_three_turns_same_pid_runtime_generation_with_memory_marker`
- `test_stdout_stderr_drain_converges_and_bad_json_is_protocol_error`

请做一次最小修复：ready 控制帧由 ready 通道消费，不得污染业务响应队列。检查是否存在类似控制帧/响应关联问题，但不要扩大到 TTL/reconcile 或重构公共契约。修复后运行：

```bash
cd platform/backend
python3 -m pytest tests/runtime_manager -q
python3 -m pytest tests/test_agent_subprocess_handle.py tests/test_agent_worker.py -q
```

严格禁止修改 legacy、DRIVER 文件、`.driver/`、规格，不 commit/push/deploy。报告修改和测试结果后停止。
