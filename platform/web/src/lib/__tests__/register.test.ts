import { test } from "node:test";
import assert from "node:assert/strict";
import {
  handleRegister,
  type CreateAuthUserResult,
  type EventInsertRow,
  type RegisterDeps,
  type RegisterRequestBody,
  type UserSnapshot,
} from "../register-handler";
import type { OpenPlatformAccessByOfficialInviteResponse } from "../platform-access-official-open";
import type { OpenPlatformAccessByUserInviteResponse } from "../platform-access-invite-open";
import type { InviteCode, InviteCodeRejectReason } from "../types/invite-code";

interface DepsRecorder {
  peekCalls: string[];
  consumeCalls: string[];
  createAuthUserCalls: Array<{
    email: string;
    password: string;
    metadata: Record<string, unknown>;
  }>;
  findUserByAuthIdCalls: string[];
  updateProfileCalls: Array<{ userId: string; patch: { waitlist_id?: string; user_type: string } }>;
  ensureUserRelationCalls: Array<{
    user_id: string;
    inviter_user_id?: string | null;
    invite_code?: string | null;
  }>;
  openByUserInviteCalls: Array<{
    user_id: string;
    email?: string | null;
    display_name?: string | null;
    waitlist_id?: string | number | null;
    invite_code: string;
    source?: string;
  }>;
  openByOfficialInviteCalls: Array<{
    user_id: string;
    email?: string | null;
    display_name?: string | null;
    waitlist_id?: string | number | null;
    invite_code: string;
    source?: string;
  }>;
  funnelCalls: EventInsertRow[];
  refundCalls: string[];
  deleteRegistrationArtifactsCalls: Array<{ user_id: string; auth_id: string }>;
}

interface DepsConfig {
  peekResult?:
    | { ok: true; code: InviteCode }
    | { ok: false; reason: InviteCodeRejectReason };
  consumeResult?:
    | { ok: true; code: InviteCode }
    | { ok: false; reason: InviteCodeRejectReason };
  createAuthUserResult?: CreateAuthUserResult;
  snapshot?: UserSnapshot | null;
  openResult?: OpenPlatformAccessByUserInviteResponse;
  openOfficialResult?: OpenPlatformAccessByOfficialInviteResponse;
}

function makeUserInviteRow(overrides: Partial<InviteCode> = {}): InviteCode & {
  type: "user";
  owner_user_id: string;
} {
  return {
    code: "test-code",
    type: "user",
    owner_user_id: "inviter-1",
    max_uses: null,
    used_count: 0,
    official_code_reason: null,
    created_at: new Date().toISOString(),
    expires_at: null,
    disabled: false,
    ...overrides,
  } as InviteCode & { type: "user"; owner_user_id: string };
}

function makeOfficialInviteRow(overrides: Partial<InviteCode> = {}): InviteCode & {
  type: "official";
} {
  return {
    code: "official-code",
    type: "official",
    owner_user_id: null,
    max_uses: 5,
    used_count: 0,
    official_code_reason: "0701",
    created_at: new Date().toISOString(),
    expires_at: null,
    disabled: false,
    ...overrides,
  } as InviteCode & { type: "official" };
}

