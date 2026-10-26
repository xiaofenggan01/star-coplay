/**
 * invite-code 集成测试（真实 DB，默认 CI 不跑）
 *
 * 与 invite-code.test.ts 的关系：
 *   - invite-code.test.ts          → 纯 mock，覆盖全部分支与错误路径，CI 必跑
 *   - invite-code.integration.test → 本地 PG 双重确认 + 并发抢码 +
 *                                    refund 真实减一，npm run test:integration 才跑
 *
 * 运行（本地 PG，无需 SSH 隧道）：
 *   1. 一次性 setup：
 *        cd platform/web && npm run setup:local-pg:reset
 *      （首次：brew install postgresql@17 && brew services start postgresql@17）
 *   2. 直接：
 *        npm run test:integration
 *      （内部用 pg + adapter 直连，不再依赖 supabase-js / PostgREST）
 *
 * 连接来源（按优先级）：
 *   - PG_TEST_URL          完整 URL（如 postgresql://river@localhost:5432/star_coplay_test）
 *   - 否则按 PGHOST/PGPORT/PGUSER/PGDATABASE 拼装；database 默认 star_coplay_test
 *
 * 覆盖（A3a 8 个用例不变）：
 *   ① happy path：test-code-pool consume 成功，used_count +1
 *   ② disabled：test-code-disabled → ok=false reason=disabled
 *   ③ expired：test-code-expired → ok=false reason=expired
 *   ④ exhausted：test-code-once 消费完后再 consume → exhausted
 *   ⑤ not_found：随机串 → not_found
 *   ⑥ 并发抢码（关键）：5 个并发请求 test-code-multi (max=3) → 必须恰好 3 ok=true
 *   ⑦ refund 幂等性：refund used_count=0 不抛异常
 *   ⑧ refund: consume 后再 refund 回到 0
 *
 * 每个用例自我隔离：afterEach 用 raw SQL UPDATE 重置 used_count=0
 *
 * 失败模式：DB 连不上时显式 throw（不 silent skip），提示先跑 npm run setup:local-pg。
 */

import { test, before, after, afterEach } from "node:test";
import assert from "node:assert/strict";
import { Client } from "pg";
import { execSync } from "node:child_process";
import { consumeInviteCode, refundInviteCode, type InviteCodeClient } from "../invite-code";
import { createPgSupabaseAdapter } from "./_helpers/pg-supabase-adapter";

function resolvePgConfig(): {
  connectionString?: string;
  host?: string;
  port?: number;
  user?: string;
  database?: string;
} {
  if (process.env.PG_TEST_URL) {
    return { connectionString: process.env.PG_TEST_URL };
  }
  const whoami = (() => {
    try {
      return execSync("whoami").toString().trim();
    } catch {
      return undefined;
    }
  })();
  return {
    host: process.env.PGHOST ?? "localhost",
    port: Number(process.env.PGPORT ?? 5432),
    user: process.env.PGUSER ?? whoami,
    database: process.env.PGDATABASE ?? "star_coplay_test",
  };
}

let pg: Client;
// adapter 通过 unknown cast 适配 InviteCodeClient（Pick<SupabaseClient,"from">）
// 原因：adapter 仅实现 invite-code.ts 实际调用的最小 fluent surface，不复刻完整 PostgrestQueryBuilder
let adapter: InviteCodeClient;

const ALL_TEST_CODES = [
  "test-code-once",
  "test-code-multi",
  "test-code-disabled",
  "test-code-expired",
  "test-code-pool",
];

async function resetCodes() {
  // 一次 SQL 把所有测试码 used_count 归零
  await pg.query(
    `UPDATE public.invite_codes SET used_count = 0 WHERE code = ANY($1::text[])`,
    [ALL_TEST_CODES],
  );
}

async function ensureSeedRows() {
  // 仅做断言：seed.sql 应已 INSERT 这 5 条；缺失则给出清晰错误
  const r = await pg.query(
    `SELECT code FROM public.invite_codes WHERE code = ANY($1::text[])`,
    [ALL_TEST_CODES],
  );
  const found = new Set(r.rows.map((row) => row.code as string));
  const missing = ALL_TEST_CODES.filter((c) => !found.has(c));
  if (missing.length > 0) {
    throw new Error(
      `[integration] seed 缺失测试码: ${missing.join(", ")}。` +
        ` 请先运行 \`npm run setup:local-pg:reset\` 重新初始化本地 PG。`,
    );
  }
}

before(async () => {
  const cfg = resolvePgConfig();
  pg = new Client(cfg);
  try {
    await pg.connect();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(
      `[integration] 无法连接本地 PG（${JSON.stringify(cfg)}）: ${msg}\n` +
        `  请先 \`cd platform/web && npm run setup:local-pg:reset\` 初始化本地测试库。\n` +
        `  也可显式设置 PG_TEST_URL=postgresql://<user>@localhost:5432/star_coplay_test`,
    );
  }
  await ensureSeedRows();
  await resetCodes();
  adapter = createPgSupabaseAdapter(pg) as unknown as InviteCodeClient;
});

