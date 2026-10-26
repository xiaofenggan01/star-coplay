import { test } from "node:test";
import assert from "node:assert/strict";
import { getAdminUserInviteSummaries } from "../admin-user-invite-summary";

interface MockState {
  configs: Record<string, unknown>[];
  inviteCodes: Record<string, unknown>[];
  relations: Record<string, unknown>[];
  users: Record<string, unknown>[];
  profiles: Record<string, unknown>[];
}

function freshState(overrides: Partial<MockState> = {}): MockState {
  return {
    configs: [
      {
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
        created_at: "2026-07-01T00:00:00.000Z",
        updated_at: "2026-07-01T00:00:00.000Z",
      },
    ],
    inviteCodes: [],
    relations: [],
    users: [],
    profiles: [],
    ...overrides,
  };
}

function tableRows(state: MockState, table: string): Record<string, unknown>[] {
  if (table === "system_configs") return state.configs;
  if (table === "invite_codes") return state.inviteCodes;
  if (table === "user_relations") return state.relations;
  if (table === "users") return state.users;
  if (table === "profiles") return state.profiles;
  throw new Error(`unexpected table=${table}`);
}

function matches(row: Record<string, unknown>, eqFilters: Array<[string, unknown]>, inFilters: Array<[string, unknown[]]>): boolean {
  return (
    eqFilters.every(([key, value]) => row[key] === value) &&
    inFilters.every(([key, values]) => values.includes(row[key]))
  );
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      const eqFilters: Array<[string, unknown]> = [];
      const inFilters: Array<[string, unknown[]]> = [];
      const query: any = {
        select() {
          return query;
        },
        eq(key: string, value: unknown) {
          eqFilters.push([key, value]);
          return query;
        },
        in(key: string, values: unknown[]) {
          inFilters.push([key, values]);
          return query;
        },
        order() {
          return query;
        },
        maybeSingle() {
          const rows = tableRows(state, table).filter((row) => matches(row, eqFilters, inFilters));
          return Promise.resolve({ data: rows[0] ?? null, error: null });
        },
        then(resolve: (value: { data: Record<string, unknown>[]; error: null }) => unknown, reject: (reason: unknown) => unknown) {
          const data = tableRows(state, table).filter((row) => matches(row, eqFilters, inFilters));
          return Promise.resolve({ data, error: null }).then(resolve, reject);
        },
      };
      return query;
    },
  };
}

test("getAdminUserInviteSummaries returns personal user invite code and invited emails", async () => {
  const state = freshState({
    inviteCodes: [
      {
        code: "SC-OWNER",
        type: "user",
        owner_user_id: "owner-1",
        used_count: 8,
        max_uses: 20,
        disabled: false,
        created_at: "2026-07-01T00:00:00.000Z",
      },
    ],
    relations: [
      {
        user_id: "invitee-1",
        inviter_user_id: "owner-1",
        invite_code: "SC-OWNER",
        created_at: "2026-07-02T00:00:00.000Z",
      },
    ],
    users: [
      {
        user_id: "invitee-1",
        email: "invitee1@example.test",
        nickname: "Legacy Invitee",
        invite_code_used: "SC-OWNER",
        created_at: "2026-07-01T12:00:00.000Z",
      },
      {
        user_id: "invitee-2",
        email: "invitee2@example.test",
        nickname: "Direct Invitee",
        invite_code_used: "SC-OWNER",
        created_at: "2026-07-01T13:00:00.000Z",
      },
    ],
    profiles: [
      { user_id: "invitee-1", nickname: "Profile Invitee" },
    ],
  });

  const result = await getAdminUserInviteSummaries(makeSupabase(state), ["owner-1"]);

  assert.equal(result["owner-1"].codes.length, 1);
  assert.equal(result["owner-1"].codes[0].code, "SC-OWNER");
  assert.equal(result["owner-1"].codes[0].used_count, 8);
  assert.equal(result["owner-1"].codes[0].usage_limit, 3);
  assert.equal(result["owner-1"].codes[0].remaining_uses, 0);
  assert.equal(result["owner-1"].invited_user_count, 2);
  assert.deepEqual(
    result["owner-1"].invited_users.map((user) => ({ user_id: user.user_id, email: user.email, nickname: user.nickname })),
    [
      { user_id: "invitee-1", email: "invitee1@example.test", nickname: "Profile Invitee" },
      { user_id: "invitee-2", email: "invitee2@example.test", nickname: "Direct Invitee" },
    ],
  );
});

test("getAdminUserInviteSummaries resolves user code limit from invite policy when max_uses is null", async () => {
  const state = freshState({
    inviteCodes: [
      {
        code: "SC-OWNER",
        type: "user",
        owner_user_id: "owner-1",
        used_count: 3,
        max_uses: null,
        disabled: false,
        created_at: "2026-07-01T00:00:00.000Z",
      },
    ],
  });

  const result = await getAdminUserInviteSummaries(makeSupabase(state), ["owner-1"]);

  assert.equal(result["owner-1"].codes[0].max_uses, null);
  assert.equal(result["owner-1"].codes[0].usage_limit, 3);
  assert.equal(result["owner-1"].codes[0].remaining_uses, 0);
});

test("getAdminUserInviteSummaries returns empty summary for users without personal code", async () => {
  const result = await getAdminUserInviteSummaries(makeSupabase(freshState()), ["owner-empty"]);

  assert.deepEqual(result["owner-empty"], {
    codes: [],
    invited_user_count: 0,
    invited_users: [],
  });
});
