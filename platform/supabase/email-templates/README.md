# Supabase Auth 邮件模板

这里是 4 个生产邮件模板的源码，配合 sync 脚本推送到 Supabase Auth。

## 文件清单

| 文件 | 触发场景 | Supabase 字段 |
|------|---------|---------------|
| `confirm-signup.html` | 用户注册后激活邮箱 | `mailer_templates_confirmation_content` |
| `invite.html` | admin 邀请用户（含 Waitlist approved 触达） | `mailer_templates_invite_content` |
| `reset-password.html` | 用户密码重置 | `mailer_templates_recovery_content` |
| `change-email.html` | 用户改绑邮箱 | `mailer_templates_email_change_content` |
| `SUBJECTS.json` | 4 个模板的 subject 中文化 | `mailer_subjects_*` |

## 设计语言

- 羊皮纸 parchment（`#f5e6c8 → #e8cc8c` 渐变 + 3px 木色边框）
- stardew 木色按钮（`#8b6914`，bulletproof button table 布局兼容 Outlook）
- 像素字体在邮件客户端 fallback 到 `Courier New, monospace`
- 中文 fallback 到 PingFang SC / Microsoft YaHei
- Logo 绝对 URL：`https://star-coplay.luosea.cn/logo-new.png`
- 4 模板视觉差异：confirm 金色 / invite 紫粉 / reset 红橙 / change 蓝色

## Supabase 占位符

- `{{ .ConfirmationURL }}` - Supabase 生成的激活/重置链接（自动 sign + redirect_to）
- `{{ .Email }}` - 用户邮箱
- `{{ .NewEmail }}` - 改邮箱场景的新邮箱
- `{{ .SiteURL }}` - 站点 URL（已配 `https://star-coplay.luosea.cn`）

## 修改与同步流程

### 1. 修改模板源码
直接编辑对应 `.html`。**保持 Supabase 占位符原文**（双花括号语法不能编码转义）。

### 2. 本地预览
建议在浏览器里打开看视觉。或用 Litmus / Email on Acid 做客户端兼容性扫描。

### 3. 同步到 Supabase 生产

需要 `SUPABASE_ACCESS_TOKEN`（PAT）和 `SUPABASE_PROJECT_REF`（已在 `.claude/secrets/supabase.env`）。

```bash
# dry-run 看 diff
source /Users/river/Dev/star/.claude/secrets/supabase.env
npx tsx /Users/river/Dev/star/star-coplay-user-auth/platform/web/scripts/sync-supabase-email-templates.ts --dry-run

# 实际推送
npx tsx /Users/river/Dev/star/star-coplay-user-auth/platform/web/scripts/sync-supabase-email-templates.ts
```

### 4. 验证

任意触发一封邮件（注册新邮箱 / 触发密码重置 / admin invite），收件箱看效果。

## 与 Dashboard 的关系

如果有人在 Supabase Dashboard 直接改了模板（应避免），下次 sync 会**完全覆盖**。
真理来源：本目录的 `.html` 文件 + git history。
