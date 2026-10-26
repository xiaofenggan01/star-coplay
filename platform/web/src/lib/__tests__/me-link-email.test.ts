/**
 * me-link-email handler 单元测试
 *
 * 测试策略：注入 mock LinkEmailDeps，覆盖鉴权来源校验、占用检查、trigger 后处理三组路径。
 *
 * 运行：
 *   cd platform/web && npx tsx --test src/lib/__tests__/me-link-email.test.ts
 *
 * 覆盖：
 *   ① 鉴权来源是 jwt → 422 already_linked，未触碰任何 deps
 *   ② user.auth_id 已存在（platform_token 但已绑过）→ 422 already_linked
 *   ③ body 非法（缺 password / email 格式错 / 密码过短）→ 422 invalid_input
 *   ④ email 已被占用 → 409 email_in_use，未 createUser
 *   ⑤ createUser 抛异常 → 500 link_failed
 *   ⑥ linkOldUser 失败 → 500 + 补偿 deleteAuthUser 被调用
 *   ⑦ deleteOrphanUsersByAuthId 失败 → 仍返回 200（孤儿是次要问题，仅 log）
 *   ⑧ 全程成功 → 200 + ok=true，调用顺序正确
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  handleMeLinkEmail,
  validateLinkEmailBody,
  type LinkEmailDeps,
} from "../me-link-email-handler";
import type { AuthenticatedUser } from "../types/auth";

// ---------- 测试桩 ----------

interface DepsRecorder {
  isEmailTakenCalls: string[];
  createAuthUserCalls: { email: string; password: string; existing_user_id: string }[];
  linkOldUserCalls: { old_user_id: string; new_auth_id: string; email: string }[];
  deleteOrphanCalls: { new_auth_id: string; keep_user_id: string }[];
  deleteAuthUserCalls: string[];
  ensureAIRelayCalls: { user_id: string; email?: string | null; display_name?: string | null; source?: string }[];
}

interface DepsConfig {
  isEmailTakenResult?: boolean;
  isEmailTakenError?: Error;
  createAuthUserResult?: string;
  createAuthUserError?: Error;
  linkOldUserError?: Error;
  deleteOrphanError?: Error;
  deleteAuthUserError?: Error;
}

function makeDeps(config: DepsConfig = {}): { deps: LinkEmailDeps; rec: DepsRecorder } {
  const rec: DepsRecorder = {
    isEmailTakenCalls: [],
    createAuthUserCalls: [],
    linkOldUserCalls: [],
    deleteOrphanCalls: [],
    deleteAuthUserCalls: [],
    ensureAIRelayCalls: [],
  };
  const deps: LinkEmailDeps = {
    async isEmailTaken(email) {
      rec.isEmailTakenCalls.push(email);
      if (config.isEmailTakenError) throw config.isEmailTakenError;
      return config.isEmailTakenResult ?? false;
    },
    async createAuthUser(email, password, existing_user_id) {
      rec.createAuthUserCalls.push({ email, password, existing_user_id });
      if (config.createAuthUserError) throw config.createAuthUserError;
      return config.createAuthUserResult ?? "auth-new-uuid";
    },
    async linkOldUser(old_user_id, new_auth_id, email) {
      rec.linkOldUserCalls.push({ old_user_id, new_auth_id, email });
      if (config.linkOldUserError) throw config.linkOldUserError;
    },
    async deleteOrphanUsersByAuthId(new_auth_id, keep_user_id) {
      rec.deleteOrphanCalls.push({ new_auth_id, keep_user_id });
      if (config.deleteOrphanError) throw config.deleteOrphanError;
    },
    async deleteAuthUser(auth_id) {
      rec.deleteAuthUserCalls.push(auth_id);
      if (config.deleteAuthUserError) throw config.deleteAuthUserError;
    },
    async ensureAIRelayAccount(input) {
      rec.ensureAIRelayCalls.push(input);
    },
  };
  return { deps, rec };
}

const oldUser: AuthenticatedUser = {
  user_id: "u-old",
  auth_id: null,
  email: null,
  source: "platform_token",
};

const goodBody = { email: "old@example.com", password: "secret123" };

// ---------- 用例 ----------

test("① source=jwt → 422 already_linked, 未触碰任何 deps", async () => {
  const { deps, rec } = makeDeps();
  const jwtUser: AuthenticatedUser = {
    user_id: "u-jwt",
    auth_id: "auth-jwt",
    email: "x@y.com",
    source: "jwt",
  };
  const res = await handleMeLinkEmail(deps, jwtUser, goodBody);

  assert.equal(res.status, 422);
  assert.equal((res.body as { reason: string }).reason, "already_linked");
  assert.equal(rec.isEmailTakenCalls.length, 0);
  assert.equal(rec.createAuthUserCalls.length, 0);
});

test("② platform_token 但 auth_id 已存在 → 422 already_linked", async () => {
  const { deps, rec } = makeDeps();
  const linkedUser: AuthenticatedUser = {
    ...oldUser,
    auth_id: "auth-existing",
  };
  const res = await handleMeLinkEmail(deps, linkedUser, goodBody);

  assert.equal(res.status, 422);
  assert.equal((res.body as { reason: string }).reason, "already_linked");
  assert.equal(rec.isEmailTakenCalls.length, 0);
});

test("③ body 非法（密码过短 / email 格式错）→ 422 invalid_input", async () => {
  const { deps, rec } = makeDeps();

  const r1 = await handleMeLinkEmail(deps, oldUser, {
    email: "ok@x.com",
    password: "short",
  });
  assert.equal(r1.status, 422);
  assert.equal((r1.body as { reason: string }).reason, "invalid_input");

  const r2 = await handleMeLinkEmail(deps, oldUser, {
    email: "not-an-email",
    password: "longenough",
  });
  assert.equal(r2.status, 422);

  const r3 = await handleMeLinkEmail(deps, oldUser, { password: "longenough" });
  assert.equal(r3.status, 422);

  assert.equal(rec.createAuthUserCalls.length, 0);
});

test("④ email 已被占用 → 409 email_in_use, 未 createUser", async () => {
  const { deps, rec } = makeDeps({ isEmailTakenResult: true });
  const res = await handleMeLinkEmail(deps, oldUser, goodBody);

  assert.equal(res.status, 409);
  assert.equal((res.body as { reason: string }).reason, "email_in_use");
  assert.deepEqual(rec.isEmailTakenCalls, ["old@example.com"]);
  assert.equal(rec.createAuthUserCalls.length, 0);
});

test("⑤ createAuthUser 抛异常 → 500 link_failed", async () => {
  const { deps, rec } = makeDeps({
    createAuthUserError: new Error("supabase auth 503"),
  });
  const res = await handleMeLinkEmail(deps, oldUser, goodBody);

  assert.equal(res.status, 500);
  assert.equal((res.body as { reason: string }).reason, "link_failed");
  assert.match((res.body as { error: string }).error, /supabase auth 503/);
  assert.equal(rec.createAuthUserCalls.length, 1);
  assert.equal(rec.linkOldUserCalls.length, 0);
});

test("⑥ linkOldUser 失败 → 500 + 补偿 deleteAuthUser 被调用", async () => {
  const { deps, rec } = makeDeps({
    createAuthUserResult: "auth-new-xyz",
    linkOldUserError: new Error("update lock conflict"),
  });
  const res = await handleMeLinkEmail(deps, oldUser, goodBody);

  assert.equal(res.status, 500);
  assert.equal((res.body as { reason: string }).reason, "link_failed");
  assert.match((res.body as { error: string }).error, /update lock conflict/);
  assert.equal(rec.linkOldUserCalls.length, 1);
  // 关键：必须执行补偿删除新 auth.users，避免 email 永久占用
  assert.deepEqual(rec.deleteAuthUserCalls, ["auth-new-xyz"]);
  // 孤儿清理不应在此路径执行
  assert.equal(rec.deleteOrphanCalls.length, 0);
});

test("⑦ deleteOrphanUsersByAuthId 失败 → 仍返回 200（孤儿是次要问题，仅 log）", async () => {
  const { deps, rec } = makeDeps({
    createAuthUserResult: "auth-new-xyz",
    deleteOrphanError: new Error("delete failed"),
  });
  const res = await handleMeLinkEmail(deps, oldUser, goodBody);

  assert.equal(res.status, 200, "孤儿清理失败不应阻断主流程");
  assert.equal((res.body as { ok: boolean }).ok, true);
  assert.equal(rec.linkOldUserCalls.length, 1);
  assert.equal(rec.deleteOrphanCalls.length, 1);
  // 主流程已成功，不应执行补偿
  assert.equal(rec.deleteAuthUserCalls.length, 0);
  assert.deepEqual(rec.ensureAIRelayCalls, [
    {
      user_id: "u-old",
      email: "old@example.com",
      display_name: "old@example.com",
      source: "link_email",
    },
  ]);
});

test("⑧ 全程成功 → 200 + ok=true, 调用顺序正确", async () => {
  const { deps, rec } = makeDeps({
    createAuthUserResult: "auth-new-xyz",
  });
  const res = await handleMeLinkEmail(deps, oldUser, goodBody);

  assert.equal(res.status, 200);
  assert.deepEqual(res.body, { ok: true, email: "old@example.com" });

  // 调用顺序与参数验证
  assert.deepEqual(rec.isEmailTakenCalls, ["old@example.com"]);
  assert.deepEqual(rec.createAuthUserCalls, [
    {
      email: "old@example.com",
      password: "secret123",
      existing_user_id: "u-old",
    },
  ]);
  assert.deepEqual(rec.linkOldUserCalls, [
    {
      old_user_id: "u-old",
      new_auth_id: "auth-new-xyz",
      email: "old@example.com",
    },
  ]);
  assert.deepEqual(rec.deleteOrphanCalls, [
    { new_auth_id: "auth-new-xyz", keep_user_id: "u-old" },
  ]);
  // 成功路径不应执行补偿删除
  assert.equal(rec.deleteAuthUserCalls.length, 0);
});

// ---------- validateLinkEmailBody 防回归 ----------

test("validateLinkEmailBody: email 自动 trim + lowercase", () => {
  const r = validateLinkEmailBody({ email: "  Foo@Bar.COM ", password: "secret123" });
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.equal(r.email, "foo@bar.com");
    assert.equal(r.password, "secret123"); // password 不可改大小写
  }
});

test("validateLinkEmailBody: 密码长度边界", () => {
  // 7 字符 → 拒绝
  assert.equal(
    validateLinkEmailBody({ email: "x@y.com", password: "1234567" }).ok,
    false,
  );
  // 8 字符 → 接受
  assert.equal(
    validateLinkEmailBody({ email: "x@y.com", password: "12345678" }).ok,
    true,
  );
  // 129 字符 → 拒绝
  assert.equal(
    validateLinkEmailBody({ email: "x@y.com", password: "a".repeat(129) }).ok,
    false,
  );
});
