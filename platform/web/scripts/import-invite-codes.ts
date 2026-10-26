#!/usr/bin/env tsx
/**
 * 邀请码导入脚本
 *
 * 用途：将旧的 INVITE_CODES 环境变量值（或文件 / 命令行 / DEV defaults）
 *      迁移到 public.invite_codes 表（type='official', max_uses=9999）。
 *
 * 用法：
 *   npx tsx scripts/import-invite-codes.ts [--dry-run]
 *                                           [--source=env|file|inline]
 *                                           [--path=<file>]
 *                                           [--codes=a,b,c]
 *                                           [--max-uses=N]
 *                                           [--reason=<text>]
 *
 * 环境变量：
 *   NEXT_PUBLIC_SUPABASE_URL    必填
 *   SUPABASE_SERVICE_ROLE_KEY   必填（service role，不可用 anon key）
 *   INVITE_CODES                可选（逗号分隔）
 *
 * 默认 dry-run = false（实际写入）。如需预览请显式传 --dry-run。
 *
 * 幂等性：基于 PK code，已存在则 SKIP 不报错。
 */

import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { createClient } from "@supabase/supabase-js";

// ---------- 配置常量（与 register/route.ts 保持一致）----------
const DEV_INVITE_DEFAULTS = ["beta-2026", "test-invite", "welcome-to-star-coplay"];
const DEFAULT_MAX_USES = 9999;
const DEFAULT_REASON = "从 INVITE_CODES env 迁移";
const DOTENV_FILE = ".env.local";

// ---------- 工具函数 ----------
function normCode(s: string): string {
  let t = s.trim().normalize("NFKC");
  if (
    (t.startsWith('"') && t.endsWith('"')) ||
    (t.startsWith("'") && t.endsWith("'"))
  ) {
    t = t.slice(1, -1).trim();
  }
  return t.replace(/[‐-―−﹘﹣－]/g, "-");
}

/** 极简 .env 解析器（仅支持 KEY=VAL 与 #注释，无插值），避免引入 dotenv 依赖 */
function loadDotenv(filePath: string): void {
  if (!existsSync(filePath)) return;
  const txt = readFileSync(filePath, "utf-8");
  for (const rawLine of txt.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = val;
  }
}

interface CliArgs {
  dryRun: boolean;
  source: "env" | "file" | "inline";
  path?: string;
  codes?: string;
  maxUses: number;
  reason: string;
}

function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = {
    dryRun: false,
    source: "env",
    maxUses: DEFAULT_MAX_USES,
    reason: DEFAULT_REASON,
  };
  let sourceExplicit = false;
  for (const a of argv.slice(2)) {
    if (a === "--dry-run") {
      args.dryRun = true;
    } else if (a.startsWith("--source=")) {
      const v = a.slice("--source=".length);
      if (v !== "env" && v !== "file" && v !== "inline") {
        throw new Error(`invalid --source: ${v} (env|file|inline)`);
      }
      args.source = v;
      sourceExplicit = true;
    } else if (a.startsWith("--path=")) {
      args.path = a.slice("--path=".length);
    } else if (a.startsWith("--codes=")) {
      args.codes = a.slice("--codes=".length);
    } else if (a.startsWith("--max-uses=")) {
      const n = Number(a.slice("--max-uses=".length));
      if (!Number.isFinite(n) || n < 1) {
        throw new Error(`invalid --max-uses: ${a}`);
      }
      args.maxUses = Math.floor(n);
    } else if (a.startsWith("--reason=")) {
      args.reason = a.slice("--reason=".length);
    } else if (a === "--help" || a === "-h") {
      console.log(
        [
          "用法: npx tsx scripts/import-invite-codes.ts [options]",
          "  --dry-run               只打印不写入",
          "  --source=env|file|inline 数据来源（默认 env）",
          "  --path=<file>           file 模式：每行一个 code",
          "  --codes=a,b,c           inline 模式：逗号分隔",
          `  --max-uses=N            写入容量（默认 ${DEFAULT_MAX_USES}）`,
          `  --reason=<text>         official_code_reason（默认 "${DEFAULT_REASON}"）`,
        ].join("\n"),
      );
      process.exit(0);
    } else {
      throw new Error(`unknown arg: ${a}`);
    }
  }
  // 标记 sourceExplicit 用于 fallback 判断
  (args as CliArgs & { _sourceExplicit: boolean })._sourceExplicit = sourceExplicit;
  return args;
}

