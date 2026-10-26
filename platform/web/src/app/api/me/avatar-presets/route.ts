import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { readAvatarPresets } from "@/lib/avatar-presets";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

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

  const items = await readAvatarPresets();
  return NextResponse.json({ items }, { headers: corsHeaders });
}
