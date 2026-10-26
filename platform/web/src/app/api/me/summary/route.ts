import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import { authenticateRequest } from "@/lib/auth";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

function sinceIso(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString();
}

function aggRows(
  rows: { type: string; data: Record<string, unknown> | null; ts: string }[] | null,
  minTsInclusive: string | null,
) {
  let eventCount = 0;
  let sessionStartDays = new Set<string>();
  let heartbeatCount = 0;

  for (const r of rows ?? []) {
    if (minTsInclusive && r.ts < minTsInclusive) continue;
    eventCount += 1;
    if (r.type === "session.start") {
      sessionStartDays.add(r.ts.slice(0, 10));
    }
    if (r.type === "session.heartbeat") heartbeatCount += 1;
  }

  return {
    event_count: eventCount,
    active_days_with_session: sessionStartDays.size,
    estimated_online_minutes: heartbeatCount * 5,
    llm_metrics_migrated: true,
    llm_calls: 0,
    tokens_in: 0,
    tokens_out: 0,
  };
}

export async function GET(req: NextRequest) {
  try {
    // 阶段四 A4a：双轨鉴权（JWT 优先，platform_token fallback）
    const authedUser = await authenticateRequest(req);
    if (!authedUser) {
      return NextResponse.json(
        { error: "unauthorized" },
        { status: 401, headers: corsHeaders },
      );
    }

    const supabase = createServiceClient();
    const { data: user, error: uErr } = await supabase
      .from("users")
      .select("user_id, nickname, created_at")
      .eq("user_id", authedUser.user_id)
      .single();

    if (uErr || !user) {
      return NextResponse.json(
        { error: "user_not_found" },
        { status: 404, headers: corsHeaders },
      );
    }

    const since = sinceIso(30);
    const { data: events, error: eErr } = await supabase
      .from("events")
      .select("type, data, ts")
      .eq("user_id", user.user_id)
      .gte("ts", since)
      .order("ts", { ascending: false })
      .limit(5000);

    if (eErr) {
      return NextResponse.json({ error: eErr.message }, { status: 500, headers: corsHeaders });
    }

    const rows = (events ?? []) as { type: string; data: Record<string, unknown> | null; ts: string }[];
    const last7 = aggRows(rows, sinceIso(7));
    const last30 = aggRows(rows, null);

    return NextResponse.json(
      {
        user_id: user.user_id,
        nickname: user.nickname,
        created_at: user.created_at,
        windows: {
          days_7: last7,
          days_30: last30,
        },
      },
      { headers: corsHeaders },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return NextResponse.json({ error: msg }, { status: 500, headers: corsHeaders });
  }
}
