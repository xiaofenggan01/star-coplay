/**
 * POST /api/me/persona/generate
 *
 * 根据 tags/description 生成偏好预览（不入库）。
 */
import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { checkRateLimit } from "@/lib/rate-limit";
import {
  buildDefaultAgenteePersonaDeps,
  handleGeneratePreview,
} from "@/lib/agentee-persona-handler";
import { canAccessAgenteePersona } from "@/lib/agentee-persona-access";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function POST(req: NextRequest) {
  try {
    const user = await authenticateRequest(req);
    if (!user) {
      return NextResponse.json(
        { error: "unauthorized", reason: "invalid_token" },
        { status: 401, headers: corsHeaders },
      );
    }
    const deps = buildDefaultAgenteePersonaDeps();
    const allowed = await canAccessAgenteePersona(user, deps.supabase);
    if (!allowed) {
      return NextResponse.json(
        { error: "forbidden", reason: "persona_access_denied" },
        { status: 403, headers: corsHeaders },
      );
    }

    // Rate limit: 5 calls per minute per user
    if (!checkRateLimit(user.user_id, 5)) {
      return NextResponse.json(
        { error: "rate_limited", reason: "too_many_requests" },
        { status: 429, headers: { ...corsHeaders, "Retry-After": "60" } },
      );
    }
    let body: unknown = null;
    try {
      body = await req.json();
    } catch {
      return NextResponse.json(
        { error: "invalid JSON body", reason: "invalid_input" },
        { status: 422, headers: corsHeaders },
      );
    }
    const result = await handleGeneratePreview(body, deps);
    return NextResponse.json(result.body, {
      status: result.status,
      headers: corsHeaders,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return NextResponse.json(
      { error: msg, reason: "internal_error" },
      { status: 500, headers: corsHeaders },
    );
  }
}
