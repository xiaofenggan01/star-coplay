/**
 * invite-code 单元测试（纯 mock，零网络/零 process.env）
 *
 * 测试策略（决策）：
 *   被测代码 src/lib/invite-code.ts 的 consumeInviteCode / refundInviteCode 已支持
 *   可选 client 参数（与 register-handler 的 RegisterDeps 同源思想）。本测试通过手写
 *   "最小 supabase client" 桩对象注入，覆盖 consume 全部分支 + refund 全部分支。
 *
 *   仿 A3c register.test.ts：放弃 node:test mock.module（tsx loader 不支持），
 *   选 dependency injection。优势：①运行 < 100ms ②无外部依赖 ③CI 友好。
 *
 * 运行：
 *   cd platform/web && npx tsx --test src/lib/__tests__/invite-code.test.ts
 *
 * 覆盖（13 个用例）：
 *   ① consume happy path → ok=true
 *   ② consume not_found（select null）
 *   ③ consume disabled
 *   ④ consume expired
 *   ⑤ consume exhausted（used_count >= max_uses）
 *   ⑥ consume CAS 第一次冲突第二次成功
 *   ⑦ consume CAS 全程冲突 → exhausted
 *   ⑧ consume select 错误 → 抛 Error
 *   ⑨ consume update 错误 → 抛 Error
 *   ⑩ refund happy path（used_count > 0 时 -1）
 *   ⑪ refund used_count=0 守卫（不发 update）
 *   ⑫ refund select 失败 → 吞错 + console.error
 *   ⑬ refund update 失败 → 吞错 + console.error
 *
 * 真实 staging DB 验证（concurrent / 真值确认）已迁移到 invite-code.integration.test.ts。
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  consumeInviteCode,
  refundInviteCode,
  type InviteCodeClient,
} from "../invite-code";
import { defaultInvitePolicy } from "../invite-policy";
import type { InviteCode } from "../types/invite-code";

// ---------- mock 框架：可编程的 supabase client ----------

interface SelectScript {
  data: unknown;
  error: { message: string } | null;
}

interface UpdateScript {
  /** update 后 select 返回的值 */
  data: unknown;
  error: { message: string } | null;
}

interface MockState {
  /** 顺序消费的 select 脚本（每次 from(...).select().eq().maybeSingle() 取一项） */
  selectScripts: SelectScript[];
  /** 顺序消费的 update 脚本 */
  updateScripts: UpdateScript[];
  /** 实际发起的 select 调用记录 */
  selectCalls: { table: string; columns: string; eqs: Array<[string, unknown]> }[];
  /** 实际发起的 update 调用记录 */
  updateCalls: {
    table: string;
    payload: Record<string, unknown>;
    eqs: Array<[string, unknown]>;
    /** 是否后接 .select(...).maybeSingle()（区分 consume vs refund） */
    chained: boolean;
  }[];
}

function makeMockClient(state: MockState): InviteCodeClient {
  return {
    from(table: string) {
      // 返回兼具 .select 与 .update 的 builder
      return {
        // ---- SELECT 链：.select(cols).eq(k,v).maybeSingle() ----
        select(columns: string) {
          const eqs: Array<[string, unknown]> = [];
          const builder = {
            eq(key: string, value: unknown) {
              eqs.push([key, value]);
              return builder;
            },
            async maybeSingle() {
              state.selectCalls.push({ table, columns, eqs });
              const next = state.selectScripts.shift();
              if (!next) {
                throw new Error(
                  `mock: select script exhausted (table=${table}, eqs=${JSON.stringify(eqs)})`,
                );
              }
              return next;
            },
          };
          return builder;
        },
        // ---- UPDATE 链：.update(payload).eq().eq().select().maybeSingle()
        //                  或 .update(payload).eq().eq() （refund，无 select）----
        update(payload: Record<string, unknown>) {
          const eqs: Array<[string, unknown]> = [];
          let chained = false;
          const exec = async () => {
            state.updateCalls.push({ table, payload, eqs, chained });
            const next = state.updateScripts.shift();
            if (!next) {
              throw new Error(
                `mock: update script exhausted (table=${table}, payload=${JSON.stringify(payload)})`,
              );
            }
            return next;
          };
          const builder = {
            eq(key: string, value: unknown) {
              eqs.push([key, value]);
              return builder;
            },
            select(_columns: string) {
              chained = true;
              return {
                async maybeSingle() {
                  return exec();
                },
              };
            },
            // refund 路径：update(...).eq(...).eq(...) 直接 await
            then(resolve: (v: UpdateScript) => unknown, reject: (e: unknown) => unknown) {
              return exec().then(resolve, reject);
            },
          };
          return builder;
        },
      } as unknown as ReturnType<InviteCodeClient["from"]>;
    },
  };
}

function freshState(): MockState {
  return {
    selectScripts: [],
    updateScripts: [],
    selectCalls: [],
    updateCalls: [],
  };
}

