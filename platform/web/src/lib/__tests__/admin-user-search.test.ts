import { test } from "node:test";
import assert from "node:assert/strict";
import {
  adminUserMatchesSearch,
  filterAdminUsersBySearch,
  normalizeAdminUserSearchQuery,
} from "../admin-user-search";

const rows = [
  { user_id: "user-alpha-001", email: "alpha@example.test", nickname: "小风" },
  { user_id: "user-beta-002", email: "beta@example.test", nickname: "River" },
  { user_id: "user-gamma-003", email: null, nickname: null },
];

test("normalizeAdminUserSearchQuery trims and lowercases input", () => {
  assert.equal(normalizeAdminUserSearchQuery("  River@Example.TEST  "), "river@example.test");
  assert.equal(normalizeAdminUserSearchQuery(null), "");
});

test("admin user search matches email, user id, and nickname", () => {
  assert.equal(adminUserMatchesSearch(rows[0], "ALPHA@EXAMPLE"), true);
  assert.equal(adminUserMatchesSearch(rows[1], "beta-002"), true);
  assert.equal(adminUserMatchesSearch(rows[0], "小风"), true);
});

test("admin user search does not match unrelated fields", () => {
  const row = { ...rows[0], phone: "13800138000" };
  assert.equal(adminUserMatchesSearch(row, "13800138000"), false);
});

test("filterAdminUsersBySearch returns all rows for blank search", () => {
  assert.deepEqual(filterAdminUsersBySearch(rows, "  "), rows);
});

test("filterAdminUsersBySearch filters by visible profile nickname", () => {
  assert.deepEqual(filterAdminUsersBySearch(rows, "river"), [rows[1]]);
});
