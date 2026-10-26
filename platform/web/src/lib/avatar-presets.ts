import {
  ConfigError,
  makeSupabaseFetcher,
  readStarcoplayConfigEnvironment,
  readSystemConfig,
} from "./system-config";
import { createServiceClient } from "./supabase-server";
import type { SupabaseClient } from "@supabase/supabase-js";

const DOMAIN = "profile";
const CONFIG_KEY = "avatar_presets";

export interface AvatarPreset {
  id: string;
  src: string;
  label: string;
}

interface AvatarPresetConfigItem {
  id?: unknown;
  src?: unknown;
  label?: unknown;
  enabled?: unknown;
  order?: unknown;
}

export const DEFAULT_AVATAR_PRESETS: AvatarPreset[] = [
  { id: "the_player", src: "/assets/icons/The_Player_Icon.png", label: "Player" },
  { id: "forester", src: "/assets/icons/Forester.png", label: "Forester" },
  { id: "junimo", src: "/assets/icons/Junimo_Icon.png", label: "Junimo" },
  { id: "smapi_mascot", src: "/assets/icons/SMAPI_mascot.png", label: "SMAPI" },
  { id: "marriage", src: "/assets/icons/Marriage_Icon.png", label: "Marriage" },
  { id: "multiplayer", src: "/assets/icons/Multiplayer_Icon.png", label: "Multiplayer" },
  { id: "achievements", src: "/assets/icons/Achievements_Icon.png", label: "Achievements" },
  { id: "bundle", src: "/assets/icons/Bundle_Icon.png", label: "Bundle" },
  { id: "quests", src: "/assets/icons/Quests_Icon.png", label: "Quests" },
  { id: "golden_walnut", src: "/assets/icons/Golden_Walnut.png", label: "Golden Walnut" },
  { id: "quality_sprinkler", src: "/assets/icons/Quality_Sprinkler.png", label: "Sprinkler" },
  { id: "health", src: "/assets/icons/Health.png", label: "Health" },
  { id: "energy", src: "/assets/icons/Energy.png", label: "Energy" },
  { id: "cinder_shard", src: "/assets/icons/Cinder_Shard.png", label: "Cinder Shard" },
  { id: "sweet_pea", src: "/assets/icons/Sweet_Pea.png", label: "Sweet Pea" },
  { id: "dandelion", src: "/assets/icons/Dandelion.png", label: "Dandelion" },
  { id: "apple_sapling", src: "/assets/icons/Apple_Sapling.png", label: "Apple Sapling" },
  { id: "corn", src: "/assets/icons/Corn.png", label: "Corn" },
  { id: "book_of_mysteries", src: "/assets/icons/Book_of_Mysteries.png", label: "Book" },
  { id: "shipping_box", src: "/assets/icons/ShippingBox.png", label: "Shipping Box" },
];

export function isValidAvatarAssetPath(src: string): boolean {
  return /^\/assets\/icons\/[A-Za-z0-9_-]+\.png$/.test(src);
}

export function normalizeAvatarPresets(raw: unknown): AvatarPreset[] {
  const obj =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  if (obj.enabled === false || !Array.isArray(obj.items)) {
    return DEFAULT_AVATAR_PRESETS;
  }

  const seen = new Set<string>();
  const items = (obj.items as AvatarPresetConfigItem[])
    .map((item, index) => {
      const src = typeof item.src === "string" ? item.src.trim() : "";
      const id = typeof item.id === "string" ? item.id.trim() : "";
      const label = typeof item.label === "string" ? item.label.trim() : "";
      const order = typeof item.order === "number" ? item.order : index;
      return { id, src, label, enabled: item.enabled !== false, order };
    })
    .filter((item) => item.enabled && item.id && isValidAvatarAssetPath(item.src))
    .filter((item) => {
      if (seen.has(item.src)) return false;
      seen.add(item.src);
      return true;
    })
    .sort((a, b) => a.order - b.order)
    .map(({ id, src, label }) => ({ id, src, label: label || id }));

  return items.length > 0 ? items : DEFAULT_AVATAR_PRESETS;
}

export async function readAvatarPresets(
  supabase: SupabaseClient = createServiceClient(),
): Promise<AvatarPreset[]> {
  try {
    const config = await readSystemConfig({
      fetcher: makeSupabaseFetcher(supabase),
      domain: DOMAIN,
      configKey: CONFIG_KEY,
      environment: readStarcoplayConfigEnvironment(),
    });
    return normalizeAvatarPresets(config.parsed_value);
  } catch (error) {
    if (error instanceof ConfigError) {
      console.warn(
        `[avatar-presets] config read failed ${error.code}: ${error.message}; using defaults`,
      );
    } else {
      console.warn(
        `[avatar-presets] config read threw: ${
          error instanceof Error ? error.message : String(error)
        }; using defaults`,
      );
    }
    return DEFAULT_AVATAR_PRESETS;
  }
}

export async function isEnabledAvatarPreset(
  src: string,
  supabase: SupabaseClient = createServiceClient(),
): Promise<boolean> {
  const normalized = src.trim();
  if (!isValidAvatarAssetPath(normalized)) return false;
  const presets = await readAvatarPresets(supabase);
  return presets.some((preset) => preset.src === normalized);
}
