import { callBackendInternal, extractErrorMessage } from "./backend-internal";
import {
  defaultInvitePolicy,
  inviteBonusCredits,
  readInvitePolicy,
  type InvitePolicy,
} from "./invite-policy";
import { createServiceClient } from "./supabase-server";
import type { SupabaseClient } from "@supabase/supabase-js";

const NEWAPI_QUOTA_PER_CREDIT = 500_000;

type RewardTarget = "invitee" | "inviter";
type GrantStatus = "pending" | "succeeded" | "failed";

interface UserRelationRow {
  user_id: string;
  inviter_user_id: string | null;
  invite_code: string | null;
}

interface InviteRewardGrantRow {
  idempotency_key: string;
  invitee_user_id: string;
  inviter_user_id: string | null;
  invite_code: string | null;
  reward_target: RewardTarget;
  credits: number | string;
  status: GrantStatus;
  ai_relay_adjustment_key: string | null;
  error: string | null;
}

export interface QuotaAdjustmentRequest {
  user_id: string;
  mode: "add";
  quota: number;
  reason: string;
  idempotency_key: string;
  biz_ref_type: "invite_reward";
  biz_ref_id: string;
}

export interface QuotaAdjustmentResponse {
  ok: boolean;
  status: number;
  payload: unknown;
  error?: string;
}

export interface InviteRewardGrantResult {
  idempotency_key: string;
  reward_target: RewardTarget;
  reward_user_id: string;
  credits: number;
  quota: number;
  status: "succeeded" | "failed" | "skipped";
  duplicate?: boolean;
  error?: string;
}

export interface InviteRewardRunResult {
  skipped: boolean;
  reason?: "no_relation" | "no_inviter" | "self_invite" | "ai_relay_unavailable" | "reward_error";
  error?: string;
  grants: InviteRewardGrantResult[];
}

export type QuotaAdjuster = (request: QuotaAdjustmentRequest) => Promise<QuotaAdjustmentResponse>;

export function creditsToQuota(credits: number): number {
  return credits * NEWAPI_QUOTA_PER_CREDIT;
}

export function inviteRewardCredits(
  rewardTarget: RewardTarget,
  policy: InvitePolicy = defaultInvitePolicy(),
): number {
  return inviteBonusCredits(rewardTarget, policy);
}

export function inviteRewardQuota(
  rewardTarget: RewardTarget,
  policy: InvitePolicy = defaultInvitePolicy(),
): number {
  return creditsToQuota(inviteRewardCredits(rewardTarget, policy));
}

export function inviteRewardIdempotencyKey(
  rewardTarget: RewardTarget,
  inviteeUserId: string,
  inviterUserId: string,
): string {
  return rewardTarget === "invitee"
    ? `invitee_bonus:${inviteeUserId}`
    : `inviter_bonus:${inviterUserId}:${inviteeUserId}`;
}

