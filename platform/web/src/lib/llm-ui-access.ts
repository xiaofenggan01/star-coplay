import { canAccessFeatureByWhitelist } from "./feature-access";
import type { AuthenticatedUser } from "./types/auth";
import type { SupabaseClient } from "@supabase/supabase-js";

const DOMAIN = "ai_relay";
const CONFIG_KEY = "ui_access_whitelist";

export async function canAccessLlmUi(
  user: Pick<AuthenticatedUser, "user_id" | "email">,
  supabase: SupabaseClient,
): Promise<boolean> {
  return canAccessFeatureByWhitelist(user, supabase, {
    domain: DOMAIN,
    configKey: CONFIG_KEY,
    errorMessage: "llm ui access config failed",
  });
}
