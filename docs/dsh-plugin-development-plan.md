# starcoplay-dsh 开发方案与流程

> 目标：用户只安装一个 DSH 插件，就能让 DSH 作为搭子和自己一起玩《星露谷物语》。
> 形态：新的独立 repository `starcoplay-dsh`；Node 原生 DSH 插件；免费、BYO-key。

## 1. 关键结论

### 1.1 Node 原生可行，而且不需要重写 protobuf

StarCoPlay Mod 的 `LocalDirectBridge` 已经支持 **V2 JSON 命令**，不是只能走 Protocol 2.4.5 的 protobuf：

- `local_command` + `wire_format: "v2-json"` + `payload: { capability, params }`
- `local_command_result` + `state` + `output`（V2Result JSON）

Node 插件只需要：

1. 起一个 127.0.0.1 的 WebSocket server。
2. 写 rendezvous 文件，等 Mod 连接。
3. 完成 `local_direct_hello` → `local_direct_ready`。
4. 收发 `local_command` / `local_command_result`。

不需要生成 TS protobuf，不需要 Python，不需要 MCP server。

### 1.2 本地直连的握手与 fence

Rendezvous 文件：`~/.starcoplay/local-direct/rendezvous.json`（权限 0600）

必需字段：

```json
{
  "endpoint": "127.0.0.1:<port>",
  "proof": "<random boot secret>",
  "context_ref": "dsh-local",
  "principal_ref": "dsh-local",
  "runtime_ref": "dsh-local",
  "generation": 1,
  "binding_ref": "dsh-local",
  "binding_version": 1,
  "game_session_ref": "dsh-local",
  "game_instance_nonce": "<random>",
  "lease_epoch": 1,
  "capability_entries": [
    {"source_surface": "v2-compat", "capability_id": "observe_current_view", "capability_contract_version": "2.4.5"}
  ],
  "capability_digest": "sha256:..."
}
```

Mod 连接后发送 `local_direct_hello`，插件校验 `proof` 后回：

```json
{"type": "local_direct_ready", "reconnect_proof": "<random>"}
```

capability digest 算法（与 C#/Python 一致）：

```text
payload = "starcoplay.capability-snapshot.v1\n"
        + sorted(set("source_surface\tcapability_id\tversion\n"))
digest  = "sha256:" + sha256(payload).hex()
```

命令帧：

```json
{
  "type": "local_command",
  "fence": {
    "context_ref": "...",
    "principal_ref": "...",
    "runtime_ref": "...",
    "game_session_ref": "...",
    "game_instance_nonce": "...",
    "generation": 1,
    "binding_ref": "...",
    "binding_version": 1,
    "lease_epoch": 1,
    "capability_digest": "sha256:..."
  },
  "command_id": "cmd_...",
  "payload": {"capability": "observe", "params": {}},
  "wire_format": "v2-json",
  "deadline_at": 1760000000.0
}
```

结果帧：

```json
{
  "type": "local_command_result",
  "command_id": "cmd_...",
  "state": "SUCCEEDED",
  "output": {"success": true, "data": {}, "observation": "..."},
  "error_code": null,
  "accepted": null,
  "control_id": null
}
```

### 1.3 原生 DSH 工具，而不是 MCP

Node 插件直接注册 DSH native tools（`defineTool`），不经过 MCP：

- 少一层进程/协议；
- 工具参数可以直接用 V2 schema；
- 工具执行就是 `local_command` 请求-响应。

MCP 仍保留给 Python/hosted 路径；本插件不需要它。

## 2. 插件架构

```text
DSH (Electron/Node)
  └─ starcoplay-dsh plugin
       ├─ RouteServer (ws, 127.0.0.1)
       │    ├─ rendezvous.json
       │    ├─ local_direct_hello/ready
       │    └─ local_command ↔ local_command_result
       ├─ Native tools
       │    ├─ starcoplay_status
       │    ├─ starcoplay_install_mod
       │    └─ observe/say/emote/move_to/go_to/...
       ├─ Mod installer
       │    ├─ game/Mods/SMAPI detection
       │    ├─ GitHub Release zip download
       │    └─ config.json merge
       └─ System prompt
```

### 2.1 v0 工具面（12 个）

| 工具 | V2 capability | 说明 |
|---|---|---|
| `observe` | `observe` | 当前视野/状态 |
| `say` | `say` | 游戏内聊天 |
| `emote` | `emote` | 表情气泡 |
| `face` | `face` | 转向 |
| `move_to` | `move_to` | 同地图移动 |
| `go_to` | `go_to` | 语义目标/跨地图 |
| `do` | `do` | 交互键（对话/开门/收获） |
| `use_tool` | `use_tool` | 手持工具 |
| `equip` | `equip` | 切换手持物品 |
| `go_bed` | `go_bed` | 回家睡觉 |
| `water_crops` | `water_crops` | 批量浇水 |
| `harvest_crops` | `harvest_crops` | 批量收获 |