function makeRow(overrides: Partial<InviteCode> = {}): InviteCode {
  return {
    code: "test-code",
    type: "official",
    owner_user_id: null,
    max_uses: 5,
    used_count: 0,
    official_code_reason: null,
    created_at: new Date().toISOString(),
    expires_at: null,
    disabled: false,
    ...overrides,
  };
}

// ---------- 临时压制 console.error，避免污染测试输出 ----------

function withSilencedConsoleError<T>(fn: () => Promise<T>): Promise<T> {
  const orig = console.error;
  const calls: unknown[][] = [];
  console.error = (...args: unknown[]) => {
    calls.push(args);
  };
  return fn().finally(() => {
    console.error = orig;
    // 把 calls 暴露在 fn 外的方式：调用方需自行检测；这里仅静默
    void calls;
  });
}

function captureConsoleError(): { restore: () => void; calls: unknown[][] } {
  const orig = console.error;
  const calls: unknown[][] = [];
  console.error = (...args: unknown[]) => {
    calls.push(args);
  };
  return {
    restore: () => {
      console.error = orig;
    },
    calls,
  };
}

// ====================== consume 用例 ======================

test("① consume happy path → ok=true + 返回 InviteCode 对象", async () => {
  const state = freshState();
  const row = makeRow({ used_count: 0, max_uses: 5 });
  state.selectScripts.push({ data: row, error: null });
  // CAS UPDATE 成功：返回 used_count+1 的新行
  state.updateScripts.push({ data: { ...row, used_count: 1 }, error: null });

  const client = makeMockClient(state);
  const policy = defaultInvitePolicy();
  const r = await consumeInviteCode("test-code", client, { invitePolicy: policy });

  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.code.code, "test-code");
    assert.equal(r.code.used_count, 1);
  }
  // 应仅一轮：一次 select + 一次 update
  assert.equal(state.selectCalls.length, 1);
  assert.equal(state.updateCalls.length, 1);
  // 校验 update 携带的 CAS 守卫
  const upd = state.updateCalls[0];
  assert.equal(upd.payload.used_count, 1);
  assert.deepEqual(upd.eqs, [
    ["code", "test-code"],
    ["used_count", 0],
  ]);
});

test("② consume not_found → reason=not_found（select 返回 null）", async () => {
  const state = freshState();
  state.selectScripts.push({ data: null, error: null });

  const r = await consumeInviteCode("missing", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.reason, "not_found");
  assert.equal(state.updateCalls.length, 0, "未找到不应触发 update");
});

test("③ consume disabled → reason=disabled", async () => {
  const state = freshState();
  state.selectScripts.push({ data: makeRow({ disabled: true }), error: null });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.reason, "disabled");
  assert.equal(state.updateCalls.length, 0);
});

test("④ consume expired → reason=expired", async () => {
  const state = freshState();
  const past = new Date(Date.now() - 10_000).toISOString();
  state.selectScripts.push({
    data: makeRow({ expires_at: past }),
    error: null,
  });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.reason, "expired");
  assert.equal(state.updateCalls.length, 0);
});

test("⑤ consume exhausted → reason=exhausted（used_count >= max_uses）", async () => {
  const state = freshState();
  state.selectScripts.push({
    data: makeRow({ used_count: 5, max_uses: 5 }),
    error: null,
  });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.reason, "exhausted");
  assert.equal(state.updateCalls.length, 0);
});

test("⑤b consume user code with max_uses=null respects per_user_limit from policy", async () => {
  const state = freshState();
  const row = makeRow({ type: "user", owner_user_id: "owner-1", used_count: 2, max_uses: null });
  state.selectScripts.push({ data: row, error: null });
  state.updateScripts.push({ data: { ...row, used_count: 3 }, error: null });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, true);
  if (r.ok) assert.equal(r.code.used_count, 3);
  assert.equal(state.updateCalls.length, 1);
});

test("⑤c consume user code at per_user_limit → invite_limit_reached", async () => {
  const state = freshState();
  state.selectScripts.push({
    data: makeRow({ type: "user", owner_user_id: "owner-1", used_count: 3, max_uses: null }),
    error: null,
  });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.reason, "invite_limit_reached");
  assert.equal(state.updateCalls.length, 0);
});

test("⑥ consume CAS 第一次冲突第二次成功 → ok=true", async () => {
  const state = freshState();
  const row = makeRow({ used_count: 2, max_uses: 5 });
  // 第 1 轮：select 拿到 used_count=2，update 影响 0 行（被并发抢先）
  state.selectScripts.push({ data: row, error: null });
  state.updateScripts.push({ data: null, error: null });
  // 第 2 轮：select 拿到 used_count=3（被别人 +1），update 成功
  const row2 = { ...row, used_count: 3 };
  state.selectScripts.push({ data: row2, error: null });
  state.updateScripts.push({ data: { ...row2, used_count: 4 }, error: null });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, true);
  if (r.ok) assert.equal(r.code.used_count, 4);
  assert.equal(state.selectCalls.length, 2, "重试一次共 2 次 select");
  assert.equal(state.updateCalls.length, 2, "重试一次共 2 次 update");
});