afterEach(async () => {
  await resetCodes();
});

after(async () => {
  try {
    await resetCodes();
  } finally {
    await pg.end();
  }
});

// 注意：整库共享一个 Client + 单连接。invite-code.ts 的"读后 CAS UPDATE"
// 在单连接下天然串行（pg 客户端按队列发 SQL），但 CAS 守卫保证了即使有并发
// 也不会超发——我们仍按预期得到 ok=3。

test("happy path: consume test-code-pool +1", async () => {
  const r = await consumeInviteCode("test-code-pool", adapter);
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.code.code, "test-code-pool");
    assert.equal(r.code.used_count, 1);
  }
  // 双重确认 DB 真值
  const dbq = await pg.query(
    `SELECT used_count FROM public.invite_codes WHERE code = $1`,
    ["test-code-pool"],
  );
  assert.equal(dbq.rows[0]?.used_count, 1);
});

test("disabled: test-code-disabled → reason=disabled", async () => {
  const r = await consumeInviteCode("test-code-disabled", adapter);
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.equal(r.reason, "disabled");
  }
});

test("expired: test-code-expired → reason=expired", async () => {
  const r = await consumeInviteCode("test-code-expired", adapter);
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.equal(r.reason, "expired");
  }
});

test("exhausted: 二次消费 test-code-once → reason=exhausted", async () => {
  const r1 = await consumeInviteCode("test-code-once", adapter);
  assert.equal(r1.ok, true, "首次必成");
  const r2 = await consumeInviteCode("test-code-once", adapter);
  assert.equal(r2.ok, false);
  if (!r2.ok) {
    assert.equal(r2.reason, "exhausted");
  }
});

test("not_found: 随机串 → reason=not_found", async () => {
  const r = await consumeInviteCode(`__nope__${Date.now()}`, adapter);
  assert.equal(r.ok, false);
  if (!r.ok) {
    assert.equal(r.reason, "not_found");
  }
});

test("并发抢码：5 个 Promise.all 抢 test-code-multi(max=3) 恰好 3 成功", async () => {
  // 关键：每个请求一个独立 Client，模拟真实并发（避免单 Client 队列把"并发"退化为"串行"）。
  // CAS 守卫在 pg 真并发下仍保证不超发——这是测试的核心断言。
  const N = 5;
  const cfg = resolvePgConfig();
  const clients: Client[] = [];
  try {
    const tasks = Array.from({ length: N }, async () => {
      const c = new Client(cfg);
      await c.connect();
      clients.push(c);
      const localAdapter = createPgSupabaseAdapter(c) as unknown as InviteCodeClient;
      return consumeInviteCode("test-code-multi", localAdapter);
    });
    const results = await Promise.all(tasks);

    const okCount = results.filter((r) => r.ok).length;
    const failCount = results.filter((r) => !r.ok).length;
    const exhaustedCount = results.filter(
      (r) => !r.ok && r.reason === "exhausted",
    ).length;

    const dbq = await pg.query(
      `SELECT used_count FROM public.invite_codes WHERE code = $1`,
      ["test-code-multi"],
    );
    const dbUsed = dbq.rows[0]?.used_count;

    // 输出诊断（即使断言失败也能看到分布）
    // eslint-disable-next-line no-console
    console.log(
      `[concurrency] 5 并发 → ok=${okCount}, fail=${failCount} (exhausted=${exhaustedCount}), DB used_count=${dbUsed}`,
    );

    assert.equal(okCount, 3, `期望 3 个成功，实际 ${okCount}（5 并发，max=3）`);
    assert.equal(failCount, 2, `期望 2 个失败，实际 ${failCount}`);
    assert.equal(
      exhaustedCount,
      2,
      `期望 2 个 exhausted，实际 ${exhaustedCount}`,
    );
    assert.equal(dbUsed, 3, "DB used_count 必须 = 3，无超发");
  } finally {
    await Promise.all(clients.map((c) => c.end().catch(() => undefined)));
  }
});

test("refund 幂等：used_count=0 时调用不抛异常", async () => {
  // 当前 used_count = 0（afterEach 重置过）
  await assert.doesNotReject(refundInviteCode("test-code-pool", adapter));
  // 验证未变成负数
  const dbq = await pg.query(
    `SELECT used_count FROM public.invite_codes WHERE code = $1`,
    ["test-code-pool"],
  );
  assert.equal(dbq.rows[0]?.used_count, 0);
});

test("refund: consume 后再 refund 回到 1", async () => {
  await consumeInviteCode("test-code-pool", adapter);
  await consumeInviteCode("test-code-pool", adapter);
  let dbq = await pg.query(
    `SELECT used_count FROM public.invite_codes WHERE code = $1`,
    ["test-code-pool"],
  );
  assert.equal(dbq.rows[0]?.used_count, 2);

  await refundInviteCode("test-code-pool", adapter);
  dbq = await pg.query(
    `SELECT used_count FROM public.invite_codes WHERE code = $1`,
    ["test-code-pool"],
  );
  assert.equal(dbq.rows[0]?.used_count, 1);
});
