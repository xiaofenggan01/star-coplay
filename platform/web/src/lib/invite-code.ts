/**
 * 邀请码原子使用与补偿封装。
 *
 * 设计要点（详见 tech-spec.md §5.2 / §5.7）：
 *   - consumeInviteCode：单条 UPDATE 完成 CAS（compare-and-set）原子递增。
 *     成功（影响 1 行）→ 直接返回新行；失败（影响 0 行）→ 再 SELECT 一次诊断 reason。
 *     "更新-失败-诊断"两步之间状态可能再变化，但诊断只用于错误分类，
 *     不会引入超发，因为真正的"是否消费"完全由 UPDATE 那一步原子决定。
 *   - type='user' 且 max_uses=NULL 时，上限取 invite/policy.per_user_limit。
 *   - refundInviteCode：signUp 失败补偿，幂等（used_count > 0 守卫，避免负数）。
 *     补偿失败仅记录日志、不抛，避免遮蔽主调用方真实错误。
 *   - 全程使用 service role client，绕过 RLS 直接操作 invite_codes。
 */

import {
  readInvitePolicy,
  resolveInviteCodeUsageLimit,
  type InvitePolicy,
} from "./invite-policy";
import { createServiceClient } from "./supabase-server";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { InviteCode, InviteCodeRejectReason } from "./types/invite-code";

export type ConsumeResult =
  | { ok: true; code: InviteCode }
  | { ok: false; reason: InviteCodeRejectReason };

export type PeekInviteCodeResult =
  | { ok: true; code: InviteCode }
  | { ok: false; reason: InviteCodeRejectReason };

export type PeekUserInviteCodeResult =
  | { ok: true; code: InviteCode & { type: "user"; owner_user_id: string } }
  | { ok: false; reason: InviteCodeRejectReason | "not_user_code" | "missing_owner" };

export type CommitUserInviteUseResult =
  | { ok: true; code: InviteCode & { type: "user"; owner_user_id: string } }
  | { ok: false; reason: InviteCodeRejectReason | "not_user_code" | "missing_owner" };

const TABLE = "invite_codes";

/**
 * 可选客户端注入：测试场景下传入 mock client；
 * 生产代码不传，仍走 createServiceClient（行为不变）。
 * 与 register-handler 的 RegisterDeps 设计一致——通过依赖注入获得可测性，
 * 而不需要 mock 模块系统（tsx loader 不支持 node:test mock.module）。
 */
export type InviteCodeClient = Pick<SupabaseClient, "from">;

export interface ConsumeInviteCodeOptions {
  /** 单测注入；生产路径由 readInvitePolicy 读取 system_configs */
  invitePolicy?: InvitePolicy;
}

function usageLimitRejectReason(codeType: InviteCode["type"]): InviteCodeRejectReason {
  return codeType === "user" ? "invite_limit_reached" : "exhausted";
}

async function resolveInvitePolicyForConsume(
  client: InviteCodeClient,
  options?: ConsumeInviteCodeOptions,
): Promise<InvitePolicy> {
  if (options?.invitePolicy) return options.invitePolicy;
  return readInvitePolicy(client as SupabaseClient);
}

function validateInviteCodeAvailability(
  row: InviteCode,
  invitePolicy: InvitePolicy,
): { ok: true } | { ok: false; reason: InviteCodeRejectReason } {
  if (row.disabled) {
    return { ok: false, reason: "disabled" };
  }
  if (row.expires_at && new Date(row.expires_at).getTime() <= Date.now()) {
    return { ok: false, reason: "expired" };
  }
  const usageLimit = resolveInviteCodeUsageLimit(row.type, row.max_uses, invitePolicy);
  if (usageLimit !== null && row.used_count >= usageLimit) {
    return { ok: false, reason: usageLimitRejectReason(row.type) };
  }
  return { ok: true };
}

function asUserInviteCode(
  row: InviteCode,
): PeekUserInviteCodeResult {
  if (row.type !== "user") {
    return { ok: false, reason: "not_user_code" };
  }
  if (!row.owner_user_id?.trim()) {
    return { ok: false, reason: "missing_owner" };
  }
  return {
    ok: true,
    code: {
      ...row,
      type: "user",
      owner_user_id: row.owner_user_id.trim(),
    },
  };
}

async function selectInviteCode(
  code: string,
  client: InviteCodeClient,
  caller: string,
): Promise<{ ok: true; code: InviteCode } | { ok: false; reason: "not_found" }> {
  const { data: row, error } = await client
    .from(TABLE)
    .select("*")
    .eq("code", code)
    .maybeSingle();

  if (error) {
    throw new Error(`${caller} select failed: ${error.message}`);
  }
  if (!row) {
    return { ok: false, reason: "not_found" };
  }
  return { ok: true, code: row as InviteCode };
}

export async function peekInviteCode(
  code: string,
  client?: InviteCodeClient,
  options?: ConsumeInviteCodeOptions,
): Promise<PeekInviteCodeResult> {
  const supabase = client ?? createServiceClient();
  const invitePolicy = await resolveInvitePolicyForConsume(supabase, options);
  const selected = await selectInviteCode(code, supabase, "peekInviteCode");
  if (!selected.ok) return selected;

  const available = validateInviteCodeAvailability(selected.code, invitePolicy);
  if (!available.ok) return available;
  return selected;
}

