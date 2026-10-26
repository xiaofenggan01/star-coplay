# Turn 4：补齐第二阶段直接证据并停止

Codex 已独立验证现有第二阶段：`24 passed`。但对照 Turn 3 明确验收项，当前测试仍缺三类**直接证据**。这是本 session 唯一一次修复，请做最小补齐，完成后停止。

## 必须补齐

1. **跨 Runtime credential 拒绝**：当前隔离测试只证明“撤销 A 不影响 B”，没有证明“A credential 不能用于 B context”。扩展 Fake MCP 测试协议或测试入口，使一次显式携带/模拟 A credential 调用 B 的请求返回结构化拒绝；不得把 credential 全文写进日志或响应。
2. **双实例隔离证据**：明确断言 A/B 的 cwd/env、credential fingerprint、日志路径/内容、checkpoint 与 marker 隔离；stop A 后 B 继续运行的现有断言保留。若当前 Fake Runtime 没有安全的 env/cwd/日志观测帧，可加入仅返回非敏感 sentinel/path/fingerprint 的测试帧，禁止泄漏环境全集或秘密。
3. **无效活动不刷新 TTL**：增加直接测试，证明至少一次重复 MCP error/credential revoked error 与纯 log 均不刷新 `last_effective_runtime_activity`，10 秒边界仍回收。不能仅靠方法注释证明。

同时修正任何由这些测试暴露的最小缺陷。不要扩大到真实 Gateway/Mod/MCP、Claude Adapter 或生产 repository。

## 约束

- 只改 `platform/backend/modules/runtime_manager/`、`platform/backend/tests/runtime_manager/`、必要的 `spike/agent-runtime-adapter/evidence/`。
- 不改 legacy、Driver 状态、`.driver/`、正式规格或任务文件。
- 不 commit/push/deploy，不读取真实凭据。
- 运行 `python3 -m pytest tests/runtime_manager -q`；如被权限拒绝则报告，由 Codex验收。
- 最终报告修改、测试、剩余风险后停止。