v1 再加 `plant_crops` / `chop_trees` / `mine_rocks` / `ship_items` / `buy_items` / `execute_on_targets` / `menu_interact`。

### 2.2 命令执行

- 串行队列：V2 runtime 同一时间只接受一个命令；Mod 忙时返回 `execution_failed`。
- 超时：原子动作 30s，复合动作（go_to/water/harvest/go_bed）120s。
- 断线：所有 pending 命令以 `route_unavailable` 失败；重连后重新可用。
- 结果：优先返回 `observation`；无 observation 时返回裁剪后的 `data` JSON。

## 3. 首次安装体验

### 3.1 触发方式

插件激活时通常没有打开的会话，所以不能可靠地在 activation 阶段弹窗。

v0 的做法：

1. 插件注册 `starcoplay_status` / `starcoplay_install_mod`。
2. system prompt 写明：新会话先调 `starcoplay_status`；如果 Mod 缺失，先问用户再调 `starcoplay_install_mod`。
3. 用户第一次和 DSH 说话时，模型调用 status，发现缺失后询问用户：
   - “未检测到 StarCoPlay 模组，是否安装最新版？”
   - 通过 `ctx.userQuestions.ask({ questions, wait: { callId: exec.callId } })` 弹出 DSH 原生选择框。
4. 用户点“安装最新版”，插件下载/安装/写配置。
5. 用户读档；Mod 连上本地 route；游戏工具可用。

v1 可以加 `/starcoplay` 命令或 client UI 卡片，做更主动的首次引导。

### 3.2 安装流程

1. 找游戏目录：`STARCOPLAY_GAME_PATH` → Steam 默认路径 → `libraryfolders.vdf`。
2. 找 Mods 目录：macOS 是 `Contents/MacOS/Mods`；Windows/Linux 是 `<game>/Mods`。
3. 检测 SMAPI：没有则提示先装 SMAPI（插件不自动装 SMAPI）。
4. 下载最新 Mod zip（GitHub Releases，固定 asset 名，例如 `StarCoPlay.zip`）。
5. 解压到临时目录，校验 `manifest.json`（`UniqueID=StarCoPlay`），防 zip 路径穿越。
6. 复制到 `Mods/StarCoPlay/`；保留已有 `config.json`、`bridge_*`、`logs`。
7. 合并配置：`BridgeMode="auto"`、`EnableAIControl=true`；`TargetPlayerId` 从 `bridge_<id>` 推断。
8. 如果游戏正在运行，提示重启游戏/读档。

### 3.3 SMAPI 前置

- 插件只检测、提示、给链接，不自动安装 SMAPI。
- 没有 SMAPI 时，Mod 不会加载；安装 Mod 仍可继续，但要在工具结果里明确提示。

## 4. Repository 结构

```text
starcoplay-dsh/
├── package.json
├── tsconfig.json
├── tsup.config.ts
├── cordis.patch.yml
├── src/
│   ├── index.ts              # apply: route + tools + prompt + lifecycle
│   ├── route/
│   │   ├── server.ts         # rendezvous + WS + hello/ready
│   │   ├── protocol.ts       # frame types + validation
│   │   ├── digest.ts         # capability snapshot digest
│   │   └── queue.ts          # serial command queue + timeouts
│   ├── tools/
│   │   ├── catalog.ts        # V2 capability schemas
│   │   ├── game.ts           # observe/say/move/... tools
│   │   ├── setup.ts          # status/install_mod tools + native question
│   │   └── format.ts         # V2Result → text
│   ├── game/
│   │   ├── paths.ts          # Steam/game/Mods/SMAPI detection
│   │   ├── release.ts        # GitHub Release download
│   │   └── install.ts        # zip validation + install + config merge
│   └── prompt.ts
├── test/
│   ├── digest.test.ts
│   ├── protocol.test.ts
│   ├── paths.test.ts
│   ├── install.test.ts
│   └── fake-mod.ts           # fake Mod WS client for contract tests
├── docs/
│   ├── architecture.md
│   ├── protocol.md
│   └── compatibility.md
├── .github/workflows/ci.yml
├── README.md
└── CHANGELOG.md
```

技术栈：

- TypeScript + ESM
- `tsup` 打包到 `lib/index.js`
- `vitest` 单测
- `ws`（本地 WebSocket）
- `adm-zip`（解压，纯 JS）
- peer：`@deepseek-ai/dsh-tools`、`@deepseek-ai/cordis`