test("⑦ consume CAS 重试耗尽（10 次全冲突）→ reason=exhausted", async () => {
  const state = freshState();
  const row = makeRow({ used_count: 1, max_uses: 5 });
  // MAX_RETRIES=10，全部模拟 CAS 失败
  for (let i = 0; i < 10; i++) {
    state.selectScripts.push({ data: row, error: null });
    state.updateScripts.push({ data: null, error: null });
  }
  state.selectScripts.push({ data: row, error: null });

  const r = await consumeInviteCode("test-code", makeMockClient(state), {
    invitePolicy: defaultInvitePolicy(),
  });

  assert.equal(r.ok, false);
  if (!r.ok) assert.equal(r.reason, "exhausted");
  assert.equal(state.selectCalls.length, 11);
  assert.equal(state.updateCalls.length, 10);
});

test("⑧ consume select 抛错 → 抛 Error 并包含 select 关键字", async () => {
  const state = freshState();
  state.selectScripts.push({
    data: null,
    error: { message: "connection refused" },
  });

  await assert.rejects(
    () => consumeInviteCode("test-code", makeMockClient(state), { invitePolicy: defaultInvitePolicy() }),
    /select failed.*connection refused/,
  );
  assert.equal(state.updateCalls.length, 0);
});

test("⑨ consume update 抛错 → 抛 Error 并包含 update 关键字", async () => {
  const state = freshState();
  state.selectScripts.push({ data: makeRow(), error: null });
  state.updateScripts.push({
    data: null,
    error: { message: "deadlock detected" },
  });

  await assert.rejects(
    () => consumeInviteCode("test-code", makeMockClient(state), { invitePolicy: defaultInvitePolicy() }),
    /update failed.*deadlock detected/,
  );
});

// ====================== refund 用例 ======================

test("⑩ refund happy path → used_count > 0 时 -1，发出 update", async () => {
  const state = freshState();
  state.selectScripts.push({ data: { used_count: 3 }, error: null });
  state.updateScripts.push({ data: null, error: null });

  await refundInviteCode("test-code", makeMockClient(state));

  assert.equal(state.updateCalls.length, 1, "应发 update");
  const upd = state.updateCalls[0];
  assert.equal(upd.payload.used_count, 2, "新值=旧值-1");
  assert.deepEqual(upd.eqs, [
    ["code", "test-code"],
    ["used_count", 3],
  ]);
  assert.equal(upd.chained, false, "refund 不接 .select()");
});

test("⑪ refund used_count=0 → 直接 return，不发 update", async () => {
  const state = freshState();
  state.selectScripts.push({ data: { used_count: 0 }, error: null });

  await refundInviteCode("test-code", makeMockClient(state));

  assert.equal(state.updateCalls.length, 0, "0 值时不应发 update（守卫）");
});

test("⑪b refund row=null → 直接 return，不发 update", async () => {
  const state = freshState();
  state.selectScripts.push({ data: null, error: null });

  await refundInviteCode("missing", makeMockClient(state));

  assert.equal(state.updateCalls.length, 0);
});

test("⑫ refund select 失败 → 吞错 + console.error，不抛", async () => {
  const state = freshState();
  state.selectScripts.push({
    data: null,
    error: { message: "network down" },
  });

  const cap = captureConsoleError();
  try {
    await assert.doesNotReject(refundInviteCode("test-code", makeMockClient(state)));
  } finally {
    cap.restore();
  }
  assert.equal(state.updateCalls.length, 0, "select 失败不应进 update");
  assert.equal(cap.calls.length, 1, "应记 1 条 console.error");
  assert.match(String(cap.calls[0][0]), /refund select failed.*network down/);
});

test("⑬ refund update 失败 → 吞错 + console.error，不抛", async () => {
  const state = freshState();
  state.selectScripts.push({ data: { used_count: 2 }, error: null });
  state.updateScripts.push({
    data: null,
    error: { message: "permission denied" },
  });

  const cap = captureConsoleError();
  try {
    await assert.doesNotReject(refundInviteCode("test-code", makeMockClient(state)));
  } finally {
    cap.restore();
  }
  assert.equal(state.updateCalls.length, 1, "update 应被尝试");
  assert.equal(cap.calls.length, 1);
  assert.match(String(cap.calls[0][0]), /refund update failed.*permission denied/);
});

test("⑭ 边界：consume 抛同步异常时被 refund 包裹时不外泄（防回归）", async () => {
  // refundInviteCode 用 try/catch 包裹整个流程；模拟 client.from() 直接抛
  const throwingClient = {
    from() {
      throw new Error("client exploded");
    },
  } as unknown as InviteCodeClient;

  const cap = captureConsoleError();
  try {
    await assert.doesNotReject(refundInviteCode("test-code", throwingClient));
  } finally {
    cap.restore();
  }
  assert.equal(cap.calls.length, 1);
  assert.match(String(cap.calls[0][0]), /refund threw.*client exploded/);
});

// ---------- 防止未使用导入告警 ----------
void withSilencedConsoleError;
