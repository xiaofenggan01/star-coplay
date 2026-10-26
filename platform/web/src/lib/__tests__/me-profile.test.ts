/**
 * me-profile handler 单元测试
 *
 * 测试策略：注入 mock MeProfileDeps，覆盖 GET / PATCH 主路径 + 白名单过滤。
 *
 * 运行：
 *   cd platform/web && npx tsx --test src/lib/__tests__/me-profile.test.ts
 *
 * 覆盖：
 *   ① GET：profile 存在 → 200 + profile
 *   ② GET：profile 不存在 → 200 + null（老用户）
 *   ③ GET：findProfile 抛异常 → 500
 *   ④ PATCH：合法 patch → upsert 被调用，仅传白名单字段 → 200
 *   ⑤ PATCH：客户端尝试改 referrer_user_id / official_invite_note / config / user_id
 *           → 服务端过滤掉（upsert 收到的 patch 中无这些字段），allowed 字段仍生效
 *   ⑥ PATCH：完全无 allowed 字段 → 422 invalid_input，未触碰 upsert
 *   ⑦ PATCH：nickname 非 string → 422
 *   ⑧ PATCH：preferences 是数组 → 422
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  handleMeProfileGet,
  handleMeProfilePatch,
  sanitizeProfilePatch,
  type MeProfileDeps,
} from "../me-profile-handler";
import type { Profile, ProfileUpdate } from "../types/profile";
import type { AuthenticatedUser } from "../types/auth";

// ---------- 测试桩 ----------

interface DepsRecorder {
  findProfileCalls: string[];
  upsertCalls: { user_id: string; patch: ProfileUpdate }[];
  syncNicknameCalls: { user_id: string; nickname: string }[];
}

interface DepsConfig {
  findProfileResult?: Profile | null;
  findProfileError?: Error;
  upsertResult?: Profile;
  upsertError?: Error;
  syncNicknameError?: Error;
  validateAvatarUrl?: (avatar_url: string) => Promise<boolean>;
}

function makeProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    user_id: "u-001",
    nickname: "Tester",
    avatar_url: null,
    preferences: {},
    config: {},
    referrer_user_id: null,
    official_invite_note: null,
    user_type: "beta_user",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeDeps(config: DepsConfig = {}): { deps: MeProfileDeps; rec: DepsRecorder } {
  const rec: DepsRecorder = {
    findProfileCalls: [],
    upsertCalls: [],
    syncNicknameCalls: [],
  };
  const deps: MeProfileDeps = {
    async findProfile(user_id) {
      rec.findProfileCalls.push(user_id);
      if (config.findProfileError) throw config.findProfileError;
      return config.findProfileResult ?? null;
    },
    async upsertProfile(user_id, patch) {
      rec.upsertCalls.push({ user_id, patch });
      if (config.upsertError) throw config.upsertError;
      return config.upsertResult ?? makeProfile({ user_id, ...patch });
    },
    async syncUserNickname(user_id, nickname) {
      rec.syncNicknameCalls.push({ user_id, nickname });
      if (config.syncNicknameError) throw config.syncNicknameError;
    },
    validateAvatarUrl: config.validateAvatarUrl,
  };
  return { deps, rec };
}

const baseUser: AuthenticatedUser = {
  user_id: "u-001",
  auth_id: "auth-uuid-1",
  email: "tester@example.com",
  source: "jwt",
};

// ---------- GET ----------

test("① GET：profile 存在 → 200 + profile", async () => {
  const profile = makeProfile({ nickname: "Alice" });
  const { deps, rec } = makeDeps({ findProfileResult: profile });
  const res = await handleMeProfileGet(deps, baseUser);

  assert.equal(res.status, 200);
  assert.deepEqual((res.body as { profile: Profile | null }).profile, profile);
  assert.deepEqual(rec.findProfileCalls, ["u-001"]);
});

test("② GET：profile 不存在 → 200 + null（老用户）", async () => {
  const { deps, rec } = makeDeps({ findProfileResult: null });
  const res = await handleMeProfileGet(deps, baseUser);

  assert.equal(res.status, 200);
  assert.equal((res.body as { profile: Profile | null }).profile, null);
  assert.deepEqual(rec.findProfileCalls, ["u-001"]);
});

test("③ GET：findProfile 抛异常 → 500", async () => {
  const { deps } = makeDeps({ findProfileError: new Error("db down") });
  const res = await handleMeProfileGet(deps, baseUser);

  assert.equal(res.status, 500);
  assert.match((res.body as { error: string }).error, /db down/);
});

// ---------- PATCH ----------

test("④ PATCH：合法 patch → upsert 仅收到白名单字段 → 200", async () => {
  const updated = makeProfile({ nickname: "Bob", avatar_url: "https://x/a.png" });
  const { deps, rec } = makeDeps({ upsertResult: updated });
  const res = await handleMeProfilePatch(deps, baseUser, {
    nickname: "Bob",
    avatar_url: "https://x/a.png",
    preferences: { theme: "dark" },
  });

  assert.equal(res.status, 200);
  assert.deepEqual((res.body as { profile: Profile }).profile, updated);
  assert.equal(rec.upsertCalls.length, 1);
  assert.equal(rec.upsertCalls[0].user_id, "u-001");
  assert.deepEqual(rec.upsertCalls[0].patch, {
    nickname: "Bob",
    avatar_url: "https://x/a.png",
    preferences: { theme: "dark" },
  });
  assert.deepEqual(rec.syncNicknameCalls, [{ user_id: "u-001", nickname: "Bob" }]);
});

test("⑤ PATCH：客户端尝试改 referrer_user_id / official_invite_note / config / user_id → 全被过滤", async () => {
  const { deps, rec } = makeDeps({ upsertResult: makeProfile({ nickname: "Charlie" }) });
  const res = await handleMeProfilePatch(deps, baseUser, {
    nickname: "Charlie",
    referrer_user_id: "u-attacker", // 必须被过滤
    official_invite_note: "fake note", // 必须被过滤
    config: { agent: "evil" }, // 必须被过滤
    user_id: "u-attacker", // 必须被过滤
    created_at: "1970-01-01", // 必须被过滤
  });

  assert.equal(res.status, 200);
  assert.equal(rec.upsertCalls.length, 1);
  // 关键断言：upsert 收到的 patch 中只有 nickname，所有非白名单字段被丢弃
  assert.deepEqual(rec.upsertCalls[0].patch, { nickname: "Charlie" });
  // 类型层面也要确保非白名单字段不存在
  const patch = rec.upsertCalls[0].patch as Record<string, unknown>;
  assert.equal(patch.referrer_user_id, undefined);
  assert.equal(patch.official_invite_note, undefined);
  assert.equal(patch.config, undefined);
  assert.equal(patch.user_id, undefined);
  assert.deepEqual(rec.syncNicknameCalls, [{ user_id: "u-001", nickname: "Charlie" }]);
});

test("PATCH：只改 avatar_url / preferences 时不触碰 users.nickname", async () => {
  const updated = makeProfile({ avatar_url: "https://x/a.png" });
  const { deps, rec } = makeDeps({ upsertResult: updated });
  const res = await handleMeProfilePatch(deps, baseUser, {
    avatar_url: "https://x/a.png",
    preferences: { theme: "light" },
  });

  assert.equal(res.status, 200);
  assert.equal(rec.upsertCalls.length, 1);
  assert.deepEqual(rec.syncNicknameCalls, []);
});

test("PATCH rejects avatar_url when it is not an enabled preset", async () => {
  const { deps, rec } = makeDeps({
    validateAvatarUrl: async () => false,
  });
  const res = await handleMeProfilePatch(deps, baseUser, {
    avatar_url: "https://example.test/not-a-preset.png",
  });

  assert.equal(res.status, 422);
  assert.equal((res.body as { reason?: string }).reason, "invalid_avatar_preset");
  assert.equal(rec.upsertCalls.length, 0);
});

test("PATCH：nickname 已写 profile 但同步 users.nickname 失败时返回 500", async () => {
  const { deps, rec } = makeDeps({
    upsertResult: makeProfile({ nickname: "Delta" }),
    syncNicknameError: new Error("users update failed"),
  });
  const res = await handleMeProfilePatch(deps, baseUser, { nickname: "Delta" });

  assert.equal(res.status, 500);
  assert.match((res.body as { error: string }).error, /users update failed/);
  assert.deepEqual(rec.syncNicknameCalls, [{ user_id: "u-001", nickname: "Delta" }]);
});

test("⑥ PATCH：完全无 allowed 字段 → 422，未触碰 upsert", async () => {
  const { deps, rec } = makeDeps();
  const res = await handleMeProfilePatch(deps, baseUser, {
    referrer_user_id: "u-attacker",
  });

  assert.equal(res.status, 422);
  assert.equal((res.body as { reason?: string }).reason, "invalid_input");
  assert.equal(rec.upsertCalls.length, 0);
});

test("⑦ PATCH：nickname 非 string → 422", async () => {
  const { deps, rec } = makeDeps();
  const res = await handleMeProfilePatch(deps, baseUser, { nickname: 123 });

  assert.equal(res.status, 422);
  assert.equal(rec.upsertCalls.length, 0);
});

test("⑧ PATCH：preferences 是数组 → 422", async () => {
  const { deps, rec } = makeDeps();
  const res = await handleMeProfilePatch(deps, baseUser, {
    preferences: ["bad"],
  });

  assert.equal(res.status, 422);
  assert.equal(rec.upsertCalls.length, 0);
});

// ---------- sanitizeProfilePatch 防回归 ----------

test("sanitizeProfilePatch: nickname trim + 长度边界", () => {
  // trim 后 0 长度
  const r1 = sanitizeProfilePatch({ nickname: "   " });
  assert.equal(r1.ok, false);

  // 65 字符 → 拒绝
  const r2 = sanitizeProfilePatch({ nickname: "a".repeat(65) });
  assert.equal(r2.ok, false);

  // 64 字符 → 接受
  const r3 = sanitizeProfilePatch({ nickname: "a".repeat(64) });
  assert.equal(r3.ok, true);
});

test("sanitizeProfilePatch: body 不是对象 → 拒绝", () => {
  assert.equal(sanitizeProfilePatch(null).ok, false);
  assert.equal(sanitizeProfilePatch("string").ok, false);
  assert.equal(sanitizeProfilePatch(42).ok, false);
  assert.equal(sanitizeProfilePatch([]).ok, false);
});
