import { canAccessFeatureByWhitelist } from "./feature-access";
import type { AuthenticatedUser } from "./types/auth";
import type { SupabaseClient } from "@supabase/supabase-js";

const DOMAIN = "agentee_persona";
const CONFIG_KEY = "access_whitelist";

export async function canAccessAgenteePersona(
  user: Pick<AuthenticatedUser, "user_id" | "email">,
  supabase: SupabaseClient,
): Promise<boolean> {
  return canAccessFeatureByWhitelist(user, supabase, {
    domain: DOMAIN,
    configKey: CONFIG_KEY,
    errorMessage: "persona access config failed",
  });
}
