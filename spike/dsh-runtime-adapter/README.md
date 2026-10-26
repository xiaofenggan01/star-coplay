# DSH as hosted runtime — MCP adaptation spike

目标：让 DeepSeek Harness（DSH）作为 StarCoPlay 的 hosted agent runtime。
用户只做两件事：装 Mod、登录；不安装、不配置 DSH。

## 已验证的链路

本 spike 在本机验证了以下链路（未使用真实游戏 / 未消耗真实模型额度）：

```text
DSH (headless profile)
  -> @deepseek-ai/dsh-mcp-client (stdio)
  -> python -m modules.mcp.transports.stdio_proxy
  -> WebSocket / hosted MCP
```

用一个 fake hosted MCP server 记录到的帧序列：

```text
connected
initialize
notifications/initialized
tools/list
```

这说明：**StarCoPlay 现有的 hosted MCP + stdio proxy 已经能被 DSH 的 MCP client 直接消费**，不需要改 MCP 协议或工具 schema。
唯一需要补的是 DSH 侧的 profile（进程内配置）和宿主的运行适配层。

## 为什么复用 stdio proxy

Claude adapter 已经在用同一套 hosted MCP：

- [platform/backend/modules/runtime_manager/adapters/claude/adapter.py](../../platform/backend/modules/runtime_manager/adapters/claude/adapter.py)
  在 `prepare()` 里生成 `mcp-config.json`，command 是
  `python -m modules.mcp.transports.stdio_proxy`，
  env 是 `STARCOPLAY_HOSTED_MCP_ENDPOINT` + `STARCOPLAY_MCP_CREDENTIAL_PATH`。

- [platform/backend/modules/mcp/transports/stdio_proxy.py](../../platform/backend/modules/mcp/transports/stdio_proxy.py)
  是标准 stdio MCP server，内部把 JSON-RPC 转发到 hosted WebSocket。

DSH 的 `dsh-mcp-client` 支持 stdio；把同一条 command/env 写成 DSH 的 profile 配置即可。

## Profile 配置

见 [cordis.patch.yml](cordis.patch.yml)。生产 profile 使用：

- bundles: `@deepseek-ai/dsh-base` + `@deepseek-ai/dsh-sdk-app`
- 额外安装：`@deepseek-ai/dsh-mcp-client`
- per-runtime patch 注入：MCP endpoint、credential path、persona、model tier。

关键点：

- `toolCallTimeoutMs: 180000`：`go_to` / `go_to_bed` / `water_crops` 可能超过默认 60s。
- `cwd` + `PYTHONPATH`：proxy 需要能从仓库根 import `modules` 和 `agent`。
- Python 环境需要同时有 `google.protobuf` 和 `aiohttp`；生产镜像应使用 backend + agent 合并依赖的 venv。
- 关闭 coding 工具（bash/fs/web/subagent）：companion 只需要 MCP 工具，既省 token 也收窄攻击面。

## 计费

不需要新写一套计量。复用现有 runtime LLM relay：

```text
DSH provider (deepseek-official)
  -> DEEPSEEK_BASE_URL = http://127.0.0.1:<http_port>/internal/runtime-llm
  -> DEEPSEEK_API_KEY = <RuntimeLLMTokenSigner 签发的 per-generation credential>
  -> SystemLLMRouter / NewAPI
  -> 用户 NewAPI 账户 quota
```

需要改的地方只有 [runtime_relay.py](../../platform/backend/modules/llm/runtime_relay.py)：
`DbRuntimeLLMEnvironmentResolver.resolve()` 目前只对 `provider == "claude"` 返回环境变量。
把 `dsh` 加进去，返回：

```python
{
    "DEEPSEEK_BASE_URL": self._relay_base_url,
    "DEEPSEEK_API_KEY": credential,
}
```

模型写 **tier 名**（例如 `balanced`），由 relay 的 `resolve_model()` 映射到具体 DeepSeek 模型；
NewAPI 按 token 扣用户 quota。这样计费口径与现有 Claude runtime 完全一致。

## 下一步（收进 platform）

1. `adapters/dsh/`：JSON-RPC client（`initialize` / `session/prompt` / `session/wait` / `shutdown`）+ `AgentRuntimeAdapter`。
2. `adapters/dsh/profile.py`：把上面的 patch 按 runtime 物化到 workdir；复用 Claude 的 `mcp-credential` 路径。
3. `runtime_relay.py`：`provider == "dsh"` 注入 `DEEPSEEK_*`。
4. `bootstrap.py` / `session_activation.py`：注册 `dsh` adapter，按 rollout 选择。
5. 版本：hosted profile 需要 DSH 的 `dsh-sdk-app`（0.2.1-rc.2 起）；本机全局 CLI 0.1.0-rc.6 只有 headless，用于 spike 验证。
