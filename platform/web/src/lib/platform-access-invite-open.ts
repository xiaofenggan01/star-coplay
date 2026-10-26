import { ensureAIRelayAccount, type EnsureAIRelayAccountResult } from "./ai-relay-sync";
import { commitUserInviteUse, peekUserInviteCode, refundInviteCode } from "./invite-code";
import { grantInviteRewardsForUser, type InviteRewardRunResult } from "./invite-rewards";
import { ensureUserInviteCode } from "./platform-access-open";
import { createServiceClient } from "./supabase-server";
import {
  attachUserInviteRelation,
  type AttachInviteRelationResult,
} from "./user-relations";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { InviteCodeRejectReason } from "./types/invite-code";

export type UserInviteOpenRejectReason =
  | InviteCodeRejectReason
  | "not_user_code"
  | "missing_owner"
  | "self_invite"
  | "relation_already_bound"
  | "open_access_failed";

export interface OpenPlatformAccessByUserInviteInput {
  user_id: string;
  email?: string | null;
  display_name?: string | null;
  waitlist_id?: string | number | null;
  invite_code: string;
  source?: string;
}

export interface OpenPlatformAccessByUserInviteResult {
  user_id: string;
  user_type: "beta_user";
  inviter_user_id: string;
  source_invite_code: string;
  ai_relay: EnsureAIRelayAccountResult;
  invite_code: string | null;
  invite_rewards: InviteRewardRunResult;
}

export type OpenPlatformAccessByUserInviteResponse =
  | { ok: true; result: OpenPlatformAccessByUserInviteResult }
  | { ok: false; reason: UserInviteOpenRejectReason; error?: string };

export interface OpenPlatformAccessByUserInviteDeps {
  ensureAIRelayAccount: typeof ensureAIRelayAccount;
  peekUserInviteCode: typeof peekUserInviteCode;
  commitUserInviteUse: typeof commitUserInviteUse;
  refundInviteCode: typeof refundInviteCode;
  attachUserInviteRelation: typeof attachUserInviteRelation;
  ensureUserInviteCode: typeof ensureUserInviteCode;
  grantInviteRewardsForUser: typeof grantInviteRewardsForUser;
}

function defaultDeps(): OpenPlatformAccessByUserInviteDeps {
  return {
    ensureAIRelayAccount,
    peekUserInviteCode,
    commitUserInviteUse,
    refundInviteCode,
    attachUserInviteRelation,
    ensureUserInviteCode,
    grantInviteRewardsForUser,
  };
}

