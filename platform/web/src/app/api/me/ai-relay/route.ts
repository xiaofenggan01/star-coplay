import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { callBackendInternal, extractErrorMessage } from "@/lib/backend-internal";
import { canAccessLlmUi } from "@/lib/llm-ui-access";
import { getPlatformKeyAccess } from "@/lib/platform-access";
import { createServiceClient } from "@/lib/supabase-server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

type JsonRecord = Record<string, unknown>;

interface RelayAccount {
  user_id: string;
  newapi_user_id: number;
  newapi_token_id: number | null;
  has_token_key: boolean;
  has_user_access_token: boolean;
  group: string;
  preferred_models: Record<string, string>;
  credential_mode: string;
  status: string;
  last_sync_error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface RelayQuota {
  user_id: string;
  newapi_user_id: number;
  newapi_token_id: number | null;
  group: string;
  status: string;
  quota: number;
  used_quota: number;
  request_count: number;
  token: {
    id: number | null;
    name: string | null;
    unlimited_quota: boolean;
    total_granted: number | null;
    total_used: number | null;
    total_available: number | null;
    expires_at: unknown;
    model_limits_enabled: boolean;
    model_limits: unknown;
  };
  display: {
    balance_points: number;
    used_points: number;
    token_available_points: number | null;
    balance_credits?: number | null;
    used_credits?: number | null;
    token_available_credits?: number | null;
  };
}

interface UsageBucket {
  created_at: unknown;
  model_name: string | null;
  count: number;
  quota: number;
  token_used: number;
  display?: {
    quota_credits?: number | null;
  };
}

interface ModelUsageSummaryItem {
  model_name: string | null;
  count: number;
  quota: number;
  token_used: number;
  quota_share?: number;
  quota_percent?: number;
  display?: {
    quota_credits?: number | null;
  };
}

interface ModelUsageSummaries {
  page: number;
  page_size: number;
  total: number;
  total_quota?: number;
  items: ModelUsageSummaryItem[];
}

interface UsageSummary {
  user_id: string;
  newapi_user_id: number;
  range: {
    start_timestamp: number | null;
    end_timestamp: number | null;
  };
  granularity?: string;
  quota: number;
  rpm: number;
  tpm: number;
  buckets: UsageBucket[];
  model_summaries?: ModelUsageSummaries;
  display?: {
    quota_credits?: number | null;
  };
}

interface UsageLog {
  id?: number | string;
  created_at?: number | string;
  type?: number;
  content?: string;
  token_name?: string;
  model_name?: string;
  quota?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  use_time?: number;
  is_stream?: boolean;
  channel_name?: string;
  group?: string;
  request_id?: string;
  display?: {
    quota_credits?: number | null;
  };
}

interface UsageLogs {
  user_id: string;
  newapi_user_id: number;
  page: number;
  page_size: number;
  total: number;
  items: UsageLog[];
  included_log_types: number[];
}

interface QuotaLedger {
  user_id: string;
  newapi_user_id: number;
  page: number;
  page_size: number;
  total: number;
  items: UsageLog[];
  included_log_types: number[];
  excluded_log_types: number[];
}

interface SystemConfigPayload {
  parsed_value?: unknown;
}

interface ModelTierOption {
  tier: string;
  model_count: number;
  selected: boolean;
}

interface InternalModelTier {
  tier: string;
  models: string[];
}

interface PublicBackendError {
  label: string;
  status: number;
  code: string;
  message: string;
}

class RouteBackendError extends Error {
  status: number;
  payload: unknown;
  code: string;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "RouteBackendError";
    this.status = status;
    this.payload = payload;
    this.code = extractBackendErrorCode(payload) ?? "BACKEND_REQUEST_FAILED";
  }
}

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
    const accessSupabase = createServiceClient();
    const keyAccess = await getPlatformKeyAccess(user.user_id, accessSupabase);
    if (!keyAccess.ok) {
      return NextResponse.json(
        { error: keyAccess.code, reason: keyAccess.code },
        { status: keyAccess.status, headers: corsHeaders },
      );
    }
    const allowed = await canAccessLlmUi(user, accessSupabase);
    if (!allowed) {
      return NextResponse.json(
        { error: "forbidden", reason: "llm_ui_access_denied" },
        { status: 403, headers: corsHeaders },
      );
    }
    if (req.nextUrl.searchParams.get("access") === "1") {
      return NextResponse.json({ allowed: true }, { headers: corsHeaders });
    }

    const now = Math.floor(Date.now() / 1000);
    const summaryStart = readTimestamp(req.nextUrl.searchParams, "summary_start_timestamp", now - 30 * 24 * 60 * 60);
    const summaryEnd = readTimestamp(req.nextUrl.searchParams, "summary_end_timestamp", now);
    const usageDetailStart = readTimestamp(req.nextUrl.searchParams, "usage_start_timestamp", now - 7 * 24 * 60 * 60);
    const usageDetailEnd = readTimestamp(req.nextUrl.searchParams, "usage_end_timestamp", now);
    const ledgerStart = summaryStart;
    const pageSize = readPageNumber(req.nextUrl.searchParams, "page_size", 10, 50);
    const usagePage = readPageNumber(req.nextUrl.searchParams, "usage_page", 1, 10_000);
    const ledgerPage = readPageNumber(req.nextUrl.searchParams, "ledger_page", 1, 10_000);
    const summaryPage = readPageNumber(req.nextUrl.searchParams, "summary_page", 1, 10_000);
    const summaryPageSize = readPageNumber(req.nextUrl.searchParams, "summary_page_size", 8, 50);
    const summaryGranularity = readGranularity(req.nextUrl.searchParams, "summary_granularity", "all");

    const [accountResult, mappingResult] = await Promise.all([
      safeBackendData<RelayAccount>(
        "account",
        `/internal/ai/relay/users/${encodeURIComponent(user.user_id)}`,
      ),
      safeBackendPayload<SystemConfigPayload>(
        "model_tier_mapping",
        "/internal/configs/ai_relay/model_tier_mapping",
      ),
    ]);

    const account = accountResult.ok ? accountResult.data : null;

    if (!accountResult.ok && accountResult.error.status !== 404) {
      return NextResponse.json(
        { error: accountResult.error.message, details: [accountResult.error] },
        { status: normalizeStatus(accountResult.error.status), headers: corsHeaders },
      );
    }

    const [quotaResult, summaryResult, logsResult, ledgerResult] = account
      ? await Promise.all([
          safeBackendData<RelayQuota>(
            "quota",
            `/internal/ai/relay/users/${encodeURIComponent(user.user_id)}/quota`,
          ),
          safeBackendData<UsageSummary>(
            "usage_summary",
            `/internal/ai/relay/users/${encodeURIComponent(user.user_id)}/usage/summary?start_timestamp=${summaryStart}&end_timestamp=${summaryEnd}&granularity=${summaryGranularity}&page=${summaryPage}&page_size=${summaryPageSize}`,
          ),
          safeBackendData<UsageLogs>(
            "usage_logs",
            `/internal/ai/relay/users/${encodeURIComponent(user.user_id)}/usage/logs?page=${usagePage}&page_size=${pageSize}&start_timestamp=${usageDetailStart}&end_timestamp=${usageDetailEnd}&include_errors=true`,
          ),
          safeBackendData<QuotaLedger>(
            "quota_ledger",
            `/internal/ai/relay/users/${encodeURIComponent(user.user_id)}/quota-ledger?page=${ledgerPage}&page_size=${pageSize}&start_timestamp=${ledgerStart}&end_timestamp=${summaryEnd}`,
          ),
        ])
      : [
          null as SafeResult<RelayQuota> | null,
          null as SafeResult<UsageSummary> | null,
          null as SafeResult<UsageLogs> | null,
          null as SafeResult<QuotaLedger> | null,
        ];

    const defaultModelTier = await getUserDefaultModelTier(user.user_id);
    const modelTiers = buildModelTiers(
      mappingResult.ok ? mappingResult.data.parsed_value : null,
      defaultModelTier,
    );
    const errors = collectErrors(
      accountResult,
      mappingResult,
      quotaResult,
      summaryResult,
      logsResult,
      ledgerResult,
    );

    return NextResponse.json(
      {
        user_id: user.user_id,
        account,
        quota: quotaResult?.ok ? quotaResult.data : null,
        usage_summary: summaryResult?.ok ? summaryResult.data : null,
        usage_logs: logsResult?.ok ? logsResult.data : null,
        quota_ledger: ledgerResult?.ok ? ledgerResult.data : null,
        health: null,
        enabled_models: [],
        model_tiers: modelTiers,
        default_model_tier: defaultModelTier,
        setup_required: !account,
        checked_at: now,
        ranges: {
          usage_summary_days: Math.max(0, Math.ceil((summaryEnd - summaryStart) / (24 * 60 * 60))),
          usage_logs_days: Math.max(0, Math.ceil((usageDetailEnd - usageDetailStart) / (24 * 60 * 60))),
          quota_ledger_days: Math.max(0, Math.ceil((summaryEnd - ledgerStart) / (24 * 60 * 60))),
        },
        errors,
      },
      { headers: corsHeaders },
    );
  } catch (err) {
    return routeErrorResponse(err);
  }
}

