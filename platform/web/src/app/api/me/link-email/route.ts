/**
 * POST /api/me/link-email
 *
 * 老用户（platform_token 登录）补绑邮箱+密码。
 * 业务逻辑（含 trigger 双写后处理）见 lib/me-link-email-handler.ts。
 */
import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import {
  buildDefaultLinkEmailDeps,
  handleMeLinkEmail,
} from "@/lib/me-link-email-handler";

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
    let body: unknown = null;
    try {
      body = await req.json();
    } catch {
      return NextResponse.json(
        { error: "invalid JSON body", reason: "invalid_input" },
        { status: 422, headers: corsHeaders },
      );
    }
    const deps = buildDefaultLinkEmailDeps();
    const result = await handleMeLinkEmail(deps, user, body);
    return NextResponse.json(result.body, {
      status: result.status,
      headers: corsHeaders,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return NextResponse.json(
      { error: msg, reason: "link_failed" },
      { status: 500, headers: corsHeaders },
    );
  }
}
