# launcher — Next.js 原型

## 归档时间
2026-05-08

## 来源路径
顶层 `launcher/` → `archive/launcher/`

## 历史角色
platform/web 之前的原型 Next.js 应用，实现了 setup 引导、auth、config、monitor、dashboard 五个完整页面。其中 config 页面用 alert("开发中") 占位、dashboard/monitor 全用 mock 数据、首页环境检测是假扫描——典型的 UI demo 阶段产物。

## 归档原因
- Vercel 部署 Root Directory = platform/web，launcher/ 不在部署链路上
- launcher 的页面已被 platform/web 的 (platform)/ 路由组替代（功能完成度更高）
- grep 验证零外部代码引用

## 替代方案
- 用户面 Web → `platform/web/`
- Setup/auth/dashboard 路由 → `platform/web/app/(platform)/`