export async function POST(req: NextRequest) {
  try {
    const user = await authenticateRequest(req);
    if (!user) {
      return NextResponse.json(
        { error: "unauthorized", reason: "invalid_token" },
        { status: 401, headers: corsHeaders },
      );
    }
    const accessSupabase = createServiceClient();
    const keyAccess = await getPlatformKeyAccess(user.user_id, accessSupabase);
    if (!keyAccess.ok) {
      return NextResponse.json(
        { error: keyAccess.code, reason: keyAccess.code },
        { status: keyAccess.status, headers: corsHeaders },
      );
    }
    const allowed = await canAccessLlmUi(user, accessSupabase);
    if (!allowed) {
      return NextResponse.json(
        { error: "forbidden", reason: "llm_ui_access_denied" },
        { status: 403, headers: corsHeaders },
      );
    }

    const account = await backendData<RelayAccount>(
      `/internal/ai/relay/users/${encodeURIComponent(user.user_id)}/ensure`,
      {
        method: "PUT",
        body: JSON.stringify({
          email: user.email ?? undefined,
          display_name: user.email ?? undefined,
        }),
        signal: AbortSignal.timeout(15_000),
      },
    );

    return NextResponse.json({ account }, { headers: corsHeaders });
  } catch (err) {
    return routeErrorResponse(err);
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
    const accessSupabase = createServiceClient();
    const keyAccess = await getPlatformKeyAccess(user.user_id, accessSupabase);
    if (!keyAccess.ok) {
      return NextResponse.json(
        { error: keyAccess.code, reason: keyAccess.code },
        { status: keyAccess.status, headers: corsHeaders },
      );
    }
    const allowed = await canAccessLlmUi(user, accessSupabase);
    if (!allowed) {
      return NextResponse.json(
        { error: "forbidden", reason: "llm_ui_access_denied" },
        { status: 403, headers: corsHeaders },
      );
    }

    const body = await readJsonObject(req);
    const defaultModelTier = typeof body.default_model_tier === "string"
      ? body.default_model_tier.trim()
      : "";
    const mappingPayload = await backendPayload<SystemConfigPayload>(
      "/internal/configs/ai_relay/model_tier_mapping",
    );
    const tiers = normalizeModelTierMapping(mappingPayload.parsed_value);
    validateDefaultModelTier(defaultModelTier, tiers);

    await setUserDefaultModelTier(user.user_id, defaultModelTier);

    return NextResponse.json({ default_model_tier: defaultModelTier }, { headers: corsHeaders });
  } catch (err) {
    return routeErrorResponse(err);
  }
}

