import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { getPlatformKeyAccess } from "@/lib/platform-access";
import { createServiceClient } from "@/lib/supabase-server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function GET(req: NextRequest) {
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  const supabase = createServiceClient();
  const access = await getPlatformKeyAccess(authedUser.user_id, supabase);
  if (!access.ok) {
    return NextResponse.json(
      { error: access.code, reason: access.code },
      { status: access.status, headers: corsHeaders },
    );
  }

  const { data, error } = await supabase
    .from("platform_tokens")
    .select("id, token, name, created_at")
    .eq("user_id", authedUser.user_id);

  if (error) {
    return NextResponse.json(
      { error: error.message },
      { status: 500, headers: corsHeaders },
    );
  }

  return NextResponse.json({ tokens: data }, { headers: corsHeaders });
}

export async function POST(req: NextRequest) {
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  const supabaseForCheck = createServiceClient();
  const access = await getPlatformKeyAccess(authedUser.user_id, supabaseForCheck);
  if (!access.ok) {
    return NextResponse.json(
      { error: access.code, reason: access.code },
      { status: access.status, headers: corsHeaders },
    );
  }

  let name = "默认令牌";
  try {
    const body = await req.json();
    if (body?.name && typeof body.name === "string") {
      name = body.name;
    }
  } catch {
    // body 为空或非 JSON，使用默认名称
  }

  const supabase = supabaseForCheck;
  const { data, error } = await supabase
    .from("platform_tokens")
    .insert({ user_id: authedUser.user_id, name })
    .select("id, token, name, created_at")
    .single();

  if (error) {
    return NextResponse.json(
      { error: error.message },
      { status: 500, headers: corsHeaders },
    );
  }

  return NextResponse.json({ token: data }, { status: 201, headers: corsHeaders });
}

export async function DELETE(req: NextRequest) {
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  const supabaseForCheck = createServiceClient();
  const access = await getPlatformKeyAccess(authedUser.user_id, supabaseForCheck);
  if (!access.ok) {
    return NextResponse.json(
      { error: access.code, reason: access.code },
      { status: access.status, headers: corsHeaders },
    );
  }

  let id: string | undefined;
  try {
    const body = await req.json();
    id = body?.id;
  } catch {
    // ignore
  }

  if (!id) {
    return NextResponse.json(
      { error: "missing id" },
      { status: 400, headers: corsHeaders },
    );
  }

  const supabase = supabaseForCheck;

  // 防越权：确认该 token 属于当前用户
  const { data: existing, error: fetchError } = await supabase
    .from("platform_tokens")
    .select("id")
    .eq("id", id)
    .eq("user_id", authedUser.user_id)
    .single();

  if (fetchError || !existing) {
    return NextResponse.json(
      { error: "token not found" },
      { status: 404, headers: corsHeaders },
    );
  }

  const { error: deleteError } = await supabase
    .from("platform_tokens")
    .delete()
    .eq("id", id);

  if (deleteError) {
    return NextResponse.json(
      { error: deleteError.message },
      { status: 500, headers: corsHeaders },
    );
  }

  return NextResponse.json({ success: true }, { headers: corsHeaders });
}
