import { callBackendInternal, extractErrorMessage } from "./backend-internal";
import type { SupabaseClient } from "@supabase/supabase-js";

type AdminGrantStage = "parse" | "read_user" | "relay_adjustment" | "audit";
const NEWAPI_QUOTA_PER_CREDIT = 500_000;

interface AdminCreditUserRow {
  user_id: string;
  email: string | null;
  nickname: string | null;
}

interface AdminCreditGrantSuccess {
  ok: true;
  user_id: string;
  credits: number;
  quota: number;
  reason: string;
  idempotency_key: string;
  relay_adjustment: {
    ok: boolean;
    status: number;
    payload: unknown;
  };
}

interface AdminCreditGrantError {
  ok: false;
  error: string;
  reason?: string;
  stage?: AdminGrantStage;
}

export type AdminCreditGrantResult =
  | { status: number; body: AdminCreditGrantSuccess }
  | { status: number; body: AdminCreditGrantError };

export interface AdminCreditBatchGrantSuccess {
  ok: true;
  credits: number;
  reason: string;
  total: number;
  succeeded: AdminCreditGrantSuccess[];
  failed: Array<AdminCreditGrantError & { user_id: string }>;
}

export type AdminCreditBatchGrantResult =
  | { status: number; body: AdminCreditBatchGrantSuccess }
  | { status: number; body: AdminCreditGrantError };

export interface AdminCreditGrantDeps {
  supabase: SupabaseClient;
  quotaAdjuster?: (request: AdminQuotaAdjustmentRequest) => Promise<AdminQuotaAdjustmentResponse>;
  now?: () => Date;
}

export interface AdminQuotaAdjustmentRequest {
  user_id: string;
  mode: "add";
  quota: number;
  reason: string;
  idempotency_key: string;
  biz_ref_type: "admin_credit_grant";
  biz_ref_id: string;
}

export interface AdminQuotaAdjustmentResponse {
  ok: boolean;
  status: number;
  payload: unknown;
  error?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readCredits(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > 100_000) return null;
  const rounded = Math.round(parsed * 100) / 100;
  return rounded > 0 ? rounded : null;
}

function readUserIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [
    ...new Set(
      value
        .map((item) => (typeof item === "string" ? item.trim() : ""))
        .filter(Boolean),
    ),
  ];
}

function creditsToRelayQuota(credits: number): number {
  return Math.round(credits * NEWAPI_QUOTA_PER_CREDIT);
}