export async function openPlatformAccessByUserInvite(
  input: OpenPlatformAccessByUserInviteInput,
  supabase: SupabaseClient = createServiceClient(),
  deps: OpenPlatformAccessByUserInviteDeps = defaultDeps(),
): Promise<OpenPlatformAccessByUserInviteResponse> {
  const userId = input.user_id.trim();
  const inviteCode = input.invite_code.trim();
  if (!userId || !inviteCode) {
    return { ok: false, reason: "not_found" };
  }

  const peeked = await deps.peekUserInviteCode(inviteCode, supabase);
  if (!peeked.ok) return { ok: false, reason: peeked.reason };

  const inviterUserId = peeked.code.owner_user_id.trim();
  if (!inviterUserId || inviterUserId === userId) {
    return { ok: false, reason: "self_invite" };
  }

  const relationCheck = await canAttachRelation(userId, inviterUserId, inviteCode, supabase);
  if (!relationCheck.ok) return relationCheck;

  let aiRelay: EnsureAIRelayAccountResult;
  try {
    aiRelay = await deps.ensureAIRelayAccount({
      user_id: userId,
      email: input.email,
      display_name: input.display_name ?? input.email,
      source: input.source ?? "open_platform_access_by_user_invite",
      timeout_ms: 15_000,
    });
  } catch (error) {
    return {
      ok: false,
      reason: "open_access_failed",
      error: error instanceof Error ? error.message : String(error),
    };
  }
  if (!aiRelay.ok) {
    const status = aiRelay.status ? ` status=${aiRelay.status}` : "";
    const detail = aiRelay.error ? `: ${aiRelay.error}` : "";
    return {
      ok: false,
      reason: "open_access_failed",
      error: `AI Relay ensure failed${status}${detail}`,
    };
  }

  try {
    await upsertBetaProfile(userId, input.waitlist_id, supabase);
  } catch (error) {
    return {
      ok: false,
      reason: "open_access_failed",
      error: error instanceof Error ? error.message : String(error),
    };
  }

  const committed = await deps.commitUserInviteUse(inviteCode, supabase);
  if (!committed.ok) {
    await rollbackProfileToGuest(userId, supabase);
    return { ok: false, reason: committed.reason };
  }

  let attached: AttachInviteRelationResult;
  try {
    attached = await deps.attachUserInviteRelation(
      {
        user_id: userId,
        inviter_user_id: inviterUserId,
        invite_code: inviteCode,
      },
      supabase,
    );
  } catch (error) {
    await deps.refundInviteCode(inviteCode, supabase);
    await rollbackProfileToGuest(userId, supabase);
    return {
      ok: false,
      reason: "open_access_failed",
      error: error instanceof Error ? error.message : String(error),
    };
  }
  if (!attached.ok) {
    await deps.refundInviteCode(inviteCode, supabase);
    await rollbackProfileToGuest(userId, supabase);
    return { ok: false, reason: attached.reason };
  }

  const ownInviteCode = await deps.ensureUserInviteCode(userId, supabase);

  let inviteRewards: InviteRewardRunResult;
  try {
    inviteRewards = await deps.grantInviteRewardsForUser({ user_id: userId }, supabase);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[platform-open-invite] invite reward failed user_id=${userId}: ${message}`);
    inviteRewards = {
      skipped: true,
      reason: "reward_error",
      error: message,
      grants: [],
    };
  }

  return {
    ok: true,
    result: {
      user_id: userId,
      user_type: "beta_user",
      inviter_user_id: inviterUserId,
      source_invite_code: inviteCode,
      ai_relay: aiRelay,
      invite_code: ownInviteCode,
      invite_rewards: inviteRewards,
    },
  };
}

async function canAttachRelation(
  userId: string,
  inviterUserId: string,
  inviteCode: string,
  supabase: SupabaseClient,
): Promise<{ ok: true } | { ok: false; reason: "relation_already_bound" | "self_invite" }> {
  if (userId === inviterUserId) return { ok: false, reason: "self_invite" };

  const { data, error } = await supabase
    .from("user_relations")
    .select("inviter_user_id, invite_code")
    .eq("user_id", userId)
    .maybeSingle();
  if (error) {
    throw new Error(`relation preflight failed: ${error.message}`);
  }
  if (!data) return { ok: true };

  const existingInviter =
    typeof data.inviter_user_id === "string" && data.inviter_user_id.trim()
      ? data.inviter_user_id.trim()
      : null;
  const existingInviteCode =
    typeof data.invite_code === "string" && data.invite_code.trim()
      ? data.invite_code.trim()
      : null;
  if (!existingInviter && !existingInviteCode) return { ok: true };
  if (existingInviter === inviterUserId && existingInviteCode === inviteCode) return { ok: true };
  return { ok: false, reason: "relation_already_bound" };
}

async function upsertBetaProfile(
  userId: string,
  waitlistId: string | number | null | undefined,
  supabase: SupabaseClient,
): Promise<void> {
  const patch: Record<string, unknown> = {
    user_id: userId,
    user_type: "beta_user",
  };
  if (waitlistId !== undefined && waitlistId !== null && `${waitlistId}`.trim()) {
    patch.waitlist_id = Number(waitlistId);
  }
  const { error } = await supabase.from("profiles").upsert(patch, { onConflict: "user_id" });
  if (error) {
    throw new Error(`profile beta upsert failed: ${error.message}`);
  }
}

async function rollbackProfileToGuest(
  userId: string,
  supabase: SupabaseClient,
): Promise<void> {
  try {
    const { error } = await supabase
      .from("profiles")
      .upsert({ user_id: userId, user_type: "guest" }, { onConflict: "user_id" });
    if (error) {
      console.error(`[platform-open-invite] profile rollback failed: ${error.message}`);
    }
  } catch (error) {
    console.error(
      `[platform-open-invite] profile rollback threw: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}