async function backendData<T>(path: string, init: RequestInit = {}): Promise<T> {
  const payload = await backendPayload<{ data?: T }>(path, init);
  if (payload && typeof payload === "object" && "data" in payload) {
    return payload.data as T;
  }
  throw new RouteBackendError("backend response missing data", 502, payload);
}

async function backendPayload<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { payload, status } = await callBackendInternal(path, init);
  if (status < 200 || status >= 300) {
    throw new RouteBackendError(
      extractErrorMessage(payload, "backend request failed"),
      status,
      payload,
    );
  }
  return payload as T;
}

type SafeResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: PublicBackendError };

async function safeBackendData<T>(label: string, path: string): Promise<SafeResult<T>> {
  try {
    return { ok: true, data: await backendData<T>(path) };
  } catch (err) {
    return { ok: false, error: publicBackendError(label, err) };
  }
}

async function safeBackendPayload<T>(label: string, path: string): Promise<SafeResult<T>> {
  try {
    return { ok: true, data: await backendPayload<T>(path) };
  } catch (err) {
    return { ok: false, error: publicBackendError(label, err) };
  }
}

function publicBackendError(label: string, err: unknown): PublicBackendError {
  if (err instanceof RouteBackendError) {
    return {
      label,
      status: err.status,
      code: err.code,
      message: err.message,
    };
  }
  return {
    label,
    status: 500,
    code: "AI_RELAY_WEB_ERROR",
    message: err instanceof Error ? err.message : String(err),
  };
}

