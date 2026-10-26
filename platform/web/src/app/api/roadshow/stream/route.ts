import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SOURCE = "roadshow";
const MESSAGE_EVENT_TYPES = [
  "external_text.input_merged",
  "external_text.agent_visible_output",
  "roadshow.input_merged",
  "roadshow.agent_say",
];
const STREAM_LOOKBACK_MS = 30 * 60 * 1000;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

function json(data: unknown, status = 200) {
  return NextResponse.json(data, { status, headers: corsHeaders });
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function eventMatchesChannel(event: { type?: string | null; data?: unknown }, routeKey: string): boolean {
  const data = asRecord(event.data);
  const channelKey = typeof data.channel_key === "string" ? data.channel_key : "";
  const legacyRouteKey = typeof data.route_key === "string" ? data.route_key : "";
  const source = typeof data.source === "string" ? data.source : SOURCE;
  if (event.type?.startsWith("external_text.") && source !== SOURCE) return false;
  return (channelKey || legacyRouteKey) === routeKey;
}

function eventToMessage(event: {
  id: number;
  type: string;
  ts: string;
  data?: Record<string, unknown> | null;
}) {
  const data = asRecord(event.data);
  if (event.type === "external_text.input_merged" || event.type === "roadshow.input_merged") {
    const text = typeof data.text === "string" ? data.text.trim() : "";
    if (!text) return null;
    const count = typeof data.input_count === "number" ? data.input_count : 1;
    return {
      id: `input_${event.id}`,
      role: "viewer",
      kind: "input_merged",
      text,
      ts: event.ts,
      meta: count > 1 ? `合并 ${count} 条` : "现场提问",
    };
  }
  if (event.type === "external_text.agent_visible_output" || event.type === "roadshow.agent_say") {
    const text = typeof data.text === "string" ? data.text.trim() : "";
    if (!text) return null;
    return {
      id: `agent_${event.id}`,
      role: "assistant",
      kind: "agent_visible_output",
      text,
      ts: event.ts,
    };
  }
  return null;
}

function encodeSse(data: unknown, event = "message"): Uint8Array {
  return new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const routeKey = (url.searchParams.get("route_key") ?? url.searchParams.get("r") ?? "").trim();
  if (!routeKey) return json({ ok: false, error: "route_key_required" }, 400);

  const supabase = createServiceClient();
  const { data: route, error: routeErr } = await supabase
    .from("roadshow_routes")
    .select("route_key, enabled, target_user_id, updated_at")
    .eq("route_key", routeKey)
    .maybeSingle();
  if (routeErr) return json({ ok: false, error: routeErr.message }, 500);
  if (!route || !route.enabled) return json({ ok: false, error: "route_not_available" }, 404);
  if (!route.target_user_id) return json({ ok: false, error: "route_not_configured" }, 409);

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(new TextEncoder().encode(": connected\n\n"));
      let lastId = Number(url.searchParams.get("after_id") ?? 0);
      const since = new Date(Date.now() - STREAM_LOOKBACK_MS).toISOString();
      let closed = false;

      const sendNewEvents = async () => {
        const { data, error } = await supabase
          .from("events")
          .select("id, type, ts, data")
          .eq("user_id", route.target_user_id)
          .gt("id", lastId)
          .gte("ts", since)
          .in("type", MESSAGE_EVENT_TYPES)
          .order("id", { ascending: true })
          .limit(60);
        if (error) throw error;
        for (const event of data ?? []) {
          lastId = Math.max(lastId, Number(event.id) || lastId);
          if (!eventMatchesChannel(event, routeKey)) continue;
          const msg = eventToMessage(event);
          if (msg) controller.enqueue(encodeSse(msg));
        }
      };

      req.signal.addEventListener("abort", () => {
        closed = true;
      });

      try {
        while (!closed) {
          await sendNewEvents();
          controller.enqueue(new TextEncoder().encode(": ping\n\n"));
          await new Promise((resolve) => setTimeout(resolve, 2000));
        }
      } catch (e) {
        console.error("[roadshow/stream] message stream failed", e);
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
      ...corsHeaders,
    },
  });
}