export async function peekUserInviteCode(
  code: string,
  client?: InviteCodeClient,
  options?: ConsumeInviteCodeOptions,
): Promise<PeekUserInviteCodeResult> {
  const supabase = client ?? createServiceClient();
  const invitePolicy = await resolveInvitePolicyForConsume(supabase, options);
  const selected = await selectInviteCode(code, supabase, "peekUserInviteCode");
  if (!selected.ok) return selected;

  const typeResult = asUserInviteCode(selected.code);
  if (!typeResult.ok) return typeResult;

  const available = validateInviteCodeAvailability(selected.code, invitePolicy);
  if (!available.ok) return available;
  return typeResult;
}

export async function commitUserInviteUse(
  code: string,
  client?: InviteCodeClient,
  options?: ConsumeInviteCodeOptions,
): Promise<CommitUserInviteUseResult> {
  const supabase = client ?? createServiceClient();
  const invitePolicy = await resolveInvitePolicyForConsume(supabase, options);
  const MAX_RETRIES = 10;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const peeked = await peekUserInviteCode(code, supabase, { invitePolicy });
    if (!peeked.ok) return peeked;

    const expected = peeked.code.used_count;
    const next = expected + 1;
    const { data: updated, error } = await supabase
      .from(TABLE)
      .update({ used_count: next })
      .eq("code", code)
      .eq("type", "user")
      .eq("used_count", expected)
      .select("*")
      .maybeSingle();

    if (error) {
      throw new Error(`commitUserInviteUse update failed: ${error.message}`);
    }
    if (updated) {
      return asUserInviteCode(updated as InviteCode) as CommitUserInviteUseResult;
    }
  }

  const finalResult = await peekUserInviteCode(code, supabase, { invitePolicy });
  if (!finalResult.ok) return finalResult;
  return { ok: false, reason: "invite_limit_reached" };
}

/**
 * 原子递增 used_count。
 *
 * SQL 等价（PostgREST 表示）：
 *   UPDATE invite_codes
 *      SET used_count = used_count + 1
 *    WHERE code = $1
 *      AND disabled = false
 *      AND used_count < max_uses
 *      AND (expires_at IS NULL OR expires_at > now())
 *   RETURNING *;
 *
 * PostgREST 的 .update() 已在 WHERE 同时下发 SET，整体在单条 SQL 内完成，原子。
 */
export async function consumeInviteCode(
  code: string,
  client?: InviteCodeClient,
  options?: ConsumeInviteCodeOptions,
): Promise<ConsumeResult> {
  const supabase = client ?? createServiceClient();
  const invitePolicy = await resolveInvitePolicyForConsume(supabase, options);

  const MAX_RETRIES = 10;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const { data: row, error: selErr } = await supabase
      .from(TABLE)
      .select("*")
      .eq("code", code)
      .maybeSingle();

    if (selErr) {
      throw new Error(`consumeInviteCode select failed: ${selErr.message}`);
    }
    if (!row) {
      return { ok: false, reason: "not_found" };
    }

    const current = row as InviteCode;
    const available = validateInviteCodeAvailability(current, invitePolicy);
    if (!available.ok) return available;

    const expected = current.used_count;
    const next = expected + 1;
    const { data: updated, error: updErr } = await supabase
      .from(TABLE)
      .update({ used_count: next })
      .eq("code", code)
      .eq("used_count", expected)
      .select("*")
      .maybeSingle();

    if (updErr) {
      throw new Error(`consumeInviteCode update failed: ${updErr.message}`);
    }

    if (updated) {
      return { ok: true, code: updated as InviteCode };
    }
  }

  const { data: finalRow } = await supabase
    .from(TABLE)
    .select("type, max_uses, used_count")
    .eq("code", code)
    .maybeSingle();
  if (finalRow) {
    const typed = finalRow as Pick<InviteCode, "type" | "max_uses" | "used_count">;
    const usageLimit = resolveInviteCodeUsageLimit(
      typed.type,
      typed.max_uses,
      invitePolicy,
    );
    if (usageLimit !== null && typed.used_count >= usageLimit) {
      return { ok: false, reason: usageLimitRejectReason(typed.type) };
    }
  }

  return { ok: false, reason: "exhausted" };
}

/**
 * 补偿减 1。
 * 幂等：used_count > 0 才减；失败只记日志，不抛。
 */
export async function refundInviteCode(
  code: string,
  client?: InviteCodeClient,
): Promise<void> {
  try {
    const supabase = client ?? createServiceClient();
    const { data: row, error: selErr } = await supabase
      .from("invite_codes")
      .select("used_count")
      .eq("code", code)
      .maybeSingle();
    if (selErr) {
      console.error(`[invite-code] refund select failed: ${selErr.message}`);
      return;
    }
    if (!row || row.used_count <= 0) {
      return;
    }
    const expected = row.used_count;
    const { error: updErr } = await supabase
      .from("invite_codes")
      .update({ used_count: expected - 1 })
      .eq("code", code)
      .eq("used_count", expected);
    if (updErr) {
      console.error(`[invite-code] refund update failed: ${updErr.message}`);
    }
  } catch (e) {
    console.error(`[invite-code] refund threw: ${e instanceof Error ? e.message : String(e)}`);
  }
}
