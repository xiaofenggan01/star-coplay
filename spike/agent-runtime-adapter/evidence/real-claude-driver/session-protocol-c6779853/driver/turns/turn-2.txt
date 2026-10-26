# Turn 1：只验证真实 Claude CLI 的单进程 stream-json 协议

本轮只调查 CLI 协议，不写 Adapter，不改产品源码或测试。唯一允许新增的持久文件是：

- `spike/agent-runtime-adapter/evidence/real-claude-driver/turn-1-cli-protocol.md`

禁止修改 `DRIVER.md`、`DRIVER_STATE.json`、`.driver/`；禁止 commit/push/deploy；禁止读取项目/平台凭据；禁止连接 Gateway、Mod、生产 MCP。受控 Claude CLI 实验可使用当前用户已经存在的 Claude Code 登录态，但不得读取、打印或复制 token/config 内容。

## 已知目标

我们需要判断 Runtime Manager 能否托管一个**单一 OS 进程**：

```text
claude -p --model claude-sonnet-5 \
  --input-format stream-json --output-format stream-json ...
```

该进程 stdin 保持开放，连续接收 3 条 user 消息；每条产生可识别的结果；整个过程 PID 不变。禁止用三次 `claude -p` 或多次 `--resume` 冒充持续进程。

## 必做调查与实验

1. 运行 `claude --version` 与 `claude --help`，只在报告中摘录和以下选项直接相关的片段：`-p/--print`、`--input-format`、`--output-format`、`--include-partial-messages`、`--verbose`、`--mcp-config`、`--strict-mcp-config`、`--model`、`--tools`、`--permission-mode`。
2. 使用 Python `subprocess.Popen` 或等价的有界 harness 启动一个真实 Claude 子进程，记录 PID；stdin/out/err 都用 pipe。所有等待必须有明确 timeout（单次不超过 60 秒，总实验不超过 180 秒），finally 中 TERM，必要时 KILL，不能留下子进程。
3. 从帮助文本/错误反馈确定合法 stream-json user 输入 schema，然后向**同一 stdin**逐条写入三条消息并 flush。消息内容要求极短，禁止工具调用，要求分别只返回 `ONE`、`TWO`、`THREE`。
4. 对每条输入记录：输入时间、观察到的 stdout 事件 type/subtype/session_id（不要记录敏感内容）、是否出现独立 turn result、进程 PID/存活状态。证明或否定“每条均有结果”。
5. 记录进程何时退出：每条 result 后是否仍存活；关闭 stdin/EOF 后是否退出；SIGTERM 是否在有界时间退出。若协议实际要求一次性 EOF 才处理全部输入，也必须如实说明。
6. 形成 ready 判定建议，但严格区分：进程已 spawn、CLI system/init 事件、MCP initialize 成功、首个 turn 可提交，这些不是同一含义。不得用 PID 存活代替 ready。
7. 调查 `--mcp-config` 如何加载本地配置、`--strict-mcp-config` 的语义，以及 output stream 中是否有可用于确认 MCP 初始化成功/失败的事件。本轮不需要启动真实 MCP server，也不得声称 MCP call 已验证。

## 报告格式

- 环境与精确命令（脱敏）
- 帮助文本明确声明
- 受控实验方法
- 实际观察（PID、三轮、事件、EOF、TERM）
- MCP config 加载契约（仅 CLI 发现）
- 已证明 / 已否定 / 仍未知
- 对下一轮 Adapter 的 Go / No-Go

任何认证、网络、模型名、费用、schema 或 timeout 阻断都必须记录精确退出码和脱敏 stderr，然后给 No-Go/Blocked；不要以 mock、文档推断或 resume 多进程替代真实结果。完成后停止。
