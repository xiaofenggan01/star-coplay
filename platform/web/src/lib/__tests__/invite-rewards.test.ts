import { test } from "node:test";
import assert from "node:assert/strict";
import {
  grantInviteRewardsForUser,
  inviteRewardIdempotencyKey,
  inviteRewardQuota,
  type QuotaAdjustmentRequest,
  type QuotaAdjuster,
} from "../invite-rewards";

interface SelectResult {
  data: unknown;
  error: { message: string } | null;
}

interface MockState {
  relations: Record<string, unknown>[];
  grants: Record<string, unknown>[];
  selectCalls: { table: string; filters: Array<[string, unknown]> }[];
  insertCalls: { table: string; payload: Record<string, unknown> }[];
  updateCalls: { table: string; payload: Record<string, unknown>; filters: Array<[string, unknown]> }[];
  insertScripts: Array<{ error?: { code?: string; message: string } | null }>;
  updateScripts: Array<{ error?: { message: string } | null }>;
}

function freshState(overrides: Partial<MockState> = {}): MockState {
  return {
    relations: [],
    grants: [],
    selectCalls: [],
    insertCalls: [],
    updateCalls: [],
    insertScripts: [],
    updateScripts: [],
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
        async maybeSingle(): Promise<SelectResult> {
          state.selectCalls.push({ table, filters: [...filters] });
          if (table === "user_relations") {
            return {
              data: findByFilters(state.relations, filters),
              error: null,
            };
          }
          if (table === "invite_reward_grants") {
            return {
              data: findByFilters(state.grants, filters),
              error: null,
            };
          }
          throw new Error(`unexpected maybeSingle table=${table}`);
        },
        insert(payload: Record<string, unknown>) {
          const insertQuery = {
            select() {
              return insertQuery;
            },
            async maybeSingle(): Promise<SelectResult> {
              state.insertCalls.push({ table, payload });
              if (table !== "invite_reward_grants") {
                throw new Error(`unexpected insert table=${table}`);
              }
              const script = state.insertScripts.shift() ?? { error: null };
              if (script.error) return { data: null, error: script.error };
              state.grants.push({ ...payload });
              return { data: payload, error: null };
            },
          };
          return insertQuery;
        },
        update(payload: Record<string, unknown>) {
          const updateQuery = {
            eq(key: string, value: unknown) {
              filters.push([key, value]);
              return updateQuery;
            },
            then(resolve: (value: { error: { message: string } | null }) => unknown, reject: (reason: unknown) => unknown) {
              state.updateCalls.push({ table, payload, filters: [...filters] });
              if (table !== "invite_reward_grants") {
                return Promise.reject(new Error(`unexpected update table=${table}`)).then(resolve, reject);
              }
              const script = state.updateScripts.shift() ?? { error: null };
              if (!script.error) {
                const row = findByFilters(state.grants, filters);
                if (row) Object.assign(row, payload);
              }
              return Promise.resolve({ error: script.error ?? null }).then(resolve, reject);
            },
          };
          return updateQuery;
        },
      };
      return query;
    },
  };
}

function findByFilters(rows: Record<string, unknown>[], filters: Array<[string, unknown]>) {
  return rows.find((row) => filters.every(([key, value]) => row[key] === value)) ?? null;
}

function captureAdjuster(): { requests: QuotaAdjustmentRequest[]; adjuster: QuotaAdjuster } {
  const requests: QuotaAdjustmentRequest[] = [];
  return {
    requests,
    adjuster: async (request) => {
      requests.push(request);
      return { ok: true, status: 200, payload: { data: { idempotency_key: request.idempotency_key } } };
    },
  };
}

test("inviteRewardIdempotencyKey uses stable product keys", () => {
  assert.equal(inviteRewardIdempotencyKey("invitee", "u2", "u1"), "invitee_bonus:u2");
  assert.equal(inviteRewardIdempotencyKey("inviter", "u2", "u1"), "inviter_bonus:u1:u2");
});

