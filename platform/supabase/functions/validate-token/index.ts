// @ts-nocheck
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { handleCors, json, error } from "../_shared/cors.ts";

const DEFAULT_KEY_ACCESS_USER_TYPES = ["beta_user", "user", "member"];

function parseAllowedUserTypes(raw: string | null): string[] {
  if (!raw) return DEFAULT_KEY_ACCESS_USER_TYPES;
  try {
    const parsed = JSON.parse(raw);
    const value = Array.isArray(parsed?.key_access_user_types)
      ? parsed.key_access_user_types
      : [];
    const types = value
      .filter((item: unknown) => typeof item === "string")
      .map((item: string) => item.trim())
      .filter(Boolean);
    return types.length > 0 ? types : DEFAULT_KEY_ACCESS_USER_TYPES;
  } catch {
    return DEFAULT_KEY_ACCESS_USER_TYPES;
  }
}

async function readAllowedUserTypes(supabase: any): Promise<string[]> {
  const environment = Deno.env.get("STARCOPLAY_CONFIG_ENV") || "prod";
  const { data } = await supabase
    .from("system_configs")
    .select("config_value")
    .eq("domain", "platform_access")
    .eq("config_key", "onboarding_policy")
    .eq("environment", environment)
    .eq("status", 1)
    .maybeSingle();
  return parseAllowedUserTypes(data?.config_value ?? null);
}

Deno.serve(async (req) => {
  const cors = handleCors(req);
  if (cors) return cors;

  if (req.method !== "POST") return error("method not allowed", 405);

  const raw = await req.text();
  let body: { platform_token?: string };
  try {
    body = JSON.parse(raw) as typeof body;
  } catch {
    return error("invalid json", 400);
  }

  const token = (body.platform_token ?? "").trim();
  if (!token) return error("platform_token required", 400);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  const { data: tokenRow, error: tokenErr } = await supabase
    .from("platform_tokens")
    .select("user_id")
    .eq("token", token)
    .maybeSingle();

  if (tokenErr) return error(tokenErr.message, 500);

  if (!tokenRow) {
    return json({ valid: false, error: "invalid_token" }, 401);
  }

  const allowedTypes = await readAllowedUserTypes(supabase);
  const { data: profile, error: profileErr } = await supabase
    .from("profiles")
    .select("user_type")
    .eq("user_id", tokenRow.user_id)
    .maybeSingle();
  if (profileErr) return error(profileErr.message, 500);
  if (!allowedTypes.includes(profile?.user_type)) {
    return json({ valid: false, error: "access_not_approved", reason: "access_not_approved" }, 403);
  }

  const { data: user, error: userErr } = await supabase
    .from("users")
    .select("user_id, nickname")
    .eq("user_id", tokenRow.user_id)
    .maybeSingle();
  if (userErr) return error(userErr.message, 500);
  if (!user) return json({ valid: false, error: "invalid_token" }, 401);

  // 写入漏斗事件：token_validated（用户下载并激活）
  const { error: evErr } = await supabase.from("events").insert({
    user_id: user.user_id,
    type: "funnel.token_validated",
    ts: new Date().toISOString(),
    session_id: "setup_ui",
    data: {},
    received_at: new Date().toISOString(),
  });
  if (evErr) console.warn("funnel event insert failed:", evErr.message);

  return json({
    valid: true,
    user_id: user.user_id,
    nickname: user.nickname,
  });
});
