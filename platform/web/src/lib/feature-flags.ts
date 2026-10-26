import { makeSupabaseFetcher, readStarcoplayConfigEnvironment, readSystemConfig, ConfigError } from "./system-config";
import type { SupabaseClient } from "@supabase/supabase-js";

export const CHANNELS_UI_CONFIG = {
  domain: "channels",
  configKey: "ui_enabled",
} as const;

export async function isBooleanFeatureEnabled(
  supabase: SupabaseClient,
  options: { domain: string; configKey: string },
): Promise<boolean> {
  try {
    const config = await readSystemConfig({
      fetcher: makeSupabaseFetcher(supabase),
      domain: options.domain,
      configKey: options.configKey,
      environment: readStarcoplayConfigEnvironment(),
    });
    return config.value_type === "bool" && config.parsed_value === true;
  } catch (err) {
    if (err instanceof ConfigError && (err.code === "CONFIG_NOT_FOUND" || err.code === "CONFIG_DISABLED")) {
      return false;
    }
    throw err;
  }
}

export function isChannelsUiEnabled(supabase: SupabaseClient): Promise<boolean> {
  return isBooleanFeatureEnabled(supabase, CHANNELS_UI_CONFIG);
}
