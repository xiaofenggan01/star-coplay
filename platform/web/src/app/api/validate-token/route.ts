import { NextRequest, NextResponse } from "next/server";
import { getPlatformKeyAccess } from "@/lib/platform-access";
import { createServiceClient } from "@/lib/supabase-server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, apikey",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function POST(req: NextRequest) {
  const raw = await req.text();
  let body: { platform_token?: string };
  try {
    body = JSON.parse(raw);
  } catch {
    return NextResponse.json(
      { error: "invalid json" },
      { status: 400, headers: corsHeaders }
    );
  }

  const token = (body.platform_token ?? "").trim();
  if (!token) {
    return NextResponse.json(
      { error: "platform_token required" },
      { status: 400, headers: corsHeaders }
    );
  }

  const supabase = createServiceClient();

  const { data: tokenRow, error: tokenErr } = await supabase
    .from("platform_tokens")
    .select("user_id")
    .eq("token", token)
    .maybeSingle();

  if (tokenErr) {
    return NextResponse.json(
      { error: tokenErr.message },
      { status: 500, headers: corsHeaders }
    );
  }

  if (!tokenRow) {
    return NextResponse.json(
      { valid: false, error: "invalid_token" },
      { status: 401, headers: corsHeaders }
    );
  }

  const access = await getPlatformKeyAccess(tokenRow.user_id as string, supabase);
  if (!access.ok) {
    return NextResponse.json(
      { valid: false, error: access.code, reason: access.code },
      { status: access.status, headers: corsHeaders }
    );
  }

  const { data: user, error: userErr } = await supabase
    .from("users")
    .select("user_id, nickname")
    .eq("user_id", tokenRow.user_id)
    .maybeSingle();

  if (userErr) {
    return NextResponse.json(
      { error: userErr.message },
      { status: 500, headers: corsHeaders }
    );
  }

  if (!user) {
    return NextResponse.json(
      { valid: false, error: "invalid_token" },
      { status: 401, headers: corsHeaders }
    );
  }

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

  return NextResponse.json(
    {
      valid: true,
      user_id: user.user_id,
      nickname: user.nickname,
    },
    { headers: corsHeaders }
  );
}
