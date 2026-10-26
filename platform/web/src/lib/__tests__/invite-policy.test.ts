import { test } from "node:test";
import assert from "node:assert/strict";
import {
  defaultInvitePolicy,
  inviteBonusCredits,
  normalizeInvitePolicy,
  resolveInviteCodeUsageLimit,
} from "../invite-policy";
import { inviteRewardCredits, inviteRewardQuota } from "../invite-rewards";

test("defaultInvitePolicy matches migration seed defaults", () => {
  const policy = defaultInvitePolicy();
  assert.equal(policy.per_user_limit, 3);
  assert.equal(policy.invitee_bonus_credits, 20);
  assert.equal(policy.inviter_bonus_credits, 20);
});

test("normalizeInvitePolicy clamps invalid numeric fields", () => {
  const policy = normalizeInvitePolicy({
    per_user_limit: 0,
    invitee_bonus_credits: -5,
    inviter_bonus_credits: 999_999,
  });
  assert.equal(policy.per_user_limit, 3);
  assert.equal(policy.invitee_bonus_credits, 20);
  assert.equal(policy.inviter_bonus_credits, 20);
});

test("normalizeInvitePolicy accepts configured limits and bonuses", () => {
  const policy = normalizeInvitePolicy({
    per_user_limit: 5,
    invitee_bonus_credits: 15,
    inviter_bonus_credits: 25,
  });
  assert.equal(policy.per_user_limit, 5);
  assert.equal(policy.invitee_bonus_credits, 15);
  assert.equal(policy.inviter_bonus_credits, 25);
});

test("resolveInviteCodeUsageLimit uses per_user_limit for user codes", () => {
  const policy = normalizeInvitePolicy({ per_user_limit: 4 });
  assert.equal(resolveInviteCodeUsageLimit("user", null, policy), 4);
  assert.equal(resolveInviteCodeUsageLimit("official", 10, policy), 10);
});

test("inviteRewardCredits and inviteRewardQuota honor policy bonuses", () => {
  const policy = normalizeInvitePolicy({
    invitee_bonus_credits: 15,
    inviter_bonus_credits: 25,
  });
  assert.equal(inviteBonusCredits("invitee", policy), 15);
  assert.equal(inviteBonusCredits("inviter", policy), 25);
  assert.equal(inviteRewardCredits("invitee", policy), 15);
  assert.equal(inviteRewardQuota("inviter", policy), 25 * 500_000);
});
