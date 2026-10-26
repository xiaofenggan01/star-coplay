/**
 * GET/PATCH /api/me/profile
 *
 * 双轨鉴权（authenticateRequest）。GET 找不到 profile 返回 200 + null（老用户尚未创建）。
 * PATCH 服务端白名单过滤：仅 nickname / avatar_url / preferences。
 *
 * 业务逻辑见 lib/me-profile-handler.ts；本文件仅保留 HTTP 薄包装。
 */
import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import {
  buildDefaultMeProfileDeps,
  handleMeProfileGet,
  handleMeProfilePatch,
} from "@/lib/me-profile-handler";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, PATCH, OPTIONS",
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
    const deps = buildDefaultMeProfileDeps();
    const result = await handleMeProfileGet(deps, user);
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

export async function PATCH(req: NextRequest) {
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
    const deps = buildDefaultMeProfileDeps();
    const result = await handleMeProfilePatch(deps, user, body);
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
