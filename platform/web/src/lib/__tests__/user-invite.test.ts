import { test } from "node:test";
import assert from "node:assert/strict";
import { generateUserInviteBatch, getUserInviteStatus } from "../user-invite";

interface MockState {
  configRows: Record<string, unknown>[];
  inviteCodes: Record<string, unknown>[];
  userRelations: Record<string, unknown>[];
  rewardGrants: Record<string, unknown>[];
  users: Record<string, unknown>[];
  profiles: Record<string, unknown>[];
  insertCalls: Array<{
    table: string;
    payload: Record<string, unknown>;
  }>;
  selectCalls: Array<{
    table: string;
    filters: Array<[string, unknown]>;
    inFilters: Array<[string, unknown[]]>;
    orderBy: string | null;
  }>;
}

function freshState(overrides: Partial<MockState> = {}): MockState {
  return {
    configRows: [
      {
        id: "config-1",
        domain: "invite",
        config_key: "policy",
        environment: "prod",
        status: 1,
        value_type: 5,
        config_value: JSON.stringify({
          enabled: true,
          per_user_limit: 3,
          invitee_bonus_credits: 20,
          inviter_bonus_credits: 20,
          auto_issue_user_invite_code: true,
          user_invite_code_format: { kind: "readable", prefix: "SC", random_length: 8 },
        }),
        description: "",
        created_at: "2026-06-01T00:00:00.000Z",
        updated_at: "2026-06-01T00:00:00.000Z",
      },
    ],
    inviteCodes: [],
    userRelations: [],
    rewardGrants: [],
    users: [],
    profiles: [],
    insertCalls: [],
    selectCalls: [],
    ...overrides,
  };
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      const filters: Array<[string, unknown]> = [];
      const inFilters: Array<[string, unknown[]]> = [];
      let orderBy: string | null = null;
      const query: any = {
        select() {
          return query;
        },
        eq(key: string, value: unknown) {
          filters.push([key, value]);
          return query;
        },
        in(key: string, values: unknown[]) {
          inFilters.push([key, values]);
          return query;
        },
        order(key: string) {
          orderBy = key;
          return query;
        },
        async maybeSingle() {
          state.selectCalls.push({ table, filters: [...filters], inFilters: [...inFilters], orderBy });
          if (table !== "invite_codes") {
            throw new Error(`unexpected maybeSingle table=${table}`);
          }
          const row =
            state.inviteCodes.find((candidate) =>
              filters.every(([key, value]) => candidate[key] === value),
            ) ?? null;
          return { data: row, error: null };
        },
        insert(payload: Record<string, unknown>) {
          state.insertCalls.push({ table, payload });
          if (table !== "invite_codes") {
            return Promise.resolve({ error: { message: `unexpected insert table=${table}` } });
          }
          state.inviteCodes.push({
            disabled: false,
            created_at: "2026-06-03T00:00:00.000Z",
            ...payload,
          });
          return Promise.resolve({ error: null });
        },
        then(resolve: (value: { data: unknown[]; error: null }) => unknown, reject: (reason: unknown) => unknown) {
          state.selectCalls.push({ table, filters: [...filters], inFilters: [...inFilters], orderBy });
          try {
            const rows = rowsForTable(state, table)
              .filter((row) => filters.every(([key, value]) => row[key] === value))
              .filter((row) => inFilters.every(([key, values]) => values.includes(row[key])));
            return Promise.resolve({ data: rows, error: null }).then(resolve, reject);
          } catch (error) {
            return Promise.reject(error).then(resolve, reject);
          }
        },
      };
      return query;
    },
  };
}

function rowsForTable(state: MockState, table: string): Record<string, unknown>[] {
  if (table === "system_configs") return state.configRows;
  if (table === "invite_codes") return state.inviteCodes;
  if (table === "user_relations") return state.userRelations;
  if (table === "invite_reward_grants") return state.rewardGrants;
  if (table === "users") return state.users;
  if (table === "profiles") return state.profiles;
  throw new Error(`unexpected table=${table}`);
}

