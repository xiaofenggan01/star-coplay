import { test } from "node:test";
import assert from "node:assert/strict";
import {
  handleAdminUserUpgradeToBeta,
  type AdminUpgradeDeps,
} from "../admin-user-upgrade";

interface MockState {
  users: Record<string, unknown>[];
  profiles: Record<string, unknown>[];
  waitlist: Record<string, unknown>[];
  inviteCodes: Record<string, unknown>[];
  events: Record<string, unknown>[];
  profileUpdates: Record<string, unknown>[];
  waitlistUpdates: Record<string, unknown>[];
}

function freshState(overrides: Partial<MockState> = {}): MockState {
  return {
    users: [],
    profiles: [],
    waitlist: [],
    inviteCodes: [],
    events: [],
    profileUpdates: [],
    waitlistUpdates: [],
    ...overrides,
  };
}

function matchesFilters(row: Record<string, unknown>, filters: Array<[string, unknown]>, inFilters: Array<[string, unknown[]]>): boolean {
  return (
    filters.every(([key, value]) => row[key] === value) &&
    inFilters.every(([key, values]) => values.includes(row[key]))
  );
}

function tableRows(state: MockState, table: string): Record<string, unknown>[] {
  if (table === "users") return state.users;
  if (table === "profiles") return state.profiles;
  if (table === "waitlist") return state.waitlist;
  if (table === "invite_codes") return state.inviteCodes;
  if (table === "events") return state.events;
  throw new Error(`unexpected table=${table}`);
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      const filters: Array<[string, unknown]> = [];
      const inFilters: Array<[string, unknown[]]> = [];
      let updatePayload: Record<string, unknown> | null = null;
      let insertPayload: Record<string, unknown> | null = null;

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
        order() {
          return query;
        },
        limit() {
          return query;
        },
        insert(payload: Record<string, unknown>) {
          insertPayload = payload;
          if (table === "profiles") {
            if (state.profiles.some((row) => row.user_id === payload.user_id)) {
              return Promise.resolve({ error: { code: "23505", message: "duplicate key" } });
            }
            state.profiles.push({ ...payload });
            return Promise.resolve({ error: null });
          }
          if (table === "events") {
            state.events.push({ ...payload });
            return Promise.resolve({ error: null });
          }
          return Promise.resolve({ error: null });
        },
        update(payload: Record<string, unknown>) {
          updatePayload = payload;
          return query;
        },
        async maybeSingle() {
          if (insertPayload) return { data: insertPayload, error: null };
          if (updatePayload) {
            const row = tableRows(state, table).find((candidate) =>
              matchesFilters(candidate, filters, inFilters),
            );
            if (!row) return { data: null, error: null };
            Object.assign(row, updatePayload);
            if (table === "profiles") state.profileUpdates.push({ ...updatePayload });
            if (table === "waitlist") state.waitlistUpdates.push({ ...updatePayload });
            return { data: row, error: null };
          }
          const row = tableRows(state, table).find((candidate) =>
            matchesFilters(candidate, filters, inFilters),
          );
          return { data: row ?? null, error: null };
        },
        then(resolve: (value: { data?: unknown; error: null }) => unknown, reject: (reason: unknown) => unknown) {
          if (!updatePayload) return Promise.resolve({ data: null, error: null }).then(resolve, reject);
          const row = tableRows(state, table).find((candidate) =>
            matchesFilters(candidate, filters, inFilters),
          );
          if (row) {
            Object.assign(row, updatePayload);
            if (table === "waitlist") state.waitlistUpdates.push({ ...updatePayload });
            if (table === "profiles") state.profileUpdates.push({ ...updatePayload });
          }
          return Promise.resolve({ data: row ?? null, error: null }).then(resolve, reject);
        },
      };
      return query;
    },
  };
}

