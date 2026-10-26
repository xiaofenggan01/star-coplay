#!/usr/bin/env tsx
/**
 * 本地 PostgreSQL 测试环境初始化脚本
 *
 * 用途：
 *   把 platform/supabase/migrations/*.sql + local-test/*.sql + seed.sql
 *   按时序执行到本地 `star_coplay_test` 数据库，用于跑 integration 测试，
 *   不再依赖 SSH 隧道 + tencent_jp staging。
 *
 * 用法：
 *   npx tsx scripts/setup-local-pg.ts                       # 幂等 apply（已存在的 SQL 不报错）
 *   npx tsx scripts/setup-local-pg.ts --reset               # 先 DROP DATABASE 再重建（彻底清空）
 *   npx tsx scripts/setup-local-pg.ts --with-prod-snapshot  # 顺便 apply 线上 invite_codes 快照（如果存在）
 *
 * 环境要求：
 *   - macOS Homebrew 安装的 postgresql@17（或 @15/@16，兼容）
 *   - `brew services start postgresql@17` 已启动；createdb / psql 在 PATH
 *   - 默认连本机 unix socket（无密码、当前用户认证）
 *
 * 可覆盖的环境变量：
 *   PGHOST       默认 localhost
 *   PGPORT       默认 5432
 *   PGUSER       默认当前 OS 用户
 *   PGDATABASE   默认 star_coplay_test
 *
 * 退出码：成功 0；任意 SQL 失败 1；环境检查失败 2。
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { execSync } from "node:child_process";
import { Client } from "pg";

const DEFAULT_DB = "star_coplay_test";
const SUPABASE_DIR = resolve(__dirname, "../../supabase");
const MIGRATIONS_DIR = join(SUPABASE_DIR, "migrations");
const LOCAL_TEST_DIR = join(SUPABASE_DIR, "local-test");
const SEED_FILE = join(SUPABASE_DIR, "seed.sql");

interface CliArgs {
  reset: boolean;
  withProdSnapshot: boolean;
}

function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = { reset: false, withProdSnapshot: false };
  for (const a of argv.slice(2)) {
    if (a === "--reset") args.reset = true;
    else if (a === "--with-prod-snapshot") args.withProdSnapshot = true;
    else if (a === "--help" || a === "-h") {
      console.log(
        [
          "用法: npx tsx scripts/setup-local-pg.ts [options]",
          "  --reset                先 DROP DATABASE star_coplay_test 再重建",
          "  --with-prod-snapshot   apply local-test/01_prod_invite_codes_snapshot.sql（如存在）",
          "",
          "环境变量: PGHOST PGPORT PGUSER PGDATABASE",
        ].join("\n"),
      );
      process.exit(0);
    } else {
      throw new Error(`unknown arg: ${a}`);
    }
  }
  return args;
}

function pgEnv() {
  const host = process.env.PGHOST ?? "localhost";
  const port = process.env.PGPORT ?? "5432";
  const user = process.env.PGUSER ?? execSync("whoami").toString().trim();
  const database = process.env.PGDATABASE ?? DEFAULT_DB;
  return { host, port, user, database };
}

function which(cmd: string): string | null {
  try {
    return execSync(`command -v ${cmd}`, { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return null;
  }
}

function checkEnv() {
  const psql = which("psql");
  const createdb = which("createdb");
  const dropdb = which("dropdb");
  if (!psql || !createdb || !dropdb) {
    console.error(
      [
        "[FATAL] 缺少 PostgreSQL 客户端命令（psql/createdb/dropdb）",
        "  Mac 安装：brew install postgresql@17 && brew services start postgresql@17",
        '  并把 /opt/homebrew/opt/postgresql@17/bin 加入 PATH（zshrc）',
      ].join("\n"),
    );
    process.exit(2);
  }
  // pg_isready 探活
  const env = pgEnv();
  try {
    execSync(`pg_isready -h ${env.host} -p ${env.port} -U ${env.user}`, {
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch {
    console.error(
      [
        "[FATAL] PostgreSQL 服务未就绪，pg_isready 失败",
        `  尝试 host=${env.host} port=${env.port} user=${env.user}`,
        "  Mac 启动：brew services start postgresql@17",
      ].join("\n"),
    );
    process.exit(2);
  }
}

function databaseExists(env: ReturnType<typeof pgEnv>): boolean {
  try {
    const out = execSync(
      `psql -h ${env.host} -p ${env.port} -U ${env.user} -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${env.database}'"`,
      { stdio: ["ignore", "pipe", "pipe"] },
    )
      .toString()
      .trim();
    return out === "1";
  } catch (e) {
    console.error(
      `[FATAL] 无法连接 postgres 系统库判断 DB 是否存在: ${(e as Error).message}`,
    );
    process.exit(2);
  }
}

async function recreateDatabase(env: ReturnType<typeof pgEnv>) {
  console.log(`[reset] DROP DATABASE IF EXISTS ${env.database} (force)`);
  // FORCE：踢掉残留连接（PG13+）
  try {
    execSync(
      `psql -h ${env.host} -p ${env.port} -U ${env.user} -d postgres -c "DROP DATABASE IF EXISTS ${env.database} WITH (FORCE)"`,
      { stdio: ["ignore", "pipe", "inherit"] },
    );
  } catch (e) {
    console.error(`[FAIL] DROP DATABASE 失败: ${(e as Error).message}`);
    process.exit(1);
  }
  console.log(`[reset] CREATE DATABASE ${env.database}`);
  execSync(
    `createdb -h ${env.host} -p ${env.port} -U ${env.user} ${env.database}`,
    { stdio: "inherit" },
  );
}

function ensureDatabase(env: ReturnType<typeof pgEnv>) {
  if (databaseExists(env)) {
    console.log(`[ok] DB ${env.database} 已存在`);
    return;
  }
  console.log(`[create] DB ${env.database} 不存在，新建`);
  execSync(
    `createdb -h ${env.host} -p ${env.port} -U ${env.user} ${env.database}`,
    { stdio: "inherit" },
  );
}

interface SqlFile {
  label: string;
  path: string;
}

function collectSqlFiles(args: CliArgs): SqlFile[] {
  const files: SqlFile[] = [];

  // 1) local-test/00_*.sql 在所有 migration 之前（auth schema 桩 + extension）
  if (existsSync(LOCAL_TEST_DIR)) {
    const stubs = readdirSync(LOCAL_TEST_DIR)
      .filter((f) => f.startsWith("00_") && f.endsWith(".sql"))
      .sort();
    for (const f of stubs) {
      files.push({
        label: `local-test/${f}`,
        path: join(LOCAL_TEST_DIR, f),
      });
    }
  }

  // 2) supabase/migrations/*.sql（按文件名时序排序，约定时间戳前缀已自然排序）
  if (!existsSync(MIGRATIONS_DIR)) {
    console.error(`[FATAL] 找不到 ${MIGRATIONS_DIR}`);
    process.exit(2);
  }
  const migrations = readdirSync(MIGRATIONS_DIR)
    .filter((f) => f.endsWith(".sql"))
    .sort();
  for (const f of migrations) {
    files.push({
      label: `migrations/${f}`,
      path: join(MIGRATIONS_DIR, f),
    });
  }

  // 3) local-test/01_prod_invite_codes_snapshot.sql（可选）
  if (args.withProdSnapshot) {
    const snap = join(LOCAL_TEST_DIR, "01_prod_invite_codes_snapshot.sql");
    if (existsSync(snap)) {
      files.push({ label: "local-test/01_prod_invite_codes_snapshot.sql", path: snap });
    } else {
      console.warn(
        `[WARN] --with-prod-snapshot 指定但 ${snap} 不存在，跳过`,
      );
    }
  }

  // 4) seed.sql 最后跑
  if (existsSync(SEED_FILE)) {
    files.push({ label: "seed.sql", path: SEED_FILE });
  }

  return files;
}

/**
 * 跟踪表：哪些 migration 已 apply 过。
 *
 * 为什么需要：部分历史 migration 不是幂等（如 20260402000001_init.sql 用裸
 * `create table public.users`，重跑会 "relation already exists" 报错）。
 * 用一张 _local_migrations_applied 表按 label 去重，模拟 Supabase CLI 的
 * supabase_migrations.schema_migrations 行为。
 *
 * Auth stub 与 seed 不入跟踪表（它们都是幂等的，每次重跑无害）。
 */
