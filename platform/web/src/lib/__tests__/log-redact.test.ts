import { test } from "node:test";
import { strict as assert } from "node:assert";
import { redactTokenInUrl } from "../log-redact";

test("redactTokenInUrl: ?token= 在 query 开头", () => {
  assert.equal(
    redactTokenInUrl("/api/me/session/stream?token=abc-123"),
    "/api/me/session/stream?token=REDACTED",
  );
});

test("redactTokenInUrl: &token= 在 query 中间", () => {
  assert.equal(
    redactTokenInUrl("/api/me/session/stream?kind=logs&token=abc-123"),
    "/api/me/session/stream?kind=logs&token=REDACTED",
  );
});

test("redactTokenInUrl: token 后面还有其他 query", () => {
  assert.equal(
    redactTokenInUrl("/api/me/session/stream?token=abc-123&trace=x"),
    "/api/me/session/stream?token=REDACTED&trace=x",
  );
});

test("redactTokenInUrl: 无 token query 不变", () => {
  assert.equal(
    redactTokenInUrl("/api/me/session/stream?kind=logs"),
    "/api/me/session/stream?kind=logs",
  );
});

test("redactTokenInUrl: tokenize 不被误伤（只匹配 token=）", () => {
  // tokenize 等其他 key 不应被替换
  assert.equal(
    redactTokenInUrl("/x?tokenize=true&access_token=x"),
    "/x?tokenize=true&access_token=x",
  );
});