function collectCodes(
  args: CliArgs & { _sourceExplicit?: boolean },
): { codes: string[]; usedFallback: boolean } {
  let raw: string[] = [];
  let usedFallback = false;

  if (args.source === "file") {
    if (!args.path) throw new Error("--source=file 需要 --path=<file>");
    const p = resolve(args.path);
    if (!existsSync(p)) throw new Error(`file not found: ${p}`);
    raw = readFileSync(p, "utf-8")
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter((s) => s && !s.startsWith("#"));
  } else if (args.source === "inline") {
    if (!args.codes) throw new Error("--source=inline 需要 --codes=a,b,c");
    raw = args.codes.split(",");
  } else {
    // source=env
    const envCsv = process.env.INVITE_CODES ?? "";
    raw = envCsv.split(",").map((s) => s.trim()).filter(Boolean);
    if (raw.length === 0 && !args._sourceExplicit) {
      console.warn(
        "[WARN] INVITE_CODES 环境变量为空，fallback 到 DEV_INVITE_DEFAULTS。" +
          "请确认非生产环境！",
      );
      raw = [...DEV_INVITE_DEFAULTS];
      usedFallback = true;
    } else if (raw.length === 0 && args._sourceExplicit) {
      throw new Error("INVITE_CODES 环境变量为空且显式指定 --source=env，拒绝 fallback");
    }
  }

  const normalized = Array.from(new Set(raw.map(normCode).filter(Boolean)));
  return { codes: normalized, usedFallback };
}

// ---------- 主流程 ----------
async function main() {
  // 加载 .env.local（如有）
  loadDotenv(resolve(process.cwd(), DOTENV_FILE));

  const args = parseArgs(process.argv) as CliArgs & { _sourceExplicit?: boolean };

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    console.error(
      "[FATAL] 缺少 NEXT_PUBLIC_SUPABASE_URL 或 SUPABASE_SERVICE_ROLE_KEY",
    );
    process.exit(1);
  }

  const { codes, usedFallback } = collectCodes(args);
  if (codes.length === 0) {
    console.error("[FATAL] 未收集到任何 code");
    process.exit(1);
  }

  console.log("=== 邀请码导入 ===");
  console.log(`目标: ${url}`);
  console.log(`来源: ${args.source}${usedFallback ? " (fallback DEV_INVITE_DEFAULTS)" : ""}`);
  console.log(`模式: ${args.dryRun ? "DRY-RUN（不写入）" : "实际写入"}`);
  console.log(`max_uses=${args.maxUses}  reason="${args.reason}"`);
  console.log(`codes (${codes.length}): ${codes.join(", ")}`);
  console.log("");

  const supabase = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  let inserted = 0;
  let skipped = 0;
  let failed = 0;

  for (const code of codes) {
    // 1) 先 SELECT 检查存在性
    const { data: existed, error: selErr } = await supabase
      .from("invite_codes")
      .select("code,type,max_uses,disabled")
      .eq("code", code)
      .maybeSingle();

    if (selErr) {
      console.error(`[FAIL] ${code}: select 失败: ${selErr.message}`);
      failed++;
      continue;
    }

    if (existed) {
      console.log(
        `[SKIP] ${code} 已存在 (type=${existed.type} max_uses=${existed.max_uses} disabled=${existed.disabled})`,
      );
      skipped++;
      continue;
    }

    if (args.dryRun) {
      console.log(
        `[DRY ] would INSERT code=${code} type=official max_uses=${args.maxUses} reason="${args.reason}"`,
      );
      inserted++;
      continue;
    }

    // 2) INSERT
    const { error: insErr } = await supabase.from("invite_codes").insert({
      code,
      type: "official",
      max_uses: args.maxUses,
      official_code_reason: args.reason,
    });

    if (insErr) {
      // 可能 race 撞 PK，按 SKIP 处理
      if (
        insErr.code === "23505" ||
        /duplicate key/i.test(insErr.message)
      ) {
        console.log(`[SKIP] ${code} race-condition 已存在`);
        skipped++;
      } else {
        console.error(`[FAIL] ${code}: ${insErr.message}`);
        failed++;
      }
      continue;
    }
    console.log(`[OK  ] ${code} 已写入`);
    inserted++;
  }

  console.log("");
  console.log("=== 汇总 ===");
  console.log(`成功: ${inserted}  跳过: ${skipped}  失败: ${failed}`);
  if (failed > 0) process.exit(1);
}

main().catch((e) => {
  console.error("[FATAL]", e instanceof Error ? e.message : e);
  process.exit(1);
});
