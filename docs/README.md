# Docs Index

当前 `docs/` 只保留与当前开发分支直接相关的方案文档和模板。

## 当前方案

- `protocol-agent-mod-capability-phase1-design.md`
  - Agent-Mod Protocol 2.4.5 在 Mod/执行链侧的阶段一落地方案。
  - 包含 protocol adapter、replay、Mod 接收边界校验和阶段一 DoD。

- `agent-protocol-2.4.5-integration-design.md`
  - Agent 侧 Protocol 2.4.5 对接方案。
  - 只包含 Agent schema、mapper、command builder、envelope builder 和 Agent 单测边界。

## 模板

- `模版.md`
  - 技术方案模板。

- `pr模版.md`
  - Merge / PR 描述模板。

## 清理规则

- 已落地的迭代计划、旧路径文档、路演/platform 专项方案不放在本目录。
- Agent 侧方案和 Mod 侧方案分开维护，不在单个文档里混写模块职责。
