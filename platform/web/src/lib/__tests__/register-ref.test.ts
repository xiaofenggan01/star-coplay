import { test } from "node:test";
import assert from "node:assert/strict";
import { readInviteCodeFromSearch } from "../register-ref";

test("register ref reads ?ref=", () => {
  assert.equal(readInviteCodeFromSearch("?ref=SC-Q228-QTKG"), "SC-Q228-QTKG");
});

test("register ref supports alternate query names", () => {
  assert.equal(readInviteCodeFromSearch("?invite_code=SC-AAAA-BBBB"), "SC-AAAA-BBBB");
  assert.equal(readInviteCodeFromSearch("?inviteCode=SC-CCCC-DDDD"), "SC-CCCC-DDDD");
  assert.equal(readInviteCodeFromSearch("?code=SC-EEEE-FFFF"), "SC-EEEE-FFFF");
});

test("register ref ignores blank values", () => {
  assert.equal(readInviteCodeFromSearch("?ref=&invite_code=SC-QQQQ-WWWW"), "SC-QQQQ-WWWW");
  assert.equal(readInviteCodeFromSearch(""), "");
});