function makeDeps(config: DepsConfig = {}): { deps: RegisterDeps; rec: DepsRecorder } {
  const rec: DepsRecorder = {
    peekCalls: [],
    consumeCalls: [],
    createAuthUserCalls: [],
    findUserByAuthIdCalls: [],
    updateProfileCalls: [],
    ensureUserRelationCalls: [],
    openByUserInviteCalls: [],
    openByOfficialInviteCalls: [],
    funnelCalls: [],
    refundCalls: [],
    deleteRegistrationArtifactsCalls: [],
  };

  const createAuthUserResult: CreateAuthUserResult =
    config.createAuthUserResult ?? {
      ok: true,
      user: { id: "auth-uuid-1", email: "tester@example.com" },
    };
  const snapshot: UserSnapshot | null =
    config.snapshot === undefined
      ? { user_id: "user-uuid-1", platform_token: "token-uuid-1" }
      : config.snapshot;

  const deps: RegisterDeps = {
    async peekInviteCode(code) {
      rec.peekCalls.push(code);
      return config.peekResult ?? { ok: true, code: makeUserInviteRow() };
    },
    async consumeInviteCode(code) {
      rec.consumeCalls.push(code);
      return config.consumeResult ?? { ok: true, code: makeOfficialInviteRow({ code, used_count: 1 }) };
    },
    async createAuthUser(input) {
      rec.createAuthUserCalls.push(input);
      return createAuthUserResult;
    },
    async findUserByAuthId(authId) {
      rec.findUserByAuthIdCalls.push(authId);
      return snapshot;
    },
    async updateProfileForRegistration(userId, patch) {
      rec.updateProfileCalls.push({ userId, patch });
    },
    async ensureUserRelation(input) {
      rec.ensureUserRelationCalls.push(input);
    },
    async openPlatformAccessByUserInvite(input) {
      rec.openByUserInviteCalls.push(input);
      return (
        config.openResult ?? {
          ok: true,
          result: {
            user_id: input.user_id,
            user_type: "beta_user",
            inviter_user_id: "inviter-1",
            source_invite_code: input.invite_code,
            ai_relay: { ok: true, status: 200 },
            invite_code: "SC-OPENED",
            invite_rewards: { skipped: true, reason: "no_inviter", grants: [] },
          },
        }
      );
    },
    async openPlatformAccessByOfficialInvite(input) {
      rec.openByOfficialInviteCalls.push(input);
      return (
        config.openOfficialResult ?? {
          ok: true,
          result: {
            user_id: input.user_id,
            user_type: "beta_user",
            source_invite_code: input.invite_code,
            official_invite_note: "yc",
            ai_relay: { ok: true, status: 200 },
            invite_code: "SC-OFFICIAL-OWN",
            invite_rewards: { skipped: true, reason: "no_inviter", grants: [] },
            relation: { ok: true, status: "inserted" },
          },
        }
      );
    },
    async deleteRegistrationArtifacts(input) {
      rec.deleteRegistrationArtifactsCalls.push(input);
    },
    async recordFunnelEvent(row) {
      rec.funnelCalls.push(row);
    },
    async refundInviteCode(code) {
      rec.refundCalls.push(code);
    },
  };

  return { deps, rec };
}

const baseBody: RegisterRequestBody = {
  invite_code: "test-code",
  email: "tester@example.com",
  password: "p@ssw0rd!",
  nickname: "Tester",
};

test("rejects invalid invite code before creating auth user", async () => {
  const { deps, rec } = makeDeps({
    peekResult: { ok: false, reason: "not_found" },
  });

  const res = await handleRegister(deps, baseBody);

  assert.equal(res.status, 403);
  assert.equal((res.body as { reason?: string }).reason, "not_found");
  assert.deepEqual(rec.peekCalls, ["test-code"]);
  assert.equal(rec.createAuthUserCalls.length, 0);
  assert.equal(rec.refundCalls.length, 0);
});

test("registers with an official invite code and opens beta access", async () => {
  const official = makeOfficialInviteRow({
    code: "official-code",
    official_code_reason: "yc",
  });
  const { deps, rec } = makeDeps({
    peekResult: { ok: true, code: official },
    consumeResult: { ok: true, code: { ...official, used_count: 1 } },
    snapshot: { user_id: "official-user-1", platform_token: "token-official" },
  });

  const res = await handleRegister(deps, { ...baseBody, invite_code: "official-code" });

  assert.equal(res.status, 200);
  const body = res.body as {
    user_id: string;
    resource_status: string;
    invite_code_type?: string;
    official_invite_note?: string | null;
  };
  assert.equal(body.user_id, "official-user-1");
  assert.equal(body.resource_status, "key_enabled");
  assert.equal(body.invite_code_type, "official");
  assert.equal(body.official_invite_note, "yc");
  assert.deepEqual(rec.peekCalls, ["official-code"]);
  assert.deepEqual(rec.consumeCalls, []);
  assert.equal(rec.openByUserInviteCalls.length, 0);
  assert.deepEqual(rec.openByOfficialInviteCalls, [
    {
      user_id: "official-user-1",
      email: "tester@example.com",
      display_name: "Tester",
      invite_code: "official-code",
      source: "register_with_official_invite_code",
    },
  ]);
  assert.equal(rec.ensureUserRelationCalls.length, 0);
  assert.equal(rec.updateProfileCalls.length, 0);
  assert.equal(rec.funnelCalls[0].data.resource_status, "key_enabled");
  assert.equal(rec.funnelCalls[0].data.invite_code_type, "official");
  assert.equal(rec.funnelCalls[0].data.official_invite_note, "yc");
});

