import { test } from "node:test";
import { strict as assert } from "node:assert";
import { escapeOrValue, escapeLikeWildcards } from "../waitlist-query";

test("escapeOrValue: 移除 PostgREST .or() 分隔符", () => {
  assert.equal(escapeOrValue("foo,bar"), "foobar");
  assert.equal(escapeOrValue("a(b)c"), "abc");
  assert.equal(escapeOrValue("normal@example.com"), "normal@example.com");
  assert.equal(escapeOrValue(""), "");
});

test("escapeLikeWildcards: % 被转义为 \\%", () => {
  assert.equal(escapeLikeWildcards("%@%.com"), "\\%@\\%.com");
});

test("escapeLikeWildcards: _ 被转义为 \\_", () => {
  assert.equal(escapeLikeWildcards("foo_bar"), "foo\\_bar");
});

test("escapeLikeWildcards: \\ 被转义为 \\\\", () => {
  assert.equal(escapeLikeWildcards("a\\b"), "a\\\\b");
});

test("escapeLikeWildcards: 普通字符串不变", () => {
  assert.equal(escapeLikeWildcards("normal@example.com"), "normal@example.com");
  assert.equal(escapeLikeWildcards("user.name+tag@example.co.uk"), "user.name+tag@example.co.uk");
});

test("escapeLikeWildcards: 复合 payload 全部转义", () => {
  // 真实攻击 payload：试图枚举所有 approved 邮箱
  assert.equal(escapeLikeWildcards("%_\\%"), "\\%\\_\\\\\\%");
});

test("escapeLikeWildcards: 空字符串", () => {
  assert.equal(escapeLikeWildcards(""), "");
});