function makeDeps(state: MockState, overrides: Partial<AdminUpgradeDeps> = {}): {
  deps: AdminUpgradeDeps;
  calls: {
    relay: unknown[];
    rootRelation: unknown[];
    userRelation: unknown[];
    officialRelation: unknown[];
    invite: string[];
    rewards: unknown[];
  };
} {
  const calls = {
    relay: [] as unknown[],
    rootRelation: [] as unknown[],
    userRelation: [] as unknown[],
    officialRelation: [] as unknown[],
    invite: [] as string[],
    rewards: [] as unknown[],
  };
  const deps: AdminUpgradeDeps = {
    supabase: makeSupabase(state),
    now: () => new Date("2026-07-01T00:00:00.000Z"),
    async ensureAIRelayAccount(input) {
      calls.relay.push(input);
      return { ok: true, status: 200 };
    },
    async ensureUserRelation(input) {
      calls.rootRelation.push(input);
    },
    async attachUserInviteRelation(input) {
      calls.userRelation.push(input);
      return { ok: true, status: "inserted" };
    },
    async attachOfficialInviteRelation(input) {
      calls.officialRelation.push(input);
      return { ok: true, status: "inserted" };
    },
    async ensureUserInviteCode(userId) {
      calls.invite.push(userId);
      return "SC-OWN-CODE";
    },
    async grantInviteRewardsForUser(input) {
      calls.rewards.push(input);
      return { skipped: true, reason: "no_inviter", grants: [] };
    },
    ...overrides,
  };
  return { deps, calls };
}

test("admin upgrade opens resources, attributes user invite, and marks waitlist registered", async () => {
  const state = freshState({
    users: [{ user_id: "u1", email: "guest@example.test", nickname: "Guest", invite_code_used: "SC-INVITER" }],
    profiles: [{ user_id: "u1", user_type: "guest", waitlist_id: null, nickname: "Guest Profile" }],
    waitlist: [{ id: 7, email: "guest@example.test", status: "approved" }],
    inviteCodes: [{ code: "SC-INVITER", type: "user", owner_user_id: "inviter-1", official_code_reason: null }],
  });
  const { deps, calls } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u1",
    reason: "manual beta approval",
  });

  assert.equal(result.status, 200);
  assert.equal(result.body.ok, true);
  if (result.body.ok) {
    assert.equal(result.body.user_type, "beta_user");
    assert.equal(result.body.waitlist_id, 7);
    assert.equal(result.body.waitlist_status_after, "registered");
    assert.deepEqual(result.body.invite_attribution, {
      invite_code: "SC-INVITER",
      invite_type: "user",
      inviter_user_id: "inviter-1",
      relation_status: "inserted",
    });
  }
  assert.equal(state.profiles[0].user_type, "beta_user");
  assert.equal(state.profiles[0].waitlist_id, 7);
  assert.equal(state.waitlist[0].status, "registered");
  assert.equal(calls.relay.length, 1);
  assert.equal(calls.userRelation.length, 1);
  assert.deepEqual(calls.invite, ["u1"]);
  assert.equal(state.events.length, 1);
});

test("admin upgrade is reentrant for existing beta users so partial upgrades can be repaired", async () => {
  const state = freshState({
    users: [{ user_id: "u2", email: "beta@example.test", nickname: "Beta", invite_code_used: "" }],
    profiles: [{ user_id: "u2", user_type: "beta_user", waitlist_id: null, nickname: "Beta" }],
  });
  const { deps, calls } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u2",
    reason: "repair partial opening",
  });

  assert.equal(result.status, 200);
  assert.equal(result.body.ok, true);
  if (result.body.ok) {
    assert.equal(result.body.repaired_existing_beta, true);
  }
  assert.equal(calls.relay.length, 1);
  assert.equal(calls.rootRelation.length, 1);
  assert.deepEqual(calls.invite, ["u2"]);
});

test("admin upgrade rejects user/member types without ensuring relay", async () => {
  const state = freshState({
    users: [{ user_id: "u3", email: "member@example.test", nickname: "Member", invite_code_used: "" }],
    profiles: [{ user_id: "u3", user_type: "member", waitlist_id: null, nickname: "Member" }],
  });
  const { deps, calls } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u3",
    reason: "bad request",
  });

  assert.equal(result.status, 409);
  assert.equal(result.body.reason, "unsupported_user_type");
  assert.equal(calls.relay.length, 0);
  assert.equal(state.profileUpdates.length, 0);
  assert.equal(state.events.length, 1);
  assert.equal((state.events[0].data as Record<string, unknown>).ok, false);
  assert.equal((state.events[0].data as Record<string, unknown>).stage, "read_profile");
});

test("admin upgrade rejects mismatched explicit waitlist id before side effects", async () => {
  const state = freshState({
    users: [{ user_id: "u4", email: "target@example.test", nickname: "Target", invite_code_used: "" }],
    profiles: [{ user_id: "u4", user_type: "guest", waitlist_id: null, nickname: "Target" }],
    waitlist: [{ id: 8, email: "other@example.test", status: "approved" }],
  });
  const { deps, calls } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u4",
    waitlist_id: 8,
    reason: "manual beta approval",
  });

  assert.equal(result.status, 409);
  assert.equal(result.body.reason, "waitlist_email_mismatch");
  assert.equal(calls.relay.length, 0);
  assert.equal(state.profileUpdates.length, 0);
  assert.equal(state.events.length, 1);
  assert.equal((state.events[0].data as Record<string, unknown>).stage, "validate_waitlist");
});

