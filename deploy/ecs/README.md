# StarCoPlay Gateway on Aliyun ECS

> **当前管理方式**：systemd 托管（服务名 `starcoplay-gateway`）+ GitHub Actions CI 自动部署（`.github/workflows/deploy-ecs.yml`）。
>
> 若尚未完成从手动 nohup 切换到 systemd 的 cutover，请先阅读 `deploy/ecs/cutover.md`。

## 快速操作

```bash
# 查看服务状态
systemctl status starcoplay-gateway

# 查看日志
journalctl -u starcoplay-gateway -f

# 手动重启（通常由 CI 完成）
systemctl restart starcoplay-gateway
```

---

# 原始手动部署参考（历史存档）

> 以下为初始手动部署步骤，已被 systemd + CI 方案取代，仅供参考。

目标：在 ECS Ubuntu 22.04 上跑 `platform/backend/app/main.py`（经由 `platform/ws/scripts/run_local.py` 兼容 shim），Nginx 反代 80 → 8765/8766，每 WS session 由 Gateway fork 一个独立 Agent 子进程。Postgres/Edge 均在 Supabase 官方云。

## 1. 系统准备

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git
sudo useradd -m -s /bin/bash deploy
sudo mkdir -p /opt/star-coplay /var/log/starcoplay
sudo chown -R deploy:deploy /opt/star-coplay /var/log/starcoplay
```

## 2. 拉代码 + venv

```bash
sudo -u deploy git clone <repo-url> /opt/star-coplay
cd /opt/star-coplay
sudo -u deploy python3.11 -m venv .venv
sudo -u deploy .venv/bin/python -m pip install --upgrade pip
sudo -u deploy .venv/bin/python -m pip install -r platform/backend/requirements.txt
sudo -u deploy .venv/bin/python -m pip install -r agent/requirements.txt
```

## 3. 环境变量 `/etc/star-coplay.env`

由运维手填，不进仓库：

```ini
WS_GATEWAY_HOST=0.0.0.0
WS_GATEWAY_PORT=8765
STARCOPLAY_PLATFORM_URL=https://<your-next-or-supabase-functions>
STARCOPLAY_CONFIG_ENV=prod
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service_role>
DATABASE_URL=postgres://postgres:<pwd>@db.<ref>.supabase.co:5432/postgres
# 可选
WS_HEARTBEAT_INTERVAL_S=30
WS_HEARTBEAT_TIMEOUT_S=90
WS_RESUME_TOKEN_TTL_S=300
LOG_LEVEL=INFO
STARCOPLAY_LOG_MAX_BYTES=20971520
STARCOPLAY_LOG_BACKUP_COUNT=7
INTERNAL_API_TOKEN=<随机生成，与 Vercel / Agent Runtime 保持一致>
```

`DATABASE_URL`：Supabase 项目 → Database → Connection string → URI。若选择直连 DSN，则 Gateway 走 asyncpg 鉴权 + telemetry 直写；否则退回 Supabase HTTP 客户端。

### Hosted Runtime production profile

Hosted Runtime uses the same authenticated multi-tenant path in local testing and production.
Set the following values in `/etc/star-coplay.env` before enabling it:

```ini
STARCOPLAY_RUNTIME_MANAGER_ENABLED=1
HOSTED_GAME_SESSION_ROUTE_ENABLED=1
STARCOPLAY_RUNTIME_MANAGER_WORKSPACE_ROOT=/var/lib/starcoplay/runtimes
STARCOPLAY_CLAUDE_BINARY=/usr/bin/node
STARCOPLAY_CLAUDE_CLI_PATH=/opt/claude-code/cli.js
STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET=<at-least-32-characters>
STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON={"platform":{"all_tenants":true}}
STARCOPLAY_RUNTIME_LLM_RELAY_SECRET=<different-at-least-32-character-secret>
STARCOPLAY_RUNTIME_LLM_RELAY_BASE_URL=http://127.0.0.1:8766/internal/runtime-llm
STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE=all_authenticated
```

Keep `STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE=all_authenticated` to make the Hosted
infrastructure available. Actual user rollout is controlled dynamically by the
`agent_runtime/hosted_rollout` system config. Apply the Supabase migration first, then use
Admin -> System Config -> Hosted Rollout to keep production disabled or select `allowlist`
and add exact user UUIDs/emails. The production migration defaults to disabled and the
runtime fails closed to Legacy when the config is missing or invalid.

Hosted and Legacy support are deployed together, but each authenticated connection selects
exactly one controller path. A matching user gets Hosted Claude Code + MCP; a non-matching
user stays on Legacy. The systemd unit runs `python -m app.preflight` before every start and
rejects incomplete Hosted profiles or an incompatible Claude launch.

## 4. systemd

> **新方式**：使用 `install-systemd.sh` 替代手动 copy，脚本有幂等性保护和前置检查。

```bash
# 一键安装 service（可重复执行）
sudo bash /root/workspace/starcoplay/star-coplay/deploy/ecs/install-systemd.sh

