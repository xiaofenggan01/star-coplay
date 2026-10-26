#!/usr/bin/env tsx

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { openPlatformAccess } from "../src/lib/platform-access-open";

type UserRow = {
  user_id: string;
  email: string | null;
  nickname: string | null;
  invite_code_used: string | null;
  created_at: string;
};

type ProfileRow = {
  user_id: string;
  user_type: string | null;
  waitlist_id: number | null;
};

type InviteCodeRow = {
  code: string;
  type: "official" | "user";
  official_code_reason: string | null;
};

function env(name: string): string {
  const value = process.env[name]?.trim() ?? "";
  if (!value) {
    throw new Error(`missing env ${name}`);
  }
  return value;
}

function createServiceClient(): SupabaseClient {
  return createClient(env("NEXT_PUBLIC_SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function main() {
  const cutoff = new Date(process.env.CUTOFF_ISO ?? "2026-06-30T16:00:00.000Z").toISOString();
  const dryRun = process.argv.includes("--dry-run");
  const supabase = createServiceClient();

  const { data: users, error: usersErr } = await supabase
    .from("users")
    .select("user_id,email,nickname,invite_code_used,created_at")
    .gt("created_at", cutoff)
    .order("created_at", { ascending: true });
  if (usersErr) throw new Error(`users query failed: ${usersErr.message}`);

  const userRows = (users ?? []) as UserRow[];
  const userIds = userRows.map((u) => u.user_id);
  const inviteCodes = [...new Set(userRows.map((u) => u.invite_code_used?.trim()).filter(Boolean))] as string[];

  const { data: profiles, error: profilesErr } = await supabase
    .from("profiles")
    .select("user_id,user_type,waitlist_id")
    .in("user_id", userIds);
  if (profilesErr) throw new Error(`profiles query failed: ${profilesErr.message}`);

  const { data: codes, error: codesErr } = await supabase
    .from("invite_codes")
    .select("code,type,official_code_reason")
    .in("code", inviteCodes);
  if (codesErr) throw new Error(`invite_codes query failed: ${codesErr.message}`);

  const profileMap = new Map((profiles ?? []).map((p) => [p.user_id, p as ProfileRow]));
  const codeMap = new Map((codes ?? []).map((c) => [c.code, c as InviteCodeRow]));

  const candidates = userRows.filter((user) => {
    const profile = profileMap.get(user.user_id);
    const invite = user.invite_code_used ? codeMap.get(user.invite_code_used) : null;
    return profile?.user_type === "guest" && invite?.type === "official";
  });

  console.log(`cutoff=${cutoff}`);
  console.log(`candidates=${candidates.length}`);
  if (candidates.length === 0) return;

  let ok = 0;
  let skipped = 0;
  let failed = 0;

  for (const user of candidates) {
    const profile = profileMap.get(user.user_id) ?? null;
    const invite = user.invite_code_used ? codeMap.get(user.invite_code_used) ?? null : null;
    const label = `${user.user_id} ${user.email ?? "-"} code=${user.invite_code_used ?? "-"} reason=${invite?.official_code_reason ?? "-"}`;

    if (dryRun) {
      console.log(`[DRY] ${label}`);
      skipped++;
      continue;
    }

    try {
      const result = await openPlatformAccess(
        {
          user_id: user.user_id,
          email: user.email,
          display_name: user.nickname ?? user.email ?? user.user_id,
          waitlist_id: profile?.waitlist_id ?? null,
          source: "ops_upgrade_official_guest_20260701",
        },
        supabase,
      );
      console.log(
        `[OK] ${label} relay_status=${result.ai_relay.status ?? "-"} invite_code=${result.invite_code ?? "-"}`,
      );
      ok++;
    } catch (error) {
      console.error(`[FAIL] ${label} error=${error instanceof Error ? error.message : String(error)}`);
      failed++;
    }
  }

  console.log(`done ok=${ok} failed=${failed} skipped=${skipped}`);
  if (failed > 0) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exit(1);
});