export async function callQuotaAdjustment(
  request: QuotaAdjustmentRequest,
): Promise<QuotaAdjustmentResponse> {
  try {
    const { payload, status } = await callBackendInternal(
      "/internal/ai/relay/quota-adjustments",
      {
        method: "POST",
        body: JSON.stringify(request),
      },
    );
    if (status < 200 || status >= 300) {
      return {
        ok: false,
        status,
        payload,
        error: extractErrorMessage(payload, "AI Relay quota adjustment failed"),
      };
    }
    return { ok: true, status, payload };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      payload: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function grantInviteRewardsForUser(
  input: { user_id: string },
  supabase: SupabaseClient = createServiceClient(),
  quotaAdjuster: QuotaAdjuster = callQuotaAdjustment,
): Promise<InviteRewardRunResult> {
  const inviteeUserId = input.user_id.trim();
  if (!inviteeUserId) throw new Error("user_id required");

  const invitePolicy = await readInvitePolicy(supabase);

  const { data: relation, error: relationErr } = await supabase
    .from("user_relations")
    .select("user_id, inviter_user_id, invite_code")
    .eq("user_id", inviteeUserId)
    .maybeSingle();
  if (relationErr) {
    throw new Error(`grantInviteRewards relation lookup failed: ${relationErr.message}`);
  }

  const relationRow = relation as UserRelationRow | null;
  if (!relationRow) return { skipped: true, reason: "no_relation", grants: [] };

  const inviterUserId = relationRow.inviter_user_id?.trim() || null;
  if (!inviterUserId) return { skipped: true, reason: "no_inviter", grants: [] };
  if (inviterUserId === inviteeUserId) return { skipped: true, reason: "self_invite", grants: [] };

  const inviteCode = relationRow.invite_code?.trim() || null;
  const grants = await Promise.all([
    grantInviteReward({
      rewardTarget: "invitee",
      rewardUserId: inviteeUserId,
      inviteeUserId,
      inviterUserId,
      inviteCode,
      invitePolicy,
      supabase,
      quotaAdjuster,
    }),
    grantInviteReward({
      rewardTarget: "inviter",
      rewardUserId: inviterUserId,
      inviteeUserId,
      inviterUserId,
      inviteCode,
      invitePolicy,
      supabase,
      quotaAdjuster,
    }),
  ]);

  return { skipped: false, grants };
}

async function grantInviteReward(input: {
  rewardTarget: RewardTarget;
  rewardUserId: string;
  inviteeUserId: string;
  inviterUserId: string;
  inviteCode: string | null;
  invitePolicy: InvitePolicy;
  supabase: SupabaseClient;
  quotaAdjuster: QuotaAdjuster;
}): Promise<InviteRewardGrantResult> {
  const credits = inviteBonusCredits(input.rewardTarget, input.invitePolicy);
  const quota = creditsToQuota(credits);
  const idempotencyKey = inviteRewardIdempotencyKey(
    input.rewardTarget,
    input.inviteeUserId,
    input.inviterUserId,
  );
  const existing = await readRewardGrant(input.supabase, idempotencyKey);
  if (existing?.status === "succeeded") {
    return {
      idempotency_key: idempotencyKey,
      reward_target: input.rewardTarget,
      reward_user_id: input.rewardUserId,
      credits: Number(existing.credits) || credits,
      quota,
      status: "skipped",
      duplicate: true,
    };
  }

  const grant = existing ?? await insertPendingRewardGrant(input.supabase, {
    idempotency_key: idempotencyKey,
    invitee_user_id: input.inviteeUserId,
    inviter_user_id: input.inviterUserId,
    invite_code: input.inviteCode,
    reward_target: input.rewardTarget,
    credits,
    status: "pending",
    ai_relay_adjustment_key: idempotencyKey,
    error: null,
  });

  const adjustment = await input.quotaAdjuster({
    user_id: input.rewardUserId,
    mode: "add",
    quota,
    reason: inviteRewardReason(input.rewardTarget),
    idempotency_key: idempotencyKey,
    biz_ref_type: "invite_reward",
    biz_ref_id: idempotencyKey,
  });
  if (!adjustment.ok) {
    const message = adjustment.error ?? `quota adjustment failed with status ${adjustment.status}`;
    await markRewardGrant(input.supabase, idempotencyKey, {
      status: "failed",
      ai_relay_adjustment_key: idempotencyKey,
      error: message,
    });
    return {
      idempotency_key: idempotencyKey,
      reward_target: input.rewardTarget,
      reward_user_id: input.rewardUserId,
      credits: Number(grant.credits) || credits,
      quota,
      status: "failed",
      error: message,
    };
  }

  await markRewardGrant(input.supabase, idempotencyKey, {
    status: "succeeded",
    ai_relay_adjustment_key: idempotencyKey,
    error: null,
  });
  return {
    idempotency_key: idempotencyKey,
    reward_target: input.rewardTarget,
    reward_user_id: input.rewardUserId,
    credits: Number(grant.credits) || credits,
    quota,
    status: "succeeded",
  };
}

async function readRewardGrant(
  supabase: SupabaseClient,
  idempotencyKey: string,
): Promise<InviteRewardGrantRow | null> {
  const { data, error } = await supabase
    .from("invite_reward_grants")
    .select("*")
    .eq("idempotency_key", idempotencyKey)
    .maybeSingle();
  if (error) {
    throw new Error(`invite reward grant lookup failed: ${error.message}`);
  }
  return (data as InviteRewardGrantRow | null) ?? null;
}

async function insertPendingRewardGrant(
  supabase: SupabaseClient,
  row: InviteRewardGrantRow,
): Promise<InviteRewardGrantRow> {
  const { data, error } = await supabase
    .from("invite_reward_grants")
    .insert(row)
    .select("*")
    .maybeSingle();
  if (!error && data) return data as InviteRewardGrantRow;
  if (error?.code === "23505") {
    const existing = await readRewardGrant(supabase, row.idempotency_key);
    if (existing) return existing;
  }
  throw new Error(`invite reward grant insert failed: ${error?.message ?? "no row returned"}`);
}

async function markRewardGrant(
  supabase: SupabaseClient,
  idempotencyKey: string,
  patch: { status: GrantStatus; ai_relay_adjustment_key: string; error: string | null },
): Promise<void> {
  const { error } = await supabase
    .from("invite_reward_grants")
    .update(patch)
    .eq("idempotency_key", idempotencyKey);
  if (error) {
    throw new Error(`invite reward grant update failed: ${error.message}`);
  }
}

function inviteRewardReason(rewardTarget: RewardTarget): string {
  return rewardTarget === "invitee"
    ? "邀请奖励：被邀请人开通"
    : "邀请奖励：成功邀请用户开通";
}
