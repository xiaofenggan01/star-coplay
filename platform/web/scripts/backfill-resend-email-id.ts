#!/usr/bin/env tsx
/**
 * 历史邮件 resend_email_id 回填脚本
 *
 * 用途：verification_tokens 表新增了 resend_email_id 字段，
 *      历史已发送邮件没有该值。本脚本从 Resend API 拉取全部发送记录，
 *      匹配后回填到 verification_tokens 表。
 *
 * 用法：
 *   RESEND_API_KEY=xxx SUPABASE_URL=xxx SUPABASE_SERVICE_ROLE_KEY=xxx \
 *     npx tsx scripts/backfill-resend-email-id.ts
 *
 * 环境变量：
 *   RESEND_API_KEY            必填（Resend API Key）
 *   SUPABASE_URL              必填
 *   SUPABASE_SERVICE_ROLE_KEY 必填（service role，不可用 anon key）
 */

import { createClient } from "@supabase/supabase-js";

// ---------- 常量 ----------
const RESEND_API_BASE = "https://api.resend.com";
const PAGE_LIMIT = 100; // Resend 单页最大值
const RATE_LIMIT_MS = 200; // 请求间隔（ms）

// 只处理我们自己的邀请邮件
const SUBJECT_KEYWORDS = ["内测审核", "设置密码"];

// ---------- 类型 ----------
interface ResendEmail {
  id: string;
  to: string[];
  subject: string;
  created_at: string;
  last_event: string;
}

interface ResendListResponse {
  object: "list";
  has_more: boolean;
  data: ResendEmail[];
}

// ---------- 工具函数 ----------
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getEnv(key: string): string {
  const val = process.env[key];
  if (!val) {
    console.error(`[FATAL] 缺少环境变量: ${key}`);
    process.exit(1);
  }
  return val;
}

// ---------- Resend API ----------
async function fetchEmailPage(
  apiKey: string,
  after?: string,
): Promise<ResendListResponse> {
  const params = new URLSearchParams({ limit: String(PAGE_LIMIT) });
  if (after) params.set("after", after);

  const url = `${RESEND_API_BASE}/emails?${params}`;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Resend API 请求失败 ${res.status}: ${body}`);
  }

  return res.json() as Promise<ResendListResponse>;
}

async function fetchAllEmails(apiKey: string): Promise<ResendEmail[]> {
  const all: ResendEmail[] = [];
  let after: string | undefined;
  let page = 1;

  console.log("[Resend] 开始拉取邮件列表...");

  while (true) {
    console.log(`[Resend] 拉取第 ${page} 页${after ? `（after=${after}）` : ""}...`);
    const resp = await fetchEmailPage(apiKey, after);

    all.push(...resp.data);
    console.log(`[Resend] 本页 ${resp.data.length} 条，累计 ${all.length} 条`);

    if (!resp.has_more || resp.data.length === 0) break;

    // 用最后一条的 id 作为下一页游标
    after = resp.data[resp.data.length - 1].id;
    page++;

    await sleep(RATE_LIMIT_MS);
  }

  console.log(`[Resend] 拉取完成，共 ${all.length} 封邮件\n`);
  return all;
}

// ---------- 主流程 ----------
async function main() {
  const resendApiKey = getEnv("RESEND_API_KEY");
  const supabaseUrl = getEnv("SUPABASE_URL");
  const supabaseKey = getEnv("SUPABASE_SERVICE_ROLE_KEY");

  console.log("=== resend_email_id 回填脚本 ===");
  console.log(`Supabase: ${supabaseUrl}`);
  console.log(`过滤关键词: ${SUBJECT_KEYWORDS.join(" / ")}\n`);

  const supabase = createClient(supabaseUrl, supabaseKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  // 1. 拉取全部 Resend 邮件
  const allEmails = await fetchAllEmails(resendApiKey);

  // 2. 过滤出我们的邀请邮件
  const inviteEmails = allEmails.filter((e) =>
    SUBJECT_KEYWORDS.some((kw) => e.subject.includes(kw)),
  );
  console.log(
    `[过滤] 共 ${allEmails.length} 封邮件，其中邀请邮件 ${inviteEmails.length} 封\n`,
  );

  if (inviteEmails.length === 0) {
    console.log("[INFO] 没有找到邀请邮件，退出。");
    return;
  }

  // 3. 逐封匹配并回填
  let matched = 0;
  let updated = 0;
  let skipped = 0;
  let failed = 0;

  for (const email of inviteEmails) {
    const toEmail = email.to[0];
    if (!toEmail) {
      console.warn(`[WARN] 邮件 ${email.id} 没有收件人，跳过`);
      skipped++;
      continue;
    }

    // 两步查询：先查 waitlist 获取 id（可能有多条，取最新），再查 verification_tokens
    const { data: waitlistRows, error: wErr } = await supabase
      .from("waitlist")
      .select("id")
      .eq("email", toEmail)
      .order("created_at", { ascending: false })
      .limit(1);

    if (wErr) {
      console.error(`[FAIL] ${toEmail}: 查询 waitlist 失败: ${wErr.message}`);
      failed++;
      await sleep(RATE_LIMIT_MS);
      continue;
    }

    if (!waitlistRows || waitlistRows.length === 0) {
      console.log(`[SKIP] ${toEmail}: waitlist 中不存在，跳过（resend_id=${email.id}）`);
      skipped++;
      await sleep(RATE_LIMIT_MS);
      continue;
    }

    const waitlistId = waitlistRows[0].id;

    const { data: vtRows, error: vtErr } = await supabase
      .from("verification_tokens")
      .select("token")
      .eq("waitlist_id", waitlistId)
      .eq("type", "waitlist_invite")
      .is("resend_email_id", null);

    if (vtErr) {
      console.error(
        `[FAIL] ${toEmail}: 查询 verification_tokens 失败: ${vtErr.message}`,
      );
      failed++;
      await sleep(RATE_LIMIT_MS);
      continue;
    }

    if (!vtRows || vtRows.length === 0) {
      console.log(
        `[SKIP] ${toEmail}: 没有找到待回填的 verification_token（可能已回填或不存在）`,
      );
      skipped++;
      await sleep(RATE_LIMIT_MS);
      continue;
    }

    matched++;

    if (vtRows.length > 1) {
      console.warn(
        `[WARN] ${toEmail}: 找到 ${vtRows.length} 条待回填记录，将全部更新`,
      );
    }

    // 更新所有匹配的 token
    const tokenValues = vtRows.map((r) => r.token);
    const { error: updErr } = await supabase
      .from("verification_tokens")
      .update({ resend_email_id: email.id })
      .in("token", tokenValues);

    if (updErr) {
      console.error(
        `[FAIL] ${toEmail}: 更新失败: ${updErr.message}`,
      );
      failed++;
    } else {
      console.log(
        `[OK  ] ${toEmail}: resend_email_id=${email.id}（更新 ${tokenValues.length} 条）`,
      );
      updated += tokenValues.length;
    }

    await sleep(RATE_LIMIT_MS);
  }

  // 4. 汇总
  console.log("\n=== 汇总 ===");
  console.log(`Resend 邮件总数:   ${allEmails.length}`);
  console.log(`邀请邮件数:        ${inviteEmails.length}`);
  console.log(`匹配到 token:      ${matched}`);
  console.log(`成功更新记录数:    ${updated}`);
  console.log(`跳过:              ${skipped}`);
  console.log(`失败:              ${failed}`);

  if (failed > 0) process.exit(1);
}

main().catch((e) => {
  console.error("[FATAL]", e instanceof Error ? e.message : e);
  process.exit(1);
});
