/**
 * internal-auth 单元测试 —— getPlatformTokenForUser
 *
 * 测试策略：
 *   注入 mock PlatformTokenLookupDeps，不依赖真实 Supabase。
 *
 * 覆盖：
 *   ① user_id 空字符串 → null（不调依赖）
 *   ② lookup 返回 token → 返回该 token
 *   ③ lookup 返回 null → 返回 null
 *   ④ lookup 抛错（默认实现已 catch；这里通过 mock 验证签名约定）
 *
 * 运行：
 *   cd platform/web && npx tsx --test src/lib/__tests__/internal-auth.test.ts
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  getPlatformTokenForUser,
  type PlatformTokenLookupDeps,
} from "../internal-auth";
import { PlatformAccessError } from "../platform-access";

interface Recorder {
  calls: string[];
}

function makeDeps(
  result: string | null,
): { deps: PlatformTokenLookupDeps; rec: Recorder } {
  const rec: Recorder = { calls: [] };
  const deps: PlatformTokenLookupDeps = {
    async findPlatformTokenByUserId(user_id) {
      rec.calls.push(user_id);
      return result;
    },
  };
  return { deps, rec };
}

test("getPlatformTokenForUser: 空 user_id 直接返回 null，不调依赖", async () => {
  const { deps, rec } = makeDeps("should-not-be-returned");
  const result = await getPlatformTokenForUser("", deps);
  assert.equal(result, null);
  assert.equal(rec.calls.length, 0);
});

test("getPlatformTokenForUser: lookup 命中 token，原样返回", async () => {
  const { deps, rec } = makeDeps("plat-token-abc");
  const result = await getPlatformTokenForUser("user-1", deps);
  assert.equal(result, "plat-token-abc");
  assert.deepEqual(rec.calls, ["user-1"]);
});

test("getPlatformTokenForUser: lookup 返回 null（账号异常）→ null", async () => {
  const { deps, rec } = makeDeps(null);
  const result = await getPlatformTokenForUser("user-2", deps);
  assert.equal(result, null);
  assert.deepEqual(rec.calls, ["user-2"]);
});

test("getPlatformTokenForUser: platform access denied propagates explicit error", async () => {
  const { deps, rec } = makeDeps("should-not-return");
  deps.assertPlatformKeyAccess = async () => {
    throw new PlatformAccessError(403, "access_not_approved", "not approved");
  };

  await assert.rejects(
    () => getPlatformTokenForUser("user-3", deps),
    (error) => {
      assert.ok(error instanceof PlatformAccessError);
      assert.equal(error.code, "access_not_approved");
      assert.equal(error.status, 403);
      return true;
    },
  );
  assert.equal(rec.calls.length, 0);
});