test("grantInviteRewardsForUser skips root relation without inviter", async () => {
  const state = freshState({
    relations: [{ user_id: "u2", inviter_user_id: null, invite_code: null }],
  });
  const { requests, adjuster } = captureAdjuster();

  const result = await grantInviteRewardsForUser({ user_id: "u2" }, makeSupabase(state), adjuster);

  assert.equal(result.skipped, true);
  assert.equal(result.reason, "no_inviter");
  assert.equal(requests.length, 0);
  assert.equal(state.insertCalls.length, 0);
});

test("grantInviteRewardsForUser grants invitee and inviter 20 credits each", async () => {
  const state = freshState({
    relations: [{ user_id: "u2", inviter_user_id: "u1", invite_code: "SC-AAAA-BBBB" }],
  });
  const { requests, adjuster } = captureAdjuster();

  const result = await grantInviteRewardsForUser({ user_id: "u2" }, makeSupabase(state), adjuster);

  assert.equal(result.skipped, false);
  assert.equal(result.grants.length, 2);
  assert.deepEqual(
    requests.map((r) => ({ user_id: r.user_id, idempotency_key: r.idempotency_key, quota: r.quota })),
    [
      { user_id: "u2", idempotency_key: "invitee_bonus:u2", quota: inviteRewardQuota("invitee") },
      { user_id: "u1", idempotency_key: "inviter_bonus:u1:u2", quota: inviteRewardQuota("inviter") },
    ],
  );
  assert.equal(state.insertCalls.length, 2);
  assert.equal(state.updateCalls.length, 2);
  assert.equal(state.grants.every((row) => row.status === "succeeded"), true);
  assert.equal(state.grants.every((row) => row.credits === 20), true);
});

test("grantInviteRewardsForUser skips already succeeded grant without quota call", async () => {
  const state = freshState({
    relations: [{ user_id: "u2", inviter_user_id: "u1", invite_code: "SC-AAAA-BBBB" }],
    grants: [
      {
        idempotency_key: "invitee_bonus:u2",
        invitee_user_id: "u2",
        inviter_user_id: "u1",
        invite_code: "SC-AAAA-BBBB",
        reward_target: "invitee",
        credits: 20,
        status: "succeeded",
        ai_relay_adjustment_key: "invitee_bonus:u2",
        error: null,
      },
      {
        idempotency_key: "inviter_bonus:u1:u2",
        invitee_user_id: "u2",
        inviter_user_id: "u1",
        invite_code: "SC-AAAA-BBBB",
        reward_target: "inviter",
        credits: 20,
        status: "succeeded",
        ai_relay_adjustment_key: "inviter_bonus:u1:u2",
        error: null,
      },
    ],
  });
  const { requests, adjuster } = captureAdjuster();

  const result = await grantInviteRewardsForUser({ user_id: "u2" }, makeSupabase(state), adjuster);

  assert.equal(result.skipped, false);
  assert.equal(result.grants.every((grant) => grant.status === "skipped" && grant.duplicate), true);
  assert.equal(requests.length, 0);
  assert.equal(state.insertCalls.length, 0);
  assert.equal(state.updateCalls.length, 0);
});

test("grantInviteRewardsForUser marks failed grant when quota adjustment fails", async () => {
  const state = freshState({
    relations: [{ user_id: "u2", inviter_user_id: "u1", invite_code: "SC-AAAA-BBBB" }],
  });
  const requests: QuotaAdjustmentRequest[] = [];
  const adjuster: QuotaAdjuster = async (request) => {
    requests.push(request);
    if (request.user_id === "u1") {
      return { ok: false, status: 502, payload: { error: "backend down" }, error: "backend down" };
    }
    return { ok: true, status: 200, payload: {} };
  };

  const result = await grantInviteRewardsForUser({ user_id: "u2" }, makeSupabase(state), adjuster);

  assert.equal(result.skipped, false);
  assert.equal(requests.length, 2);
  const failed = result.grants.find((grant) => grant.reward_target === "inviter");
  assert.equal(failed?.status, "failed");
  assert.equal(failed?.error, "backend down");
  assert.equal(
    state.grants.find((row) => row.reward_target === "inviter")?.status,
    "failed",
  );
});