function collectErrors(...results: Array<SafeResult<unknown> | null>): PublicBackendError[] {
  return results.flatMap((result) => (result && !result.ok ? [result.error] : []));
}

async function readJsonObject(req: NextRequest): Promise<JsonRecord> {
  try {
    const value = await req.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new RouteBackendError("request body must be a JSON object", 400, {
        error: { code: "INVALID_JSON", message: "request body must be a JSON object" },
      });
    }
    return value as JsonRecord;
  } catch (err) {
    if (err instanceof RouteBackendError) throw err;
    throw new RouteBackendError("request body must be valid JSON", 400, {
      error: { code: "INVALID_JSON", message: "request body must be valid JSON" },
    });
  }
}

function readPageNumber(
  params: URLSearchParams,
  key: string,
  fallback: number,
  max: number,
): number {
  const raw = params.get(key);
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) return fallback;
  return Math.min(value, max);
}

function readTimestamp(params: URLSearchParams, key: string, fallback: number): number {
  const raw = params.get(key);
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) return fallback;
  return value;
}

function readGranularity(
  params: URLSearchParams,
  key: string,
  fallback: "all" | "hour" | "day" | "month",
): "all" | "hour" | "day" | "month" {
  const raw = params.get(key)?.trim().toLowerCase();
  if (raw === "all" || raw === "hour" || raw === "day" || raw === "month") return raw;
  return fallback;
}

function validateDefaultModelTier(
  defaultModelTier: string,
  tiers: InternalModelTier[],
): void {
  if (tiers.length === 0) {
    throw new RouteBackendError("model tier mapping is not configured", 409, {
      error: {
        code: "MODEL_TIER_MAPPING_MISSING",
        message: "model tier mapping is not configured",
      },
    });
  }

  if (!defaultModelTier) {
    throw new RouteBackendError("default_model_tier is required", 400, {
      error: {
        code: "MODEL_TIER_REQUIRED",
        message: "default_model_tier is required",
      },
    });
  }

  if (!tiers.some((tier) => tier.tier === defaultModelTier)) {
    throw new RouteBackendError(`model tier is not allowed: ${defaultModelTier}`, 400, {
      error: {
        code: "MODEL_TIER_NOT_ALLOWED",
        message: `model tier is not allowed: ${defaultModelTier}`,
      },
    });
  }
}

function buildModelTiers(
  rawMapping: unknown,
  defaultModelTier: string | null,
): ModelTierOption[] {
  return normalizeModelTierMapping(rawMapping).map((tier) => {
    return {
      tier: tier.tier,
      model_count: tier.models.length,
      selected: tier.tier === defaultModelTier,
    };
  });
}