const TRACK_TABLE = "_local_migrations_applied";

async function ensureTrackTable(client: Client) {
  await client.query(`
    CREATE TABLE IF NOT EXISTS public.${TRACK_TABLE} (
      label       text PRIMARY KEY,
      applied_at  timestamptz NOT NULL DEFAULT now(),
      sha256      text
    );
  `);
}

async function isApplied(client: Client, label: string): Promise<boolean> {
  const r = await client.query(
    `SELECT 1 FROM public.${TRACK_TABLE} WHERE label = $1`,
    [label],
  );
  return r.rowCount === 1;
}

async function markApplied(client: Client, label: string) {
  await client.query(
    `INSERT INTO public.${TRACK_TABLE} (label) VALUES ($1) ON CONFLICT (label) DO NOTHING`,
    [label],
  );
}

/** 这些 label 每次都重跑（幂等设计，重跑无害）：auth stub、seed、prod snapshot */
function isAlwaysReapply(label: string): boolean {
  return (
    label.startsWith("local-test/") ||
    label === "seed.sql"
  );
}

async function runSqlFiles(env: ReturnType<typeof pgEnv>, files: SqlFile[]) {
  const client = new Client({
    host: env.host,
    port: Number(env.port),
    user: env.user,
    database: env.database,
  });
  await client.connect();

  let applied = 0;
  let skipped = 0;
  let failed = 0;
  try {
    // 先确保跟踪表存在（auth stub 之前；跟踪表在 public schema，不依赖 auth）
    await ensureTrackTable(client);

    for (const f of files) {
      // migration 类需要去重；auth stub / seed 总是重跑
      if (!isAlwaysReapply(f.label) && (await isApplied(client, f.label))) {
        console.log(`[skip] ${f.label} (已在 ${TRACK_TABLE} 中)`);
        skipped++;
        continue;
      }
      const sql = readFileSync(f.path, "utf-8");
      try {
        await client.query(sql);
        if (!isAlwaysReapply(f.label)) {
          await markApplied(client, f.label);
        }
        console.log(`[ok  ] ${f.label}`);
        applied++;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        console.error(`[FAIL] ${f.label}: ${msg}`);
        failed++;
        // 立即停止，避免后续 migration 在脏状态上跑
        throw e;
      }
    }
  } finally {
    await client.end();
  }
  console.log("");
  console.log(
    `=== 汇总: apply ${applied}  skip ${skipped}  失败 ${failed} ===`,
  );
  if (failed > 0) process.exit(1);
}

async function main() {
  const args = parseArgs(process.argv);

  console.log("=== Local PG Test 环境初始化 ===");
  checkEnv();

  const env = pgEnv();
  console.log(
    `目标: postgresql://${env.user}@${env.host}:${env.port}/${env.database}`,
  );

  if (args.reset) {
    await recreateDatabase(env);
  } else {
    ensureDatabase(env);
  }

  const files = collectSqlFiles(args);
  console.log(`待执行 SQL 文件: ${files.length}`);
  for (const f of files) console.log(`  - ${f.label}`);
  console.log("");

  await runSqlFiles(env, files);

  // 输出连接 URL，方便复制
  console.log("");
  console.log("=== 完成 ===");
  console.log(`连接 URL（PG_TEST_URL）：`);
  console.log(
    `  postgresql://${env.user}@${env.host}:${env.port}/${env.database}`,
  );
  console.log(`psql 调试：`);
  console.log(`  psql -h ${env.host} -p ${env.port} -d ${env.database}`);
}

main().catch((e) => {
  console.error("[FATAL]", e instanceof Error ? e.message : e);
  process.exit(1);
});
