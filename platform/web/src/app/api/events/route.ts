import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import { shouldPersistEvent } from "@/lib/event-ingest-policy";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, apikey",
};

function validateEvent(e: unknown): string | null {
  if (!e || typeof e !== "object") return "invalid event object";
  const o = e as Record<string, unknown>;
  if (typeof o.type !== "string" || !o.type.trim()) return "event.type required";
  if (typeof o.ts !== "string" || !o.ts.trim()) return "event.ts required";
  if (typeof o.session_id !== "string" || !o.session_id.trim()) {
    return "event.session_id required";
  }
  let data = o.data;
  if (data === undefined || data === null) data = {};
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return "event.data must be object";
  }
  const d = data as Record<string, unknown>;
  if (o.type === "user_report") {
    const msg = d.message;
    if (!msg || typeof msg !== "object" || Array.isArray(msg)) {
      return "user_report requires data.message object";
    }
    const m = msg as Record<string, unknown>;
    if (typeof m.text !== "string" || !m.text.trim()) {
      return "user_report requires data.message.text non-empty string";
    }
  }
  return null;
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function POST(req: NextRequest) {
  const token = (req.headers.get("authorization") ?? "")
    .replace("Bearer ", "")
    .trim();
  if (!token) {
    return NextResponse.json(
      { error: "missing token" },
      { status: 401, headers: corsHeaders }
    );
  }

  const supabase = createServiceClient();

  const { data: ptRow, error: ptErr } = await supabase
    .from("platform_tokens")
    .select("user_id")
    .eq("token", token)
    .maybeSingle();

  if (ptErr || !ptRow) {
    return NextResponse.json(
      { error: "invalid token" },
      { status: 401, headers: corsHeaders }
    );
  }

  const user = { user_id: ptRow.user_id as string };

  const raw = await req.text();
  let body: { events?: unknown[] };
  try {
    body = JSON.parse(raw) as { events?: unknown[] };
  } catch {
    return NextResponse.json(
      { error: "invalid json" },
      { status: 400, headers: corsHeaders }
    );
  }
  const events: unknown[] = body.events ?? [];
  if (!Array.isArray(events) || events.length === 0) {
    return NextResponse.json(
      { error: "events array required" },
      { status: 400, headers: corsHeaders }
    );
  }

  for (let i = 0; i < events.length; i++) {
    const errMsg = validateEvent(events[i]);
    if (errMsg) {
      return NextResponse.json(
        { error: `${errMsg} (index ${i})` },
        { status: 400, headers: corsHeaders }
      );
    }
  }

  const now = new Date().toISOString();
  const persistableEvents = (events as Record<string, unknown>[]).filter((e) =>
    shouldPersistEvent(e.type)
  );
  const dropped = events.length - persistableEvents.length;
  const rows = persistableEvents.map((e) => {
    const data = e.data;
    const dataObj =
      data !== undefined &&
      data !== null &&
      typeof data === "object" &&
      !Array.isArray(data)
        ? data
        : {};
    const ceid =
      typeof e.client_event_id === "string" && e.client_event_id.trim()
        ? (e.client_event_id as string).trim()
        : null;
    return {
      user_id: user.user_id,
      type: e.type,
      ts: e.ts,
      session_id: e.session_id,
      data: dataObj,
      received_at: now,
      client_event_id: ceid,
    };
  });

  if (rows.length > 0) {
    const { error: insertErr } = await supabase
      .from("events")
      .upsert(rows, { onConflict: "user_id,client_event_id", ignoreDuplicates: true });
    if (insertErr) {
      return NextResponse.json(
        { error: insertErr.message },
        { status: 500, headers: corsHeaders }
      );
    }
  }

  return NextResponse.json(
    { accepted: rows.length, dropped },
    { headers: corsHeaders }
  );
}