## 5. 开发流程

### 5.1 分支与 CI

- `main` 保护；功能走 `feat/<name>`；PR 合并。
- CI：`pnpm lint`、`pnpm test`、`pnpm build`、`pnpm pack`。
- PR 模板沿用主仓库的 `docs/pr模版.md` 风格。
- 每次 PR 必须说明：影响的 Mod 版本、DSH 版本、是否改协议。

### 5.2 里程碑与 DoD

#### M0：仓库与插件骨架（1–2 天）

- DSH 能加载插件；`starcoplay_status` 可见。
- 路径检测、digest、frame 类型、测试脚手架。
- DoD：`pnpm test` 全绿；在干净 DSH profile 里安装本地 `file:` 包，工具出现。

#### M1：本地 route + observe/say（2–3 天）

- WS server、rendezvous、hello/ready、命令队列。
- fake Mod contract test：握手 → observe → say。
- DoD：fake Mod 测试通过；真机 `say` 能在游戏聊天框出现。

#### M2：核心动作工具（3–4 天）

- `move_to` / `go_to` / `face` / `do` / `use_tool` / `equip`。
- 串行队列、超时、断线错误、结果格式化。
- DoD：实机清单 8/8（走位、对话、浇水、砍树、切工具、睡觉、observe、say）。

#### M3：Mod 自动安装与原生弹窗（2–3 天）

- `starcoplay_install_mod`：检测、下载、校验、安装、配置合并。
- `ctx.userQuestions.ask` 原生确认框。
- 版本比较与更新提示。
- DoD：干净机器（无 Mod）→ 首次对话 → 弹窗 → 安装 → 读档 → 工具可用。

#### M4：发布（2 天）

- system prompt / persona、README、故障排查、协议文档。
- GitHub Release + npm（可选）。
- DoD：在干净 DSH profile 里用 GitHub URL 安装；3 步 README 可走通。

总计约 2 周。

### 5.3 测试矩阵

| 层 | 内容 | 入口 |
|---|---|---|
| 单元 | digest、frame 校验、路径检测、zip 安全、结果格式化 | vitest |
| 契约 | fake Mod：hello/ready、命令/结果、超时、断线 | `test/fake-mod.ts` |
| 实机 | say、observe、move_to、go_to、do、use_tool、water、go_bed | 手动清单 |
| 安装 | 干净 DSH profile 从 GitHub 安装 | 手动 |
| 回归 | Mod 版本升级后工具兼容 | compatibility.md |

### 5.4 发布与版本

- 插件 semver；Mod 版本独立。
- `docs/compatibility.md` 维护：插件版本 ↔ 最低 Mod 版本 ↔ DSH 版本。
- 插件读取 Mod `manifest.json` 版本，低于最低版本时提示更新。
- 主仓库 GitHub Release 必须附固定名称的 Mod zip（例如 `StarCoPlay.zip`）。
- 发布步骤：tag → CI → GitHub Release → npm publish（可选）→ README 更新。

### 5.5 安全

- WS 只绑 `127.0.0.1`，随机端口。
- rendezvous 文件 0600；proof 随机；hello 校验 proof/fence。
- 下载只走 HTTPS；校验 `manifest.json`；zip 解压拒绝 `..` 路径。
- 不自动安装 SMAPI；不执行任意 shell；不写游戏目录以外的路径。
- Mod 安装在用户确认后执行；游戏运行时提示重启。

## 6. 风险与待确认

| 风险 | 影响 | 缓解 |
|---|---|---|
| Mod 的 V2 JSON 路径未来被移除 | 插件失效 | 在 Mod 侧把 `v2-json` local_direct 作为稳定兼容面；加契约测试 |
| DSH 插件 API 版本变化 | 加载失败 | 锁定 DSH 版本；CI 做加载 smoke test |
| Steam 库路径多样 | 找不到游戏 | 支持 env 覆盖 + 手动选择路径 |
| Windows 文件锁/路径 | 安装失败 | 检测游戏运行；用路径 API；错误提示 |
| Mod release 资产缺失 | 无法自动安装 | 主仓库 release workflow 固定产出 `StarCoPlay.zip` |
| 首次弹窗时机 | 用户看不到 | system prompt + 首次对话触发；v1 加命令/UI |

## 7. 待你确认

1. 新 repo 名与 npm 包名：默认 `starcoplay-dsh`。
2. 主仓库 Release 的 Mod 资产名：默认 `StarCoPlay.zip`。
3. 目标平台优先级：macOS 先跑通，Windows 作为 M2/M3 一并验证？
