import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { handleCors, json, error } from "../_shared/cors.ts";
import { shouldPersistEvent } from "./policy.ts";

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

Deno.serve(async (req) => {
  const cors = handleCors(req);
  if (cors) return cors;

  if (req.method !== "POST") return error("method not allowed", 405);

  const token = (req.headers.get("authorization") ?? "").replace("Bearer ", "").trim();
  if (!token) return error("missing token", 401);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  const { data: user, error: userErr } = await supabase
    .from("users")
    .select("user_id")
    .eq("platform_token", token)
    .single();

  if (userErr || !user) return error("invalid token", 401);

  const raw = await req.text();
  let body: { events?: unknown[] };
  try {
    body = JSON.parse(raw) as { events?: unknown[] };
  } catch {
    return error("invalid json", 400);
  }
  const events: unknown[] = body.events ?? [];
  if (!Array.isArray(events) || events.length === 0) return error("events array required");

  for (let i = 0; i < events.length; i++) {
    const errMsg = validateEvent(events[i]);
    if (errMsg) return error(`${errMsg} (index ${i})`, 400);
  }

  const now = new Date().toISOString();
  const persistableEvents = (events as Record<string, unknown>[]).filter((e) =>
    shouldPersistEvent(e.type)
  );
  const dropped = events.length - persistableEvents.length;
  const rows = persistableEvents.map((e) => {
    const data = e.data;
    const dataObj =
      data !== undefined && data !== null && typeof data === "object" && !Array.isArray(data)
        ? data
        : {};
    return {
      user_id: user.user_id,
      type: e.type,
      ts: e.ts,
      session_id: e.session_id,
      data: dataObj,
      received_at: now,
    };
  });

  if (rows.length > 0) {
    const { error: insertErr } = await supabase.from("events").insert(rows);
    if (insertErr) return error(insertErr.message, 500);
  }

  return json({ accepted: rows.length, dropped });
});