test("admin upgrade rejects explicit waitlist id with unverifiable email before side effects", async () => {
  const state = freshState({
    users: [{ user_id: "u6", email: "target@example.test", nickname: "Target", invite_code_used: "" }],
    profiles: [{ user_id: "u6", user_type: "guest", waitlist_id: null, nickname: "Target" }],
    waitlist: [{ id: 9, email: null, status: "approved" }],
  });
  const { deps, calls } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u6",
    waitlist_id: 9,
    reason: "manual beta approval",
  });

  assert.equal(result.status, 409);
  assert.equal(result.body.reason, "waitlist_unverifiable");
  assert.equal(calls.relay.length, 0);
  assert.equal(state.profileUpdates.length, 0);
  assert.equal(state.events.length, 1);
  assert.equal((state.events[0].data as Record<string, unknown>).stage, "validate_waitlist");
});

test("admin upgrade ignores raw invite_code override and uses persisted user attribution", async () => {
  const state = freshState({
    users: [{ user_id: "u7", email: "guest@example.test", nickname: "Guest", invite_code_used: "" }],
    profiles: [{ user_id: "u7", user_type: "guest", waitlist_id: null, nickname: "Guest" }],
    inviteCodes: [{ code: "SC-ATTACKER", type: "user", owner_user_id: "attacker", official_code_reason: null }],
  });
  const { deps, calls } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u7",
    invite_code: "SC-ATTACKER",
    reason: "manual beta approval",
  });

  assert.equal(result.status, 200);
  assert.equal(result.body.ok, true);
  if (result.body.ok) {
    assert.equal(result.body.invite_attribution.invite_code, null);
    assert.equal(result.body.invite_attribution.relation_status, "root");
  }
  assert.equal(calls.userRelation.length, 0);
  assert.equal(calls.rootRelation.length, 1);
});

test("admin upgrade does not write beta profile when invite relation binding fails", async () => {
  const state = freshState({
    users: [{ user_id: "u8", email: "guest@example.test", nickname: "Guest", invite_code_used: "SC-INVITER" }],
    profiles: [{ user_id: "u8", user_type: "guest", waitlist_id: null, nickname: "Guest" }],
    inviteCodes: [{ code: "SC-INVITER", type: "user", owner_user_id: "inviter-1", official_code_reason: null }],
  });
  const { deps } = makeDeps(state, {
    async attachUserInviteRelation() {
      return { ok: false, reason: "relation_already_bound" };
    },
  });

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u8",
    reason: "manual beta approval",
  });

  assert.equal(result.status, 500);
  assert.equal(result.body.reason, "internal_error");
  assert.equal(result.body.ok, false);
  if (result.body.ok === false) {
    assert.equal(result.body.stage, "write_relation");
  }
  assert.equal(state.profiles[0].user_type, "guest");
  assert.equal(state.profileUpdates.length, 0);
  assert.equal(state.events.length, 1);
  assert.equal((state.events[0].data as Record<string, unknown>).stage, "write_relation");
});

test("admin upgrade does not write profile when AI Relay ensure fails", async () => {
  const state = freshState({
    users: [{ user_id: "u5", email: "guest@example.test", nickname: "Guest", invite_code_used: "" }],
    profiles: [{ user_id: "u5", user_type: "guest", waitlist_id: null, nickname: "Guest" }],
  });
  const { deps } = makeDeps(state, {
    async ensureAIRelayAccount() {
      return { ok: false, status: 502, error: "relay down" };
    },
  });

  const result = await handleAdminUserUpgradeToBeta(deps, {
    user_id: "u5",
    reason: "manual beta approval",
  });

  assert.equal(result.status, 503);
  assert.equal(result.body.reason, "ai_relay_failed");
  assert.equal(state.profiles[0].user_type, "guest");
  assert.equal(state.profileUpdates.length, 0);
  assert.equal(state.events.length, 1);
  assert.equal((state.events[0].data as Record<string, unknown>).stage, "ensure_ai_relay");
});

test("admin upgrade requires an audit reason", async () => {
  const state = freshState();
  const { deps } = makeDeps(state);

  const result = await handleAdminUserUpgradeToBeta(deps, { user_id: "u1" });

  assert.equal(result.status, 422);
  assert.equal(result.body.reason, "missing_reason");
});
