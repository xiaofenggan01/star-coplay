# 真实 Claude CLI 单进程 stream-json 协议验证

日期：2026-07-11
执行会话：`c6779853-6856-4447-b6c9-72c919f14f4e`
结论：**单一 Claude CLI OS 进程可通过持续开放的 stdin 接收至少三条 user 消息，并为每条消息产生独立 result；关闭 stdin 后正常退出。CLI 协议进入 Adapter 实现为 Go，但 MCP 尚未验证。**

## 1. 边界

- 模型显式指定为 `claude-sonnet-5`。
- 未连接 Gateway、Mod、生产 MCP、数据库或平台凭据。
- 未 commit、push 或 deploy。
- 外层实现/调查任务通过 `codex-claude-driver` 代理包装器委派。
- 本实验使用当前用户既有 Claude Code 登录态，但没有读取、打印或复制认证材料。

## 2. 精确实验命令

Claude 执行者生成了有界 Python `subprocess.Popen` harness，核心子进程命令为：

```text
/Users/river/.local/bin/claude -p \
  --model claude-sonnet-5 \
  --input-format stream-json \
  --output-format stream-json \
  --verbose \
  --no-session-persistence \
  --strict-mcp-config \
  --tools "" \
  --permission-mode bypassPermissions
```

合法输入帧的实测 schema：

```json
{"type":"user","message":{"role":"user","content":"ONE"}}
```

每条消息写入同一个 `stdin` 后立即 `flush()`；stdout/stderr 由独立 reader 线程持续 drain。第一轮错误地移除代理环境，真实观察为连续 `system/api_retry`，因此未将其视为协议成功。第二轮保留代理环境后成功，以下结论只使用第二轮证据。

## 3. 实际观察

真实子进程 PID：`52416`，三轮全过程不变。Claude session id：`f1b273f4-3f87-42d4-8f82-6966373c27ed`，三轮不变。

| 输入 | 观察事件 | result | result 后进程 |
|---|---|---|---|
| ONE | `system/init` → `assistant` → `result/success` | 有，`num_turns=1` | 存活 |
| TWO | `system/init` → `assistant`（2 帧）→ `result/success` | 有，`num_turns=1` | 存活 |
| THREE | `system/init` → `assistant` → `result/success` | 有，`num_turns=1` | 存活 |

时间证据：

- spawn：`1783766992.0281181`
- 第一轮 result：`1783766995.534318`
- 第二轮 result：`1783766998.6513221`
- 第三轮 result：`1783767001.295995`
- 三轮后、EOF 前：进程仍存活，`returncode=null`
- stdin close：`1783767001.29642`
- stdout/stderr close：约 `1783767001.8315`
- 最终退出：`returncode=0`

因此实测否定“stream-json 必须等 EOF 后才批量处理”的假设。每条 user frame 均在 stdin 保持开放时独立完成。

## 4. Ready 与 turn 边界

当前可证明的事件语义：

- `spawn` / PID 存活：只证明进程已创建，不能作为 ready。
- `system/init`：对每个输入 turn 都出现，且携带稳定 session id；它不是整个进程仅一次的全局 ready。
- `result` 且 `subtype=success`：可作为当前 turn 完成边界；三轮均独立出现。
- stdin EOF：触发进程正常退出，不适合作为每轮边界。

首版 Adapter 的 ready 建议：进程启动后先提交一个受控 bootstrap user frame，只有观察到匹配 session 的 `system/init` 且该 bootstrap turn 收敛到 `result`，才声明可接受业务输入。若未来真实 MCP 验证能提供明确的 MCP 初始化状态，应把“process ready”和“MCP ready”拆开，不能仅凭 `system/init` 推断 MCP 可用。

## 5. EOF、TERM 与失败诚实传播

- 成功实验在三轮结束后关闭 stdin，进程正常 `returncode=0`。
- 失败实验（代理被移除）持续输出 `system/api_retry`，没有任何 turn result；关闭 stdin 后仍存活，随后 SIGTERM，约 0.61 秒退出，`returncode=143`。
- 因此 `system/api_retry` 不能刷新有效活动或伪装为 turn 成功；Adapter 必须对 result 设 deadline，超时后进入有界停止路径。

## 6. MCP config 加载契约

本轮命令确认 CLI 接受 `--strict-mcp-config`，但没有启动真实 MCP server，也没有取得足够证据证明：

- `--mcp-config` 的精确 JSON schema；
- MCP initialize 成功/失败在 stream-json 中的明确事件；
- tools/list/call/result；
- credential revoke 对存活 Claude 进程的影响。

这些均保持 **未验证**，不得从本轮单进程成功外推。

## 7. 已证明 / 已否定 / 未知

已证明：

- 单一 OS 进程连续承接三轮输入。
- 合法 user frame schema 可被 CLI 接受。
- 每轮有独立 `result/success`，三轮 session id 稳定。
- result 后进程保持存活。
- EOF 后正常退出；网络失败路径 SIGTERM 可有界退出。

已否定：

- 多轮必须使用多次 `-p --resume`。
- stream-json 必须等 EOF 才处理输入。
- PID 存活或 `api_retry` 可以代表 ready/成功。

仍未知：

- 帮助文本所承诺的完整 option 语义（Claude 执行者未能在外层 Driver 卡住前落盘摘录）。
- 真实 MCP initialize/list/call、失败传播与凭据撤销。
- Claude 进程忽略 SIGTERM 时的 SIGKILL 路径。
- 双实例 WorkDir/MCP/session 隔离。

## 8. Go / No-Go

**Go：** 可以进入 provider-private Claude Adapter 的最小实现，公共 contract 不需要改成 Claude 专有协议。Adapter 应把 stream-json 解析、session id、CLI flags 放在 `adapters/claude/` 私有层。

**Gate：** 进入“真实 MCP 已验证”之前，必须补齐受控本地 MCP server、MCP 初始化状态、tools/list/call、失败诚实传播和 credential revoke。当前证据不能宣称 MCP 能力已完成。

## 9. 证据完整性说明

原始成功日志由 Claude 执行者写在 `/tmp/turn1_spike/run_stdout2.txt`；外层 Driver 在实验完成后无子进程但长时间无最终输出，Subagent 在约 7 分钟时有界终止外层进程。以上表格和时间点由 Subagent独立读取原始 NDJSON 后整理。原始 `/tmp` 不是长期证据源，因此本文件保留关键脱敏事件；Driver 的零字节 turn-2 log 同时证明执行者未正常交付报告。
