# 一次性 Cutover：从手动 nohup 切换到 systemd 管理

> 本文档只需执行一次。完成后，后续部署通过 GitHub Actions（deploy-ecs.yml）自动完成。

## 前置条件

- [ ] `deploy/ecs/starcoplay-gateway.service` 已更新（指向 conda env 直接路径）
- [ ] `deploy/ecs/install-systemd.sh` 已就位
- [ ] `.github/workflows/deploy-ecs.yml` 已修复（使用正确 secrets 名称）
- [ ] GitHub Secrets 已配置（见下方）
- [ ] ECS 上 `/etc/star-coplay.env` 存在且内容正确

## 必须配置的 GitHub Secrets

在 GitHub repo → Settings → Secrets and variables → Actions → New repository secret 中添加：

| Secret 名称    | 值                         | 说明                              |
|---------------|---------------------------|-----------------------------------|
| `ECS_HOST`    | `<your-host>`          | ECS 公网 IP                       |
| `ECS_USER`    | `root`                    | SSH 登录用户                       |
| `ECS_SSH_KEY` | `<私钥内容，完整 PEM>`      | 从 1Password 导出对应 ECS 的私钥   |

## 风险说明

| 风险 | 缓解措施 |
|------|---------|
| 停旧进程会断开所有在线 mod 的 WS 连接 | 选低活跃时段执行（深夜或维护窗口） |
| origin/main 包含约 2 周的代码变更（v2.4.0 → 57b22a1） | cutover 前先在本地 `git diff v2.4.0..origin/main` 审视变更 |
| next-env.d.ts 工作区脏修改 | 步骤 2 中 `git stash` 处理 |
| conda env 缺少新依赖 | 步骤 3 中执行 `pip install -r requirements.txt` |

## 执行步骤

### 步骤 0：变更审视（本地执行）

```bash
# 在本地 star-coplay 目录审视 2 周变更
cd /path/to/star-coplay
git fetch origin
git log --oneline v2.4.0..origin/main
git diff v2.4.0..origin/main -- platform/ws/
```

确认无 breaking change 后再继续。

### 步骤 1：SSH 进入 ECS

```bash
ssh star-ecs
```

### 步骤 2：记录旧进程并备份现状

```bash
# 记录当前运行的 nohup 进程 PID
ps aux | grep run_local.py
# 输出示例：root  2529923 ...  python platform/ws/scripts/run_local.py

# 确认 systemd service 目前不存在
ls /etc/systemd/system/starcoplay-gateway.service 2>&1 || echo "（正常：尚未安装）"
```

### 步骤 3：更新代码到最新 main

```bash
cd /root/workspace/starcoplay/star-coplay

# 处理脏修改（next-env.d.ts 等）
git status
git stash  # 如有脏修改

git fetch origin
git fetch origin --tags
git checkout main
git reset --hard origin/main

echo "HEAD: $(git rev-parse --short HEAD)"

# 更新 Python 依赖（如有新增）
/root/miniconda3/envs/starcoplay-ws/bin/python -m pip install -r platform/backend/requirements.txt
/root/miniconda3/envs/starcoplay-ws/bin/python -m pip install -r agent/requirements.txt
```

### 步骤 4：安装 systemd unit

```bash
bash /root/workspace/starcoplay/star-coplay/deploy/ecs/install-systemd.sh
```

脚本会：
- 检查 conda env 路径是否正确
- 检查 `/etc/star-coplay.env` 是否存在（不存在时会警告并提示确认）
- 备份旧 service 文件（若存在）
- 安装新 service 到 `/etc/systemd/system/`
- 执行 `systemctl daemon-reload` + `systemctl enable`
- **不自动 start**（由下一步人工控制）

### 步骤 5：停旧 nohup 进程

```bash
# 使用步骤 2 中记录的 PID，或重新查找
OLD_PID=$(pgrep -f "python.*run_local.py" | head -1)
echo "即将停止 PID: $OLD_PID"

kill "$OLD_PID"
sleep 3

# 确认已停
ps aux | grep run_local.py | grep -v grep || echo "✓ 旧进程已停止"
```

### 步骤 6：启动 systemd service

```bash
systemctl start starcoplay-gateway
sleep 3
systemctl status starcoplay-gateway --no-pager
```

### 步骤 7：验证

```bash
# 检查端口
ss -tlnp | grep -E "8765|8766"

# 检查日志
journalctl -u starcoplay-gateway -n 50 --no-pager

# 查看应用日志
tail -f /var/log/starcoplay/operations/gateway-info.jsonl

# 按关联标识、错误或指标排查
bash deploy/ecs/starlog.sh logid <log_id>
bash deploy/ecs/starlog.sh runtime <runtime_id> [generation]
bash deploy/ecs/starlog.sh binding <binding_id>
bash deploy/ecs/starlog.sh command <command_id>
bash deploy/ecs/starlog.sh errors
bash deploy/ecs/starlog.sh metrics <metric_name>

# HTTP health
curl -s http://localhost:8766/api/ping
```

### 步骤 8：验证 CI 部署链路（可选）

1. 在 GitHub Actions 页面找到 "Deploy ECS (Gateway + Platform)"
2. 点击 "Run workflow"（手动触发，留空 ref 即部署 main）
3. 确认 workflow 执行成功，service 正常重启

## 回滚方案

如果 systemd 启动失败，需要切回手动 nohup：

```bash
# 停止（可能失败的）service
systemctl stop starcoplay-gateway 2>/dev/null || true
systemctl disable starcoplay-gateway

# 重新手动启动（与原生产方式一致）
source /root/miniconda3/etc/profile.d/conda.sh
conda activate starcoplay-ws
if [[ -f /etc/star-coplay.env ]]; then
  set -a; source /etc/star-coplay.env; set +a
fi
cd /root/workspace/starcoplay/star-coplay
nohup /root/miniconda3/envs/starcoplay-ws/bin/python -m app \
  >> /var/log/starcoplay/systemd/gateway.stdout.log 2>> /var/log/starcoplay/systemd/gateway.stderr.log &
echo "回滚完成，PID=$!"

# 诊断 systemd 失败原因
journalctl -u starcoplay-gateway -n 100 --no-pager
systemctl status starcoplay-gateway -l --no-pager
```

## Cutover 完成后的日常维护

| 操作 | 命令 |
|------|------|
| 查看状态 | `systemctl status starcoplay-gateway` |
| 查看日志 | `journalctl -u starcoplay-gateway -f` |
| 手动重启 | `systemctl restart starcoplay-gateway` |
| CI 自动部署 | 打 tag `v*` 或在 Actions 页面手动触发 workflow |
| 更新 service 文件 | 重新运行 `install-systemd.sh`，然后 `systemctl restart starcoplay-gateway` |
