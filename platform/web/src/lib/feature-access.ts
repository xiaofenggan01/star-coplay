import { BackendInternalError } from "./backend-internal";
import { makeSupabaseFetcher, readStarcoplayConfigEnvironment, readSystemConfig, ConfigError } from "./system-config";
import type { AuthenticatedUser } from "./types/auth";
import type { SupabaseClient } from "@supabase/supabase-js";

type AccessConfig = {
  enabled?: boolean;
  allow_all?: boolean;
  emails?: string[];
  user_ids?: string[];
};

interface FeatureAccessOptions {
  domain: string;
  configKey: string;
  errorMessage: string;
}

function normalizeList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean);
}

function parseAccessConfig(value: unknown): AccessConfig {
  if (Array.isArray(value)) {
    return { enabled: true, emails: normalizeList(value) };
  }
  if (!value || typeof value !== "object") {
    return { enabled: true, allow_all: true };
  }
  const obj = value as Record<string, unknown>;
  return {
    enabled: typeof obj.enabled === "boolean" ? obj.enabled : true,
    allow_all: obj.allow_all === true,
    emails: normalizeList(obj.emails ?? obj.allowed_emails),
    user_ids: normalizeList(obj.user_ids ?? obj.allowed_user_ids),
  };
}

export async function canAccessFeatureByWhitelist(
  user: Pick<AuthenticatedUser, "user_id" | "email">,
  supabase: SupabaseClient,
  options: FeatureAccessOptions,
): Promise<boolean> {
  try {
    const fetcher = makeSupabaseFetcher(supabase);
    const environment = readStarcoplayConfigEnvironment();
    const config = await readSystemConfig({
      fetcher,
      domain: options.domain,
      configKey: options.configKey,
      environment,
    });
    const access = parseAccessConfig(config.parsed_value);
    if (access.enabled === false) return false;
    if (access.allow_all) return true;

    const email = (user.email ?? "").trim().toLowerCase();
    const allowedEmails = new Set((access.emails ?? []).map((item) => item.toLowerCase()));
    const allowedUserIds = new Set(access.user_ids ?? []);
    return allowedUserIds.has(user.user_id) || (!!email && allowedEmails.has(email));
  } catch (err) {
    if (err instanceof ConfigError && (err.code === "CONFIG_NOT_FOUND" || err.code === "CONFIG_DISABLED")) {
      return false;
    }
    throw new BackendInternalError(options.errorMessage, 503, err);
  }
}