test("getUserInviteStatus returns invite code and referral reward status", async () => {
  const state = freshState({
    inviteCodes: [
      {
        code: "SC-AAAA-BBBB",
        type: "user",
        owner_user_id: "u-inviter",
        disabled: false,
        used_count: 1,
        max_uses: null,
        created_at: "2026-06-01T00:00:00.000Z",
      },
    ],
    userRelations: [
      {
        user_id: "u-referee",
        inviter_user_id: "u-inviter",
        invite_code: "SC-AAAA-BBBB",
        created_at: "2026-06-02T00:00:00.000Z",
      },
    ],
    rewardGrants: [
      {
        invitee_user_id: "u-referee",
        inviter_user_id: "u-inviter",
        reward_target: "inviter",
        status: "succeeded",
      },
      {
        invitee_user_id: "u-referee",
        inviter_user_id: "u-inviter",
        reward_target: "invitee",
        status: "failed",
      },
    ],
    users: [
      {
        user_id: "u-referee",
        email: "referee@example.test",
        nickname: "Referee",
      },
    ],
    profiles: [
      {
        user_id: "u-referee",
        nickname: "Profile Referee",
      },
    ],
  });

  const status = await getUserInviteStatus("u-inviter", makeSupabase(state));

  assert.equal(status.totalCodes, 1);
  assert.equal(status.canGenerate, false);
  assert.equal(status.remainingUses, 2);
  assert.deepEqual(status.codes, [
    {
      code: "SC-AAAA-BBBB",
      used_count: 1,
      max_uses: null,
      usage_limit: 3,
      remaining_uses: 2,
      is_exhausted: false,
      batch_number: null,
    },
  ]);
  assert.deepEqual(status.referrals, [
    {
      id: "u-referee",
      referee_user_id: "u-referee",
      referee_email: "referee@example.test",
      referee_nickname: "Profile Referee",
      invite_code: "SC-AAAA-BBBB",
      reward_status: "succeeded",
      inviter_reward_status: "succeeded",
      invitee_reward_status: "failed",
      created_at: "2026-06-02T00:00:00.000Z",
    },
  ]);
});

test("getUserInviteStatus allows explicit generation when user has no invite code", async () => {
  const state = freshState();

  const status = await getUserInviteStatus("u-inviter", makeSupabase(state));

  assert.equal(status.totalCodes, 0);
  assert.equal(status.remainingUses, 0);
  assert.equal(status.canGenerate, true);
  assert.deepEqual(status.codes, []);
});

test("generateUserInviteBatch creates a personal user code with policy default limit", async () => {
  const state = freshState();

  const result = await generateUserInviteBatch("u-inviter", makeSupabase(state));
  const status = await getUserInviteStatus("u-inviter", makeSupabase(state));

  assert.equal(result.ok, true);
  assert.match(result.ok ? result.invite_code ?? "" : "", /^SC-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/);
  assert.equal(state.insertCalls.length, 1);
  assert.equal(state.insertCalls[0].payload.type, "user");
  assert.equal(state.insertCalls[0].payload.owner_user_id, "u-inviter");
  assert.equal(state.insertCalls[0].payload.max_uses, null);
  assert.equal(state.insertCalls[0].payload.used_count, 0);
  assert.equal(status.canGenerate, false);
  assert.equal(status.codes[0].usage_limit, 3);
  assert.equal(status.codes[0].remaining_uses, 3);
});

test("getUserInviteStatus marks user invite code exhausted when policy limit is reached", async () => {
  const state = freshState({
    configRows: [
      {
        ...freshState().configRows[0],
        config_value: JSON.stringify({
          enabled: true,
          per_user_limit: 2,
          invitee_bonus_credits: 20,
          inviter_bonus_credits: 20,
          auto_issue_user_invite_code: true,
          user_invite_code_format: { kind: "readable", prefix: "SC", random_length: 8 },
        }),
      },
    ],
    inviteCodes: [
      {
        code: "SC-FULL-CODE",
        type: "user",
        owner_user_id: "u-inviter",
        disabled: false,
        used_count: 2,
        max_uses: null,
        created_at: "2026-06-01T00:00:00.000Z",
      },
    ],
  });

  const status = await getUserInviteStatus("u-inviter", makeSupabase(state));

  assert.equal(status.remainingUses, 0);
  assert.equal(status.usedCodes, 1);
  assert.equal(status.codes[0].usage_limit, 2);
  assert.equal(status.codes[0].remaining_uses, 0);
  assert.equal(status.codes[0].is_exhausted, true);
  assert.equal(status.codes[0].code, "");
});

test("getUserInviteStatus marks referral rewards pending when ledger rows are absent", async () => {
  const state = freshState({
    userRelations: [
      {
        user_id: "u-referee",
        inviter_user_id: "u-inviter",
        invite_code: "SC-AAAA-BBBB",
        created_at: "2026-06-02T00:00:00.000Z",
      },
    ],
  });

  const status = await getUserInviteStatus("u-inviter", makeSupabase(state));

  assert.deepEqual(status.referrals, [
    {
      id: "u-referee",
      referee_user_id: "u-referee",
      referee_email: null,
      referee_nickname: null,
      invite_code: "SC-AAAA-BBBB",
      reward_status: "pending",
      inviter_reward_status: "pending",
      invitee_reward_status: "pending",
      created_at: "2026-06-02T00:00:00.000Z",
    },
  ]);
});
