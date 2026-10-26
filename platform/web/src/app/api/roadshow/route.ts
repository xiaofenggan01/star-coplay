import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { getGatewayHttpBaseUrl } from "@/lib/gateway-url";
import { createServiceClient } from "@/lib/supabase-server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const MAX_TEXT_CHARS = 300;
const HISTORY_LIMIT = 30;
const HISTORY_SCAN_LIMIT = 300;
const HISTORY_WINDOW_MS = 30 * 60 * 1000;
const RATE_WINDOW_MS = 10_000;
const RATE_LIMIT = 3;
const SOURCE = "roadshow";
const MESSAGE_EVENT_TYPES = [
  "external_text.input_merged",
  "external_text.agent_visible_output",
  "roadshow.input_merged",
  "roadshow.agent_say",
];

const rateBuckets = new Map<string, number[]>();

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

function json(data: unknown, status = 200) {
  return NextResponse.json(data, { status, headers: corsHeaders });
}

function clientIp(req: NextRequest): string {
  return (
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-real-ip") ||
    "unknown"
  );
}

function checkRateLimit(routeKey: string, ip: string): boolean {
  const key = `${routeKey}:${ip}`;
  const now = Date.now();
  const kept = (rateBuckets.get(key) ?? []).filter((ts) => now - ts < RATE_WINDOW_MS);
  if (kept.length >= RATE_LIMIT) {
    rateBuckets.set(key, kept);
    return false;
  }
  kept.push(now);
  rateBuckets.set(key, kept);
  return true;
}

function sanitizeRoadshowText(text: string): string {
  return text
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n")
    .slice(0, MAX_TEXT_CHARS);
}

function eventMatchesChannel(event: { type?: string | null; data?: unknown }, routeKey: string): boolean {
  const data = event.data && typeof event.data === "object" && !Array.isArray(event.data)
    ? event.data as Record<string, unknown>
    : {};
  const channelKey = typeof data.channel_key === "string" ? data.channel_key : "";
  const legacyRouteKey = typeof data.route_key === "string" ? data.route_key : "";
  const source = typeof data.source === "string" ? data.source : SOURCE;
  if (event.type?.startsWith("external_text.") && source !== SOURCE) return false;
  return (channelKey || legacyRouteKey) === routeKey;
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const routeKey = (url.searchParams.get("route_key") ?? url.searchParams.get("r") ?? "").trim();
  const limit = Math.min(HISTORY_LIMIT, Math.max(1, Number(url.searchParams.get("limit") ?? HISTORY_LIMIT)));
  const since = new Date(Date.now() - HISTORY_WINDOW_MS).toISOString();

  if (!routeKey) return json({ ok: false, error: "route_key_required" }, 400);

  const supabase = createServiceClient();
  const { data: route, error: routeErr } = await supabase
    .from("roadshow_routes")
    .select("route_key, title, enabled, target_user_id, target_agent_instance_id, updated_at")
    .eq("route_key", routeKey)
    .maybeSingle();

  if (routeErr) return json({ ok: false, error: routeErr.message }, 500);
  if (!route || !route.enabled) return json({ ok: false, error: "route_not_available" }, 404);

  let agentEvents: unknown[] = [];
  if (route.target_user_id) {
    const { data, error } = await supabase
      .from("events")
      .select("id, type, ts, session_id, data, received_at")
      .eq("user_id", route.target_user_id)
      .gte("ts", since)
      .in("type", MESSAGE_EVENT_TYPES)
      .order("ts", { ascending: false })
      .limit(HISTORY_SCAN_LIMIT);
    if (error) return json({ ok: false, error: error.message }, 500);
    agentEvents = (data ?? []).filter((event) => eventMatchesChannel(event, routeKey)).slice(0, limit);
  }

  return json({
    ok: true,
    route: {
      route_key: route.route_key,
      title: route.title,
      updated_at: route.updated_at,
    },
    agent_events: agentEvents,
  });
}

