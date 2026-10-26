import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { handleCors, json, error } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  const cors = handleCors(req);
  if (cors) return cors;

  const secret = (Deno.env.get("ADMIN_SECRET") ?? "").trim();
  const fromX = (req.headers.get("x-admin-secret") ?? "").trim();
  const fromBearer = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  const token = fromX || fromBearer;
  if (!secret) {
    return error("ADMIN_SECRET missing in Edge env (use: npm run fn:serve in platform, or npx supabase functions serve --env-file supabase/.env)", 503);
  }
  if (token !== secret) return error("unauthorized", 401);

  const url = new URL(req.url);
  const action = url.searchParams.get("action");
  const days = Number(url.searchParams.get("days") ?? 30);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  if (action === "users") {
    const { data, error: e } = await supabase
      .from("users")
      .select("*")
      .order("created_at", { ascending: false });
    if (e) return error(e.message, 500);
    return json({ total: data?.length ?? 0, users: data });
  }

  if (action === "stats") {
    const { data: bySource, error: rpcErr } = await supabase.rpc("admin_source_stats");
    if (rpcErr) return error(rpcErr.message, 500);

    const { data: byDay } = await supabase
      .from("users")
      .select("created_at");

    const dayCounts: Record<string, number> = {};
    (byDay ?? []).forEach((u: { created_at: string }) => {
      const day = u.created_at.slice(0, 10);
      dayCounts[day] = (dayCounts[day] || 0) + 1;
    });

    return json({
      by_source: bySource ?? [],
      by_day: Object.entries(dayCounts)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([day, count]) => ({ day, count })),
    });
  }

  if (action === "event_stats") {
    const { data: rows, error: rpcErr } = await supabase.rpc("admin_event_stats");
    if (rpcErr) return error(rpcErr.message, 500);
    return json({ by_type: rows ?? [] });
  }

  if (action === "dashboard") {
    const [dauRes, durationRes, retentionRes, funnelRes, eventStatsRes] = await Promise.all([
      supabase.rpc("admin_dau", { days }),
      supabase.rpc("admin_avg_session_duration", { days }),
      supabase.rpc("admin_retention"),
      supabase.rpc("admin_onboard_funnel"),
      supabase.rpc("admin_event_stats"),
    ]);
    return json({
      dau: dauRes.data ?? [],
      avg_session_duration: durationRes.data ?? [],
      retention: retentionRes.data ?? [],
      onboard_funnel: funnelRes.data ?? [],
      event_stats: eventStatsRes.data ?? [],
    });
  }

  if (action === "user_reports") {
    const { data: rows, error: e } = await supabase
      .from("events")
      .select("user_id, ts, session_id, data")
      .eq("type", "user_report")
      .order("ts", { ascending: false })
      .limit(100);
    if (e) return error(e.message, 500);
    return json({ total: rows?.length ?? 0, reports: rows ?? [] });
  }

  if (action === "llm_stats") {
    const d = Number(url.searchParams.get("days") ?? 30);
    const { data, error: e } = await supabase.rpc("admin_llm_stats", { days: d });
    if (e) return error(e.message, 500);
    return json(
      data?.[0] ?? {
        total_calls: 0,
        avg_latency_ms: 0,
        p95_latency_ms: 0,
        total_tokens_in: 0,
        total_tokens_out: 0,
        model_distribution: {},
      },
    );
  }

  if (action === "action_stats") {
    const d = Number(url.searchParams.get("days") ?? 30);
    const { data, error: e } = await supabase.rpc("admin_action_stats", { days: d });
    if (e) return error(e.message, 500);
    return json({ actions: data ?? [] });
  }

  if (action === "error_ranking") {
    const d = Number(url.searchParams.get("days") ?? 30);
    const top_n = Number(url.searchParams.get("top_n") ?? 10);
    const { data, error: e } = await supabase.rpc("admin_error_ranking", { days: d, top_n });
    if (e) return error(e.message, 500);
    return json({ errors: data ?? [] });
  }

  if (action === "active_users") {
    const d = Number(url.searchParams.get("days") ?? 30);
    const { data, error: e } = await supabase.rpc("admin_active_users", { days: d });
    if (e) return error(e.message, 500);
    return json({ users: data ?? [] });
  }

  if (action === "user_profile") {
    const userId = url.searchParams.get("user_id");
    if (!userId) return error("missing user_id parameter");
    const { data, error: e } = await supabase.rpc("admin_user_profile", { target_user_id: userId });
    if (e) return error(e.message, 500);
    return json(data?.[0] ?? null);
  }

  if (action === "conversations") {
    const userId = url.searchParams.get("user_id");
    if (!userId) return error("missing user_id parameter");
    const page = Math.max(0, Number(url.searchParams.get("page") ?? 0));
    const pageSize = Math.min(200, Math.max(1, Number(url.searchParams.get("page_size") ?? 50)));
    const pageOffset = page * pageSize;
    const { data, error: e } = await supabase.rpc("admin_conversation_log", {
      target_user_id: userId,
      page_size: pageSize,
      page_offset: pageOffset,
    });
    if (e) return error(e.message, 500);
    const rows = data ?? [];
    const total = rows.length > 0 ? Number((rows[0] as { total?: number }).total ?? 0) : 0;
    const messages = rows.map(({ ts, type, session_id, data }: Record<string, unknown>) => ({
      ts,
      type,
      session_id,
      data,
    }));
    return json({ messages, total });
  }

  if (action === "user_timeline") {
    const userId = url.searchParams.get("user_id");
    if (!userId) return error("missing user_id parameter");
    const { data, error: e } = await supabase.rpc("admin_user_timeline", { target_user_id: userId });
    if (e) return error(e.message, 500);
    return json({ timeline: data ?? [] });
  }

  return error(
    "unknown action, use ?action=users|stats|event_stats|dashboard|user_reports|llm_stats|action_stats|error_ranking|active_users|user_profile|conversations|user_timeline",
  );
});
