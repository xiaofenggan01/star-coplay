import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { createServiceClient } from "@/lib/supabase-server";
import {
  getUserInviteStatus,
  generateUserInviteBatch,
} from "@/lib/user-invite";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

/** Helper: authenticate + check non-guest */
async function authenticateNonGuest(req: NextRequest) {
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return {
      error: NextResponse.json(
        { error: "unauthorized" },
        { status: 401, headers: corsHeaders },
      ),
      userId: null,
    };
  }

  const supabase = createServiceClient();
  const { data: profile } = await supabase
    .from("profiles")
    .select("user_type")
    .eq("user_id", authedUser.user_id)
    .maybeSingle();

  if (profile?.user_type === "guest") {
    return {
      error: NextResponse.json(
        { error: "guest users cannot manage invitations" },
        { status: 403, headers: corsHeaders },
      ),
      userId: null,
    };
  }

  return { error: null, userId: authedUser.user_id };
}

/**
 * GET /api/me/invitations — 获取邀请状态
 */
export async function GET(req: NextRequest) {
  const { error, userId } = await authenticateNonGuest(req);
  if (error || !userId) {
    return error ?? NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  try {
    const status = await getUserInviteStatus(userId);
    return NextResponse.json(status, { headers: corsHeaders });
  } catch (err) {
    console.error("[invitations] GET error:", err);
    return NextResponse.json(
      { error: "internal server error" },
      { status: 500, headers: corsHeaders },
    );
  }
}

/**
 * POST /api/me/invitations — 生成新一批邀请码
 */
export async function POST(req: NextRequest) {
  const { error, userId } = await authenticateNonGuest(req);
  if (error || !userId) {
    return error ?? NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  try {
    const result = await generateUserInviteBatch(userId);

    if (!result.ok) {
      const status =
        result.reason === "max_batches_reached" ? 429 : 400;
      return NextResponse.json(
        { ok: false, reason: result.reason },
        { status, headers: corsHeaders },
      );
    }

    return NextResponse.json(result, { status: 201, headers: corsHeaders });
  } catch (err) {
    console.error("[invitations] POST error:", err);
    return NextResponse.json(
      { error: "internal server error" },
      { status: 500, headers: corsHeaders },
    );
  }
}
