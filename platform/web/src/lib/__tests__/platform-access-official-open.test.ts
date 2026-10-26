import { test } from "node:test";
import assert from "node:assert/strict";
import {
  openPlatformAccessByOfficialInvite,
  type OpenPlatformAccessByOfficialInviteDeps,
} from "../platform-access-official-open";
import type { InviteCode } from "../types/invite-code";

interface MockState {
  upsertCalls: { table: string; payload: Record<string, unknown>; options?: Record<string, unknown> }[];
}

function makeSupabase(state: MockState): any {
  return {
    from(table: string) {
      return {
        upsert(payload: Record<string, unknown>, options?: Record<string, unknown>) {
          state.upsertCalls.push({ table, payload, options });
          return Promise.resolve({ error: null });
        },
      };
    },
  };
}

function makeOfficialRow(overrides: Partial<InviteCode> = {}): InviteCode {
  return {
    code: "OFFICIAL-1234",
    type: "official",
    owner_user_id: null,
    max_uses: 3,
    used_count: 0,
    official_code_reason: "campaign-0701",
    created_at: new Date().toISOString(),
    expires_at: null,
    disabled: false,
    ...overrides,
  };
}

function makeDeps(overrides: Partial<OpenPlatformAccessByOfficialInviteDeps> = {}): {
  deps: OpenPlatformAccessByOfficialInviteDeps;
  calls: {
    peek: string[];
    consume: string[];
    refund: string[];
    relay: unknown[];
    relation: unknown[];
    invite: string[];
    rewards: unknown[];
  };
} {
  const calls = {
    peek: [] as string[],
    consume: [] as string[],
    refund: [] as string[],
    relay: [] as unknown[],
    relation: [] as unknown[],
    invite: [] as string[],
    rewards: [] as unknown[],
  };
  const deps: OpenPlatformAccessByOfficialInviteDeps = {
    async peekInviteCode(code) {
      calls.peek.push(code);
      return { ok: true, code: makeOfficialRow({ code }) };
    },
    async consumeInviteCode(code) {
      calls.consume.push(code);
      return { ok: true, code: makeOfficialRow({ code, used_count: 1 }) };
    },
    async refundInviteCode(code) {
      calls.refund.push(code);
    },
    async ensureAIRelayAccount(input) {
      calls.relay.push(input);
      return { ok: true, status: 200 };
    },
    async attachOfficialInviteRelation(input) {
      calls.relation.push(input);
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

test("openPlatformAccessByOfficialInvite opens beta access and records official relation", async () => {
  const state: MockState = { upsertCalls: [] };
  const { deps, calls } = makeDeps();

  const result = await openPlatformAccessByOfficialInvite(
    {
      user_id: "official-user-1",
      email: "official@example.test",
      display_name: "Official User",
      invite_code: "OFFICIAL-1234",
      source: "register_with_official_invite_code",
    },
    makeSupabase(state),
    deps,
  );

  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.result.user_type, "beta_user");
    assert.equal(result.result.source_invite_code, "OFFICIAL-1234");
    assert.equal(result.result.official_invite_note, "campaign-0701");
    assert.equal(result.result.invite_code, "SC-OWN-CODE");
  }
  assert.deepEqual(calls.peek, ["OFFICIAL-1234"]);
  assert.deepEqual(calls.consume, ["OFFICIAL-1234"]);
  assert.deepEqual(calls.refund, []);
  assert.deepEqual(calls.relation, [
    { user_id: "official-user-1", invite_code: "OFFICIAL-1234" },
  ]);
  assert.deepEqual(state.upsertCalls, [
    {
      table: "profiles",
      payload: {
        user_id: "official-user-1",
        user_type: "beta_user",
        official_invite_note: "campaign-0701",
      },
      options: { onConflict: "user_id" },
    },
  ]);
});

test("openPlatformAccessByOfficialInvite rejects user invite codes before consuming", async () => {
  const state: MockState = { upsertCalls: [] };
  const { deps, calls } = makeDeps({
    async peekInviteCode(code) {
      calls.peek.push(code);
      return {
        ok: true,
        code: makeOfficialRow({
          code,
          type: "user",
          owner_user_id: "inviter-1",
          official_code_reason: null,
        }),
      };
    },
  });

  const result = await openPlatformAccessByOfficialInvite(
    { user_id: "user-1", invite_code: "SC-USER-CODE" },
    makeSupabase(state),
    deps,
  );

  assert.deepEqual(result, { ok: false, reason: "not_official_code" });
  assert.deepEqual(calls.consume, []);
  assert.deepEqual(calls.relay, []);
  assert.equal(state.upsertCalls.length, 0);
});

test("openPlatformAccessByOfficialInvite refunds official code when AI Relay fails", async () => {
  const state: MockState = { upsertCalls: [] };
  const { deps, calls } = makeDeps({
    async ensureAIRelayAccount(input) {
      calls.relay.push(input);
      return { ok: false, status: 502, error: "relay unavailable" };
    },
  });

  const result = await openPlatformAccessByOfficialInvite(
    { user_id: "official-user-1", email: "official@example.test", invite_code: "OFFICIAL-1234" },
    makeSupabase(state),
    deps,
  );

  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.reason, "open_access_failed");
    assert.match(result.error ?? "", /AI Relay ensure failed status=502/);
  }
  assert.deepEqual(calls.consume, ["OFFICIAL-1234"]);
  assert.deepEqual(calls.refund, ["OFFICIAL-1234"]);
  assert.equal(state.upsertCalls.length, 0);
});
