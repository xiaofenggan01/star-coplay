import type { SupabaseClient } from "@supabase/supabase-js";
import { ensureUserInviteCode } from "./platform-access-open";
import {
  readInvitePolicy,
  resolveInviteCodeUsageLimit,
} from "./invite-policy";
import { createServiceClient } from "./supabase-server";

interface InviteCodeRow {
  code: string;
  used_count: number;
  max_uses: number | null;
  created_at: string;
}

interface UserRelationReferralRow {
  user_id: string;
  invite_code: string | null;
  created_at: string;
}

interface InviteRewardGrantStatusRow {
  invitee_user_id: string;
  reward_target: "invitee" | "inviter";
  status: string;
}

interface RefereeUserRow {
  user_id: string;
  email: string | null;
  nickname: string | null;
}

interface RefereeProfileRow {
  user_id: string;
  nickname: string | null;
}

export interface UserInviteStatus {
  totalBatches: number;
  maxBatches: number;
  totalCodes: number;
  usedCodes: number;
  remainingUses: number | null;
  canGenerate: boolean;
  codes: Array<{
    code: string;
    used_count: number;
    max_uses: number | null;
    usage_limit: number | null;
    remaining_uses: number | null;
    is_exhausted: boolean;
    batch_number: number | null;
  }>;
  referrals: Array<{
    id: string;
    referee_user_id: string;
    referee_email: string | null;
    referee_nickname: string | null;
    invite_code: string | null;
    reward_status: string;
    inviter_reward_status: string;
    invitee_reward_status: string;
    created_at: string;
  }>;
}

function sanitizeVisibleInviteCodes(
  codes: UserInviteStatus["codes"],
): UserInviteStatus["codes"] {
  return codes.map((code) => {
    if (code.remaining_uses === null || code.remaining_uses > 0) {
      return code;
    }
    return {
      ...code,
      code: "",
    };
  });
}

export async function getUserInviteStatus(
  userId: string,
  supabase: SupabaseClient = createServiceClient(),
): Promise<UserInviteStatus> {
  const { data, error } = await supabase
    .from("invite_codes")
    .select("code, used_count, max_uses, created_at")
    .eq("type", "user")
    .eq("owner_user_id", userId)
    .eq("disabled", false)
    .order("created_at", { ascending: true });

  if (error) {
    throw new Error(`getUserInviteStatus lookup failed: ${error.message}`);
  }

  const invitePolicy = await readInvitePolicy(supabase);
  const rows = ((data ?? []) as InviteCodeRow[]).map((row) => ({
    code: row.code,
    used_count: row.used_count ?? 0,
    max_uses: row.max_uses ?? null,
    usage_limit: resolveInviteCodeUsageLimit("user", row.max_uses ?? null, invitePolicy),
    batch_number: null,
  })).map((row) => {
    const remainingUses =
      row.usage_limit === null ? null : Math.max(row.usage_limit - row.used_count, 0);
    return {
      ...row,
      remaining_uses: remainingUses,
      is_exhausted: remainingUses !== null && remainingUses <= 0,
    };
  });

  const referrals = await getUserInviteReferrals(userId, supabase);
  const remainingUses = rows.reduce<number | null>((sum, row) => {
    if (sum === null || row.remaining_uses === null) return null;
    return sum + row.remaining_uses;
  }, 0);

  return {
    totalBatches: rows.length,
    maxBatches: 1,
    totalCodes: rows.length,
    usedCodes: rows.filter((row) => row.is_exhausted).length,
    remainingUses,
    canGenerate: rows.length === 0,
    codes: sanitizeVisibleInviteCodes(rows),
    referrals,
  };
}

async function getUserInviteReferrals(
  userId: string,
  supabase: SupabaseClient,
): Promise<UserInviteStatus["referrals"]> {
  const { data: relationData, error: relationError } = await supabase
    .from("user_relations")
    .select("user_id, invite_code, created_at")
    .eq("inviter_user_id", userId)
    .order("created_at", { ascending: false });

  if (relationError) {
    throw new Error(`getUserInviteStatus referrals lookup failed: ${relationError.message}`);
  }

  const relations = (relationData ?? []) as UserRelationReferralRow[];
  if (relations.length === 0) return [];

  const refereeUserIds = relations.map((row) => row.user_id);
  const [
    { data: grantData, error: grantError },
    { data: refereeData, error: refereeError },
    { data: profileData, error: profileError },
  ] = await Promise.all([
    supabase
    .from("invite_reward_grants")
      .select("invitee_user_id, reward_target, status")
    .eq("inviter_user_id", userId)
      .in("invitee_user_id", refereeUserIds),
    supabase
      .from("users")
      .select("user_id, email, nickname")
      .in("user_id", refereeUserIds),
    supabase
      .from("profiles")
      .select("user_id, nickname")
      .in("user_id", refereeUserIds),
  ]);

  if (grantError) {
    throw new Error(`getUserInviteStatus reward lookup failed: ${grantError.message}`);
  }
  if (refereeError) {
    throw new Error(`getUserInviteStatus referee lookup failed: ${refereeError.message}`);
  }
  if (profileError) {
    throw new Error(`getUserInviteStatus referee profile lookup failed: ${profileError.message}`);
  }

  const rewardStatusByInviteeAndTarget = new Map(
    ((grantData ?? []) as InviteRewardGrantStatusRow[]).map((row) => [
      `${row.invitee_user_id}:${row.reward_target}`,
      row.status,
    ]),
  );
  const refereeById = new Map(
    ((refereeData ?? []) as RefereeUserRow[]).map((row) => [row.user_id, row]),
  );
  const profileById = new Map(
    ((profileData ?? []) as RefereeProfileRow[]).map((row) => [row.user_id, row]),
  );

  return relations.map((row) => {
    const referee = refereeById.get(row.user_id);
    const profile = profileById.get(row.user_id);
    const inviterRewardStatus =
      rewardStatusByInviteeAndTarget.get(`${row.user_id}:inviter`) ?? "pending";
    const inviteeRewardStatus =
      rewardStatusByInviteeAndTarget.get(`${row.user_id}:invitee`) ?? "pending";
    return {
      id: row.user_id,
      referee_user_id: row.user_id,
      referee_email: referee?.email ?? null,
      referee_nickname: profile?.nickname?.trim() || referee?.nickname || null,
      invite_code: row.invite_code,
      reward_status: inviterRewardStatus,
      inviter_reward_status: inviterRewardStatus,
      invitee_reward_status: inviteeRewardStatus,
      created_at: row.created_at,
    };
  });
}

export async function generateUserInviteBatch(
  userId: string,
  supabase: SupabaseClient = createServiceClient(),
): Promise<{ ok: true; invite_code: string | null } | { ok: false; reason: string }> {
  const status = await getUserInviteStatus(userId, supabase);
  if (!status.canGenerate) {
    return { ok: false, reason: "max_batches_reached" };
  }

  const inviteCode = await ensureUserInviteCode(userId, supabase);
  return { ok: true, invite_code: inviteCode };
}