export async function POST(req: NextRequest) {
  let body: { route_key?: string; input_id?: string; text?: string };
  try {
    body = (await req.json()) as { route_key?: string; input_id?: string; text?: string };
  } catch {
    return json({ ok: false, error: "invalid_json" }, 400);
  }

  const routeKey = (body.route_key ?? "").trim();
  const inputId = (body.input_id ?? randomUUID()).trim();
  const text = (body.text ?? "").trim();
  const sanitizedText = sanitizeRoadshowText(text);

  if (!routeKey) return json({ ok: false, error: "route_key_required" }, 400);
  if (!inputId) return json({ ok: false, error: "input_id_required" }, 400);
  if (!text) return json({ ok: false, error: "text_required" }, 400);
  if (text.length > MAX_TEXT_CHARS) {
    return json({ ok: false, error: "text_too_long", max_chars: MAX_TEXT_CHARS }, 413);
  }
  if (!sanitizedText) return json({ ok: false, error: "text_required" }, 400);
  if (!checkRateLimit(routeKey, clientIp(req))) {
    return json({ ok: false, error: "rate_limited" }, 429);
  }

  const supabase = createServiceClient();
  const { data: route, error: routeErr } = await supabase
    .from("roadshow_routes")
    .select("route_key, enabled, target_user_id, target_agent_instance_id")
    .eq("route_key", routeKey)
    .maybeSingle();

  if (routeErr) return json({ ok: false, error: routeErr.message }, 500);
  if (!route || !route.enabled) return json({ ok: false, error: "route_not_available" }, 404);
  if (!route.target_user_id || !route.target_agent_instance_id) {
    return json({ ok: false, error: "route_not_configured" }, 409);
  }

  const receivedAt = new Date().toISOString();
  const { data: inserted, error: insertErr } = await supabase
    .from("external_text_inputs")
    .insert({
      input_id: inputId,
      source: "roadshow",
      route_key: routeKey,
      user_id: route.target_user_id,
      text,
      sanitized_text: sanitizedText,
      received_at: receivedAt,
      agent_instance_id: route.target_agent_instance_id,
      delivery_status: "received",
      raw_metadata: {
        ip: clientIp(req),
        user_agent: req.headers.get("user-agent") ?? "",
      },
    })
    .select("id")
    .maybeSingle();

  if (insertErr) {
    if (insertErr.code === "23505" || insertErr.message.toLowerCase().includes("duplicate")) {
      return json({ ok: true, status: "duplicate", input_id: inputId });
    }
    return json({ ok: false, error: insertErr.message }, 500);
  }

  await recordDeliveryEvent(supabase, {
    inputId,
    routeKey,
    userId: route.target_user_id,
    agentInstanceId: route.target_agent_instance_id,
    eventType: "received",
    deliveryStatus: "received",
    sanitizedText,
    payload: {
      source: "roadshow",
      text_length: text.length,
      sanitized_length: sanitizedText.length,
    },
  });

  const base = getGatewayHttpBaseUrl();
  const adminSecret = (process.env.ADMIN_SECRET ?? "").trim();
  if (!adminSecret) {
    await markInput(supabase, inserted?.id, "delivery_failed", "ADMIN_SECRET missing");
    await recordDeliveryEvent(supabase, {
      inputId,
      routeKey,
      userId: route.target_user_id,
      agentInstanceId: route.target_agent_instance_id,
      eventType: "gateway_unavailable",
      deliveryStatus: "delivery_failed",
      sanitizedText,
      error: "ADMIN_SECRET missing",
    });
    return json({ ok: false, error: "gateway_unavailable" }, 503);
  }

  try {
    await recordDeliveryEvent(supabase, {
      inputId,
      routeKey,
      userId: route.target_user_id,
      agentInstanceId: route.target_agent_instance_id,
      eventType: "gateway_request",
      deliveryStatus: "sending",
      sanitizedText,
      payload: {
        gateway_url: `${base}/api/v1/admin/external-text/roadshow`,
      },
    });
    const res = await fetch(`${base}/api/v1/admin/external-text/roadshow`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-admin-secret": adminSecret,
      },
      body: JSON.stringify({
        input_id: inputId,
        text: sanitizedText,
        received_at: receivedAt,
        route_key: routeKey,
        source: SOURCE,
        agent_instance_id: route.target_agent_instance_id,
      }),
      signal: AbortSignal.timeout(10_000),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) {
      const err = typeof data?.error === "string" ? data.error : "delivery_failed";
      await markInput(supabase, inserted?.id, "delivery_failed", err);
      await recordDeliveryEvent(supabase, {
        inputId,
        routeKey,
        userId: route.target_user_id,
        agentInstanceId: route.target_agent_instance_id,
        eventType: "gateway_rejected",
        deliveryStatus: "delivery_failed",
        sanitizedText,
        error: err,
        payload: data,
      });
      return json({ ok: false, error: err }, res.status || 502);
    }
    await markInput(supabase, inserted?.id, "delivered", null);
    await recordDeliveryEvent(supabase, {
      inputId,
      routeKey,
      userId: route.target_user_id,
      agentInstanceId: route.target_agent_instance_id,
      eventType: "agent_delivered",
      deliveryStatus: "delivered",
      sanitizedText,
      payload: data,
    });
    return json({ ok: true, status: "accepted", input_id: inputId });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "gateway_unreachable";
    await markInput(supabase, inserted?.id, "delivery_failed", msg);
    await recordDeliveryEvent(supabase, {
      inputId,
      routeKey,
      userId: route.target_user_id,
      agentInstanceId: route.target_agent_instance_id,
      eventType: "gateway_unreachable",
      deliveryStatus: "delivery_failed",
      sanitizedText,
      error: msg,
    });
    return json({ ok: false, error: "gateway_unreachable" }, 502);
  }
}

async function markInput(
  supabase: ReturnType<typeof createServiceClient>,
  id: number | undefined,
  status: string,
  error: string | null,
) {
  if (!id) return;
  await supabase
    .from("external_text_inputs")
    .update({
      delivery_status: status,
      error,
      delivered_at: status === "delivered" ? new Date().toISOString() : null,
    })
    .eq("id", id);
}

async function recordDeliveryEvent(
  supabase: ReturnType<typeof createServiceClient>,
  event: {
    inputId: string;
    routeKey: string;
    userId: string | null;
    agentInstanceId: string | null;
    eventType: string;
    deliveryStatus: string;
    sanitizedText?: string | null;
    payload?: Record<string, unknown>;
    error?: string | null;
  },
) {
  await supabase.from("roadshow_delivery_events").insert({
    input_id: event.inputId,
    route_key: event.routeKey,
    user_id: event.userId,
    agent_instance_id: event.agentInstanceId,
    event_type: event.eventType,
    delivery_status: event.deliveryStatus,
    payload_type: "external_text_input",
    sanitized_text: event.sanitizedText ?? null,
    payload: event.payload ?? {},
    error: event.error ?? null,
  });
}
