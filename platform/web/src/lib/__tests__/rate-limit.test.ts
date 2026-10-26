/**
 * rate-limit 单元测试
 *
 * 运行：在 platform/web/ 下执行
 *   npx tsx --test src/lib/__tests__/rate-limit.test.ts
 *
 * 覆盖：
 *   ① 第 6 次请求返回 false（5/min 阈值）
 *   ② 60s 后窗口滑动（旧记录被清理，重新放行）
 *   ③ 不同 key 互不影响
 *   ④ 大于 0 的 max 边界 / 0 阈值直接拒绝
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { checkRateLimit, __resetRateLimitForTests } from "../rate-limit";

// 用 mock Date.now 控时；node:test 内置 t.mock.timers 不可控 Date.now，所以手动桩
function withMockedNow(seq: () => void) {
  const origNow = Date.now;
  let current = 1_700_000_000_000; // 固定起点
  Date.now = () => current;
  const advance = (ms: number) => {
    current += ms;
  };
  // 把 advance 暴露在闭包外
  (Date as unknown as { __advance: (ms: number) => void }).__advance = advance;
  try {
    seq();
  } finally {
    Date.now = origNow;
    delete (Date as unknown as { __advance?: unknown }).__advance;
  }
}

function advance(ms: number) {
  (Date as unknown as { __advance: (ms: number) => void }).__advance(ms);
}

test("rate-limit: 第 6 次请求返回 false", () => {
  __resetRateLimitForTests();
  withMockedNow(() => {
    const key = "ip-1";
    for (let i = 1; i <= 5; i++) {
      assert.equal(checkRateLimit(key, 5), true, `第 ${i} 次应放行`);
    }
    assert.equal(checkRateLimit(key, 5), false, "第 6 次应被限流");
    assert.equal(checkRateLimit(key, 5), false, "第 7 次应被限流");
  });
});

test("rate-limit: 60s 后窗口滑动，旧记录被清理", () => {
  __resetRateLimitForTests();
  withMockedNow(() => {
    const key = "ip-2";
    // 打满 5 次
    for (let i = 1; i <= 5; i++) {
      assert.equal(checkRateLimit(key, 5), true);
    }
    assert.equal(checkRateLimit(key, 5), false, "立即第 6 次被限流");

    // 推进 59s，仍在窗口内
    advance(59_000);
    assert.equal(checkRateLimit(key, 5), false, "59s 后仍限流");

    // 再推进 1.5s（共 60.5s），窗口外
    advance(1_500);
    assert.equal(checkRateLimit(key, 5), true, "60s 后窗口滑动，应放行");
  });
});

test("rate-limit: 不同 key 互不影响", () => {
  __resetRateLimitForTests();
  withMockedNow(() => {
    for (let i = 1; i <= 5; i++) {
      assert.equal(checkRateLimit("ip-A", 5), true);
    }
    assert.equal(checkRateLimit("ip-A", 5), false);
    // ip-B 应完全不受影响
    for (let i = 1; i <= 5; i++) {
      assert.equal(checkRateLimit("ip-B", 5), true, `ip-B 第 ${i} 次应放行`);
    }
    assert.equal(checkRateLimit("ip-B", 5), false);
  });
});

test("rate-limit: maxPerMinute=0 直接拒绝", () => {
  __resetRateLimitForTests();
  withMockedNow(() => {
    assert.equal(checkRateLimit("ip-X", 0), false);
  });
});

test("rate-limit: 部分窗口滑动 — 仅过期记录被清理", () => {
  __resetRateLimitForTests();
  withMockedNow(() => {
    const key = "ip-3";
    // t=0: 3 次
    for (let i = 0; i < 3; i++) checkRateLimit(key, 5);
    // t=30s: 2 次，达到 5 次
    advance(30_000);
    checkRateLimit(key, 5);
    checkRateLimit(key, 5);
    assert.equal(checkRateLimit(key, 5), false, "已满应限流");

    // t=61s（相对起点）：前 3 条过期，剩 2 条；可再放 3 次
    advance(31_000);
    assert.equal(checkRateLimit(key, 5), true);
    assert.equal(checkRateLimit(key, 5), true);
    assert.equal(checkRateLimit(key, 5), true);
    assert.equal(checkRateLimit(key, 5), false, "再次满");
  });
});