function stableHash(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function adminCreditGrantIdempotencyKey(input: {
  user_id: string;
  credits: number;
  reason: string;
  now: Date;
}): string {
  const day = input.now.toISOString().slice(0, 10);
  const creditsText = input.credits.toFixed(2);
  const digest = stableHash(`${input.user_id}\n${creditsText}\n${input.reason}\n${day}`);
  return `admin_credit_grant:${input.user_id}:${day}:${digest}`;
}

function errorResult(
  status: number,
  error: string,
  reason: string,
  stage: AdminGrantStage,
): AdminCreditGrantResult {
  return { status, body: { ok: false, error, reason, stage } };
}

function batchErrorResult(
  status: number,
  error: string,
  reason: string,
  stage: AdminGrantStage,
): AdminCreditBatchGrantResult {
  return { status, body: { ok: false, error, reason, stage } };
}

export async function callAdminCreditQuotaAdjustment(
  request: AdminQuotaAdjustmentRequest,
): Promise<AdminQuotaAdjustmentResponse> {
  try {
    const { payload, status } = await callBackendInternal(
      "/internal/ai/relay/quota-adjustments",
      {
        method: "POST",
        body: JSON.stringify(request),
      },
    );
    if (status < 200 || status >= 300) {
      return {
        ok: false,
        status,
        payload,
        error: extractErrorMessage(payload, "AI Relay quota adjustment failed"),
      };
    }
    return { ok: true, status, payload };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      payload: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function auditCreditGrant(
  deps: AdminCreditGrantDeps,
  input: {
    user_id: string;
    ok: boolean;
    stage: AdminGrantStage;
    credits: number | null;
    quota: number | null;
    reason: string | null;
    idempotency_key: string | null;
    error?: string | null;
    relay_status?: number | null;
    relay_payload?: unknown;
  },
): Promise<void> {
  const now = (deps.now?.() ?? new Date()).toISOString();
  try {
    await deps.supabase.from("events").insert({
      user_id: input.user_id,
      type: "admin.user_credit_grant",
      ts: now,
      session_id: "admin",
      received_at: now,
      data: {
        ok: input.ok,
        stage: input.stage,
        credits: input.credits,
        quota: input.quota,
        reason: input.reason,
        idempotency_key: input.idempotency_key,
        error: input.error ?? null,
        relay_status: input.relay_status ?? null,
        relay_payload: input.relay_payload ?? null,
      },
    });
  } catch (error) {
    console.warn(
      `[admin-user-credit] audit failed user_id=${input.user_id}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}

export async function handleAdminGrantUserCredits(
  deps: AdminCreditGrantDeps,
  body: unknown,
): Promise<AdminCreditGrantResult> {
  if (!isRecord(body)) {
    return errorResult(400, "invalid json body", "invalid_body", "parse");
  }

  const userId = readString(body.user_id);
  const credits = readCredits(body.credits);
  const reason = readString(body.reason);
  if (!userId) return errorResult(400, "user_id required", "missing_user_id", "parse");
  if (credits == null) {
    return errorResult(400, "credits must be a number between 0 and 100000", "invalid_credits", "parse");
  }
  if (!reason) return errorResult(400, "reason required", "missing_reason", "parse");

  const { data: user, error: userErr } = await deps.supabase
    .from("users")
    .select("user_id, email, nickname")
    .eq("user_id", userId)
    .maybeSingle();
  if (userErr) return errorResult(500, userErr.message, "user_lookup_failed", "read_user");
  if (!user) return errorResult(404, "user not found", "user_not_found", "read_user");

  const row = user as AdminCreditUserRow;
  const quota = creditsToRelayQuota(credits);
  const now = deps.now?.() ?? new Date();
  const idempotencyKey = adminCreditGrantIdempotencyKey({
    user_id: row.user_id,
    credits,
    reason,
    now,
  });
  const adjustment = await (deps.quotaAdjuster ?? callAdminCreditQuotaAdjustment)({
    user_id: row.user_id,
    mode: "add",
    quota,
    reason,
    idempotency_key: idempotencyKey,
    biz_ref_type: "admin_credit_grant",
    biz_ref_id: idempotencyKey,
  });

  if (!adjustment.ok) {
    const message = adjustment.error ?? `quota adjustment failed with status ${adjustment.status}`;
    await auditCreditGrant(deps, {
      user_id: row.user_id,
      ok: false,
      stage: "relay_adjustment",
      credits,
      quota,
      reason,
      idempotency_key: idempotencyKey,
      error: message,
      relay_status: adjustment.status,
      relay_payload: adjustment.payload,
    });
    return {
      status: adjustment.status > 0 ? adjustment.status : 502,
      body: { ok: false, error: message, reason: "relay_adjustment_failed", stage: "relay_adjustment" },
    };
  }

  await auditCreditGrant(deps, {
    user_id: row.user_id,
    ok: true,
    stage: "audit",
    credits,
    quota,
    reason,
    idempotency_key: idempotencyKey,
    relay_status: adjustment.status,
    relay_payload: adjustment.payload,
  });

  return {
    status: 200,
    body: {
      ok: true,
      user_id: row.user_id,
      credits,
      quota,
      reason,
      idempotency_key: idempotencyKey,
      relay_adjustment: {
        ok: true,
        status: adjustment.status,
        payload: adjustment.payload,
      },
    },
  };
}

export async function handleAdminGrantUserCreditsBatch(
  deps: AdminCreditGrantDeps,
  body: unknown,
): Promise<AdminCreditBatchGrantResult> {
  if (!isRecord(body)) {
    return batchErrorResult(400, "invalid json body", "invalid_body", "parse");
  }

  const userIds = readUserIds(body.user_ids);
  const credits = readCredits(body.credits);
  const reason = readString(body.reason);
  if (userIds.length === 0) return batchErrorResult(400, "user_ids required", "missing_user_ids", "parse");
  if (userIds.length > 200) return batchErrorResult(400, "user_ids must contain at most 200 users", "too_many_users", "parse");
  if (credits == null) {
    return batchErrorResult(400, "credits must be a number between 0 and 100000", "invalid_credits", "parse");
  }
  if (!reason) return batchErrorResult(400, "reason required", "missing_reason", "parse");

  const settled = await Promise.all(
    userIds.map(async (userId) => {
      const result = await handleAdminGrantUserCredits(deps, { user_id: userId, credits, reason });
      return { userId, result };
    }),
  );

  const succeeded: AdminCreditGrantSuccess[] = [];
  const failed: Array<AdminCreditGrantError & { user_id: string }> = [];
  for (const item of settled) {
    if (item.result.body.ok) {
      succeeded.push(item.result.body);
    } else {
      failed.push({ ...item.result.body, user_id: item.userId });
    }
  }

  return {
    status: failed.length === 0 ? 200 : 207,
    body: {
      ok: true,
      credits,
      reason,
      total: userIds.length,
      succeeded,
      failed,
    },
  };
}
