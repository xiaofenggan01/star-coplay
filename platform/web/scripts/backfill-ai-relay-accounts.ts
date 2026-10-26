#!/usr/bin/env tsx
/**
 * Backfill platform users into NewAPI relay accounts.
 *
 * Usage:
 *   SUPABASE_URL=https://xxx.supabase.co SUPABASE_SERVICE_ROLE_KEY=xxx \
 *   PLATFORM_WS_HTTP_URL=http://127.0.0.1:8766 INTERNAL_API_TOKEN=xxx \
 *     npx tsx scripts/backfill-ai-relay-accounts.ts --dry-run
 *
 * Then run for real:
 *     npx tsx scripts/backfill-ai-relay-accounts.ts
 */

import { createClient } from "@supabase/supabase-js";

interface UserRow {
  user_id: string;
  email: string | null;
  nickname: string | null;
  auth_id: string | null;
  created_at: string;
}

interface CliOptions {
  dryRun: boolean;
  limit: number | null;
  pageSize: number;
  delayMs: number;
  onlyMissingAuth: boolean;
}

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8766";

function readEnv(name: string, fallback?: string): string {
  const value = process.env[name]?.trim() || fallback || "";
  if (!value) {
    console.error(`[FATAL] missing env ${name}`);
    process.exit(1);
  }
  return value;
}

function readOptionalEnv(name: string): string {
  return process.env[name]?.trim() || "";
}

function parseArgs(argv: string[]): CliOptions {
  const options: CliOptions = {
    dryRun: false,
    limit: null,
    pageSize: 100,
    delayMs: 100,
    onlyMissingAuth: false,
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--dry-run") {
      options.dryRun = true;
    } else if (arg === "--only-missing-auth") {
      options.onlyMissingAuth = true;
    } else if (arg === "--limit") {
      options.limit = Number(argv[++i]);
    } else if (arg.startsWith("--limit=")) {
      options.limit = Number(arg.slice("--limit=".length));
    } else if (arg === "--page-size") {
      options.pageSize = Number(argv[++i]);
    } else if (arg.startsWith("--page-size=")) {
      options.pageSize = Number(arg.slice("--page-size=".length));
    } else if (arg === "--delay-ms") {
      options.delayMs = Number(argv[++i]);
    } else if (arg.startsWith("--delay-ms=")) {
      options.delayMs = Number(arg.slice("--delay-ms=".length));
    } else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      console.error(`[FATAL] unknown argument: ${arg}`);
      printHelp();
      process.exit(1);
    }
  }

  if (options.limit !== null && (!Number.isInteger(options.limit) || options.limit <= 0)) {
    console.error("[FATAL] --limit must be a positive integer");
    process.exit(1);
  }
  if (!Number.isInteger(options.pageSize) || options.pageSize <= 0 || options.pageSize > 1000) {
    console.error("[FATAL] --page-size must be an integer between 1 and 1000");
    process.exit(1);
  }
  if (!Number.isInteger(options.delayMs) || options.delayMs < 0) {
    console.error("[FATAL] --delay-ms must be a non-negative integer");
    process.exit(1);
  }

  return options;
}

