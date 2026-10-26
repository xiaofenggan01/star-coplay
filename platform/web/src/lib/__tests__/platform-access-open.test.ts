import { test } from "node:test";
import assert from "node:assert/strict";
import {
  defaultInvitePolicy,
  ensureUserInviteCode,
  generateReadableInviteCode,
  openPlatformAccess,
  readInvitePolicy,
  type OpenPlatformAccessDeps,
} from "../platform-access-open";

interface MockConfigRow {
  id: string;
  domain: string;
  config_key: string;
  environment: string;
  status: number;
  value_type: number;
  config_value: string;
  description: string;
  created_at: string;
  updated_at: string;
}

interface InsertScript {
  error: { code?: string; message: string } | null;
  addRow?: Record<string, unknown>;
}

interface MockState {
  configRows: MockConfigRow[];
  inviteRows: Record<string, unknown>[];
  insertScripts: InsertScript[];
  selectCalls: { table: string; filters: Array<[string, unknown]> }[];
  insertCalls: { table: string; payload: Record<string, unknown> }[];
  upsertCalls: { table: string; payload: Record<string, unknown>; options?: Record<string, unknown> }[];
}

function makeConfigRow(configValue: unknown): MockConfigRow {
  return {
    id: "config-1",
    domain: "invite",
    config_key: "policy",
    environment: "prod",
    status: 1,
    value_type: 5,
    config_value: JSON.stringify(configValue),
    description: "",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function freshState(overrides: Partial<MockState> = {}): MockState {
  return {
    configRows: [makeConfigRow(defaultInvitePolicy())],
    inviteRows: [],
    insertScripts: [],
    selectCalls: [],
    insertCalls: [],
    upsertCalls: [],
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
        async maybeSingle() {
          state.selectCalls.push({ table, filters: [...filters] });
          if (table !== "invite_codes") {
            throw new Error(`unexpected maybeSingle table=${table}`);
          }
          const row =
            state.inviteRows.find((candidate) =>
              filters.every(([key, value]) => candidate[key] === value),
            ) ?? null;
          return { data: row, error: null };
        },
        insert(payload: Record<string, unknown>) {
          state.insertCalls.push({ table, payload });
          if (table !== "invite_codes") {
            return Promise.resolve({ error: { message: `unexpected insert table=${table}` } });
          }
          const script = state.insertScripts.shift() ?? { error: null };
          if (script.addRow) state.inviteRows.push(script.addRow);
          if (!script.error) state.inviteRows.push(payload);
          return Promise.resolve({ error: script.error });
        },
        upsert(payload: Record<string, unknown>, options?: Record<string, unknown>) {
          state.upsertCalls.push({ table, payload, options });
          return Promise.resolve({ error: null });
        },
        then(resolve: (value: unknown) => unknown, reject: (reason: unknown) => unknown) {
          if (table !== "system_configs") {
            return Promise.reject(new Error(`unexpected awaited table=${table}`)).then(resolve, reject);
          }
          const rows = state.configRows.filter((row) =>
            filters.every(([key, value]) => (row as unknown as Record<string, unknown>)[key] === value),
          );
          return Promise.resolve({ data: rows, error: null }).then(resolve, reject);
        },
      };
      return query;
    },
  };
}

function makeOpenDeps(overrides: Partial<OpenPlatformAccessDeps> = {}): {
  deps: OpenPlatformAccessDeps;
  calls: {
    relay: unknown[];
    relation: unknown[];
    invite: unknown[];
    rewards: unknown[];
  };
} {
  const calls = {
    relay: [] as unknown[],
    relation: [] as unknown[],
    invite: [] as unknown[],
    rewards: [] as unknown[],
  };
  const deps: OpenPlatformAccessDeps = {
    async ensureAIRelayAccount(input) {
      calls.relay.push(input);
      return { ok: true, status: 200 };
    },
    async ensureUserInviteCode(userId) {
      calls.invite.push(userId);
      return "SC-INVITE";
    },
    async ensureUserRelation(input) {
      calls.relation.push(input);
    },
    async grantInviteRewardsForUser(input) {
      calls.rewards.push(input);
      return { skipped: true, reason: "no_inviter", grants: [] };
    },
    ...overrides,
  };
  return { deps, calls };
}

test("generateReadableInviteCode returns uppercase grouped readable code", () => {
  const code = generateReadableInviteCode("sc", 8);
  assert.match(code, /^SC-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/);
});

test("readInvitePolicy normalizes invalid format fields to safe defaults", async () => {
  const state = freshState({
    configRows: [
      makeConfigRow({
        enabled: true,
        auto_issue_user_invite_code: true,
        user_invite_code_format: {
          kind: "readable",
          prefix: " vip ",
          random_length: 99,
        },
      }),
    ],
  });

  const policy = await readInvitePolicy(makeSupabase(state));

  assert.equal(policy.user_invite_code_format.prefix, "VIP");
  assert.equal(policy.user_invite_code_format.random_length, 8);
});

test("ensureUserInviteCode returns null when automatic user codes are disabled", async () => {
  const state = freshState({
    configRows: [
      makeConfigRow({
        ...defaultInvitePolicy(),
        auto_issue_user_invite_code: false,
      }),
    ],
  });

  const code = await ensureUserInviteCode("user-1", makeSupabase(state));

  assert.equal(code, null);
  assert.equal(state.insertCalls.length, 0);
});

test("ensureUserInviteCode returns null when invite policy config is disabled", async () => {
  const disabledRow = makeConfigRow(defaultInvitePolicy());
  disabledRow.status = 0;
  const state = freshState({ configRows: [disabledRow] });

  const code = await ensureUserInviteCode("user-1", makeSupabase(state));

  assert.equal(code, null);
  assert.equal(state.insertCalls.length, 0);
});

