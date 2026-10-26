import { ensureAIRelayAccount, type EnsureAIRelayAccountResult } from "./ai-relay-sync";
import { consumeInviteCode, peekInviteCode, refundInviteCode } from "./invite-code";
import { grantInviteRewardsForUser, type InviteRewardRunResult } from "./invite-rewards";
import { ensureUserInviteCode } from "./platform-access-open";
import { createServiceClient } from "./supabase-server";
import {
  attachOfficialInviteRelation,
  type AttachInviteRelationResult,
} from "./user-relations";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { InviteCodeRejectReason } from "./types/invite-code";

export type OfficialInviteOpenRejectReason =
  | InviteCodeRejectReason
  | "not_official_code"
  | "self_invite"
  | "relation_already_bound"
  | "open_access_failed";

export interface OpenPlatformAccessByOfficialInviteInput {
  user_id: string;
  email?: string | null;
  display_name?: string | null;
  waitlist_id?: string | number | null;
  invite_code: string;
  source?: string;
}

export interface OpenPlatformAccessByOfficialInviteResult {
  user_id: string;
  user_type: "beta_user";
  source_invite_code: string;
  official_invite_note: string | null;
  ai_relay: EnsureAIRelayAccountResult;
  invite_code: string | null;
  invite_rewards: InviteRewardRunResult;
  relation: AttachInviteRelationResult;
}

export type OpenPlatformAccessByOfficialInviteResponse =
  | { ok: true; result: OpenPlatformAccessByOfficialInviteResult }
  | { ok: false; reason: OfficialInviteOpenRejectReason; error?: string };

export interface OpenPlatformAccessByOfficialInviteDeps {
  ensureAIRelayAccount: typeof ensureAIRelayAccount;
  peekInviteCode: typeof peekInviteCode;
  consumeInviteCode: typeof consumeInviteCode;
  refundInviteCode: typeof refundInviteCode;
  attachOfficialInviteRelation: typeof attachOfficialInviteRelation;
  ensureUserInviteCode: typeof ensureUserInviteCode;
  grantInviteRewardsForUser: typeof grantInviteRewardsForUser;
}

function defaultDeps(): OpenPlatformAccessByOfficialInviteDeps {
  return {
    ensureAIRelayAccount,
    peekInviteCode,
    consumeInviteCode,
    refundInviteCode,
    attachOfficialInviteRelation,
    ensureUserInviteCode,
    grantInviteRewardsForUser,
  };
}

export async function openPlatformAccessByOfficialInvite(
  input: OpenPlatformAccessByOfficialInviteInput,
  supabase: SupabaseClient = createServiceClient(),
  deps: OpenPlatformAccessByOfficialInviteDeps = defaultDeps(),
): Promise<OpenPlatformAccessByOfficialInviteResponse> {
  const userId = input.user_id.trim();
  const inviteCode = input.invite_code.trim();
  if (!userId || !inviteCode) {
    return { ok: false, reason: "not_found" };
  }

  const peeked = await deps.peekInviteCode(inviteCode, supabase);
  if (!peeked.ok) return { ok: false, reason: peeked.reason };
  if (peeked.code.type !== "official") {
    return { ok: false, reason: "not_official_code" };
  }
  const officialInviteNote = peeked.code.official_code_reason ?? null;

  const consumed = await deps.consumeInviteCode(inviteCode, supabase);
  if (!consumed.ok) return { ok: false, reason: consumed.reason };
  if (consumed.code.type !== "official") {
    await deps.refundInviteCode(inviteCode, supabase);
    return { ok: false, reason: "not_official_code" };
  }

  let profileUpgraded = false;
  try {
    const aiRelay = await deps.ensureAIRelayAccount({
      user_id: userId,
      email: input.email,
      display_name: input.display_name ?? input.email,
      source: input.source ?? "open_platform_access_by_official_invite",
      timeout_ms: 15_000,
    });
    if (!aiRelay.ok) {
      const status = aiRelay.status ? ` status=${aiRelay.status}` : "";
      const detail = aiRelay.error ? `: ${aiRelay.error}` : "";
      await deps.refundInviteCode(inviteCode, supabase);
      return {
        ok: false,
        reason: "open_access_failed",
        error: `AI Relay ensure failed${status}${detail}`,
      };
    }

    await upsertBetaProfile(
      {
        user_id: userId,
        waitlist_id: input.waitlist_id,
        official_invite_note: officialInviteNote,
      },
      supabase,
    );
    profileUpgraded = true;

    const relation = await deps.attachOfficialInviteRelation(
      { user_id: userId, invite_code: inviteCode },
      supabase,
    );
    if (!relation.ok) {
      await deps.refundInviteCode(inviteCode, supabase);
      await rollbackProfileToGuest(userId, supabase);
      return { ok: false, reason: relation.reason };
    }

    let ownInviteCode: string | null = null;
    try {
      ownInviteCode = await deps.ensureUserInviteCode(userId, supabase);
    } catch (error) {
      console.warn(
        `[platform-open-official] user invite code generation failed user_id=${userId}: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }

    let inviteRewards: InviteRewardRunResult;
    try {
      inviteRewards = await deps.grantInviteRewardsForUser({ user_id: userId }, supabase);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[platform-open-official] invite reward failed user_id=${userId}: ${message}`);
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
        source_invite_code: inviteCode,
        official_invite_note: officialInviteNote,
        ai_relay: aiRelay,
        invite_code: ownInviteCode,
        invite_rewards: inviteRewards,
        relation,
      },
    };
  } catch (error) {
    await deps.refundInviteCode(inviteCode, supabase);
    if (profileUpgraded) {
      await rollbackProfileToGuest(userId, supabase);
    }
    return {
      ok: false,
      reason: "open_access_failed",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function upsertBetaProfile(
  input: {
    user_id: string;
    waitlist_id?: string | number | null;
    official_invite_note?: string | null;
  },
  supabase: SupabaseClient,
): Promise<void> {
  const patch: Record<string, unknown> = {
    user_id: input.user_id,
    user_type: "beta_user",
    official_invite_note: input.official_invite_note ?? null,
  };
  if (input.waitlist_id !== undefined && input.waitlist_id !== null && `${input.waitlist_id}`.trim()) {
    patch.waitlist_id = Number(input.waitlist_id);
  }
  const { error } = await supabase.from("profiles").upsert(patch, { onConflict: "user_id" });
  if (error) {
    throw new Error(`official beta profile upsert failed: ${error.message}`);
  }
}

async function rollbackProfileToGuest(userId: string, supabase: SupabaseClient): Promise<void> {
  try {
    const { error } = await supabase
      .from("profiles")
      .upsert({ user_id: userId, user_type: "guest" }, { onConflict: "user_id" });
    if (error) {
      console.warn(`[platform-open-official] rollback profile failed user_id=${userId}: ${error.message}`);
    }
  } catch (error) {
    console.warn(
      `[platform-open-official] rollback profile threw user_id=${userId}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}
