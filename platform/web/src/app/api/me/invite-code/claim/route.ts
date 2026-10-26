import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { peekInviteCode } from "@/lib/invite-code";
import { openPlatformAccessByOfficialInvite } from "@/lib/platform-access-official-open";
import { openPlatformAccessByUserInvite } from "@/lib/platform-access-invite-open";
import { createServiceClient } from "@/lib/supabase-server";
import { getUserInviteStatus } from "@/lib/user-invite";
import type { Profile } from "@/lib/types/profile";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function POST(req: NextRequest) {
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return NextResponse.json(
      { error: "unauthorized", reason: "invalid_token" },
      { status: 401, headers: corsHeaders },
    );
  }

  let rawBody: unknown;
  try {
    rawBody = await req.json();
  } catch {
    return NextResponse.json(
      { error: "invalid JSON body", reason: "invalid_input" },
      { status: 422, headers: corsHeaders },
    );
  }

  const inviteCode = normalizeInviteCode(
    typeof rawBody === "object" && rawBody !== null && !Array.isArray(rawBody)
      ? (rawBody as Record<string, unknown>).invite_code
      : "",
  );
  if (!inviteCode) {
    return NextResponse.json(
      { error: "invite_code required", reason: "missing_field" },
      { status: 400, headers: corsHeaders },
    );
  }

  const supabase = createServiceClient();
  const profile = await readProfile(authedUser.user_id, supabase);
  if (profile && profile.user_type !== "guest") {
    return NextResponse.json(
      {
        ok: true,
        already_opened: true,
        profile,
        invite_status: await getUserInviteStatus(authedUser.user_id, supabase),
      },
      { headers: corsHeaders },
    );
  }

  const peeked = await peekInviteCode(inviteCode, supabase);
  if (!peeked.ok) {
    return NextResponse.json(
      { ok: false, error: claimErrorMessage(peeked.reason), reason: peeked.reason },
      { status: claimStatus(peeked.reason), headers: corsHeaders },
    );
  }

  const commonInput = {
    user_id: authedUser.user_id,
    email: authedUser.email,
    display_name: profile?.nickname ?? authedUser.email,
    invite_code: inviteCode,
    source: "me_invite_code_claim",
  };
  const result =
    peeked.code.type === "official"
      ? await openPlatformAccessByOfficialInvite(commonInput, supabase)
      : await openPlatformAccessByUserInvite(commonInput, supabase);

  if (!result.ok) {
    return NextResponse.json(
      { ok: false, error: claimErrorMessage(result.reason), reason: result.reason },
      { status: claimStatus(result.reason), headers: corsHeaders },
    );
  }

  const updatedProfile = await readProfile(authedUser.user_id, supabase);
  return NextResponse.json(
    {
      ok: true,
      already_opened: false,
      profile: updatedProfile,
      open_access: result.result,
      invite_status: await getUserInviteStatus(authedUser.user_id, supabase),
    },
    { headers: corsHeaders },
  );
}

function normalizeInviteCode(raw: unknown): string {
  let value = typeof raw === "string" ? raw.trim().normalize("NFKC") : "";
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    value = value.slice(1, -1).trim();
  }
  return value.replace(/[‐‑‒–—―－]/g, "-");
}

async function readProfile(userId: string, supabase: ReturnType<typeof createServiceClient>) {
  const { data, error } = await supabase
    .from("profiles")
    .select(
      "user_id, nickname, avatar_url, preferences, config, referrer_user_id, official_invite_note, user_type, created_at, updated_at",
    )
    .eq("user_id", userId)
    .maybeSingle();
  if (error) {
    throw new Error(`profile read failed: ${error.message}`);
  }
  return (data as Profile | null) ?? null;
}

function claimStatus(reason: string): number {
  if (reason === "relation_already_bound" || reason === "self_invite") return 409;
  if (reason === "open_access_failed") return 503;
  if (reason === "invite_limit_reached" || reason === "exhausted") return 409;
  return 403;
}

function claimErrorMessage(reason: string): string {
  if (reason === "not_found") return "邀请码不存在";
  if (reason === "disabled") return "邀请码已被禁用";
  if (reason === "expired") return "邀请码已过期";
  if (reason === "invite_limit_reached" || reason === "exhausted") return "邀请码名额已用完";
  if (reason === "not_user_code" || reason === "not_official_code" || reason === "missing_owner") {
    return "邀请码类型不支持当前操作";
  }
  if (reason === "self_invite") return "不能使用自己的邀请码";
  if (reason === "relation_already_bound") return "当前账号已经绑定过邀请关系";
  return "开通失败，请稍后重试";
}
