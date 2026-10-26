import {
  ConfigError,
  makeSupabaseFetcher,
  readStarcoplayConfigEnvironment,
  readSystemConfig,
} from "./system-config";
import { createServiceClient } from "./supabase-server";
import type { SupabaseClient } from "@supabase/supabase-js";

const INVITE_DOMAIN = "invite";
const INVITE_CONFIG_KEY = "policy";

export interface InvitePolicy {
  enabled: boolean;
  per_user_limit: number;
  invitee_bonus_credits: number;
  inviter_bonus_credits: number;
  auto_issue_user_invite_code: boolean;
  user_invite_code_format: {
    kind: "readable";
    prefix: string;
    random_length: number;
  };
}

export function defaultInvitePolicy(): InvitePolicy {
  return {
    enabled: true,
    per_user_limit: 3,
    invitee_bonus_credits: 20,
    inviter_bonus_credits: 20,
    auto_issue_user_invite_code: true,
    user_invite_code_format: {
      kind: "readable",
      prefix: "SC",
      random_length: 8,
    },
  };
}

function disabledInvitePolicy(): InvitePolicy {
  return {
    ...defaultInvitePolicy(),
    enabled: false,
    auto_issue_user_invite_code: false,
  };
}

function normalizePositiveInt(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) return fallback;
  return parsed;
}

export function normalizeInvitePolicy(value: unknown): InvitePolicy {
  const fallback = defaultInvitePolicy();
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  const obj = value as Record<string, unknown>;
  const format =
    obj.user_invite_code_format &&
    typeof obj.user_invite_code_format === "object" &&
    !Array.isArray(obj.user_invite_code_format)
      ? (obj.user_invite_code_format as Record<string, unknown>)
      : {};
  const prefix = typeof format.prefix === "string" && format.prefix.trim()
    ? format.prefix.trim().toUpperCase()
    : fallback.user_invite_code_format.prefix;
  const randomLength = Number(format.random_length);

  return {
    enabled: obj.enabled !== false,
    per_user_limit: normalizePositiveInt(obj.per_user_limit, fallback.per_user_limit, 1, 1000),
    invitee_bonus_credits: normalizePositiveInt(
      obj.invitee_bonus_credits,
      fallback.invitee_bonus_credits,
      1,
      10_000,
    ),
    inviter_bonus_credits: normalizePositiveInt(
      obj.inviter_bonus_credits,
      fallback.inviter_bonus_credits,
      1,
      10_000,
    ),
    auto_issue_user_invite_code: obj.auto_issue_user_invite_code !== false,
    user_invite_code_format: {
      kind: "readable",
      prefix,
      random_length: Number.isInteger(randomLength) && randomLength >= 4 && randomLength <= 32
        ? randomLength
        : fallback.user_invite_code_format.random_length,
    },
  };
}

export async function readInvitePolicy(
  supabase: SupabaseClient = createServiceClient(),
): Promise<InvitePolicy> {
  try {
    const config = await readSystemConfig({
      fetcher: makeSupabaseFetcher(supabase),
      domain: INVITE_DOMAIN,
      configKey: INVITE_CONFIG_KEY,
      environment: readStarcoplayConfigEnvironment(),
    });
    return normalizeInvitePolicy(config.parsed_value);
  } catch (error) {
    if (error instanceof ConfigError) {
      if (error.code === "CONFIG_DISABLED") {
        console.warn(
          `[invite-policy] invite policy is disabled: ${error.message}; disabling automatic user invite codes`,
        );
        return disabledInvitePolicy();
      }
      console.warn(
        `[invite-policy] invite policy read failed ${error.code}: ${error.message}; using default policy`,
      );
    } else {
      console.warn(
        `[invite-policy] invite policy read threw: ${
          error instanceof Error ? error.message : String(error)
        }; using default policy`,
      );
    }
    return defaultInvitePolicy();
  }
}

export function inviteBonusCredits(
  rewardTarget: "invitee" | "inviter",
  policy: InvitePolicy = defaultInvitePolicy(),
): number {
  return rewardTarget === "invitee" ? policy.invitee_bonus_credits : policy.inviter_bonus_credits;
}

export function resolveInviteCodeUsageLimit(
  codeType: "official" | "user",
  maxUses: number | null,
  policy: InvitePolicy = defaultInvitePolicy(),
): number | null {
  if (codeType === "user") return policy.per_user_limit;
  return maxUses;
}
