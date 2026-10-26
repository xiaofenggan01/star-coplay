/**
 * /api/me/appearance business logic.
 *
 * Platform can only read appearance. The write path is Mod -> Gateway
 * appearance_sync -> profiles.config.appearance.
 */

import { createServiceClient } from "./supabase-server";
import type { AppearanceConfig } from "./types/appearance";
import type { AuthenticatedUser } from "./types/auth";

export type MeAppearanceResult =
  | { status: number; body: { appearance: AppearanceConfig | null } }
  | { status: number; body: { error: string; reason?: string } };

export interface MeAppearanceDeps {
  getConfig: (user_id: string) => Promise<Record<string, unknown> | null>;
}

export async function handleGetAppearance(
  deps: MeAppearanceDeps,
  user: AuthenticatedUser,
): Promise<MeAppearanceResult> {
  try {
    const config = await deps.getConfig(user.user_id);
    return {
      status: 200,
      body: { appearance: (config?.appearance as AppearanceConfig | undefined) ?? null },
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "internal_error" } };
  }
}

export function buildDefaultMeAppearanceDeps(): MeAppearanceDeps {
  return {
    async getConfig(user_id) {
      const supabase = createServiceClient();
      const { data, error } = await supabase
        .from("profiles")
        .select("config")
        .eq("user_id", user_id)
        .maybeSingle();
      if (error) throw new Error(`getConfig failed: ${error.message}`);
      if (!data) return null;
      return (data.config ?? {}) as Record<string, unknown>;
    },
  };
}
