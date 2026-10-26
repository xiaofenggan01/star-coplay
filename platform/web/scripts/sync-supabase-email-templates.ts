#!/usr/bin/env tsx
/**
 * 把 platform/supabase/email-templates/ 下的 4 个模板同步到 Supabase Auth。
 *
 * 用法：
 *   source /Users/river/Dev/star/.claude/secrets/supabase.env
 *   npx tsx scripts/sync-supabase-email-templates.ts [--dry-run]
 *
 * 需要 env：
 *   - SUPABASE_ACCESS_TOKEN（PAT）
 *   - SUPABASE_PROJECT_REF
 *
 * 同步：subject + HTML body 各 4 项 = 共 8 个 mailer_* 字段。
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEMPLATES_DIR = resolve(__dirname, "../../supabase/email-templates");

const PAT = process.env.SUPABASE_ACCESS_TOKEN;
const REF = process.env.SUPABASE_PROJECT_REF;
if (!PAT || !REF) {
  console.error("Missing SUPABASE_ACCESS_TOKEN or SUPABASE_PROJECT_REF env");
  console.error("Hint: source /Users/river/Dev/star/.claude/secrets/supabase.env");
  process.exit(1);
}

const DRY_RUN = process.argv.includes("--dry-run");

// 文件名 → Supabase 字段名映射
const MAPPING: Array<{ file: string; subjectKey: string; bodyKey: string }> = [
  {
    file: "confirm-signup",
    subjectKey: "mailer_subjects_confirmation",
    bodyKey: "mailer_templates_confirmation_content",
  },
  {
    file: "invite",
    subjectKey: "mailer_subjects_invite",
    bodyKey: "mailer_templates_invite_content",
  },
  {
    file: "reset-password",
    subjectKey: "mailer_subjects_recovery",
    bodyKey: "mailer_templates_recovery_content",
  },
  {
    file: "change-email",
    subjectKey: "mailer_subjects_email_change",
    bodyKey: "mailer_templates_email_change_content",
  },
];

const subjects: Record<string, string> = JSON.parse(
  readFileSync(resolve(TEMPLATES_DIR, "SUBJECTS.json"), "utf-8"),
);

const payload: Record<string, string> = {};
for (const { file, subjectKey, bodyKey } of MAPPING) {
  const html = readFileSync(resolve(TEMPLATES_DIR, `${file}.html`), "utf-8");
  const subject = subjects[file];
  if (!subject) {
    console.error(`!! SUBJECTS.json missing key: ${file}`);
    process.exit(1);
  }
  payload[subjectKey] = subject;
  payload[bodyKey] = html;
  console.log(`prepared ${file}: subject="${subject}" body=${html.length} chars`);
}

async function main() {
  if (DRY_RUN) {
    console.log("\n[dry-run] would PATCH /v1/projects/" + REF + "/config/auth");
    console.log("[dry-run] payload keys: " + Object.keys(payload).join(", "));
    return;
  }

  console.log("\n→ PATCH https://api.supabase.com/v1/projects/" + REF + "/config/auth ...");
  const res = await fetch(
    `https://api.supabase.com/v1/projects/${REF}/config/auth`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${PAT}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );

  if (!res.ok) {
    const text = await res.text();
    console.error(`HTTP ${res.status}: ${text}`);
    process.exit(1);
  }

  console.log(`✓ HTTP ${res.status}: 4 templates + 4 subjects synced`);
  console.log("→ 触发任意邮件验证（注册 / 邀请 / 重置 / 换邮箱）");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