# 安装后手动启动（cutover 时执行）
systemctl start starcoplay-gateway
systemctl status starcoplay-gateway
```

日志：
- `journalctl -u starcoplay-gateway -f`（systemd journal）
- `/var/log/starcoplay/operations/gateway-info.jsonl`（INFO 运行日志）
- `/var/log/starcoplay/operations/gateway-warn.jsonl`（WARN 运行日志）
- `/var/log/starcoplay/operations/gateway-error.jsonl`（ERROR/CRITICAL 运行日志）
- `/var/log/starcoplay/metrics/gateway-metrics.jsonl`（独立指标事件）
- `/var/log/starcoplay/audit/`（执行审计；目录 `0700`、文件 `0600`，由审计 sink 独占并在应用内有界轮转）
- `bash deploy/ecs/starlog.sh logid <log_id>`（沿一次请求关联排查）
- `bash deploy/ecs/starlog.sh runtime <runtime_id> [generation]`
- `bash deploy/ecs/starlog.sh binding <binding_id>`
- `bash deploy/ecs/starlog.sh command <command_id>`
- `bash deploy/ecs/starlog.sh errors`
- `bash deploy/ecs/starlog.sh metrics <metric_name>`

> 运行时诊断日志不写入数据库。应用 JSONL 按大小在应用内轮转（默认 20 MiB、7 个备份）；logrotate 只轮转 `systemd/*.log`，避免双重轮转，并在其 `postrotate` 中清理超过 7 日的 Session 诊断文件与空目录。
> `starlog.sh` 只读取当前 JSONL 和数字轮转备份，不维护索引或缓存。Gateway 不再支持通过环境变量恢复运行类 telemetry 写库；排障请统一使用 `/var/log/starcoplay` 和该脚本。

## 5. 防火墙

- 阿里云安全组：入方向只开放 `TCP 80`（Nginx 反代），**不直接暴露 8765/8766**。
- Nginx 将 `/ws` 反代到 `127.0.0.1:8765`，其余 HTTP 请求反代到 `127.0.0.1:8766`。
- 系统内（若启用）：`sudo ufw allow 80/tcp`。

## 6. 联调验证

本机：

```powershell
# websocat 或浏览器 devtools
# 1) 明文握手（经 Nginx 反代）
$env:WS_URL = 'ws://<your-host>/ws'
```

预期：ECS 日志出现 `conn conn_xxx accepted; instance ai_...`，hello 校验通过后 `hello_ok` 回写。

Mod 侧 `WsUrl = ws://<your-host>/ws`，`PlatformToken = <注册页拿到>`，启动 Stardew → SMAPI 日志 `WsBridge connected: ...`。

断连重连三档：

1. 10s 断网（拔网/切 Wi-Fi）：Mod 5s 内指数退避重连，Gateway 在 `resume_token_ttl_s` 窗口内保留子进程，Mod `hello` 携带 `resume_token`，命中 `resume` 路径，子进程复用。
2. 60s：同上。
3. >300s（超过 `WS_RESUME_TOKEN_TTL_S`）：旧实例被 `_schedule_destroy` 清理，新连接走全新 spawn。

## 7. 滚动升级

```bash
sudo -u deploy git -C /opt/star-coplay pull
sudo -u deploy /opt/star-coplay/.venv/bin/python -m pip install -r platform/backend/requirements.txt
sudo systemctl restart starcoplay-gateway
```

## 8. 域名与 HTTPS 方案

### 域名架构

| 域名 | 用途 | 部署位置 | DNS 记录 |
|------|------|---------|---------|
| `star-coplay.luosea.cn` | 前端（官网 + 管理后台 + API Routes） | Vercel（海外，中国加速节点） | Vercel 托管 |
| `<your-host>` | 后端 API + WebSocket Gateway | 阿里云 ECS | 当前生产通过公网 IP 暴露，尚未挂独立域名 |

### 短期方案（域名未备案）

`luosea.cn` 未在中国大陆备案，无法直接绑定国内 ECS。过渡方案：

1. 当前生产后端直接部署在 **阿里云 ECS**
2. Vercel 通过 ECS 公网 IP 访问 backend internal API
3. Mod 侧当前使用 `WsUrl = ws://<your-host>/ws`

### 长期方案（备案完成后）

1. 阿里云 Nginx 配置后端独立域名的 SSL 证书 + 反向代理
2. DNS 增加 **A 记录** 指向阿里云公网 IP
3. Nginx 反代到 `127.0.0.1:8765`

### 后端 API 路由规范

- 当前健康检查：`http://<your-host>/api/ping`
- 当前业务接口：`http://<your-host>/api/v1/xxx`
- 当前 WebSocket：`ws://<your-host>/ws`

### Vercel 当前应使用的后端地址

当前生产前端部署在 Vercel，后端尚未挂独立域名，因此 Vercel 环境变量必须显式指向 ECS 公网 IP：

```ini
PLATFORM_BACKEND_API_URL=http://<your-host>/api
PLATFORM_WS_HTTP_URL=http://<your-host>
```

不要在生产 Vercel 环境中使用 `127.0.0.1` / `localhost`，否则 Next.js API Routes 会回落到 Vercel 容器本机，导致 internal 接口调用失败。
