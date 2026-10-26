/**
 * GET /api/me/appearance
 *
 * Appearance is read-only for Platform users. The source of truth is the
 * Mod-reported Farmer appearance synced through the Gateway.
 */
import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import {
  buildDefaultMeAppearanceDeps,
  handleGetAppearance,
} from "@/lib/me-appearance-handler";

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
    const deps = buildDefaultMeAppearanceDeps();
    const result = await handleGetAppearance(deps, user);
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

export async function PUT() {
  return NextResponse.json(
    {
      error: "appearance is synced from the Stardew Valley Mod and cannot be edited here",
      reason: "mod_synced_readonly",
    },
    { status: 405, headers: corsHeaders },
  );
}