function normalizeModelTierMapping(raw: unknown): InternalModelTier[] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const result: InternalModelTier[] = [];
  for (const [key, value] of Object.entries(raw as JsonRecord)) {
    const tier = key.trim();
    if (!tier) continue;
    const models = extractModels(value);
    if (models.length === 0) continue;
    result.push({
      tier,
      models,
    });
  }
  return result;
}

async function getUserDefaultModelTier(userId: string): Promise<string | null> {
  const supabase = createServiceClient();
  const { data, error } = await supabase
    .from("profiles")
    .select("preferences")
    .eq("user_id", userId)
    .maybeSingle();
  if (error || !data) return null;
  const preferences = data.preferences;
  if (!preferences || typeof preferences !== "object" || Array.isArray(preferences)) return null;
  const aiRelay = (preferences as JsonRecord).ai_relay;
  if (!aiRelay || typeof aiRelay !== "object" || Array.isArray(aiRelay)) return null;
  const value = (aiRelay as JsonRecord).default_model_tier;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

async function setUserDefaultModelTier(userId: string, defaultModelTier: string): Promise<void> {
  const supabase = createServiceClient();
  const { data, error } = await supabase
    .from("profiles")
    .select("preferences")
    .eq("user_id", userId)
    .maybeSingle();
  if (error) {
    throw new RouteBackendError("failed to read user profile", 500, {
      error: { code: "PROFILE_READ_FAILED", message: error.message },
    });
  }

  const current = data?.preferences;
  const preferences: JsonRecord =
    current && typeof current === "object" && !Array.isArray(current) ? { ...(current as JsonRecord) } : {};
  const currentAiRelay = preferences.ai_relay;
  const aiRelay: JsonRecord =
    currentAiRelay && typeof currentAiRelay === "object" && !Array.isArray(currentAiRelay)
      ? { ...(currentAiRelay as JsonRecord) }
      : {};
  aiRelay.default_model_tier = defaultModelTier;
  preferences.ai_relay = aiRelay;

  const { error: upsertError } = await supabase
    .from("profiles")
    .upsert({ user_id: userId, preferences }, { onConflict: "user_id" });
  if (upsertError) {
    throw new RouteBackendError("failed to save user model tier", 500, {
      error: { code: "PROFILE_WRITE_FAILED", message: upsertError.message },
    });
  }
}

function extractModels(value: unknown): string[] {
  if (typeof value === "string") return dedupe([value]);
  if (Array.isArray(value)) return dedupe(value);
  if (!value || typeof value !== "object") return [];

  const record = value as JsonRecord;
  const rawModels = record.models;
  const items: unknown[] = [];
  if (typeof record.default === "string") items.push(record.default);
  if (typeof rawModels === "string") items.push(rawModels);
  if (Array.isArray(rawModels)) items.push(...rawModels);
  return dedupe(items);
}

function dedupe(items: unknown[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of items) {
    const value = typeof item === "string" ? item.trim() : "";
    if (!value || seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}

function extractBackendErrorCode(payload: unknown): string | undefined {
  if (!payload || typeof payload !== "object" || !("error" in payload)) return undefined;
  const error = (payload as { error?: unknown }).error;
  if (!error || typeof error !== "object" || !("code" in error)) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

function routeErrorResponse(err: unknown) {
  if (err instanceof RouteBackendError) {
    return NextResponse.json(
      {
        error: err.message,
        reason: err.code,
        details: err.payload,
      },
      { status: normalizeStatus(err.status), headers: corsHeaders },
    );
  }
  const msg = err instanceof Error ? err.message : "internal error";
  return NextResponse.json(
    { error: msg, reason: "internal_error" },
    { status: 500, headers: corsHeaders },
  );
}

function normalizeStatus(status: number): number {
  if (status >= 400 && status <= 599) return status;
  return 502;
}
