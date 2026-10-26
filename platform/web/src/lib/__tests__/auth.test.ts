/**
 * authenticateRequest 单元测试 —— 双轨鉴权 7 路径
 *
 * 测试策略：
 *   注入 mock AuthDeps（verifyJwt / findUserByAuthId / findUserByPlatformToken）。
 *   不依赖真实 Supabase / 网络 / 数据库，运行时间 < 100ms。
 *
 * 覆盖：
 *   ① 缺 Authorization header → null
 *   ② JWT 成功 + findUserByAuthId 成功 → source='jwt'
 *   ③ JWT 成功 + findUserByAuthId null（孤儿账号）→ null
 *   ④ JWT 失败 + platform_token 成功 → source='platform_token'
 *   ⑤ JWT 失败 + platform_token 也失败 → null
 *   ⑥ Authorization 不带 Bearer 前缀 → null
 *   ⑦ token 是空字符串 → null
 *
 * 运行：
 *   cd platform/web && npx tsx --test src/lib/__tests__/auth.test.ts
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { authenticateRequest, extractBearerToken, type AuthDeps } from "../auth";

// ---------- 测试桩工厂 ----------

interface DepsRecorder {
  verifyJwtCalls: string[];
  findByAuthIdCalls: string[];
  findByPlatformTokenCalls: string[];
}

interface DepsConfig {
  verifyJwtResult?: { auth_id: string; email: string | null } | null;
  findByAuthIdResult?: { user_id: string; email: string | null } | null;
  findByPlatformTokenResult?:
    | { user_id: string; auth_id: string | null; email: string | null }
    | null;
}

function makeDeps(config: DepsConfig = {}): { deps: AuthDeps; rec: DepsRecorder } {
  const rec: DepsRecorder = {
    verifyJwtCalls: [],
    findByAuthIdCalls: [],
    findByPlatformTokenCalls: [],
  };
  const deps: AuthDeps = {
    async verifyJwt(token) {
      rec.verifyJwtCalls.push(token);
      return config.verifyJwtResult ?? null;
    },
    async findUserByAuthId(auth_id) {
      rec.findByAuthIdCalls.push(auth_id);
      return config.findByAuthIdResult ?? null;
    },
    async findUserByPlatformToken(token) {
      rec.findByPlatformTokenCalls.push(token);
      return config.findByPlatformTokenResult ?? null;
    },
  };
  return { deps, rec };
}

function makeReq(authHeader: string | null): Request {
  const headers = new Headers();
  if (authHeader !== null) headers.set("Authorization", authHeader);
  return new Request("http://localhost/api/test", { headers });
}

// ---------- 用例 ----------

test("① 缺 Authorization header → null, 不调用任何 deps", async () => {
  const { deps, rec } = makeDeps();
  const result = await authenticateRequest(makeReq(null), deps);

  assert.equal(result, null);
  assert.equal(rec.verifyJwtCalls.length, 0);
  assert.equal(rec.findByAuthIdCalls.length, 0);
  assert.equal(rec.findByPlatformTokenCalls.length, 0);
});

test("② JWT 成功 + findUserByAuthId 成功 → source='jwt'", async () => {
  const { deps, rec } = makeDeps({
    verifyJwtResult: { auth_id: "auth-uuid-1", email: "user@example.com" },
    findByAuthIdResult: { user_id: "u-001", email: "user@example.com" },
  });
  const result = await authenticateRequest(
    makeReq("Bearer eyJhbGc.payload.sig"),
    deps,
  );

  assert.ok(result, "应返回 AuthenticatedUser");
  assert.equal(result!.user_id, "u-001");
  assert.equal(result!.auth_id, "auth-uuid-1");
  assert.equal(result!.email, "user@example.com");
  assert.equal(result!.source, "jwt");

  assert.deepEqual(rec.verifyJwtCalls, ["eyJhbGc.payload.sig"]);
  assert.deepEqual(rec.findByAuthIdCalls, ["auth-uuid-1"]);
  assert.equal(
    rec.findByPlatformTokenCalls.length,
    0,
    "JWT 路径成功不应触发 platform_token fallback",
  );
});

test("③ JWT 成功但 findUserByAuthId null（孤儿账号）→ null", async () => {
  const { deps, rec } = makeDeps({
    verifyJwtResult: { auth_id: "auth-orphan", email: "orphan@example.com" },
    findByAuthIdResult: null,
  });
  const result = await authenticateRequest(
    makeReq("Bearer eyJhbGc.payload.sig"),
    deps,
  );

  assert.equal(result, null, "孤儿账号不应通过鉴权");
  assert.deepEqual(rec.findByAuthIdCalls, ["auth-orphan"]);
  assert.equal(
    rec.findByPlatformTokenCalls.length,
    0,
    "JWT 校验通过后即使 user 缺失也不应 fallback 到 platform_token",
  );
});

test("④ JWT 失败 + platform_token 成功 → source='platform_token'", async () => {
  const { deps, rec } = makeDeps({
    verifyJwtResult: null,
    findByPlatformTokenResult: {
      user_id: "u-old",
      auth_id: null,
      email: null,
    },
  });
  const result = await authenticateRequest(
    makeReq("Bearer 11111111-2222-3333-4444-555555555555"),
    deps,
  );

  assert.ok(result, "platform_token 应通过");
  assert.equal(result!.user_id, "u-old");
  assert.equal(result!.auth_id, null);
  assert.equal(result!.email, null);
  assert.equal(result!.source, "platform_token");

  assert.equal(rec.verifyJwtCalls.length, 1, "应先尝试 JWT");
  assert.deepEqual(rec.findByPlatformTokenCalls, [
    "11111111-2222-3333-4444-555555555555",
  ]);
});

test("⑤ JWT 失败 + platform_token 也失败 → null", async () => {
  const { deps, rec } = makeDeps({
    verifyJwtResult: null,
    findByPlatformTokenResult: null,
  });
  const result = await authenticateRequest(makeReq("Bearer bogus-token"), deps);

  assert.equal(result, null);
  assert.equal(rec.verifyJwtCalls.length, 1);
  assert.equal(rec.findByPlatformTokenCalls.length, 1);
});

test("⑥ Authorization 不带 Bearer 前缀 → null, 不调用 deps", async () => {
  const { deps, rec } = makeDeps();
  const result = await authenticateRequest(
    makeReq("Basic dXNlcjpwYXNz"),
    deps,
  );

  assert.equal(result, null);
  assert.equal(rec.verifyJwtCalls.length, 0);
  assert.equal(rec.findByPlatformTokenCalls.length, 0);
});

test("⑦ Bearer 后 token 为空 → null, 不调用 deps", async () => {
  const { deps, rec } = makeDeps();
  const result = await authenticateRequest(makeReq("Bearer    "), deps);

  assert.equal(result, null);
  assert.equal(rec.verifyJwtCalls.length, 0);
  assert.equal(rec.findByPlatformTokenCalls.length, 0);
});

// ---------- extractBearerToken 单测（防回归） ----------

test("extractBearerToken: 大小写不敏感 + trim", () => {
  const r1 = new Request("http://x/", {
    headers: { authorization: "bearer abc.def.ghi" },
  });
  assert.equal(extractBearerToken(r1), "abc.def.ghi");

  const r2 = new Request("http://x/", {
    headers: { Authorization: "  Bearer    xyz  " },
  });
  assert.equal(extractBearerToken(r2), "xyz");

  const r3 = new Request("http://x/", { headers: {} });
  assert.equal(extractBearerToken(r3), null);
});