test("cleans up auth user when official invite opening rejects after auth creation", async () => {
  const official = makeOfficialInviteRow({
    code: "official-code",
    max_uses: 1,
    used_count: 0,
  });
  const { deps, rec } = makeDeps({
    peekResult: { ok: true, code: official },
    openOfficialResult: { ok: false, reason: "exhausted" },
    snapshot: { user_id: "official-race-1", platform_token: "token-race" },
  });

  const res = await handleRegister(deps, { ...baseBody, invite_code: "official-code" });

  assert.equal(res.status, 409);
  assert.equal((res.body as { reason?: string }).reason, "exhausted");
  assert.deepEqual(rec.openByOfficialInviteCalls, [
    {
      user_id: "official-race-1",
      email: "tester@example.com",
      display_name: "Tester",
      invite_code: "official-code",
      source: "register_with_official_invite_code",
    },
  ]);
  assert.deepEqual(rec.deleteRegistrationArtifactsCalls, [
    { user_id: "official-race-1", auth_id: "auth-uuid-1" },
  ]);
  assert.equal(rec.ensureUserRelationCalls.length, 0);
  assert.equal(rec.updateProfileCalls.length, 0);
  assert.equal(rec.funnelCalls.length, 0);
});

test("registers without invite code as guest and does not create waitlist/open resources", async () => {
  const { deps, rec } = makeDeps({
    snapshot: { user_id: "guest-1", platform_token: "token-guest" },
  });

  const res = await handleRegister(deps, {
    email: "Guest@Example.com ",
    password: "p@ssw0rd!",
    invite_code: " ",
    nickname: "Guest",
  });

  assert.equal(res.status, 200);
  const body = res.body as { user_id: string; email: string; resource_status: string };
  assert.equal(body.user_id, "guest-1");
  assert.equal(body.email, "guest@example.com");
  assert.equal(body.resource_status, "guest");
  assert.equal("platform_token" in body, false);
  assert.equal(rec.peekCalls.length, 0);
  assert.equal(rec.consumeCalls.length, 0);
  assert.equal(rec.openByUserInviteCalls.length, 0);
  assert.equal(rec.openByOfficialInviteCalls.length, 0);
  assert.deepEqual(rec.updateProfileCalls, [
    { userId: "guest-1", patch: { user_type: "guest" } },
  ]);
  assert.deepEqual(rec.ensureUserRelationCalls, [
    { user_id: "guest-1", inviter_user_id: null, invite_code: null },
  ]);
  assert.equal(rec.funnelCalls[0].data.resource_status, "guest");
  assert.equal(rec.funnelCalls[0].data.invite_code, null);
});

test("registers with a user invite code and opens beta access", async () => {
  const { deps, rec } = makeDeps({
    snapshot: { user_id: "invitee-1", platform_token: "token-invitee" },
  });

  const res = await handleRegister(deps, baseBody);

  assert.equal(res.status, 200);
  const body = res.body as {
    user_id: string;
    email: string;
    resource_status: string;
    invite_code_type?: string;
  };
  assert.equal(body.user_id, "invitee-1");
  assert.equal(body.resource_status, "key_enabled");
  assert.equal(body.invite_code_type, "user");
  assert.equal("platform_token" in body, false);
  assert.deepEqual(rec.ensureUserRelationCalls, [
    { user_id: "invitee-1", inviter_user_id: null, invite_code: null },
  ]);
  assert.deepEqual(rec.openByUserInviteCalls, [
    {
      user_id: "invitee-1",
      email: "tester@example.com",
      display_name: "Tester",
      invite_code: "test-code",
      source: "register_with_user_invite_code",
    },
  ]);
  assert.equal(rec.funnelCalls[0].data.resource_status, "key_enabled");
  assert.equal(rec.funnelCalls[0].data.open_failure_reason, null);
  assert.equal(rec.consumeCalls.length, 0);
});

