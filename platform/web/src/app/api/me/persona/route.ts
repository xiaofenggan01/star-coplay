/**
 * GET/PUT /api/me/persona
 *
 * GET  → 读取当前用户的 agentee persona 配置
 * PUT  → 保存 agentee persona 配置（profile + persona + 生成 guidelines）
 */
import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import {
  buildDefaultAgenteePersonaDeps,
  handleGetAgenteePersona,
  handlePutAgenteePersona,
} from "@/lib/agentee-persona-handler";
import { canAccessAgenteePersona } from "@/lib/agentee-persona-access";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
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
    const deps = buildDefaultAgenteePersonaDeps();
    const allowed = await canAccessAgenteePersona(user, deps.supabase);
    if (!allowed) {
      return NextResponse.json(
        { error: "forbidden", reason: "persona_access_denied" },
        { status: 403, headers: corsHeaders },
      );
    }
    const result = await handleGetAgenteePersona(user.user_id, deps);
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

export async function PUT(req: NextRequest) {
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
    const deps = buildDefaultAgenteePersonaDeps();
    const allowed = await canAccessAgenteePersona(user, deps.supabase);
    if (!allowed) {
      return NextResponse.json(
        { error: "forbidden", reason: "persona_access_denied" },
        { status: 403, headers: corsHeaders },
      );
    }
    const result = await handlePutAgenteePersona(user.user_id, body, deps);
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
