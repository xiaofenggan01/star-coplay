import { NextRequest, NextResponse } from "next/server";
import { handleAdminGrantUserCredits, handleAdminGrantUserCreditsBatch } from "@/lib/admin-user-credit";
import { getAdminUserInviteSummaries } from "@/lib/admin-user-invite-summary";
import { handleAdminUserUpgradeToBeta } from "@/lib/admin-user-upgrade";
import { filterAdminUsersBySearch } from "@/lib/admin-user-search";
import { getGatewayHttpBaseUrl } from "@/lib/gateway-url";
import { callBackendInternal, extractErrorMessage } from "@/lib/backend-internal";
import { generateReadableInviteCode } from "@/lib/platform-access-open";
import { createServiceClient } from "@/lib/supabase-server";
import { escapeLikeWildcards, escapeOrValue } from "@/lib/waitlist-query";

function jsonResp(data: unknown, status = 200) {
  return NextResponse.json(data, { status });
}

function errorResp(message: string, status = 400) {
  return NextResponse.json({ error: message }, { status });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readPositiveInt(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  if (!Number.isInteger(parsed) || parsed <= 0) return null;
  return parsed;
}

function readInviteCode(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readBoundedPositiveInt(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const parsed = readPositiveInt(value);
  if (parsed == null) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function readValidatedDaysParam(
  value: string | null,
  fallback: number,
  maximum: number,
): number | null {
  if (value == null || value.trim() === "") return fallback;
  const parsed = readPositiveInt(value);
  if (parsed == null || parsed > maximum) return null;
  return parsed;
}

type CreditSummary =
  | {
      status: "available";
      available_credits: number;
      used_credits: number;
      available_percent: number;
    }
  | {
      status: "not_provisioned";
      reason_code: string;
    }
  | {
      status: "unavailable";
      reason_code: string;
    };

function readErrorCode(payload: unknown): string {
  if (!isRecord(payload)) return "";
  const error = payload.error;
  if (!isRecord(error)) return "";
  return typeof error.code === "string" ? error.code : "";
}

function unavailableCreditSummary(reasonCode: string): CreditSummary {
  return {
    status: "unavailable",
    reason_code: reasonCode,
  };
}

function parseCreditSummary(payload: unknown, status: number): CreditSummary {
  if (status === 404 && readErrorCode(payload) === "ACCOUNT_NOT_FOUND") {
    return {
      status: "not_provisioned",
      reason_code: "ACCOUNT_NOT_FOUND",
    };
  }
  if (status !== 200 || !isRecord(payload)) {
    return unavailableCreditSummary(readErrorCode(payload) || `HTTP_${status}`);
  }
  const quota = isRecord(payload.data) ? payload.data : payload;
  const display = isRecord(quota.display) ? quota.display : {};
  const available = Number(display.balance_credits);
  const used = Number(display.used_credits);
  if (!Number.isFinite(available) || !Number.isFinite(used)) {
    return unavailableCreditSummary("INVALID_QUOTA_PAYLOAD");
  }
  const safeAvailable = Math.max(0, available);
  const safeUsed = Math.max(0, used);
  const total = safeAvailable + safeUsed;
  return {
    status: "available",
    available_credits: safeAvailable,
    used_credits: safeUsed,
    available_percent: total > 0 ? safeAvailable / total * 100 : 0,
  };
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const limit = Math.max(1, concurrency);

  async function runWorker() {
    while (true) {
      const current = nextIndex;
      nextIndex += 1;
      if (current >= items.length) return;
      results[current] = await worker(items[current], current);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, () => runWorker()),
  );
  return results;
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  onTimeout: () => T,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => resolve(onTimeout()), timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

const ADMIN_USERS_PAGE_SIZE_DEFAULT = 50;
const ADMIN_USERS_PAGE_SIZE_MAX = 100;
const ADMIN_USERS_CREDIT_CONCURRENCY = 8;
const ADMIN_USERS_CREDIT_TIMEOUT_MS = 12_000;

function officialInviteStatus(row: {
  disabled?: boolean | null;
  expires_at?: string | null;
  max_uses?: number | null;
  used_count?: number | null;
}): "disabled" | "expired" | "exhausted" | "active" {
  if (row.disabled) return "disabled";
  if (row.expires_at && new Date(row.expires_at).getTime() <= Date.now()) return "expired";
  const maxUses = row.max_uses;
  const usedCount = Number(row.used_count ?? 0);
  if (maxUses !== null && maxUses !== undefined && usedCount >= Number(maxUses)) return "exhausted";
  return "active";
}

function relayQuerySuffix(
  url: URL,
  keys: string[],
  extra?: Record<string, string>,
): string {
  const params = new URLSearchParams(extra ?? {});
  for (const key of keys) {
    const value = url.searchParams.get(key);
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

const UPGRADE_EMAIL_HTML = `
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#fdf6e3;font-family:'Courier New',Courier,'PingFang SC','Microsoft YaHei',sans-serif;color:#6b4226;">
  <tr>
    <td align="center" style="padding:0;">

      <!-- Hero Banner -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background:#2d6a4f;background:linear-gradient(180deg,#2d6a4f 0%,#40916c 50%,#f4c58e 100%);">
        <tr>
          <td align="center" style="padding:32px 20px 24px 20px;">
            <img src="https://star-coplay.luosea.cn/logo-new.png" alt="Star CoPlay" width="220" height="auto" style="display:block;max-width:80%;height:auto;border:0;outline:none;text-decoration:none;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.4));">
            <p style="margin:14px 0 0 0;font-family:'Courier New',monospace;font-size:13px;color:#ffffff;letter-spacing:1px;text-shadow:0 1px 2px rgba(0,0,0,0.4);">星 露 谷 中 永 远 在 线 的 搭 子</p>
          </td>
        </tr>
      </table>

      <!-- Parchment Card -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">
        <tr>
          <td style="padding:24px 24px 0 24px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5e6c8;background:linear-gradient(135deg,#f5e6c8 0%,#eed9a4 50%,#e8cc8c 100%);border:3px solid #8b6914;border-radius:12px;">

              <tr>
                <td align="center" style="padding:24px 28px 8px 28px;">
                  <img src="https://star-coplay.luosea.cn/assets/icons/Prismatic_Shard.png" alt="" width="48" height="48" style="display:block;border:0;outline:none;image-rendering:pixelated;">
                </td>
              </tr>
              <tr>
                <td align="center" style="padding:0 28px 8px 28px;">
                  <h1 style="margin:0;font-family:'Courier New',monospace;font-size:22px;line-height:1.4;color:#5a3e0a;letter-spacing:1px;">你的账号已升级！</h1>
                </td>
              </tr>
              <tr>
                <td align="center" style="padding:8px 28px 20px 28px;">
                  <p style="margin:0;font-size:14px;line-height:1.8;color:#6b4226;">
                    恭喜！你的 StarCoPlay 账号已从访客升级为<strong>内测用户</strong>。<br>
                    现在你可以使用全部功能，包括 AI 搭子联机。<br>
                    直接用原有密码登录即可开始体验。
                  </p>
                </td>
              </tr>

              <!-- CTA -->
              <tr>
                <td align="center" style="padding:4px 28px 24px 28px;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td align="center" bgcolor="#8b6914" style="border-radius:8px;background:#8b6914;background:linear-gradient(180deg,#c4a35a 0%,#8b6914 100%);border:3px solid #5a3e0a;box-shadow:0 3px 0 #5a3e0a;">
                        <a href="{{LOGIN_URL}}" target="_blank" style="display:inline-block;padding:14px 36px;min-height:24px;font-family:'Courier New',monospace;font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;letter-spacing:1px;text-shadow:1px 1px 2px rgba(0,0,0,0.5);">✦ 立 即 登 录 ✦</a>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <!-- 提示 -->
              <tr>
                <td style="padding:0 28px 28px 28px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fdf6e3;border:2px solid #c4a35a;border-radius:8px;">
                    <tr>
                      <td style="padding:12px 14px;">
                        <p style="margin:0;font-size:12px;line-height:1.7;color:#6b4226;">
                          <strong style="color:#8b6914;">🌟 内测权益：</strong>AI 搭子联机、优先体验新功能、专属反馈通道。<br>
                          <strong style="color:#8b6914;">🔑 登录方式：</strong>使用你注册时的邮箱和密码直接登录。
                        </p>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <!-- Footer -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">
        <tr>
          <td align="center" style="padding:24px 24px 32px 24px;">
            <p style="margin:0 0 6px 0;font-family:'Courier New',monospace;font-size:11px;color:#8b6914;letter-spacing:0.5px;">— Star CoPlay · AI Co-Player for Stardew Valley —</p>
            <p style="margin:0 0 6px 0;font-size:11px;color:#8b6914;">本邮件由系统自动发送，请勿直接回复。</p>
            <p style="margin:0;font-size:10px;color:#c4a35a;">Powered by Star CoPlay · {{SITE_URL}}</p>
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>
`.trim();

export async function GET(req: NextRequest) {
  try {
  const secret = (process.env.ADMIN_SECRET ?? "").trim();
  const fromX = (req.headers.get("x-admin-secret") ?? "").trim();
  const fromBearer = (req.headers.get("authorization") ?? "")
    .replace(/^Bearer\s+/i, "")
    .trim();
  const token = fromX || fromBearer;

  if (!secret) {
    return errorResp(
      "ADMIN_SECRET missing in environment variables",
      503
    );
  }
  if (token !== secret) return errorResp("unauthorized", 401);

  const url = new URL(req.url);
  const action = url.searchParams.get("action");

  let supabase;
  try {
    supabase = createServiceClient();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "supabase init failed";
    return errorResp(msg, 503);
  }

  if (action === "users") {
    const userSearch = url.searchParams.get("q");
    const page = readBoundedPositiveInt(url.searchParams.get("page"), 1, 1, 10_000);
    const pageSize = readBoundedPositiveInt(
      url.searchParams.get("pageSize") ?? url.searchParams.get("page_size"),
      ADMIN_USERS_PAGE_SIZE_DEFAULT,
      1,
      ADMIN_USERS_PAGE_SIZE_MAX,
    );
    const { data, error: e } = await supabase
      .from("users")
      .select("*")
      .order("created_at", { ascending: false });
    if (e) return errorResp(e.message, 500);

    // Enrich with invite source info
    const users = data ?? [];
    const userIds = users.map((u: { user_id?: string }) => u.user_id).filter(Boolean);
    const profileMap: Record<string, { nickname: string | null; user_type: string | null }> = {};
    if (userIds.length > 0) {
      const { data: profileRows, error: profileErr } = await supabase
        .from("profiles")
        .select("user_id, nickname, user_type")
        .in("user_id", userIds);
      if (profileErr) return errorResp(profileErr.message, 500);
      if (profileRows) {
        for (const p of profileRows) {
          profileMap[p.user_id] = {
            nickname: p.nickname ?? null,
            user_type: p.user_type ?? null,
          };
        }
      }
    }
    const inviteCodes = [...new Set(users.map((u: { invite_code_used?: string }) => u.invite_code_used).filter(Boolean))];
    let inviteSourceMap: Record<string, { type: string; owner_user_id: string | null; official_code_reason: string | null }> = {};
    let ownerNicknameMap: Record<string, string | null> = {};

    if (inviteCodes.length > 0) {
      const { data: codeRows } = await supabase
        .from("invite_codes")
        .select("code, type, owner_user_id, official_code_reason")
        .in("code", inviteCodes);
      if (codeRows) {
        for (const row of codeRows) {
          inviteSourceMap[row.code] = { type: row.type, owner_user_id: row.owner_user_id, official_code_reason: row.official_code_reason };
        }
        // Fetch nicknames for user-type code owners
        const ownerIds = [...new Set(codeRows.filter((r) => r.type === "user" && r.owner_user_id).map((r) => r.owner_user_id!))];
        if (ownerIds.length > 0) {
          const { data: profileRows, error: ownerProfileErr } = await supabase
            .from("profiles")
            .select("user_id, nickname")
            .in("user_id", ownerIds);
          if (ownerProfileErr) return errorResp(ownerProfileErr.message, 500);
          if (profileRows) {
            for (const p of profileRows) {
              ownerNicknameMap[p.user_id] = p.nickname;
            }
          }
        }
      }
    }

    const enrichedUsers = users.map((u: { user_id?: string; email?: string | null; invite_code_used?: string; nickname?: string | null }) => {
      const code = u.invite_code_used;
      const info = code ? inviteSourceMap[code] : undefined;
      let invite_source: string | null = null;
      if (info) {
        if (info.type === "official") {
          invite_source = "官方邀请";
        } else if (info.type === "user" && info.owner_user_id) {
          const nickname = ownerNicknameMap[info.owner_user_id];
          invite_source = nickname || info.owner_user_id.slice(0, 8);
        }
      }
      const profile = u.user_id ? profileMap[u.user_id] : null;
      return {
        ...u,
        nickname: profile?.nickname || u.nickname,
        user_type: profile?.user_type ?? null,
        invite_source,
      };
    });
    const filteredUsers = filterAdminUsersBySearch(enrichedUsers, userSearch);
    const pageOffset = (page - 1) * pageSize;
    const pagedUsers = filteredUsers.slice(pageOffset, pageOffset + pageSize);
    const inviteSummaries = await getAdminUserInviteSummaries(
      supabase,
      pagedUsers.map((u: { user_id?: string }) => u.user_id ?? ""),
    );
    const usersWithInviteSummaries = pagedUsers.map((u: { user_id?: string }) => ({
      ...u,
      invite_code_summary: u.user_id ? inviteSummaries[u.user_id] ?? { codes: [], invited_user_count: 0, invited_users: [] } : { codes: [], invited_user_count: 0, invited_users: [] },
    }));

    const usersWithCredits = await withTimeout(
      mapWithConcurrency(
        usersWithInviteSummaries,
        ADMIN_USERS_CREDIT_CONCURRENCY,
        async (user) => {
          if (!user.user_id) return { ...user, credit_summary: unavailableCreditSummary("MISSING_USER_ID") };
          try {
            const { payload, status } = await callBackendInternal(
              `/internal/ai/relay/quota?user_id=${encodeURIComponent(user.user_id)}`,
            );
            return {
              ...user,
              credit_summary: parseCreditSummary(payload, status),
            };
          } catch (error) {
            const reasonCode =
              error instanceof Error && error.name === "BackendInternalError"
                ? "INTERNAL_BACKEND_UNAVAILABLE"
                : "CREDIT_SUMMARY_FETCH_FAILED";
            return { ...user, credit_summary: unavailableCreditSummary(reasonCode) };
          }
        },
      ),
      ADMIN_USERS_CREDIT_TIMEOUT_MS,
      () =>
        usersWithInviteSummaries.map((user) => ({
          ...user,
          credit_summary: unavailableCreditSummary("CREDIT_SUMMARY_TIMEOUT"),
        })),
    );

    return jsonResp({ total: filteredUsers.length, page, pageSize, users: usersWithCredits });
  }

  if (action === "stats") {
    const { data: bySource, error: rpcErr } = await supabase.rpc(
      "admin_source_stats"
    );
    if (rpcErr) return errorResp(rpcErr.message, 500);

    const { data: byDay } = await supabase.from("users").select("created_at");

    const dayCounts: Record<string, number> = {};
    (byDay ?? []).forEach((u: { created_at: string }) => {
      const day = u.created_at.slice(0, 10);
      dayCounts[day] = (dayCounts[day] || 0) + 1;
    });

    return jsonResp({
      by_source: bySource ?? [],
      by_day: Object.entries(dayCounts)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([day, count]) => ({ day, count })),
    });
  }

  if (action === "event_stats") {
    const { data: rows, error: rpcErr } = await supabase.rpc(
      "admin_event_stats"
    );
    if (rpcErr) return errorResp(rpcErr.message, 500);
    return jsonResp({ by_type: rows ?? [] });
  }

  if (action === "dashboard") {
    const days = readValidatedDaysParam(url.searchParams.get("days"), 30, 365);
    if (days == null) return errorResp("invalid days parameter", 400);
    const [runtimeRes, funnelRes, eventStatsRes] =
      await Promise.all([
        callBackendInternal(`/internal/admin/dashboard?days=${days}`),
        supabase.rpc("admin_onboard_funnel"),
        supabase.rpc("admin_event_stats"),
      ]);
    if (runtimeRes.status !== 200 || !isRecord(runtimeRes.payload)) {
      return errorResp(extractErrorMessage(runtimeRes.payload, "ECS dashboard metrics unavailable"), runtimeRes.status || 502);
    }
    const rpcError = funnelRes.error ?? eventStatsRes.error;
    if (rpcError) return errorResp(rpcError.message, 500);
    return jsonResp({
      dau: Array.isArray(runtimeRes.payload.dau) ? runtimeRes.payload.dau : [],
      avg_session_duration: Array.isArray(runtimeRes.payload.avg_session_duration) ? runtimeRes.payload.avg_session_duration : [],
      retention: Array.isArray(runtimeRes.payload.retention) ? runtimeRes.payload.retention : [],
      onboard_funnel: funnelRes.data ?? [],
      event_stats: eventStatsRes.data ?? [],
      freshness: isRecord(runtimeRes.payload.freshness) ? runtimeRes.payload.freshness : null,
    });
  }

  if (action === "user_reports") {
    const { data: rows, error: e } = await supabase
      .from("events")
      .select("user_id, ts, session_id, data")
      .eq("type", "user_report")
      .order("ts", { ascending: false })
      .limit(100);
    if (e) return errorResp(e.message, 500);
    return jsonResp({ total: rows?.length ?? 0, reports: rows ?? [] });
  }

  if (action === "llm_stats") {
    const days = readValidatedDaysParam(url.searchParams.get("days"), 30, 365);
    if (days == null) return errorResp("invalid days parameter", 400);
    const { data, error: e } = await supabase.rpc("admin_llm_stats", { days });
    if (e) return errorResp(e.message, 500);
    return jsonResp(data?.[0] ?? { total_calls: 0, avg_latency_ms: 0, p95_latency_ms: 0, total_tokens_in: 0, total_tokens_out: 0, model_distribution: {} });
  }

  if (action === "action_stats") {
    const days = readValidatedDaysParam(url.searchParams.get("days"), 30, 365);
    if (days == null) return errorResp("invalid days parameter", 400);
    const { data, error: e } = await supabase.rpc("admin_action_stats", { days });
    if (e) return errorResp(e.message, 500);
    return jsonResp({ actions: data ?? [] });
  }

  if (action === "error_ranking") {
    const days = readValidatedDaysParam(url.searchParams.get("days"), 30, 365);
    if (days == null) return errorResp("invalid days parameter", 400);
    const top_n = Number(url.searchParams.get("top_n") ?? 10);
    const { data, error: e } = await supabase.rpc("admin_error_ranking", { days, top_n });
    if (e) return errorResp(e.message, 500);
    return jsonResp({ errors: data ?? [] });
  }

  if (action === "active_users") {
    const days = readValidatedDaysParam(url.searchParams.get("days"), 30, 365);
    if (days == null) return errorResp("invalid days parameter", 400);
    const { data, error: e } = await supabase.rpc("admin_active_users", { days });
    if (e) return errorResp(e.message, 500);
    return jsonResp({ users: data ?? [] });
  }

  if (action === "user_profile") {
    const userId = url.searchParams.get("user_id");
    if (!userId) return errorResp("missing user_id parameter");
    const { data, error: e } = await supabase.rpc("admin_user_profile", { target_user_id: userId });
    if (e) return errorResp(e.message, 500);
    return jsonResp(data?.[0] ?? null);
  }

  if (action === "conversations") {
    const userId = url.searchParams.get("user_id");
    if (!userId) return errorResp("missing user_id parameter");
    const page = Math.max(0, Number(url.searchParams.get("page") ?? 0));
    const pageSize = Math.min(200, Math.max(1, Number(url.searchParams.get("page_size") ?? 50)));
    const pageOffset = page * pageSize;
    const { data, error: e } = await supabase.rpc("admin_conversation_log", {
      target_user_id: userId,
      page_size: pageSize,
      page_offset: pageOffset,
    });
    if (e) return errorResp(e.message, 500);
    const rows = data ?? [];
    const total = rows.length > 0 ? Number((rows[0] as { total?: number }).total ?? 0) : 0;
    const messages = rows.map(({ ts, type, session_id, data }: Record<string, unknown>) => ({
      ts,
      type,
      session_id,
      data,
    }));
    return jsonResp({ messages, total });
  }

  if (action === "live_instances") {
    const mode = (url.searchParams.get("mode") ?? "alive").toLowerCase();
    const windowMin = Math.max(1, Math.min(1440, Number(url.searchParams.get("window_min") ?? 30)));
    const ALIVE_STATES = [
      "connecting",
      "authenticating",
      "starting",
      "running",
      "degraded",
      "draining",
      "suspended",
    ];
    const selectCols =
      "agent_instance_id, user_id, worker_id, connection_id, target_player_id, state, state_changed_at, created_at, last_game_event_at, command_inflight_count, ended_reason, updated_at";

    let query = supabase.from("agent_instances").select(selectCols).limit(500);
    if (mode === "recent") {
      query = query
        .gte("state_changed_at", new Date(Date.now() - windowMin * 60_000).toISOString())
        .order("state_changed_at", { ascending: false });
    } else {
      query = query.in("state", ALIVE_STATES).order("updated_at", { ascending: false });
    }
    const { data, error: e } = await query;
    if (e) return errorResp(e.message, 500);

    const nowMs = Date.now();
    const rows = (data ?? []).map((r) => {
      const u = (r as { updated_at?: string }).updated_at;
      const staleSeconds = u ? Math.max(0, Math.floor((nowMs - new Date(u).getTime()) / 1000)) : null;
      return { ...r, stale_seconds: staleSeconds };
    });
    const counts: Record<string, number> = {};
    let inflight = 0;
    for (const r of rows) {
      const s = String((r as { state: string }).state);
      counts[s] = (counts[s] ?? 0) + 1;
      inflight += Number((r as { command_inflight_count?: number }).command_inflight_count ?? 0);
    }
    return jsonResp({
      mode: mode === "recent" ? "recent" : "alive",
      window_min: windowMin,
      instances: rows,
      summary: {
        total: rows.length,
        by_state: counts,
        inflight_commands: inflight,
        server_now: new Date().toISOString(),
      },
    });
  }

  if (action === "waitlist_list") {
    const statusParam = (url.searchParams.get("status") ?? "pending").trim();
    const ALLOWED_FILTER = ["pending", "approved", "rejected", "registered", "unknown", "all"];
    if (!ALLOWED_FILTER.includes(statusParam)) {
      return errorResp("invalid status filter", 400);
    }

    const hasPageParams = url.searchParams.has("page") || url.searchParams.has("pageSize");
    let pageSize: number;
    let page: number;
    let offset: number;

    if (hasPageParams) {
      pageSize = Math.min(200, Math.max(1, Number(url.searchParams.get("pageSize") ?? 50)));
      page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
      offset = (page - 1) * pageSize;
    } else {
      pageSize = Math.min(200, Math.max(1, Number(url.searchParams.get("limit") ?? 50)));
      page = 1;
      offset = Math.max(0, Number(url.searchParams.get("offset") ?? 0));
    }

    let query = supabase
      .from("waitlist")
      .select("id, email, wechat, qq, message, status, created_at", { count: "exact" })
      .order("created_at", { ascending: false })
      .range(offset, offset + pageSize - 1);

    if (statusParam !== "all") {
      query = query.eq("status", statusParam);
    }

    const q = (url.searchParams.get("q") ?? "").trim();
    if (q) {
      const escaped = escapeOrValue(escapeLikeWildcards(q)) + "%";
      query = query.or(`email.ilike.${escaped},wechat.ilike.${escaped},qq.ilike.${escaped}`);
    }

    const { data, error: e, count } = await query;
    if (e) return errorResp(e.message, 500);
    return jsonResp({
      total: count ?? data?.length ?? 0,
      page,
      pageSize,
      offset,
      status: statusParam,
      waitlist: data ?? [],
    });
  }

  if (action === "waitlist_user_detail") {
    const waitlistId = Number(url.searchParams.get("waitlist_id"));
    if (!Number.isInteger(waitlistId) || waitlistId <= 0) {
      return errorResp("missing or invalid waitlist_id parameter");
    }

    // 1. 审核状态
    const { data: wRow, error: wErr } = await supabase
      .from("waitlist")
      .select("id, email, status")
      .eq("id", waitlistId)
      .maybeSingle();
    if (wErr) return errorResp(wErr.message, 500);
    if (!wRow) return errorResp("waitlist entry not found", 404);

    // 2. 邮件发送 + 3. 密码设置（同一张表，取最新一条）
    const { data: tokenRow, error: tErr } = await supabase
      .from("verification_tokens")
      .select("resend_email_id, used_at")
      .eq("waitlist_id", waitlistId)
      .eq("type", "waitlist_invite")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (tErr) return errorResp(tErr.message, 500);

    const emailSent = tokenRow != null && tokenRow.resend_email_id != null;
    const passwordSet = tokenRow != null && tokenRow.used_at != null;

    // 4. 首次登录：waitlist.email → public.users.auth_id → auth.users.last_sign_in_at
    let firstLogin = false;
    let firstLoginAt: string | null = null;

    if (wRow.email) {
      const { data: pubUser, error: pubErr } = await supabase
        .from("users")
        .select("auth_id")
        .eq("email", wRow.email)
        .maybeSingle();
      if (pubErr) return errorResp(pubErr.message, 500);

      if (pubUser?.auth_id) {
        const { data: authUserData, error: authErr } =
          await supabase.auth.admin.getUserById(pubUser.auth_id);
        if (authErr) return errorResp(authErr.message, 500);

        const lastSignIn = authUserData?.user?.last_sign_in_at ?? null;
        firstLogin = lastSignIn != null;
        firstLoginAt = lastSignIn ?? null;
      }
    }

    return jsonResp({
      ok: true,
      detail: {
        status: wRow.status as string,
        email_sent: emailSent,
        resend_email_id: tokenRow?.resend_email_id ?? null,
        password_set: passwordSet,
        password_set_at: tokenRow?.used_at ?? null,
        first_login: firstLogin,
        first_login_at: firstLoginAt,
      },
    });
  }

  if (action === "user_timeline") {
    const userId = url.searchParams.get("user_id");
    if (!userId) return errorResp("missing user_id parameter");
    const { data, error: e } = await supabase.rpc("admin_user_timeline", { target_user_id: userId });
    if (e) return errorResp(e.message, 500);
    return jsonResp({ timeline: data ?? [] });
  }

  if (action === "gateway_status") {
    const base = getGatewayHttpBaseUrl();
    const res = await fetch(`${base}/api/v1/admin/gateway/status`, {
      headers: { "x-admin-secret": secret },
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  }

  if (action === "hosted_runtime_health") {
    const { payload, status } = await callBackendInternal("/internal/runtime-manager/v1/health");
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_account") {
    const userId = (url.searchParams.get("user_id") ?? "").trim();
    if (!userId) return errorResp("missing user_id parameter", 400);
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/users/${encodeURIComponent(userId)}`,
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_groups") {
    const suffix = relayQuerySuffix(url, ["include_disabled"]);
    const { payload, status } = await callBackendInternal(`/internal/ai/relay/groups${suffix}`);
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_quota") {
    const userId = (url.searchParams.get("user_id") ?? "").trim();
    if (!userId) return errorResp("missing user_id parameter", 400);
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/quota?user_id=${encodeURIComponent(userId)}`,
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_quota_ledger") {
    const userId = (url.searchParams.get("user_id") ?? "").trim();
    if (!userId) return errorResp("missing user_id parameter", 400);
    const suffix = relayQuerySuffix(
      url,
      ["page", "page_size", "start_timestamp", "end_timestamp", "types"],
      { user_id: userId },
    );
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/quota-ledger${suffix}`,
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_usage_logs") {
    const userId = (url.searchParams.get("user_id") ?? "").trim();
    if (!userId) return errorResp("missing user_id parameter", 400);
    const suffix = relayQuerySuffix(
      url,
      [
        "page",
        "page_size",
        "start_timestamp",
        "end_timestamp",
        "model_name",
        "token_name",
        "group",
        "request_id",
        "include_errors",
      ],
      { user_id: userId },
    );
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/usage/logs${suffix}`,
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_usage_summary") {
    const userId = (url.searchParams.get("user_id") ?? "").trim();
    if (!userId) return errorResp("missing user_id parameter", 400);
    const suffix = relayQuerySuffix(
      url,
      ["start_timestamp", "end_timestamp", "model_name", "token_name", "group", "granularity", "page", "page_size"],
      { user_id: userId },
    );
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/usage/summary${suffix}`,
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_health") {
    const group = (url.searchParams.get("group") ?? "").trim();
    const suffix = group ? `?group=${encodeURIComponent(group)}` : "";
    const { payload, status } = await callBackendInternal(`/internal/ai/relay/health/overview${suffix}`);
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_health_models") {
    const suffix = relayQuerySuffix(url, ["group"]);
    const { payload, status } = await callBackendInternal(`/internal/ai/relay/health/models${suffix}`);
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_health_channels") {
    const suffix = relayQuerySuffix(url, ["group", "model", "include_balance"]);
    const { payload, status } = await callBackendInternal(`/internal/ai/relay/health/channels${suffix}`);
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_health_users") {
    const suffix = relayQuerySuffix(url, ["start_timestamp", "end_timestamp", "granularity", "page", "page_size"]);
    const { payload, status } = await callBackendInternal(`/internal/ai/relay/health/users${suffix}`);
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_tokens") {
    const userId = (url.searchParams.get("user_id") ?? "").trim();
    if (!userId) return errorResp("missing user_id parameter", 400);
    const suffix = relayQuerySuffix(url, ["page", "page_size"]);
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/users/${encodeURIComponent(userId)}/tokens${suffix}`,
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "invite_codes_list") {
    const { data: codes, error: e } = await supabase
      .from("invite_codes")
      .select("code, type, owner_user_id, official_code_reason, created_at, used_count, max_uses, expires_at, disabled")
      .eq("type", "official")
      .order("created_at", { ascending: false });
    if (e) return errorResp(e.message, 500);

    const officialCodes = codes ?? [];
    const codeValues = officialCodes.map((row: { code: string }) => row.code).filter(Boolean);
    const usersByCode: Record<string, unknown[]> = {};
    let directUsers: Array<{
      user_id: string;
      nickname: string | null;
      email: string | null;
      phone: string | null;
      wechat: string | null;
      invite_code_used: string;
      created_at: string;
      source: string | null;
    }> = [];
    let relationRows: Array<{
      user_id: string;
      inviter_user_id: string | null;
      invite_code: string | null;
      created_at: string;
    }> = [];

    if (codeValues.length > 0) {
      const { data: userRows, error: usersErr } = await supabase
        .from("users")
        .select("user_id, nickname, email, phone, wechat, invite_code_used, created_at, source")
        .in("invite_code_used", codeValues)
        .order("created_at", { ascending: false });
      if (usersErr) return errorResp(usersErr.message, 500);
      directUsers = userRows ?? [];

      const { data: relations, error: relationsErr } = await supabase
        .from("user_relations")
        .select("user_id, inviter_user_id, invite_code, created_at")
        .in("invite_code", codeValues)
        .order("created_at", { ascending: false });
      if (relationsErr) return errorResp(relationsErr.message, 500);
      relationRows = relations ?? [];

      const userIds = [
        ...new Set([
          ...directUsers.map((u) => u.user_id).filter(Boolean),
          ...relationRows.map((r) => r.user_id).filter(Boolean),
        ]),
      ];
      const userMap: Record<string, {
        user_id: string;
        nickname: string | null;
        email: string | null;
        phone: string | null;
        wechat: string | null;
        invite_code_used: string | null;
        created_at: string;
        source: string | null;
      }> = {};
      const profileMap: Record<string, { nickname: string | null; user_type: string | null }> = {};
      if (userIds.length > 0) {
        const { data: allUserRows, error: allUsersErr } = await supabase
          .from("users")
          .select("user_id, nickname, email, phone, wechat, invite_code_used, created_at, source")
          .in("user_id", userIds);
        if (allUsersErr) return errorResp(allUsersErr.message, 500);
        for (const user of allUserRows ?? []) {
          userMap[user.user_id] = user;
        }

        const { data: profileRows, error: profileErr } = await supabase
          .from("profiles")
          .select("user_id, nickname, user_type")
          .in("user_id", userIds);
        if (profileErr) return errorResp(profileErr.message, 500);
        for (const profile of profileRows ?? []) {
          profileMap[profile.user_id] = {
            nickname: profile.nickname ?? null,
            user_type: profile.user_type ?? null,
          };
        }
      }

      function appendInvitedUser(
        code: string,
        userId: string,
        sourceKind: "users.invite_code_used" | "user_relations",
        relationCreatedAt?: string,
      ) {
        const user = userMap[userId] ?? directUsers.find((u) => u.user_id === userId);
        if (!user) return;
        const profile = profileMap[userId];
        const item = {
          user_id: userId,
          nickname: profile?.nickname ?? user.nickname ?? null,
          email: user.email ?? null,
          phone: user.phone ?? null,
          wechat: user.wechat ?? null,
          user_type: profile?.user_type ?? null,
          source: user.source ?? null,
          created_at: relationCreatedAt ?? user.created_at,
          usage_source: sourceKind,
        };
        if (!usersByCode[code]) usersByCode[code] = [];
        const exists = usersByCode[code].some((existing) =>
          isRecord(existing) && existing.user_id === userId,
        );
        if (!exists) usersByCode[code].push(item);
      }

      for (const user of directUsers) {
        appendInvitedUser(user.invite_code_used, user.user_id, "users.invite_code_used");
      }
      for (const relation of relationRows) {
        if (relation.invite_code) {
          appendInvitedUser(
            relation.invite_code,
            relation.user_id,
            "user_relations",
            relation.created_at,
          );
        }
      }
    }

    const enriched = officialCodes.map((row: {
      code: string;
      type: string;
      owner_user_id: string | null;
      official_code_reason: string | null;
      created_at: string;
      used_count: number;
      max_uses: number | null;
      expires_at: string | null;
      disabled: boolean;
    }) => {
      const invitedUsers = usersByCode[row.code] ?? [];
      const maxUses = row.max_uses;
      const usedCount = Number(row.used_count ?? 0);
      const remaining = maxUses == null ? null : Math.max(0, Number(maxUses) - usedCount);
      const usageRate = maxUses == null ? null : Math.round((usedCount / Math.max(1, Number(maxUses))) * 100);
      return {
        ...row,
        invited_users: invitedUsers,
        usage: {
          used_count: usedCount,
          invited_user_count: invitedUsers.length,
          max_uses: maxUses,
          remaining,
          usage_rate: usageRate,
          status: officialInviteStatus(row),
        },
      };
    });

    const totalCapacity = enriched.reduce(
      (sum, row) => (row.max_uses == null ? sum : sum + Number(row.max_uses)),
      0,
    );
    const hasUnlimited = enriched.some((row) => row.max_uses == null);
    const totalUsed = enriched.reduce((sum, row) => sum + Number(row.used_count ?? 0), 0);

    return jsonResp({
      invite_codes: enriched,
      summary: {
        total_codes: enriched.length,
        active_codes: enriched.filter((row) => row.usage.status === "active").length,
        disabled_codes: enriched.filter((row) => row.disabled).length,
        exhausted_codes: enriched.filter((row) => row.usage.status === "exhausted").length,
        total_capacity: hasUnlimited ? null : totalCapacity,
        total_used: totalUsed,
        total_remaining: hasUnlimited ? null : Math.max(0, totalCapacity - totalUsed),
        invited_user_count: Object.values(usersByCode).reduce((sum, rows) => sum + rows.length, 0),
      },
    });
  }

  if (action === "roadshow_routes") {
    const routeKey = (url.searchParams.get("route_key") ?? "").trim();
    const status = (url.searchParams.get("status") ?? "").trim();
    const q = (url.searchParams.get("q") ?? "").trim().replace(/[%(),]/g, " ");
    const from = (url.searchParams.get("from") ?? "").trim();
    const to = (url.searchParams.get("to") ?? "").trim();
    const limit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit") ?? 80)));

    const { data: routes, error: routeErr } = await supabase
      .from("roadshow_routes")
      .select("*")
      .order("updated_at", { ascending: false });
    if (routeErr) return errorResp(routeErr.message, 500);

    let inputQuery = supabase
      .from("external_text_inputs")
      .select("input_id, route_key, user_id, text, sanitized_text, received_at, agent_instance_id, delivery_status, error")
      .order("received_at", { ascending: false })
      .limit(limit);
    if (routeKey) inputQuery = inputQuery.eq("route_key", routeKey);
    if (status) inputQuery = inputQuery.eq("delivery_status", status);
    if (from) inputQuery = inputQuery.gte("received_at", from);
    if (to) inputQuery = inputQuery.lte("received_at", to);
    if (q) inputQuery = inputQuery.or(`text.ilike.%${q}%,sanitized_text.ilike.%${q}%,input_id.ilike.%${q}%`);

    const { data: inputs, error: inputErr } = await inputQuery;
    if (inputErr) return errorResp(inputErr.message, 500);

    let eventQuery = supabase
      .from("roadshow_delivery_events")
      .select("id, input_id, route_key, user_id, agent_instance_id, event_type, delivery_status, payload_type, sanitized_text, payload, error, created_at")
      .order("created_at", { ascending: false })
      .limit(limit);
    if (routeKey) eventQuery = eventQuery.eq("route_key", routeKey);
    if (status) eventQuery = eventQuery.eq("delivery_status", status);
    if (from) eventQuery = eventQuery.gte("created_at", from);
    if (to) eventQuery = eventQuery.lte("created_at", to);
    if (q) eventQuery = eventQuery.or(`sanitized_text.ilike.%${q}%,input_id.ilike.%${q}%,event_type.ilike.%${q}%`);

    const { data: deliveryEvents, error: eventErr } = await eventQuery;
    if (eventErr) return errorResp(eventErr.message, 500);

    return jsonResp({
      routes: routes ?? [],
      recent_inputs: inputs ?? [],
      delivery_events: deliveryEvents ?? [],
      filters: { route_key: routeKey, status, q, from, to, limit },
    });
  }

  return errorResp(
    "unknown action, use ?action=users|stats|event_stats|dashboard|user_reports|llm_stats|action_stats|error_ranking|active_users|user_profile|conversations|user_timeline|live_instances|gateway_status|hosted_runtime_health|ai_relay_account|ai_relay_groups|ai_relay_quota|ai_relay_quota_ledger|ai_relay_usage_logs|ai_relay_usage_summary|ai_relay_health|ai_relay_health_models|ai_relay_health_channels|ai_relay_health_users|ai_relay_tokens|invite_codes_list|waitlist_list|waitlist_user_detail|roadshow_routes"
  );
  } catch (e) {
    const msg = e instanceof Error ? e.message : "internal error";
    return errorResp(msg, 500);
  }
}

export async function POST(req: NextRequest) {
  const adminSecret = (process.env.ADMIN_SECRET ?? "").trim();
  const fromX = (req.headers.get("x-admin-secret") ?? "").trim();
  const fromBearer = (req.headers.get("authorization") ?? "")
    .replace(/^Bearer\s+/i, "")
    .trim();
  const token = fromX || fromBearer;

  if (!adminSecret || token !== adminSecret) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
  const url = new URL(req.url);
  const action = url.searchParams.get("action");

  if (action === "kick_instance") {
    const base = getGatewayHttpBaseUrl();
    const instanceId = url.searchParams.get("instance_id");
    if (!instanceId || !/^[a-zA-Z0-9_-]+$/.test(instanceId)) {
      return NextResponse.json({ error: "invalid or missing instance_id" }, { status: 400 });
    }
    const body = await req.json().catch(() => ({}));
    const res = await fetch(`${base}/api/v1/admin/instances/${instanceId}/kick`, {
      signal: AbortSignal.timeout(10_000),
      method: "POST",
      headers: {
        "x-admin-secret": adminSecret,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  }

  if (action === "set_maintenance") {
    const base = getGatewayHttpBaseUrl();
    const body = await req.json().catch(() => ({}));
    const res = await fetch(`${base}/api/v1/admin/gateway/maintenance`, {
      method: "POST",
      headers: {
        "x-admin-secret": adminSecret,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  }

  if (action === "ai_relay_ensure_account") {
    const body = await req.json().catch(() => ({}));
    const userId = typeof body.user_id === "string" ? body.user_id.trim() : "";
    if (!userId) return NextResponse.json({ error: "user_id required" }, { status: 400 });
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/users/${encodeURIComponent(userId)}/ensure`,
      {
        method: "PUT",
        body: JSON.stringify({
          email: typeof body.email === "string" ? body.email : undefined,
          display_name: typeof body.display_name === "string" ? body.display_name : undefined,
          default_group: typeof body.default_group === "string" ? body.default_group : undefined,
          preferred_models: isRecord(body.preferred_models) ? body.preferred_models : undefined,
        }),
      },
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_update_account") {
    const body = await req.json().catch(() => ({}));
    const userId = typeof body.user_id === "string" ? body.user_id.trim() : "";
    if (!userId) return NextResponse.json({ error: "user_id required" }, { status: 400 });
    const payloadBody: Record<string, unknown> = {};
    if (typeof body.group === "string") payloadBody.group = body.group;
    if (typeof body.status === "string") payloadBody.status = body.status;
    if (isRecord(body.preferred_models)) payloadBody.preferred_models = body.preferred_models;

    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/users/${encodeURIComponent(userId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(payloadBody),
      },
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_disable_account") {
    const body = await req.json().catch(() => ({}));
    const userId = typeof body.user_id === "string" ? body.user_id.trim() : "";
    if (!userId) return NextResponse.json({ error: "user_id required" }, { status: 400 });
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/users/${encodeURIComponent(userId)}?mode=disable`,
      { method: "DELETE" },
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_update_token") {
    const body = await req.json().catch(() => ({}));
    const tokenId = Number(body.token_id);
    if (!Number.isInteger(tokenId) || tokenId <= 0) {
      return NextResponse.json({ error: "valid token_id required" }, { status: 400 });
    }
    const payloadBody: Record<string, unknown> = {};
    if (typeof body.status === "number") payloadBody.status = body.status;
    if (typeof body.group === "string") payloadBody.group = body.group;
    if (isRecord(body.quota_policy)) payloadBody.quota_policy = body.quota_policy;
    if (typeof body.model_limits === "string" || Array.isArray(body.model_limits) || isRecord(body.model_limits)) {
      payloadBody.model_limits = body.model_limits;
    }
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/tokens/${tokenId}`,
      {
        method: "PATCH",
        body: JSON.stringify(payloadBody),
      },
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_delete_token") {
    const body = await req.json().catch(() => ({}));
    const tokenId = Number(body.token_id);
    if (!Number.isInteger(tokenId) || tokenId <= 0) {
      return NextResponse.json({ error: "valid token_id required" }, { status: 400 });
    }
    const reason = typeof body.reason === "string" && body.reason.trim()
      ? `?reason=${encodeURIComponent(body.reason.trim())}`
      : "";
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/tokens/${tokenId}${reason}`,
      { method: "DELETE" },
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "ai_relay_quota_adjustment") {
    const body = await req.json().catch(() => ({}));
    const userId = typeof body.user_id === "string" ? body.user_id.trim() : "";
    const mode = typeof body.mode === "string" ? body.mode.trim().toLowerCase() : "";
    const quotaUsd = Number(body.quota_usd);
    const reason = typeof body.reason === "string" ? body.reason.trim() : "";
    const generatedIdempotencyKey = `admin-quota-${Date.now()}-${crypto.randomUUID()}`;
    const idempotencyKey = typeof body.idempotency_key === "string" && body.idempotency_key.trim()
      ? body.idempotency_key.trim()
      : generatedIdempotencyKey;
    if (!userId) return NextResponse.json({ error: "user_id required" }, { status: 400 });
    if (!mode) return NextResponse.json({ error: "mode required" }, { status: 400 });
    if (!Number.isFinite(quotaUsd)) return NextResponse.json({ error: "quota_usd must be a number" }, { status: 400 });
    if (quotaUsd < 0) return NextResponse.json({ error: "quota_usd must be >= 0" }, { status: 400 });
    if ((mode === "add" || mode === "subtract") && quotaUsd <= 0) {
      return NextResponse.json({ error: "quota_usd must be > 0 for add/subtract" }, { status: 400 });
    }
    const fallbackReason = `后台额度调整：${mode}`;
    const { payload, status } = await callBackendInternal(
      "/internal/ai/relay/quota-adjustments",
      {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          mode,
          quota_usd: quotaUsd,
          reason: reason || fallbackReason,
          idempotency_key: idempotencyKey,
          biz_ref_type: typeof body.biz_ref_type === "string" ? body.biz_ref_type : "admin_quota_adjustment",
          biz_ref_id: typeof body.biz_ref_id === "string" ? body.biz_ref_id : idempotencyKey,
        }),
      },
    );
    return NextResponse.json(payload, { status });
  }

  if (action === "official_invite_disable") {
    const body = await req.json().catch(() => ({}));
    const code = readInviteCode(body.code);
    if (!code) return NextResponse.json({ error: "code required" }, { status: 400 });

    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    const { data: updated, error } = await supabase
      .from("invite_codes")
      .update({ disabled: true })
      .eq("code", code)
      .eq("type", "official")
      .select("code, type, official_code_reason, created_at, used_count, max_uses, expires_at, disabled")
      .maybeSingle();
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    if (!updated) return NextResponse.json({ error: "official invite code not found" }, { status: 404 });
    return NextResponse.json({ ok: true, invite_code: updated });
  }

  if (action === "official_invite_increase_max_uses") {
    const body = await req.json().catch(() => ({}));
    const code = readInviteCode(body.code);
    const additionalUses = readPositiveInt(body.add_uses ?? body.increment);
    if (!code) return NextResponse.json({ error: "code required" }, { status: 400 });
    if (additionalUses == null || additionalUses > 1_000_000) {
      return NextResponse.json({ error: "add_uses must be an integer between 1 and 1000000" }, { status: 400 });
    }

    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    const { data: current, error: readErr } = await supabase
      .from("invite_codes")
      .select("code, type, max_uses, used_count")
      .eq("code", code)
      .eq("type", "official")
      .maybeSingle();
    if (readErr) return NextResponse.json({ error: readErr.message }, { status: 500 });
    if (!current) return NextResponse.json({ error: "official invite code not found" }, { status: 404 });
    if (current.max_uses == null) {
      return NextResponse.json({ error: "max_uses is already unlimited for this code" }, { status: 422 });
    }
    const nextMaxUses = Number(current.max_uses) + additionalUses;

    const { data: updated, error: updateErr } = await supabase
      .from("invite_codes")
      .update({ max_uses: nextMaxUses })
      .eq("code", code)
      .eq("type", "official")
      .select("code, type, official_code_reason, created_at, used_count, max_uses, expires_at, disabled")
      .maybeSingle();
    if (updateErr) return NextResponse.json({ error: updateErr.message }, { status: 500 });
    return NextResponse.json({ ok: true, invite_code: updated });
  }

  if (action === "official_invite_batch_create") {
    const body = await req.json().catch(() => ({}));
    const quantity = readPositiveInt(body.quantity);
    const maxUses = readPositiveInt(body.max_uses);
    const reason = readString(body.official_code_reason) || "official-admin-batch";
    const rawPrefix = readString(body.prefix) || "OFFICIAL";
    const prefix = rawPrefix.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 16) || "OFFICIAL";

    if (quantity == null || quantity > 100) {
      return NextResponse.json({ error: "quantity must be an integer between 1 and 100" }, { status: 400 });
    }
    if (maxUses == null || maxUses > 1_000_000) {
      return NextResponse.json({ error: "max_uses must be an integer between 1 and 1000000" }, { status: 400 });
    }
    if (reason.length > 200) {
      return NextResponse.json({ error: "official_code_reason must be <= 200 characters" }, { status: 400 });
    }

    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    for (let attempt = 0; attempt < 8; attempt++) {
      const codeSet = new Set<string>();
      while (codeSet.size < quantity) {
        codeSet.add(generateReadableInviteCode(prefix, 8));
      }
      const rows = Array.from(codeSet).map((code) => ({
        code,
        type: "official",
        owner_user_id: null,
        max_uses: maxUses,
        used_count: 0,
        official_code_reason: reason,
        expires_at: null,
        disabled: false,
      }));

      const { data: inserted, error } = await supabase
        .from("invite_codes")
        .insert(rows)
        .select("code, type, official_code_reason, created_at, used_count, max_uses, expires_at, disabled");
      if (!error) {
        return NextResponse.json({ ok: true, invite_codes: inserted ?? [] });
      }
      if (error.code !== "23505") {
        return NextResponse.json({ error: error.message }, { status: 500 });
      }
    }

    return NextResponse.json({ error: "failed to generate unique invite codes after retries" }, { status: 500 });
  }

  if (action === "user_upgrade_to_beta") {
    const body = await req.json().catch(() => null);
    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    const result = await handleAdminUserUpgradeToBeta({ supabase }, body);
    return NextResponse.json(result.body, { status: result.status });
  }

  if (action === "user_grant_credits") {
    const body = await req.json().catch(() => null);
    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    const result = await handleAdminGrantUserCredits({ supabase }, body);
    return NextResponse.json(result.body, { status: result.status });
  }

  if (action === "user_grant_credits_batch") {
    const body = await req.json().catch(() => null);
    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    const result = await handleAdminGrantUserCreditsBatch({ supabase }, body);
    return NextResponse.json(result.body, { status: result.status });
  }

  if (action === "waitlist_update_status") {
    let body: { id?: number; status?: string };
    try {
      body = await req.json();
    } catch {
      return NextResponse.json({ error: "invalid json" }, { status: 400 });
    }
    const id = Number(body.id);
    const nextStatus = (body.status ?? "").trim();
    if (!Number.isInteger(id) || id <= 0) {
      return NextResponse.json({ error: "invalid id" }, { status: 422 });
    }
    const ALLOWED_TARGETS = ["pending", "approved", "rejected"];
    if (!ALLOWED_TARGETS.includes(nextStatus)) {
      return NextResponse.json(
        { error: "status must be one of pending|approved|rejected" },
        { status: 422 },
      );
    }

    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }

    // 读取当前状态 + email（approved 时用于发送邀请邮件），校验状态机
    const { data: row, error: readErr } = await supabase
      .from("waitlist")
      .select("id, status, email")
      .eq("id", id)
      .maybeSingle();
    if (readErr) {
      return NextResponse.json({ error: readErr.message }, { status: 500 });
    }
    if (!row) {
      return NextResponse.json({ error: "waitlist entry not found" }, { status: 422 });
    }
    const current = String(row.status);
    if (current === "registered") {
      return NextResponse.json(
        { error: "registered status is read-only" },
        { status: 422 },
      );
    }
    // 当前若为 unknown 也允许重新归类到 pending/approved/rejected
    const VALID_FROM = ["pending", "approved", "rejected", "unknown"];
    if (!VALID_FROM.includes(current)) {
      return NextResponse.json(
        { error: `cannot transition from status ${current}` },
        { status: 422 },
      );
    }

    async function sendExistingUserUpgradeEmail(email: string): Promise<{ sent: boolean; reason?: string }> {
      try {
        const { Resend } = await import("resend");
        const resend = new Resend(process.env.RESEND_API_KEY);
        const senderEmail = process.env.RESEND_SENDER_EMAIL ?? "noreply@luosea.cn";
        const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://star-coplay.luosea.cn").replace(/\/$/, "");
        const loginUrl = `${siteUrl}/login`;

        const { error: sendErr } = await resend.emails.send({
          from: `StarCoPlay <${senderEmail}>`,
          to: email,
          subject: "你的 StarCoPlay 账号已升级为内测用户",
          html: UPGRADE_EMAIL_HTML.replaceAll("{{LOGIN_URL}}", loginUrl).replaceAll("{{SITE_URL}}", siteUrl),
        });

        if (sendErr) {
          console.error(`[admin] upgrade email send failed: ${sendErr.message}`);
          return { sent: false, reason: sendErr.message };
        }
        return { sent: true };
      } catch (e) {
        console.error(`[admin] upgrade email threw: ${e instanceof Error ? e.message : String(e)}`);
        return { sent: false, reason: "resend_exception" };
      }
    }

    let inviteResult: { sent: boolean; reason?: string } = { sent: false };
    let openResult: unknown = null;
    if (nextStatus === "approved" && row.email) {
      const { data: existingUser } = await supabase
        .from("users")
        .select("user_id, auth_id")
        .eq("email", row.email as string)
        .not("auth_id", "is", null)
        .maybeSingle();

      if (existingUser) {
        const upgradeResult = await handleAdminUserUpgradeToBeta(
          { supabase },
          {
            user_id: existingUser.user_id,
            waitlist_id: id,
            reason: "admin_waitlist_approve",
          },
        );
        if (upgradeResult.status >= 400) {
          return NextResponse.json(upgradeResult.body, { status: upgradeResult.status });
        }

        openResult = upgradeResult.body;
        inviteResult = await sendExistingUserUpgradeEmail(row.email as string);

        return NextResponse.json({
          ok: true,
          id,
          status: "registered",
          invite: inviteResult,
          open_access: openResult,
        });
      }
    }

    const updatePayload: { status: string } = { status: nextStatus };

    const { data: updated, error: updErr } = await supabase
      .from("waitlist")
      .update(updatePayload)
      .eq("id", id)
      .select("id, status")
      .maybeSingle();
    if (updErr) {
      return NextResponse.json({ error: updErr.message }, { status: 500 });
    }
    if (!updated) {
      return NextResponse.json({ error: "update failed" }, { status: 500 });
    }

    // 状态变为 approved 时：
    // 路径 A — 该邮箱已有账号：直接开通资源并置 registered。
    // 路径 B — 无账号：保留 approved，发送设置密码邮件；激活时再开通资源。
    if (
      updated.status === "approved" &&
      current !== "approved" &&
      row.email
    ) {
      // 1. 作废同邮箱旧的未使用 token
      await supabase
        .from("verification_tokens")
        .update({ used_at: new Date().toISOString() })
        .eq("email", row.email)
        .is("used_at", null);

      // 2. 生成新 token（INSERT，让 DB 生成 UUID）
      const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
      const { data: tokenRow, error: tokenErr } = await supabase
        .from("verification_tokens")
        .insert({
          email: row.email,
          type: "waitlist_invite",
          waitlist_id: id,
          expires_at: expiresAt,
        })
        .select("token")
        .single();

      if (tokenErr || !tokenRow) {
        // token 生成失败，记录但不阻断 status 更新
        console.error(`[admin] verification_tokens insert failed: ${tokenErr?.message}`);
        inviteResult = { sent: false, reason: tokenErr?.message ?? "token_insert_failed" };
      } else {
        // 3. 用 Resend 发自定义邮件
        const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://star-coplay.luosea.cn").replace(/\/$/, "");
        const setPasswordUrl = `${siteUrl}/set-password?token=${tokenRow.token}`;

        try {
          const { Resend } = await import("resend");
          const resend = new Resend(process.env.RESEND_API_KEY);
          const senderEmail = process.env.RESEND_SENDER_EMAIL ?? "noreply@luosea.cn";

          const { data: sendData, error: sendErr } = await resend.emails.send({
            from: `StarCoPlay <${senderEmail}>`,
            to: row.email as string,
            subject: "🌟 你已通过 StarCoPlay 内测审核，点击设置密码",
            html: `
                <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
                  <h2 style="color: #5c4a1e;">欢迎加入 StarCoPlay 内测！</h2>
                  <p>你的申请已通过审核，点击下方按钮设置密码，完成注册：</p>
                  <a href="${setPasswordUrl}"
                     style="display:inline-block;margin:16px 0;padding:12px 24px;background:#7c5c2e;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;">
                    设置密码并激活账号
                  </a>
                  <p style="color:#888;font-size:12px;">链接 7 天内有效。如非本人操作请忽略此邮件。</p>
                  <p style="color:#888;font-size:12px;">如按钮无法点击，请复制以下链接到浏览器：<br/>${setPasswordUrl}</p>
                </div>
              `,
          });

          if (sendErr) {
            console.error(`[admin] resend send failed: ${sendErr.message}`);
            inviteResult = { sent: false, reason: sendErr.message };
          } else {
            inviteResult = { sent: true };
            // 将 Resend 返回的邮件 ID 写回 verification_tokens，便于追踪投递状态
            if (sendData?.id) {
              const { error: updateErr } = await supabase
                .from("verification_tokens")
                .update({ resend_email_id: sendData.id })
                .eq("waitlist_id", id)
                .eq("type", "waitlist_invite");
              if (updateErr) {
                console.warn(`[admin] failed to save resend_email_id: ${updateErr.message}`);
              }
            }
          }
        } catch (e) {
          console.error(`[admin] resend threw: ${e instanceof Error ? e.message : String(e)}`);
          inviteResult = { sent: false, reason: "resend_exception" };
        }
      }
    }

    return NextResponse.json({
      ok: true,
      id: updated.id,
      status: openResult ? "registered" : updated.status,
      invite: inviteResult,
      open_access: openResult,
    });
  }

  if (action === "roadshow_lookup_token") {
    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }
    const body = await req.json().catch(() => ({}));
    const platformToken = String(body.platform_token ?? "").trim();
    if (!platformToken) {
      return NextResponse.json({ error: "platform_token required" }, { status: 400 });
    }
    const { data: user, error: userErr } = await supabase
      .from("users")
      .select("user_id, platform_token, nickname, phone, wechat, email, created_at")
      .eq("platform_token", platformToken)
      .maybeSingle();
    if (userErr) return NextResponse.json({ error: userErr.message }, { status: 500 });
    if (!user) return NextResponse.json({ error: "user_not_found" }, { status: 404 });

    const { data: profile, error: profileErr } = await supabase
      .from("profiles")
      .select("nickname")
      .eq("user_id", user.user_id)
      .maybeSingle();
    if (profileErr) return NextResponse.json({ error: profileErr.message }, { status: 500 });

    const enrichedUser = {
      ...user,
      nickname: profile?.nickname ?? user.nickname ?? null,
    };

    const { data: instances, error: instErr } = await supabase
      .from("agent_instances")
      .select("agent_instance_id, user_id, worker_id, connection_id, target_player_id, state, state_changed_at, created_at, last_game_event_at, command_inflight_count, ended_reason, updated_at")
      .eq("user_id", user.user_id)
      .order("updated_at", { ascending: false })
      .limit(30);
    if (instErr) return NextResponse.json({ error: instErr.message }, { status: 500 });
    return NextResponse.json({ user: enrichedUser, instances: instances ?? [] });
  }

  if (action === "roadshow_save_route") {
    let supabase;
    try {
      supabase = createServiceClient();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "supabase init failed";
      return NextResponse.json({ error: msg }, { status: 503 });
    }
    const body = await req.json().catch(() => ({}));
    const routeKey = String(body.route_key ?? "").trim();
    const title = String(body.title ?? "路演入口").trim() || "路演入口";
    const targetUserId = String(body.target_user_id ?? "").trim();
    const targetAgentInstanceId = String(body.target_agent_instance_id ?? "").trim();
    const enabled = body.enabled !== false;

    if (!/^[a-zA-Z0-9_-]{3,64}$/.test(routeKey)) {
      return NextResponse.json({ error: "route_key must be 3-64 chars: letters, numbers, _, -" }, { status: 400 });
    }
    if (!targetUserId || !targetAgentInstanceId) {
      return NextResponse.json({ error: "target_user_id and target_agent_instance_id required" }, { status: 400 });
    }

    const { data: inst, error: instErr } = await supabase
      .from("agent_instances")
      .select("agent_instance_id, user_id")
      .eq("agent_instance_id", targetAgentInstanceId)
      .maybeSingle();
    if (instErr) return NextResponse.json({ error: instErr.message }, { status: 500 });
    if (!inst || inst.user_id !== targetUserId) {
      return NextResponse.json({ error: "agent_instance_not_owned_by_user" }, { status: 409 });
    }

    const { data, error: saveErr } = await supabase
      .from("roadshow_routes")
      .upsert({
        route_key: routeKey,
        title,
        enabled,
        target_user_id: targetUserId,
        target_agent_instance_id: targetAgentInstanceId,
      }, { onConflict: "route_key" })
      .select("*")
      .maybeSingle();
    if (saveErr) return NextResponse.json({ error: saveErr.message }, { status: 500 });
    return NextResponse.json({ ok: true, route: data });
  }

  return NextResponse.json({ error: "unknown action" }, { status: 400 });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "internal error";
    // 区分 gateway 不可达和内部错误
    if (msg.includes("fetch") || msg.includes("ECONNREFUSED") || msg.includes("timeout")) {
      return NextResponse.json({ error: "gateway unreachable" }, { status: 502 });
    }
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