test("keeps user as guest when invite opening fails after auth creation", async () => {
  const { deps, rec } = makeDeps({
    snapshot: { user_id: "invitee-failed", platform_token: "token-failed" },
    openResult: {
      ok: false,
      reason: "open_access_failed",
      error: "AI Relay ensure failed",
    },
  });

  const res = await handleRegister(deps, baseBody);

  assert.equal(res.status, 503);
  assert.equal((res.body as { reason?: string }).reason, "open_access_failed");
  assert.equal(rec.openByUserInviteCalls.length, 1);
  assert.deepEqual(rec.deleteRegistrationArtifactsCalls, [
    { user_id: "invitee-failed", auth_id: "auth-uuid-1" },
  ]);
  assert.equal(rec.refundCalls.length, 0);
  assert.equal(rec.funnelCalls.length, 0);
});

test("auth email conflict returns 409 and does not refund because invite was only peeked", async () => {
  const { deps, rec } = makeDeps({
    createAuthUserResult: {
      ok: false,
      kind: "email_exists",
      message: "User with this email already exists",
    },
  });

  const res = await handleRegister(deps, baseBody);

  assert.equal(res.status, 409);
  assert.equal((res.body as { reason?: string }).reason, "email_already_registered");
  assert.deepEqual(rec.peekCalls, ["test-code"]);
  assert.equal(rec.findUserByAuthIdCalls.length, 0);
  assert.equal(rec.refundCalls.length, 0);
});

test("auth creation failure returns 500 and does not refund because invite was only peeked", async () => {
  const { deps, rec } = makeDeps({
    createAuthUserResult: {
      ok: false,
      kind: "other",
      message: "network error",
    },
  });

  const res = await handleRegister(deps, baseBody);

  assert.equal(res.status, 500);
  assert.equal((res.body as { reason?: string }).reason, "auth_create_failed");
  assert.match((res.body as { error: string }).error, /network error/);
  assert.equal(rec.refundCalls.length, 0);
  assert.equal(rec.funnelCalls.length, 0);
});

test("auth user without public user snapshot returns inconsistent state", async () => {
  const origErr = console.error;
  const errCalls: string[] = [];
  console.error = (msg: unknown) => {
    errCalls.push(String(msg));
  };
  try {
    const { deps, rec } = makeDeps({
      createAuthUserResult: {
        ok: true,
        user: { id: "auth-orphan", email: "tester@example.com" },
      },
      snapshot: null,
    });

    const res = await handleRegister(deps, baseBody);

    assert.equal(res.status, 500);
    assert.equal((res.body as { reason?: string }).reason, "user_creation_inconsistent");
    assert.deepEqual(rec.findUserByAuthIdCalls, ["auth-orphan"]);
    assert.equal(rec.refundCalls.length, 0);
    assert.equal(rec.funnelCalls.length, 0);
    assert.ok(errCalls.some((msg) => msg.includes("CRITICAL inconsistent state")));
  } finally {
    console.error = origErr;
  }
});

test("missing email or password returns missing_field before invite lookup", async () => {
  const { deps, rec } = makeDeps();

  const missingEmail = await handleRegister(deps, {
    invite_code: "test-code",
    password: "p@ssw0rd!",
  });
  const missingPassword = await handleRegister(deps, {
    invite_code: "test-code",
    email: "tester@example.com",
  });

  assert.equal(missingEmail.status, 400);
  assert.equal((missingEmail.body as { reason?: string }).reason, "missing_field");
  assert.equal(missingPassword.status, 400);
  assert.equal((missingPassword.body as { reason?: string }).reason, "missing_field");
  assert.equal(rec.peekCalls.length, 0);
  assert.equal(rec.createAuthUserCalls.length, 0);
});
