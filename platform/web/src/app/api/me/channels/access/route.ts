import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { isChannelsUiEnabled } from "@/lib/feature-flags";
import { createServiceClient } from "@/lib/supabase-server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function GET(req: NextRequest) {
  try {
    const user = await authenticateRequest(req);
    if (!user) {
      return NextResponse.json(
        { error: "unauthorized", reason: "invalid_token" },
        { status: 401, headers: corsHeaders },
      );
    }

    const enabled = await isChannelsUiEnabled(createServiceClient());
    if (!enabled) {
      return NextResponse.json(
        { error: "forbidden", reason: "channels_ui_disabled" },
        { status: 403, headers: corsHeaders },
      );
    }

    return NextResponse.json({ allowed: true }, { headers: corsHeaders });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return NextResponse.json(
      { error: msg, reason: "internal_error" },
      { status: 500, headers: corsHeaders },
    );
  }
}