function printHelp() {
  console.log(`
Backfill platform users into NewAPI relay accounts.

Required env:
  SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  INTERNAL_API_TOKEN

Optional env:
  PLATFORM_WS_HTTP_URL            backend HTTP base, default http://127.0.0.1:8766
  AI_RELAY_BACKFILL_DEFAULT_GROUP default NewAPI group

Options:
  --dry-run              print users without calling backend
  --limit N              process at most N users
  --page-size N          Supabase page size, default 100
  --delay-ms N           delay between backend calls, default 100
  --only-missing-auth    only process users whose auth_id is null
`.trim());
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mask(value: string | null | undefined): string {
  if (!value) return "-";
  if (value.includes("@")) {
    const [name, domain] = value.split("@");
    return `${name.slice(0, 2)}***@${domain}`;
  }
  return value.length <= 10 ? value : `${value.slice(0, 6)}...${value.slice(-4)}`;
}

async function fetchUsers(options: CliOptions): Promise<UserRow[]> {
  const supabaseUrl =
    readOptionalEnv("SUPABASE_URL") || readEnv("NEXT_PUBLIC_SUPABASE_URL");
  const serviceKey = readEnv("SUPABASE_SERVICE_ROLE_KEY");
  const supabase = createClient(supabaseUrl, serviceKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const rows: UserRow[] = [];
  let from = 0;
  while (true) {
    const to = from + options.pageSize - 1;
    let query = supabase
      .from("users")
      .select("user_id,email,nickname,auth_id,created_at")
      .order("created_at", { ascending: true })
      .range(from, to);

    if (options.onlyMissingAuth) {
      query = query.is("auth_id", null);
    }

    const { data, error } = await query;
    if (error) {
      throw new Error(`users query failed: ${error.message}`);
    }
    if (!data || data.length === 0) break;

    for (const row of data as UserRow[]) {
      rows.push(row);
      if (options.limit !== null && rows.length >= options.limit) {
        return rows;
      }
    }
    if (data.length < options.pageSize) break;
    from += options.pageSize;
  }
  return rows;
}

async function ensureUser(
  backendUrl: string,
  token: string,
  user: UserRow,
  defaultGroup: string,
): Promise<{ ok: boolean; status: number; error?: string }> {
  const body: Record<string, unknown> = {};
  if (user.email) body.email = user.email;
  body.display_name = user.nickname || user.email || user.user_id;
  if (defaultGroup) body.default_group = defaultGroup;

  const res = await fetch(
    `${backendUrl.replace(/\/+$/, "")}/internal/ai/relay/users/${encodeURIComponent(user.user_id)}/ensure`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    },
  );
  const text = await res.text();
  if (!res.ok) {
    return { ok: false, status: res.status, error: text.slice(0, 500) };
  }
  return { ok: true, status: res.status };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const backendUrl = readOptionalEnv("PLATFORM_WS_HTTP_URL") || DEFAULT_BACKEND_URL;
  const internalToken = options.dryRun ? "" : readEnv("INTERNAL_API_TOKEN");
  const defaultGroup = readOptionalEnv("AI_RELAY_BACKFILL_DEFAULT_GROUP");

  console.log("=== AI Relay account backfill ===");
  console.log(`backend: ${backendUrl}`);
  console.log(`mode: ${options.dryRun ? "dry-run" : "write"}`);
  console.log(`page_size: ${options.pageSize}`);
  console.log(`delay_ms: ${options.delayMs}`);
  console.log(`only_missing_auth: ${options.onlyMissingAuth}`);
  console.log("");

  const users = await fetchUsers(options);
  console.log(`loaded users: ${users.length}`);
  if (users.length === 0) return;

  let ok = 0;
  let failed = 0;
  let skipped = 0;

  for (const [idx, user] of users.entries()) {
    const label = `${idx + 1}/${users.length} user=${mask(user.user_id)} email=${mask(user.email)} auth=${user.auth_id ? "yes" : "no"}`;
    if (options.dryRun) {
      console.log(`[DRY] ${label}`);
      skipped++;
      continue;
    }

    try {
      const result = await ensureUser(backendUrl, internalToken, user, defaultGroup);
      if (result.ok) {
        ok++;
        console.log(`[OK] ${label}`);
      } else {
        failed++;
        console.error(`[FAIL] ${label} status=${result.status} error=${result.error ?? ""}`);
      }
    } catch (err) {
      failed++;
      console.error(`[FAIL] ${label} error=${err instanceof Error ? err.message : String(err)}`);
    }

    if (options.delayMs > 0 && idx < users.length - 1) {
      await sleep(options.delayMs);
    }
  }

  console.log("");
  console.log(`done ok=${ok} failed=${failed} skipped=${skipped}`);
  if (failed > 0) process.exitCode = 1;
}

main().catch((err) => {
  console.error(`[FATAL] ${err instanceof Error ? err.stack || err.message : String(err)}`);
  process.exit(1);
});
