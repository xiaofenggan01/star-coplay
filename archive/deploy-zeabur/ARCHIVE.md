# deploy-zeabur — Zeabur 部署配置（已废弃）

## 归档时间
2026-05-08

## 来源路径
`deploy/zeabur/` → `archive/deploy-zeabur/`

## 历史角色
早期试用 Zeabur 部署平台的 FastAPI 框架。
结构：`app/main.py`（仅 `/ping` 端点）+ `app/v1/__init__.py`（空 APIRouter，无注册路由）+ Dockerfile + requirements.txt。

## 归档原因
- `app/v1/__init__.py` 是空 router（仅声明 `router = APIRouter()`，从未挂载业务逻辑）
- CI 不触达该路径（deploy-ecs.yml 和 build-release.yml 均无 zeabur 引用）
- 当前生产部署：阿里云 ECS（`deploy/ecs/` + systemd，CI 通过 SSH 触发 autorun.sh）
- platform/README.md 提及的"Zeabur 短期方案"已停留在设计意图阶段，从未上线

## 替代方案
- 服务端运行时：`deploy/ecs/`（ECS + autorun.sh + systemd service）
- Web 部署：Vercel（platform/web）
