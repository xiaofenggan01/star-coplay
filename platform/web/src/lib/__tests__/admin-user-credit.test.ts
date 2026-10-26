import { test } from "node:test";
import assert from "node:assert/strict";
import {
  adminCreditGrantIdempotencyKey,
  handleAdminGrantUserCreditsBatch,
  handleAdminGrantUserCredits,
  type AdminCreditGrantDeps,
  type AdminQuotaAdjustmentRequest,
} from "../admin-user-credit";

interface MockState {
  users: Record<string, unknown>[];
  events: Record<string, unknown>[];
}

function freshState(overrides: Partial<MockState> = {}): MockState {
  return {
    users: [],
    events: [],
    ...overrides,
  };
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      const filters: Array<[string, unknown]> = [];
      const query: any = {
        select() {
          return query;
        },
        eq(key: string, value: unknown) {
          filters.push([key, value]);
          return query;
        },
        insert(payload: Record<string, unknown>) {
          if (table !== "events") throw new Error(`unexpected insert table=${table}`);
          state.events.push({ ...payload });
          return Promise.resolve({ error: null });
        },
        async maybeSingle() {
          if (table !== "users") throw new Error(`unexpected maybeSingle table=${table}`);
          const row = state.users.find((candidate) =>
            filters.every(([key, value]) => candidate[key] === value),
          );
          return { data: row ?? null, error: null };
        },
      };
      return query;
    },
  };
}

function makeDeps(
  state: MockState,
  adjuster: AdminCreditGrantDeps["quotaAdjuster"],
): { deps: AdminCreditGrantDeps; requests: AdminQuotaAdjustmentRequest[] } {
  const requests: AdminQuotaAdjustmentRequest[] = [];
  return {
    requests,
    deps: {
      supabase: makeSupabase(state),
      now: () => new Date("2026-07-02T00:00:00.000Z"),
      async quotaAdjuster(request) {
        requests.push(request);
        return adjuster
          ? adjuster(request)
          : { ok: true, status: 200, payload: { status: "succeeded" } };
      },
    },
  };
}

test("handleAdminGrantUserCredits converts credits to relay quota and audits success", async () => {
  const state = freshState({
    users: [{ user_id: "u1", email: "u1@example.test", nickname: "User One" }],
  });
  const { deps, requests } = makeDeps(state, undefined);

  const result = await handleAdminGrantUserCredits(deps, {
    user_id: "u1",
    credits: 20,
    reason: "运营手动发放积分",
  });
  const expectedKey = adminCreditGrantIdempotencyKey({
    user_id: "u1",
    credits: 20,
    reason: "运营手动发放积分",
    now: new Date("2026-07-02T00:00:00.000Z"),
  });

  assert.equal(result.status, 200);
  assert.equal(result.body.ok, true);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], {
    user_id: "u1",
    mode: "add",
    quota: 10_000_000,
    reason: "运营手动发放积分",
    idempotency_key: expectedKey,
    biz_ref_type: "admin_credit_grant",
    biz_ref_id: expectedKey,
  });
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].type, "admin.user_credit_grant");
  assert.deepEqual(state.events[0].data, {
    ok: true,
    stage: "audit",
    credits: 20,
    quota: 10_000_000,
    reason: "运营手动发放积分",
    idempotency_key: expectedKey,
    error: null,
    relay_status: 200,
    relay_payload: { status: "succeeded" },
  });
});

test("adminCreditGrantIdempotencyKey is stable for same user, amount, reason, and day", () => {
  const input = {
    user_id: "u1",
    credits: 20,
    reason: "工单 #42",
    now: new Date("2026-07-02T12:00:00.000Z"),
  };

  assert.equal(
    adminCreditGrantIdempotencyKey(input),
    adminCreditGrantIdempotencyKey({
      ...input,
      now: new Date("2026-07-02T23:59:59.000Z"),
    }),
  );
  assert.notEqual(
    adminCreditGrantIdempotencyKey(input),
    adminCreditGrantIdempotencyKey({ ...input, reason: "工单 #43" }),
  );
});

test("handleAdminGrantUserCredits rejects missing reason before relay call", async () => {
  const state = freshState({
    users: [{ user_id: "u1", email: "u1@example.test", nickname: "User One" }],
  });
  const { deps, requests } = makeDeps(state, undefined);

  const result = await handleAdminGrantUserCredits(deps, {
    user_id: "u1",
    credits: 20,
    reason: "",
  });

  assert.equal(result.status, 400);
  assert.equal(result.body.ok, false);
  assert.equal(result.body.reason, "missing_reason");
  assert.equal(requests.length, 0);
  assert.equal(state.events.length, 0);
});

test("handleAdminGrantUserCredits audits relay failures", async () => {
  const state = freshState({
    users: [{ user_id: "u2", email: "u2@example.test", nickname: "User Two" }],
  });
  const { deps, requests } = makeDeps(state, async () => ({
    ok: false,
    status: 502,
    payload: { error: "relay down" },
    error: "relay down",
  }));

  const result = await handleAdminGrantUserCredits(deps, {
    user_id: "u2",
    credits: "1.5",
    reason: "补偿",
  });

  assert.equal(result.status, 502);
  assert.equal(result.body.ok, false);
  assert.equal(result.body.reason, "relay_adjustment_failed");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].quota, 750_000);
  assert.equal(state.events.length, 1);
  assert.equal((state.events[0].data as Record<string, unknown>).ok, false);
  assert.equal((state.events[0].data as Record<string, unknown>).error, "relay down");
});

test("handleAdminGrantUserCreditsBatch deduplicates user ids and reports partial failures", async () => {
  const state = freshState({
    users: [
      { user_id: "u1", email: "u1@example.test", nickname: "User One" },
      { user_id: "u2", email: "u2@example.test", nickname: "User Two" },
    ],
  });
  const { deps, requests } = makeDeps(state, async (request) => {
    if (request.user_id === "u2") {
      return { ok: false, status: 502, payload: { error: "relay down" }, error: "relay down" };
    }
    return { ok: true, status: 200, payload: { status: "succeeded" } };
  });

  const result = await handleAdminGrantUserCreditsBatch(deps, {
    user_ids: ["u1", "u1", "u2", "missing"],
    credits: 2,
    reason: "批量补偿",
  });

  assert.equal(result.status, 207);
  assert.equal(result.body.ok, true);
  if (result.body.ok) {
    assert.equal(result.body.total, 3);
    assert.deepEqual(result.body.succeeded.map((item) => item.user_id), ["u1"]);
    assert.deepEqual(
      result.body.failed.map((item) => ({ user_id: item.user_id, reason: item.reason })),
      [
        { user_id: "u2", reason: "relay_adjustment_failed" },
        { user_id: "missing", reason: "user_not_found" },
      ],
    );
  }
  assert.deepEqual(requests.map((request) => request.user_id), ["u1", "u2"]);
  assert.equal(state.events.length, 2);
});

test("handleAdminGrantUserCreditsBatch rejects empty target list", async () => {
  const state = freshState();
  const { deps, requests } = makeDeps(state, undefined);

  const result = await handleAdminGrantUserCreditsBatch(deps, {
    user_ids: [],
    credits: 2,
    reason: "批量补偿",
  });

  assert.equal(result.status, 400);
  assert.equal(result.body.ok, false);
  assert.equal(result.body.reason, "missing_user_ids");
  assert.equal(requests.length, 0);
});
