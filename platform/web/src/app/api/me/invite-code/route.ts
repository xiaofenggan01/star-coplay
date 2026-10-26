import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { createServiceClient } from "@/lib/supabase-server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

const INVITE_ENABLED_USER_TYPES = ["beta_user", "user", "member"] as const;

interface InviteCodeRow {
  code: string;
  used_count: number;
  max_uses: number | null;
  disabled: boolean;
  expires_at: string | null;
  created_at: string;
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function GET(req: NextRequest) {
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return NextResponse.json(
      { error: "unauthorized", reason: "invalid_token" },
      { status: 401, headers: corsHeaders },
    );
  }

  const supabase = createServiceClient();
  const { data: profile, error: profileErr } = await supabase
    .from("profiles")
    .select("user_type")
    .eq("user_id", authedUser.user_id)
    .maybeSingle();

  if (profileErr) {
    return NextResponse.json(
      { error: profileErr.message, reason: "profile_read_failed" },
      { status: 500, headers: corsHeaders },
    );
  }

  const userType = typeof profile?.user_type === "string" ? profile.user_type : "guest";
  if (!INVITE_ENABLED_USER_TYPES.includes(userType as (typeof INVITE_ENABLED_USER_TYPES)[number])) {
    return NextResponse.json(
      { invite_code: null, eligible: false, reason: "not_enabled_user_type" },
      { headers: corsHeaders },
    );
  }

  const { data, error } = await supabase
    .from("invite_codes")
    .select("code, used_count, max_uses, disabled, expires_at, created_at")
    .eq("type", "user")
    .eq("owner_user_id", authedUser.user_id)
    .maybeSingle<InviteCodeRow>();

  if (error) {
    return NextResponse.json(
      { error: error.message, reason: "invite_code_read_failed" },
      { status: 500, headers: corsHeaders },
    );
  }

  return NextResponse.json(
    {
      invite_code: data?.code ?? null,
      eligible: true,
      used_count: data?.used_count ?? 0,
      max_uses: data?.max_uses ?? null,
      disabled: data?.disabled ?? false,
      expires_at: data?.expires_at ?? null,
      created_at: data?.created_at ?? null,
    },
    { headers: corsHeaders },
  );
}
