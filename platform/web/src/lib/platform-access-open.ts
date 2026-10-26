import { ensureAIRelayAccount, type EnsureAIRelayAccountResult } from "./ai-relay-sync";
import {
  defaultInvitePolicy,
  readInvitePolicy,
  type InvitePolicy,
} from "./invite-policy";
export { defaultInvitePolicy, readInvitePolicy, type InvitePolicy };
import { grantInviteRewardsForUser, type InviteRewardRunResult } from "./invite-rewards";
import { createServiceClient } from "./supabase-server";
import { ensureUserRelation } from "./user-relations";
import type { SupabaseClient } from "@supabase/supabase-js";

const READABLE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

export interface OpenPlatformAccessInput {
  user_id: string;
  email?: string | null;
  display_name?: string | null;
  waitlist_id?: string | number | null;
  inviter_user_id?: string | null;
  invite_code?: string | null;
  official_invite_note?: string | null;
  source?: string;
}

export interface OpenPlatformAccessResult {
  user_id: string;
  user_type: "beta_user";
  ai_relay: EnsureAIRelayAccountResult;
  invite_code: string | null;
  invite_rewards: InviteRewardRunResult;
}

export interface OpenPlatformAccessDeps {
  ensureAIRelayAccount: typeof ensureAIRelayAccount;
  ensureUserInviteCode: typeof ensureUserInviteCode;
  ensureUserRelation: typeof ensureUserRelation;
  grantInviteRewardsForUser: typeof grantInviteRewardsForUser;
}

function defaultOpenPlatformAccessDeps(): OpenPlatformAccessDeps {
  return {
    ensureAIRelayAccount,
    ensureUserInviteCode,
    ensureUserRelation,
    grantInviteRewardsForUser,
  };
}

export function generateReadableInviteCode(prefix: string, randomLength: number): string {
  const bytes = new Uint8Array(randomLength);
  crypto.getRandomValues(bytes);
  let suffix = "";
  for (const byte of bytes) {
    suffix += READABLE_CODE_ALPHABET[byte % READABLE_CODE_ALPHABET.length];
  }
  const grouped = suffix.match(/.{1,4}/g)?.join("-") ?? suffix;
  return `${prefix}-${grouped}`.toUpperCase();
}

export async function ensureUserInviteCode(
  userId: string,
  supabase: SupabaseClient = createServiceClient(),
): Promise<string | null> {
  const policy = await readInvitePolicy(supabase);
  if (!policy.enabled || !policy.auto_issue_user_invite_code) return null;

  const { data: existing, error: existingErr } = await supabase
    .from("invite_codes")
    .select("code")
    .eq("type", "user")
    .eq("owner_user_id", userId)
    .maybeSingle();
  if (existingErr) {
    throw new Error(`ensureUserInviteCode lookup failed: ${existingErr.message}`);
  }
  if (existing?.code) return existing.code as string;

  const { prefix, random_length } = policy.user_invite_code_format;
  for (let attempt = 0; attempt < 8; attempt++) {
    const code = generateReadableInviteCode(prefix, random_length);
    const { error } = await supabase.from("invite_codes").insert({
      code,
      type: "user",
      owner_user_id: userId,
      max_uses: null,
      used_count: 0,
    });
    if (!error) return code;
    if (error.code !== "23505") {
      throw new Error(`ensureUserInviteCode insert failed: ${error.message}`);
    }

    const { data: existingAfterConflict } = await supabase
      .from("invite_codes")
      .select("code")
      .eq("type", "user")
      .eq("owner_user_id", userId)
      .maybeSingle();
    if (existingAfterConflict?.code) return existingAfterConflict.code as string;
  }

  throw new Error("ensureUserInviteCode failed after retries");
}

export async function openPlatformAccess(
  input: OpenPlatformAccessInput,
  supabase: SupabaseClient = createServiceClient(),
  deps: OpenPlatformAccessDeps = defaultOpenPlatformAccessDeps(),
): Promise<OpenPlatformAccessResult> {
  const userId = input.user_id.trim();
  if (!userId) throw new Error("user_id required");

  const aiRelay = await deps.ensureAIRelayAccount({
    user_id: userId,
    email: input.email,
    display_name: input.display_name ?? input.email,
    source: input.source ?? "open_platform_access",
    timeout_ms: 15_000,
  });
  if (!aiRelay.ok) {
    const status = aiRelay.status ? ` status=${aiRelay.status}` : "";
    const detail = aiRelay.error ? `: ${aiRelay.error}` : "";
    throw new Error(`openPlatformAccess AI Relay ensure failed${status}${detail}`);
  }

  const profilePatch: Record<string, unknown> = {
    user_id: userId,
    user_type: "beta_user",
  };
  if (input.waitlist_id !== undefined && input.waitlist_id !== null && `${input.waitlist_id}`.trim()) {
    profilePatch.waitlist_id = Number(input.waitlist_id);
  }
  if (input.official_invite_note !== undefined) {
    profilePatch.official_invite_note = input.official_invite_note;
  }

  const { error: profileErr } = await supabase
    .from("profiles")
    .upsert(profilePatch, { onConflict: "user_id" });
  if (profileErr) {
    throw new Error(`openPlatformAccess profile upsert failed: ${profileErr.message}`);
  }

  await deps.ensureUserRelation(
    {
      user_id: userId,
      inviter_user_id: input.inviter_user_id,
      invite_code: input.invite_code,
    },
    supabase,
  );

  const inviteCode = await deps.ensureUserInviteCode(userId, supabase);

  let inviteRewards: InviteRewardRunResult;
  try {
    inviteRewards = await deps.grantInviteRewardsForUser({ user_id: userId }, supabase);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[platform-open] invite reward failed user_id=${userId}: ${message}`);
    inviteRewards = {
      skipped: true,
      reason: "reward_error",
      error: message,
      grants: [],
    };
  }

  return {
    user_id: userId,
    user_type: "beta_user",
    ai_relay: aiRelay,
    invite_code: inviteCode,
    invite_rewards: inviteRewards,
  };
}