test("ensureUserInviteCode reuses existing user invite code", async () => {
  const state = freshState({
    inviteRows: [{ code: "SC-EXISTING", type: "user", owner_user_id: "user-1" }],
  });

  const code = await ensureUserInviteCode("user-1", makeSupabase(state));

  assert.equal(code, "SC-EXISTING");
  assert.equal(state.insertCalls.length, 0);
});

test("ensureUserInviteCode inserts long-term user invite code", async () => {
  const state = freshState();

  const code = await ensureUserInviteCode("user-1", makeSupabase(state));

  assert.match(code ?? "", /^SC-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/);
  assert.equal(state.insertCalls.length, 1);
  assert.equal(state.insertCalls[0].payload.type, "user");
  assert.equal(state.insertCalls[0].payload.owner_user_id, "user-1");
  assert.equal(state.insertCalls[0].payload.max_uses, null);
  assert.equal(state.insertCalls[0].payload.used_count, 0);
});

test("ensureUserInviteCode rereads owner code after unique conflict", async () => {
  const state = freshState({
    insertScripts: [
      {
        error: { code: "23505", message: "duplicate key value violates unique constraint" },
        addRow: { code: "SC-RACE-WIN", type: "user", owner_user_id: "user-1" },
      },
    ],
  });

  const code = await ensureUserInviteCode("user-1", makeSupabase(state));

  assert.equal(code, "SC-RACE-WIN");
  assert.equal(state.insertCalls.length, 1);
});

test("openPlatformAccess fails closed when AI Relay ensure fails", async () => {
  const state = freshState();
  const { deps, calls } = makeOpenDeps({
    async ensureAIRelayAccount(input) {
      calls.relay.push(input);
      return { ok: false, status: 502, error: "relay unavailable" };
    },
  });

  await assert.rejects(
    openPlatformAccess(
      { user_id: "user-1", email: "u1@example.test", waitlist_id: 1 },
      makeSupabase(state),
      deps,
    ),
    /AI Relay ensure failed status=502: relay unavailable/,
  );

  assert.equal(calls.relay.length, 1);
  assert.equal(state.upsertCalls.length, 0);
  assert.equal(calls.relation.length, 0);
  assert.equal(calls.invite.length, 0);
  assert.equal(calls.rewards.length, 0);
});

test("openPlatformAccess fails closed when AI Relay ensure fails", async () => {
  const state = freshState();
  const { deps, calls } = makeOpenDeps({
    async ensureAIRelayAccount(input) {
      calls.relay.push(input);
      return { ok: false, status: 500, error: "relay down" };
    },
  });

  await assert.rejects(
    openPlatformAccess(
      { user_id: "user-2", email: "u2@example.test" },
      makeSupabase(state),
      deps,
    ),
    /AI Relay ensure failed status=500: relay down/,
  );

  assert.equal(calls.relay.length, 1);
});

test("openPlatformAccess upgrades profile only after AI Relay is ready", async () => {
  const state = freshState();
  const { deps, calls } = makeOpenDeps();

  const result = await openPlatformAccess(
    { user_id: "user-1", email: "u1@example.test", waitlist_id: 7 },
    makeSupabase(state),
    deps,
  );

  assert.equal(result.user_type, "beta_user");
  assert.equal(result.ai_relay.ok, true);
  assert.equal(result.invite_code, "SC-INVITE");
  assert.equal(state.upsertCalls.length, 1);
  assert.deepEqual(state.upsertCalls[0], {
    table: "profiles",
    payload: { user_id: "user-1", user_type: "beta_user", waitlist_id: 7 },
    options: { onConflict: "user_id" },
  });
  assert.equal(calls.relay.length, 1);
  assert.equal(calls.relation.length, 1);
  assert.deepEqual(calls.relation[0], {
    user_id: "user-1",
    inviter_user_id: undefined,
    invite_code: undefined,
  });
  assert.equal(calls.invite.length, 1);
  assert.equal(calls.rewards.length, 1);
});

test("openPlatformAccess preserves inviter relation from invite activation", async () => {
  const state = freshState();
  const { deps, calls } = makeOpenDeps();

  const result = await openPlatformAccess(
    {
      user_id: "invitee-1",
      email: "invitee@example.test",
      waitlist_id: 9,
      inviter_user_id: "inviter-1",
      invite_code: "SC-AAAA-BBBB",
      source: "activate_new_user",
    },
    makeSupabase(state),
    deps,
  );

  assert.equal(result.user_type, "beta_user");
  assert.deepEqual(calls.relation[0], {
    user_id: "invitee-1",
    inviter_user_id: "inviter-1",
    invite_code: "SC-AAAA-BBBB",
  });
  assert.deepEqual(calls.rewards[0], { user_id: "invitee-1" });
});

test("openPlatformAccess can persist official invite note when opening beta access", async () => {
  const state = freshState();
  const { deps } = makeOpenDeps();

  await openPlatformAccess(
    {
      user_id: "official-user-1",
      email: "official@example.test",
      invite_code: "OFFICIAL-1234",
      official_invite_note: "campaign-0701",
      source: "register_with_official_invite_code",
    },
    makeSupabase(state),
    deps,
  );

  assert.deepEqual(state.upsertCalls[0], {
    table: "profiles",
    payload: {
      user_id: "official-user-1",
      user_type: "beta_user",
      official_invite_note: "campaign-0701",
    },
    options: { onConflict: "user_id" },
  });
});
