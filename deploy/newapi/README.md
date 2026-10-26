# NewAPI Docker Compose 部署配置

这个目录保存 star-coplay 自己维护的 NewAPI 本地/服务器部署参数，不提交上游
`new-api/` 源码仓库和本地运行数据。

## 本地启动

```bash
cd deploy/newapi
cp .env.example .env
docker compose up -d
```

默认访问地址：

- NewAPI: `http://127.0.0.1:13000`
- PostgreSQL/Redis: 仅在 Docker network 内暴露，不映射宿主机端口

## 服务器约定

- NewAPI 宿主机端口使用五位数端口，默认 `13000`，避免占用 `3000`。
- 生产环境建议只让 Nginx 或同机 backend 访问 NewAPI，不直接对公网开放。
- `docker-compose.yml` 已绑定 `127.0.0.1:${NEWAPI_HOST_PORT}:3000`，所以服务器公网不会开放 `13000`。
- 管理员只能通过 SSH 隧道访问 NewAPI 管理界面：

```bash
ssh -N -L 13000:127.0.0.1:13000 deploy@<your-host>
```

隧道建立后，在本机浏览器打开：

```text
http://127.0.0.1:13000
```

也就是说，不应该访问 `http://<your-host>:13000`。从公网看，只有 SSH 端口负责建立隧道；NewAPI 管理界面本身没有公网 HTTP 入口。

如果实际 SSH 用户不是 `deploy`，把命令里的用户替换为你能登录服务器的用户，例如：

```bash
ssh -N -L 13000:127.0.0.1:13000 root@<your-host>
```

## 内部管理限流

本部署默认关闭 NewAPI 的内部 API、关键操作和搜索限流：

```env
NEWAPI_GLOBAL_API_RATE_LIMIT_ENABLE=false
NEWAPI_CRITICAL_RATE_LIMIT_ENABLE=false
NEWAPI_SEARCH_RATE_LIMIT_ENABLE=false
```

原因是老用户一比一迁移会连续调用 NewAPI 的用户搜索、用户创建、登录取 token、默认 token 创建等管理接口；如果保留默认搜索/关键操作限流，迁移过程会被 `429` 中断。这个配置只适用于当前 `127.0.0.1` 绑定、无公网 NewAPI 管理入口的部署方式。如果将来把 NewAPI 直接暴露给其他公网入口，必须重新评估并恢复或调高这些限流。

## 服务器首次拉取和启动

如果服务器还没有拉过本仓库，先执行：

```bash
sudo mkdir -p /root/workspace/starcoplay
cd /root/workspace/starcoplay
sudo git clone https://github.com/xiaofenggan01/star-coplay.git
cd star-coplay
sudo git fetch origin --prune
sudo git checkout main
sudo git pull --ff-only origin main
```

如果服务器已经有仓库，只需要更新到当前分支：

```bash
cd /root/workspace/starcoplay/star-coplay
sudo git fetch origin --prune
sudo git checkout main
sudo git pull --ff-only origin main
```

然后准备 NewAPI 的服务器 `.env`：

```bash
cd /root/workspace/starcoplay/star-coplay/deploy/newapi
sudo cp .env.example .env
sudo chmod 600 .env
```

`.env.example` 里已经填了随机生成的初始密码和 secret。因为 `.env.example` 会进入 Git 仓库，正式长期生产前建议只在服务器 `.env` 中再轮换一次这些值，尤其是：

```text
NEWAPI_POSTGRES_PASSWORD
NEWAPI_REDIS_PASSWORD
NEWAPI_SESSION_SECRET
INTERNAL_API_TOKEN
NEWAPI_ROOT_PASSWORD
```

启动 NewAPI：

```bash
cd /root/workspace/starcoplay/star-coplay/deploy/newapi
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
curl http://127.0.0.1:13000/api/status
```

确认 `success=true` 后，通过上面的 SSH 隧道打开 `http://127.0.0.1:13000`，完成 NewAPI 初始化、登录管理员账号，并生成管理员 access token。生成后填回服务器：

```bash
sudo nano /root/workspace/starcoplay/star-coplay/deploy/newapi/.env
```

填入：

```text
NEWAPI_ADMIN_ACCESS_TOKEN=<NewAPI 管理员 access token>
NEWAPI_ADMIN_USER_ID=1
```

## 后端联调环境变量

`platform/backend` 至少需要指向本服务：

```bash
NEWAPI_BASE_URL=http://127.0.0.1:13000
NEWAPI_ADMIN_ACCESS_TOKEN=<NewAPI 管理员访问令牌>
INTERNAL_API_TOKEN=<本地 backend/web/agent 共用 internal token>
AI_RELAY_AGENT_MODEL_TIER=balanced
```

`.env.example` 里的密码和 session secret 只用于本地样例，服务器部署前必须替换。
