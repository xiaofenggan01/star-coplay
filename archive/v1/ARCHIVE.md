# v1 — 黑客松/早期阶段整体仓库

## 归档时间
2026-05-08

## 来源路径
顶层 `v1/` → `archive/v1/`

## 历史角色
项目早期实现（黑客松 2026-03-28~29）：自有 agent + mod + landing + 工具链。
三层架构：Agent（Python）通过文件桥与 Mod（C# SMAPI v0.3.0）通信；Landing 是 Next.js 静态导出营销页。

## 归档原因
- v2 对 v1 **零代码依赖**（grep 验证：所有 `/api/v1/` 命中均为 HTTP REST 版本号字符串，非目录引用）
- v2 已完整替代 v1 的 agent / mod / landing 三层
- v1/mod 与 v2/mod 同 UniqueID `StarCoPlay.Mod`（v0.3.0 vs v2.4.0，无法共存于同一 SDV 实例）
- CI/CD/deploy 完全不触达 v1（build-release.yml 只编译 v2/mod，autorun.sh 只部署 v2/platform_ws 和 platform/web）
- 仅 v1/agent 本地有手工调试入口（2026-04-01 真实运行记录 336 条，已存档在 agent/debug/store/）

## 替代方案
- agent → `v2/agent/`（Python，session_runner.py 主入口，LLM 驱动 DeliberativeLoop）
- mod → `v2/mod/StarCoPlay/`（C# SMAPI v2.4.0，WsBridge + V2 能力框架）
- landing → `platform/web/(marketing)/`（Next.js，随 ECS autorun.sh 部署）
- 工具链 → `v2/scripts/` 或 `v2/tools/`

## 子目录原状（保留未动）
- `agent/`：含 bridge/（文件桥）、evomap/（EvoMap 社区集成）、debug/store/（336 个 JSON 运行记录，2026-04-01）
- `docs/`：10 个 .md（bridge 协议、Adapter 层、SMAPI 技术摸底、黑客松 P0 方案等，均为 v1 实现细节）
- `landing/`：Next.js 静态导出营销页（output: export），无部署配置
- `mod/`：SMAPI Mod v0.3.0，文件桥协议，21 个旧式工具名
- `tools/`：bridge-cli（Python 命令行调试工具）
- `assets/`：设计素材、品牌图标（含与 landing/public/assets 的重复文件）
- `player_launcher.spec`：PyInstaller 打包配置，引用的 player_launcher.py 不存在，已损坏

## 提炼到 design-docs 的核心决策
无——v1/docs 内容均为黑客松 30 小时决策产物或 v1 实现细节，v2 架构已完全替代，无独立历史价值需提炼。
