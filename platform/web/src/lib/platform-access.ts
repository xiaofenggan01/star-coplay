import {
  ConfigError,
  makeSupabaseFetcher,
  readStarcoplayConfigEnvironment,
  readSystemConfig,
} from "./system-config";
import { createServiceClient } from "./supabase-server";
import type { SupabaseClient } from "@supabase/supabase-js";

const DOMAIN = "platform_access";
const CONFIG_KEY = "onboarding_policy";

export const DEFAULT_KEY_ACCESS_USER_TYPES = ["beta_user", "user", "member"] as const;

export interface PlatformOnboardingPolicy {
  mode: "manual_review";
  key_issue_policy: "approval_required" | "allow_invited_registered" | "allow_all_registered";
  key_access_user_types: string[];
}

export type PlatformKeyAccessResult =
  | { ok: true; user_type: string; policy: PlatformOnboardingPolicy }
  | {
      ok: false;
      status: number;
      code: PlatformAccessErrorCode;
      message: string;
      user_type?: string | null;
      policy?: PlatformOnboardingPolicy;
    };

export type PlatformAccessErrorCode = "access_not_approved" | "access_check_failed";

export class PlatformAccessError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: PlatformAccessErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "PlatformAccessError";
  }
}

export function isPlatformAccessError(error: unknown): error is PlatformAccessError {
  return error instanceof PlatformAccessError;
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parsePolicy(value: unknown): PlatformOnboardingPolicy {
  const obj = value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
  const keyIssuePolicy = obj.key_issue_policy;
  const keyAccessUserTypes = normalizeStringList(obj.key_access_user_types);

  return {
    mode: "manual_review",
    key_issue_policy:
      keyIssuePolicy === "allow_invited_registered" || keyIssuePolicy === "allow_all_registered"
        ? keyIssuePolicy
        : "approval_required",
    key_access_user_types:
      keyAccessUserTypes.length > 0 ? keyAccessUserTypes : [...DEFAULT_KEY_ACCESS_USER_TYPES],
  };
}

export function defaultPlatformOnboardingPolicy(): PlatformOnboardingPolicy {
  return {
    mode: "manual_review",
    key_issue_policy: "approval_required",
    key_access_user_types: [...DEFAULT_KEY_ACCESS_USER_TYPES],
  };
}

export async function readPlatformOnboardingPolicy(
  supabase: SupabaseClient = createServiceClient(),
): Promise<PlatformOnboardingPolicy> {
  try {
    const fetcher = makeSupabaseFetcher(supabase);
    const config = await readSystemConfig({
      fetcher,
      domain: DOMAIN,
      configKey: CONFIG_KEY,
      environment: readStarcoplayConfigEnvironment(),
    });
    return parsePolicy(config.parsed_value);
  } catch (error) {
    if (error instanceof ConfigError) {
      console.warn(
        `[platform-access] policy read failed ${error.code}: ${error.message}; using fail-closed default`,
      );
    } else {
      console.warn(
        `[platform-access] policy read threw: ${
          error instanceof Error ? error.message : String(error)
        }; using fail-closed default`,
      );
    }
    return defaultPlatformOnboardingPolicy();
  }
}

export async function getPlatformKeyAccess(
  userId: string,
  supabase: SupabaseClient = createServiceClient(),
): Promise<PlatformKeyAccessResult> {
  if (!userId) {
    return {
      ok: false,
      status: 403,
      code: "access_not_approved",
      message: "platform access is not approved",
      user_type: null,
      policy: defaultPlatformOnboardingPolicy(),
    };
  }

  const policy = await readPlatformOnboardingPolicy(supabase);
  const { data, error } = await supabase
    .from("profiles")
    .select("user_type")
    .eq("user_id", userId)
    .maybeSingle();

  if (error) {
    return {
      ok: false,
      status: 503,
      code: "access_check_failed",
      message: error.message,
      policy,
    };
  }

  const userType = typeof data?.user_type === "string" ? data.user_type : null;
  if (!userType || !policy.key_access_user_types.includes(userType)) {
    return {
      ok: false,
      status: 403,
      code: "access_not_approved",
      message: "platform access is not approved",
      user_type: userType,
      policy,
    };
  }

  return { ok: true, user_type: userType, policy };
}

export async function assertPlatformKeyAccess(
  userId: string,
  supabase: SupabaseClient = createServiceClient(),
): Promise<void> {
  const access = await getPlatformKeyAccess(userId, supabase);
  if (!access.ok) {
    throw new PlatformAccessError(access.status, access.code, access.message);
  }
}
