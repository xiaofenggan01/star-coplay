import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { getGatewayHttpBaseUrl } from "@/lib/gateway-url";
import { getPlatformTokenForUser } from "@/lib/internal-auth";
import { isPlatformAccessError } from "@/lib/platform-access";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function GET(req: NextRequest) {
  // 阶段四 A4a：双轨鉴权
  const authedUser = await authenticateRequest(req);
  if (!authedUser) {
    return NextResponse.json(
      { error: "unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  // WS HTTP API 只认 platform_token；JWT 用户需反查 platform_token 再透传
  let platformToken: string | null;
  try {
    platformToken = await getPlatformTokenForUser(authedUser.user_id);
  } catch (error) {
    if (isPlatformAccessError(error)) {
      return NextResponse.json(
        { error: error.code, reason: error.code },
        { status: error.status, headers: corsHeaders },
      );
    }
    throw error;
  }
  if (!platformToken) {
    return NextResponse.json(
      { error: "platform_token_unavailable" },
      { status: 500, headers: corsHeaders },
    );
  }

  const base = getGatewayHttpBaseUrl();
  try {
    const res = await fetch(`${base}/api/v1/sessions/current`, {
      headers: { Authorization: `Bearer ${platformToken}` },
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status, headers: corsHeaders });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "upstream error";
    return NextResponse.json(
      { online: false, error: msg },
      { status: 502, headers: corsHeaders },
    );
  }
}
